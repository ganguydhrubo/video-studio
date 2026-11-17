"""Offline voiceover: Kokoro-82M (Apache-2.0) when available, Windows SAPI otherwise.

Every voice line comes back with word timings, used for captions and for
cutting scenes to the voice. Sources, best first:
  1. the engine's own timings (SAPI SpeakProgress, Kokoro tokens)
  2. faster-whisper forced alignment of the generated audio
  3. proportional estimate by character count
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from video_studio import detect
from video_studio.engines import ff, ml
from video_studio.util import ToolError, console, read_json, run, sha, write_json

SAPI_SCRIPT = r"""
param([string]$Items, [string]$Voice, [int]$Rate)
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @"
using System; using System.Collections.Generic; using System.Speech.Synthesis; using System.Speech.AudioFormat;
public class VsSpeaker {
  public static string Speak(string text, string path, string voice, int rate) {
    var words = new List<string>();
    using (var s = new SpeechSynthesizer()) {
      if (!String.IsNullOrEmpty(voice)) { try { s.SelectVoice(voice); } catch {} }
      s.Rate = rate;
      s.SetOutputToWaveFile(path, new SpeechAudioFormatInfo(24000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
      s.SpeakProgress += (o, e) => words.Add(e.AudioPosition.TotalMilliseconds.ToString(System.Globalization.CultureInfo.InvariantCulture)
                                             + "\t" + e.CharacterPosition + "\t" + e.CharacterCount + "\t" + e.Text.Replace("\t", " "));
      // A bookmark after the last word gives where SAPI thinks the audio ends, on the same (wrong) clock as the
      // word positions. Python divides the real file length by it to get the true scale (see sapi_words).
      s.BookmarkReached += (o, e) => words.Add("#END\t" + e.AudioPosition.TotalMilliseconds.ToString(System.Globalization.CultureInfo.InvariantCulture));
      var p = new PromptBuilder();
      p.AppendText(text);
      p.AppendBookmark("vs_end");
      s.Speak(p);
      s.SetOutputToNull();
    }
    return String.Join("\n", words);
  }
}
"@
$list = Get-Content -Raw -Encoding UTF8 $Items | ConvertFrom-Json
$out = @()
foreach ($it in $list) {
  $w = [VsSpeaker]::Speak($it.text, $it.out, $Voice, $Rate)
  $out += [pscustomobject]@{ id = $it.id; words = $w }
}
$out | ConvertTo-Json -Depth 4 -Compress | Out-File -Encoding utf8 ($Items + ".result.json")
"""

KOKORO_VOICES = {"male": "am_michael", "female": "af_heart", "male_uk": "bm_george", "female_uk": "bf_emma",
                 "hindi_female": "hf_alpha", "hindi_male": "hm_omega"}


def speakable(text: str) -> str:
    """Normalise symbols so TTS reads them naturally."""
    text = text.replace("₹", " rupees ").replace("&", " and ").replace("%", " percent")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def choose_engine(preference: str) -> str:
    pref = (preference or "auto").lower()
    if pref.startswith("sapi") or pref.startswith("windows"):
        return "sapi"
    if pref in KOKORO_VOICES or pref.startswith(("af_", "am_", "bf_", "bm_", "hf_", "hm_")) or pref in ("kokoro", "auto"):
        if ml.kokoro_ready():
            return "kokoro"
        if pref not in ("auto",):
            console.warn("Kokoro voice is not set up; using the Windows voice. Enable it with: video-studio install kokoro")
    if detect.sapi_voices():
        return "sapi"
    raise ToolError("No offline voice engine found.", "Run `video-studio install kokoro` "
                    "or add a Windows voice in Settings > Time & language > Speech.")


def synthesize(lines: list[dict], out_dir: Path, voice: str, rate: int, speed: float, align: bool = True) -> dict[str, dict]:
    """lines: [{id, text}] -> {id: {path, duration, words:[{text,start,end}]}} (cached by content)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = choose_engine(voice)
    results: dict[str, dict] = {}
    todo = []
    for line in lines:
        text = speakable(line["text"])
        # "t2": SAPI word times before 1.9.0 were 1.5x too big, so old cached SAPI lines are made again
        key = sha([engine, voice, rate, speed, text] + (["t2"] if engine == "sapi" else []))
        wav = out_dir / f"{line['id']}_{key}.wav"
        meta = out_dir / f"{line['id']}_{key}.json"
        cached = read_json(meta)
        if wav.exists() and cached:
            results[line["id"]] = cached
        else:
            todo.append({"id": line["id"], "text": text, "out": str(wav), "meta": meta, "raw": line["text"]})
    if todo:
        console.step(f"Voiceover: {len(todo)} line(s) with {engine}" + ("" if engine == "sapi" else f" ({kokoro_voice(voice)})"))
        made = _kokoro(todo, voice, speed) if engine == "kokoro" else _sapi(todo, voice, rate)
        durations = {item["id"]: ff.duration(Path(item["out"])) for item in todo}
        need = [i for i in todo if len(made[i["id"]].get("words") or []) < len(i["raw"].split()) * 0.6]
        aligned = _batch_align(need, durations) if (need and align and ml.available("faster_whisper")) else {}
        for item in todo:
            dur = durations[item["id"]]
            words = made[item["id"]].get("words") or []
            source = made[item["id"]].get("source", engine)
            if item["id"] in aligned:
                words, source = aligned[item["id"]], "whisper-align"
            if not words:
                words, source = estimate_words(item["raw"], dur), "estimate"
            words = _map_to_script(words, item["raw"], dur)
            record = {"path": item["out"], "duration": dur, "words": words, "engine": engine, "timing": source}
            write_json(item["meta"], record)
            results[item["id"]] = record
    return results


def _batch_align(items: list[dict], durations: dict[str, float]) -> dict[str, list[dict]]:
    """Align many voice lines with one Whisper pass: join them with gaps, then split words back."""
    import tempfile

    gap = 0.6
    work = Path(tempfile.mkdtemp(prefix="vs-align-"))
    listing, offsets, t = [], {}, 0.0
    silence = work / "gap.wav"
    ff.ff(["-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", str(gap), "-c:a", "pcm_s16le", str(silence)])
    for item in items:
        offsets[item["id"]] = (t, t + durations[item["id"]])
        listing += [f"file '{Path(item['out']).as_posix()}'", f"file '{silence.as_posix()}'"]
        t += durations[item["id"]] + gap
    (work / "list.txt").write_text("\n".join(listing), encoding="utf-8")
    joined = work / "joined.wav"
    ff.ff(["-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(joined)])
    try:
        tr = ml.transcribe(joined, model="base.en")
    except ToolError as exc:
        console.warn(f"Word alignment unavailable ({str(exc).splitlines()[0][:100]}); estimating timings.")
        return {}
    out: dict[str, list[dict]] = {k: [] for k in offsets}
    for w in tr["words"]:
        mid = (w["start"] + w["end"]) / 2
        for key, (a, b) in offsets.items():
            if a - 0.05 <= mid <= b + 0.05:
                out[key].append({"text": w["text"], "start": round(max(0.0, w["start"] - a), 3), "end": round(max(0.0, w["end"] - a), 3)})
                break
    return {k: v for k, v in out.items() if v}


def kokoro_voice(voice: str) -> str:
    v = (voice or "auto").lower()
    if v in KOKORO_VOICES:
        return KOKORO_VOICES[v]
    if re.match(r"^[abhjefipz][fm]_", v):
        return v
    return "am_michael"


def _kokoro(todo: list[dict], voice: str, speed: float) -> dict:
    v = kokoro_voice(voice)
    lang = v[0]
    data = ml.kokoro([{"id": t["id"], "text": t["text"], "out": t["out"]} for t in todo], v, speed, lang)
    return {it["id"]: {"words": it.get("words", []), "source": "kokoro"} for it in data["items"]}


def _sapi(todo: list[dict], voice: str, rate: int) -> dict:
    voices = detect.sapi_voices()
    name = ""
    if voice and voice not in ("auto", "sapi", "windows"):
        name = next((v for v in voices if voice.lower() in v.lower()), "")
    if not name:
        name = next((v for v in voices if "zira" in v.lower() or "aria" in v.lower()), voices[0] if voices else "")
    tmp = Path(tempfile.mkdtemp(prefix="vs-sapi-"))
    script = tmp / "speak.ps1"
    script.write_text(SAPI_SCRIPT, encoding="utf-8-sig")
    items = tmp / "items.json"
    items.write_text(json.dumps([{"id": t["id"], "text": t["text"], "out": t["out"]} for t in todo]), encoding="utf-8")
    run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-Items", str(items), "-Voice", name, "-Rate", str(max(-10, min(10, rate)))], timeout=900)
    raw = json.loads(Path(str(items) + ".result.json").read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        raw = [raw]
    paths = {t["id"]: t["out"] for t in todo}
    out = {}
    for entry in raw:
        words = sapi_words(entry.get("words") or "", _wav_seconds(Path(paths.get(entry["id"], ""))))
        out[entry["id"]] = {"words": words, "source": "sapi", "voice": name}
    return out


# System.Speech reports SpeakProgress/Bookmark AudioPosition as if the audio were 16 kHz, whatever format the file is
# written in (measured on Zira and David at 8, 16, 22.05, 24, 44.1 and 48 kHz: positions grow exactly with the rate).
# We write 24 kHz, so raw positions are 1.5 times too big. Used only when the end bookmark is missing.
SAPI_CLOCK_SCALE = 16000 / 24000


def _wav_seconds(path: Path) -> float:
    import wave

    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (OSError, EOFError, wave.Error):
        try:
            return ff.duration(path)
        except Exception:  # noqa: BLE001 - no length known: the fixed scale is used
            return 0.0


def sapi_words(rows: str, duration: float) -> list[dict]:
    """SAPI SpeakProgress rows (ms, char pos, char count, text) + the "#END" bookmark -> true word times in seconds.

    The scale is the real audio length divided by the bookmark's position (the same wrong clock as the words), so it
    holds for any voice or output format; without a usable bookmark the measured 16 kHz clock is assumed."""
    words, end_ms = [], 0.0
    for row in rows.splitlines():
        parts = row.rstrip("\r").split("\t")
        if parts[0] == "#END" and len(parts) == 2:
            try:
                end_ms = float(parts[1])
            except ValueError:
                pass
        elif len(parts) == 4:
            try:
                words.append({"text": parts[3], "start": float(parts[0]) / 1000.0})
            except ValueError:
                continue
    scale = SAPI_CLOCK_SCALE
    if end_ms > 0 and duration > 0 and 0.25 <= duration / (end_ms / 1000.0) <= 4.0:
        scale = duration / (end_ms / 1000.0)
    for w in words:
        w["start"] = round(w["start"] * scale, 3)
        if duration > 0:
            w["start"] = min(w["start"], max(0.0, duration - 0.1))
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["start"] + 0.45
        w["end"] = round(max(w["start"] + 0.08, min(nxt, w["start"] + 0.9)), 3)
        if duration > 0 and i + 1 == len(words):
            w["end"] = round(max(w["start"] + 0.05, min(w["end"], duration)), 3)
    return words


def estimate_words(text: str, duration: float) -> list[dict]:
    tokens = text.split()
    total = sum(len(t) + 2 for t in tokens) or 1
    t, words = 0.05, []
    span = max(0.1, duration - 0.15)
    for tok in tokens:
        d = span * (len(tok) + 2) / total
        words.append({"text": tok, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return words


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _map_to_script(words: list[dict], script_text: str, duration: float) -> list[dict]:
    """Give timed words the script's own spelling/punctuation (captions show what the user wrote)."""
    script_words = script_text.split()
    if not words:
        return estimate_words(script_text, duration)
    if len(words) == len(script_words):
        return [{"text": s, "start": w["start"], "end": w["end"]} for s, w in zip(script_words, words)]
    # Greedy alignment on normalised tokens; unmatched script words inherit neighbour timing.
    out, j = [], 0
    for s in script_words:
        ns = _norm(s)
        match = None
        for k in range(j, min(j + 4, len(words))):
            nw = _norm(words[k]["text"])
            if nw and (nw == ns or ns.startswith(nw) or nw.startswith(ns)):
                match = k
                break
        if match is not None:
            out.append({"text": s, "start": words[match]["start"], "end": words[match]["end"]})
            j = match + 1
        else:
            prev_end = out[-1]["end"] if out else 0.05
            nxt = words[j]["start"] if j < len(words) else min(duration, prev_end + 0.35)
            out.append({"text": s, "start": prev_end, "end": max(prev_end + 0.08, nxt)})
    return out


def polish(src: Path, dest: Path) -> Path:
    """Broadcast-style voice chain: clean low end, presence, gentle compression, de-ess."""
    chain = ("highpass=f=70,lowpass=f=15000,"
             "equalizer=f=180:t=q:w=1.2:g=1.5,equalizer=f=3200:t=q:w=1.4:g=2.5,equalizer=f=7000:t=q:w=2:g=-1.5,"
             "acompressor=threshold=-20dB:ratio=3:attack=8:release=120:makeup=2,"
             "alimiter=limit=0.89")
    ff.ff(["-i", str(src), "-af", chain, "-ar", "48000", "-ac", "1", str(dest)])
    return dest
