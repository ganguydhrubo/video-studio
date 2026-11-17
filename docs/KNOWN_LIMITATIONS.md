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

## Interface 1.6.0

- "Proven in an edit" for an AI provider is read from finished projects in the current projects folder. A provider that worked only in a deleted project shows "Not yet proven in an edit".
- An original-versus-finished comparison is not in the result view. The source preview on Create is the only place the original plays.
- The plain reason shown for a stopped edit is matched from the engine's last lines while the app is open. After a restart the card gives the general message; the full lines stay under Technical details only for the session that ran the edit.
- The opening and the animated world need WebGL. Without it a still backdrop is shown.

## Editing instructions 1.7.0

- Times in the instructions are read in English, Hindi and Bengali (digits, "two sec", "২ সেকেন্ড", "2 सेकंड"). Other languages, ranges ("30-45 sec") and more than one total length in one brief are not understood yet; when unsure, the whole recording is kept and a question is shown.
- The free model's corrected wording is used only when it keeps every time and shortening instruction the same and does not add text. Otherwise your own wording is used.
- Gemini CLI with a personal Google sign-in can be refused by Google ("not eligible"). Takebloom then tries the next free AI first for six hours. A free GEMINI_API_KEY from aistudio.google.com avoids the refusal.
- With no shorter length asked for, every spoken line is kept in recorded order. An AI editor's cut that drops lines is refused. This keeps the message whole, but it also keeps weak lines; ask for a length or "shorter" to let the editor cut.
- The learned keep/cut model only gives advice when a validated one exists. It never cuts on its own. No audience or retention model exists.

## Production features 1.8.0

- AI clip generation was removed in 1.9.0 (see below).
- Hardware limits, speeds and expected times are estimates for the kind of PC until Takebloom has measured this PC on a finished edit. Hardware encoders are tested with a one-frame encode; NVIDIA NVENC and AMD AMF were not available on the release-test PC.
- The background cut-out runs on the processor at about 4 to 7 frames a second on the release-test laptop. A 60-second clip takes several minutes. Hair edges are soft, thin objects (glasses arms) can be partly cut, and a dark area next to a person can bleed into the cut-out. Transparent WebM is slow to encode. Phone video with a variable frame rate may drift slightly. Cut-out masks are cached in the app's cache folder until you clean it.
- In-app update install and rollback were tested with unit tests, not by installing a real update. Rollback is possible only after an update, when the installer has kept the previous copy. When running from source code, updating is done with git.
- Photo videos take still pictures only (JPG, PNG, WEBP; HEIC only if FFmpeg can read it). Live Photos and RAW files are not supported. Titles are placed as captions; there is no per-picture title design yet.
- Restyle has no per-shot reframing that follows a face. The frame shape is a centred crop or a blurred fill.
- The taskbar logo is set on the Edge app window by the small launcher that stays open while the window is open. Windows opened another way (for example from the browser) show the page icon only.

## Sharing and covers 1.8.1

- Takebloom does not post to Instagram, YouTube, TikTok, Facebook, LinkedIn, X or WhatsApp by itself. Posting by itself needs each site's developer approval and your sign-in, so each card opens the site's upload page and copies the caption. You still pick the video and press Post.
- "Share with apps on this PC" uses the Windows share panel. It lists only apps installed on this PC that accept videos. If Windows refuses the first press (it needs a fresh click), press it again.
- Covers are drawn from the finished video in the app window. The reel cover replaces the automatic thumbnail; the automatic one is kept once as `*_thumbnail_auto.jpg`. Instagram and TikTok let you upload a reel cover; YouTube Shorts only lets you pick a frame from the video.
- Videos the window cannot play (iPhone HEVC .MOV without the HEVC extension) get a small H.264 preview copy in `_previews`. It takes a few seconds per minute of video. The edit always uses your original file.
- Script-to-video makes animated text scenes (kinetic words, stats, quotes, calls to action) on a moving background. Real pictures appear only with `[IMAGE: path]`; 3D needs Blender. The highlighted caption word can land slightly early or late inside a word.

## Automatic editing and prompts 1.9.0

- Takebloom now edits only footage you shot. Script-to-video and AI clips (text-to-video, picture-to-video) are no longer in the app. The script engine still exists in the command line so older script projects can open and continue; it is not offered for new work.
- The edit starts by itself 5 seconds after every dropped file has finished copying. Clicking into the prompt or changing any option pauses it, and then you press Create my draft. If you write the prompt first and then drop the videos, it starts by itself with your prompt.
- One automatic retry: if the engine stops with an error, it tries once more from the stage where it stopped. A second stop is shown to you. A problem that is not temporary (for example missing speech tools, no clear speech) fails the same way twice.
- Covers (reel 9:16, profile grid 3:4, YouTube 16:9) are made by themselves from the hook frame with the film's title in bold white. The title is drawn by FFmpeg with a Windows font (Segoe UI Bold; Nirmala UI for Hindi, Bengali and other Indian scripts). Line breaks are estimated from letter counts, so a very long word can come close to the edge. Covers you save from the cover maker are never replaced; automatic covers are remade after a re-edit.
- Pictures added to an edit appear once each, in the order added, as short moving cutaways (1.2 to 2.6 seconds) while the sound carries on. A short film fits fewer pictures; the log says how many were left out. Pictures never cover the first moment, the last second, a giant-text moment or a B-roll cutaway. You cannot yet choose where a picture goes.
- A song in the drop is used as the music, from its start. Only one song per film. With "no music" in the prompt and a song in the drop, the song is still used.
- The prompt is read by fixed word rules (English with some common Hinglish words), not by AI, so it works offline and instantly. Words outside the rules are not turned into settings, but the AI editor still reads the whole prompt for the story. Unusual phrasing may be missed; the "Takebloom will" list shows exactly what was understood before the edit starts. Fine-tune choices always win over the prompt.
- Not possible in an edit yet, and said so when asked: 3D objects or 3D text, slow motion inside an edit (use Restyle), a logo or watermark placement, a new voice-over, created or AI-generated footage. Stock footage needs your own free key (see the J-cuts, L-cuts and free stock footage section). Background removal is the separate Remove background tool.

## Speech 1.9.0

- Word times from speech recognition (faster-whisper) are close, not exact. On clear test speech a word's start is off by about 60 ms on average; with loud background noise, about 100 to 135 ms. Whisper tends to start words a little early, so a highlight usually lights up just before the word, not after it.
- Takebloom now moves a word's start out of a pause when Whisper put it there (this was up to 0.8 s early). It only does this when there is a clear pause of at least 0.12 s. A word that runs straight on from the last one keeps Whisper's time, and some starts can still be 0.3 to 0.7 s early in noisy audio.
- With music or constant loud noise under the voice, there may be no clear pause. Then no start is moved.
- The Windows voice (SAPI) now gives true word times. Before 1.9.0 they were 1.5 times too big, so captions on a Windows voice ran later and later along each line. Voice lines made by older versions are made again once, because their saved times were wrong.
- The Kokoro voice still gives no word times of its own. Its lines are timed by Whisper (base.en), with the same limits as above.
- Karaoke captions now wait through any gap between two words' times. Before 1.9.0 such a gap made the later words in the line light up early. In a real test edit, every karaoke word lit up between 160 ms before and 26 ms after it was heard (57 ms on average).
- Faster batched speech recognition exists only as a test option of the helper (`--batch`). It was 13 to 28% faster on this laptop, but in one test it lost a word, and when its safety check throws a part away it throws away up to 30 s of speech. It is off; normal recognition is unchanged.
- Speaker detection (who is speaking) is not in 1.9.0.
