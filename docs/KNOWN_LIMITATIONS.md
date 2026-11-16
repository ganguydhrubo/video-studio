# Known limitations and recovery

## Honest limits

- **No generative AI video.** Avatar, Arcane or Ramayana-level CGI and text-to-video models (Wan, LTX, Hunyuan) need a 12–24 GB+ NVIDIA GPU. On a 16 GB, Intel-iGPU laptop the studio uses Blender 3D, motion graphics and your own footage instead. A ComfyUI adapter for a remote or free GPU is planned, not built.
- **Blender on an iGPU is slow.** EEVEE takes about 25–35 s per frame at standard quality (1080×1920 × 0.75) on Intel UHD. Use `--quality draft` for previews or `--no-3d` for speed. 3D renders are resumable.
- **Transcription speed.**
  - On CPU, large-v3-turbo takes about 2 minutes per minute of speech; `small` takes about 5 minutes per minute (it is slower here because of how the model is cached).
  - `--whisper-model auto` picks the best model already downloaded.
  - Accuracy on Hinglish is good but not perfect, and some English loanwords come out misspelled in Devanagari. `--caption-script roman` uses the AI to transliterate and fix them.
- **The keep/cut model is not trained yet.** `video-studio learn` trains on finished edits and is tested on videos it
  never saw. On the owner's 8 past edits (6 recordings, 2,164 words) it found 59% of the cuts but also cut 41% of the
  kept words, so it stays OFF; it switches on automatically only when it beats the built-in rules on unseen videos.
- **Style learning measures, it does not imitate content.** Cut rhythm, beat sync, punch-ins and colour are learned
  from your reference videos. Text graphics, stickers, 3D props and AI effects inside those references are not.
- **Effects render at about 15 minutes of computer time per minute of footage** on an i7-13620H (16 threads) with
  full effects. `--fx clean` or `--fx off` is much faster. Hardware encoding does not help: encoding is not the slow step.
- **Roman (Hinglish) captions without AI** use a built-in transliterator plus an English-loanword dictionary. Common
  words come out right (product, launch, digital, tax, window), but rare English words may be spelled phonetically.
- **Montage shot variety depends on the footage.** A clip with only a few distinct shots will reuse them.
- **Retake detection is lexical.** It catches restarts of the same words; it does not judge delivery quality. The AI director can drop weak lines, and you can edit the `keep` list in `build/edit_plan.json`, then run `video-studio resume --from cut`.
- **The story editor judges meaning through a local translation.** Where the speech model skips a stretch, that stretch
  is re-translated line by line. Where translation is still missing, English terms are read from the line itself.
  Mis-heard words are mis-read ("store" heard as "story"; "Tally" not caught). It was tuned on Hinglish talking-head
  reels and has not been blind-rated yet.
- **The AI editor is a second opinion, not a trained model.** It is a general language model (a free one by default) given
  the transcript and a senior-editor brief. Nothing is trained or fine-tuned on videos. It reads text only: it does not
  see your face, framing or footage. A free model takes about 2 minutes to answer and can be busy; the local draft is
  then used. The transcript text leaves this PC when the AI editor is on (`--no-ai` keeps it local).
- **Speech the model skips.** Whisper sometimes loses its place and skips a stretch. The studio now ends over-stretched
  words, finds stretches where the translation heard speech but no words were kept, and listens to them again. Speech
  that is skipped in both passes is still missed (no captions there).
- **B-roll uses only your own clips.** Nothing is downloaded or generated. A line is matched only when it shares a
  meaningful (rare) word with a clip, so a small library gives few cutaways. The first index of each clip takes a few
  minutes on CPU (light "small" speech model). It is cached in `%LOCALAPPDATA%\VideoStudio\broll_index`.
- **OpenCode free models change.** The free list (DeepSeek, GLM, MiniMax, MiMo, Big Pickle) is set by OpenCode and may
  be retired or busy. The studio tries the next one, then the next provider. The local rules always work (`--no-ai`).
- **Gemini personal login** is currently rejected by Google ("ineligible"); set `GEMINI_API_KEY`.
- **Qwen OAuth** may need a paid plan.
- **Script mode is Windows-only.** The AI CLIs and Studio are cross-platform in principle, but SAPI, the paths and the installer are Windows-only.
- **Studio is single-user and local.** It binds to 127.0.0.1 only, POSTs need a per-run token, and media serving is restricted to the workspace.
- **Not code-signed.** SmartScreen may warn on first run ("More info › Run anyway"). The SHA256 is in `dist/SHA256SUMS.txt`.

## Recovery

- Open the **Video Studio** desktop/Start-menu shortcut or `VideoStudio.exe`, which keeps the server independent of a console window. The explicit CLI command `video-studio studio` still runs in the foreground: closing that terminal stops that instance.
- If no browser opens, inspect `%LOCALAPPDATA%\VideoStudio\logs\startup.log` and `launcher-error.log`. Installer errors go to `install.log` in the same folder.
- Portable users must extract the entire folder, including `_internal`. A bare executable copied out of that folder cannot run.

| Problem | Fix |
|---|---|
| A stage failed | Read the hint printed with the error, then `video-studio resume`. Finished work is reused. |
| Redo from a stage | `video-studio resume --from plan` (or `cut`, `render`, `produce`) |
| Disk filling up | `video-studio clean` (keeps master + exports) |
| A tool is missing or broken | `video-studio doctor` shows a fix per row; `video-studio install <tool>` |
| AI provider failing | `video-studio ai status` → fix or `ai use <other>`; `--no-ai` always works |
| Blender EEVEE crash | recorded automatically; the next renders use Cycles (learned fix). `video-studio memory failures` |
| Bad taste learned | `video-studio memory taste`, `memory reset-taste`, `memory delete <id>`, `memory wipe` |
| Uninstall | Settings › Apps › Video Studio (projects and memory kept; `uninstall.ps1 -RemoveData` removes them) |
