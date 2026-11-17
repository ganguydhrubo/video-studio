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
- Not possible in an edit yet, and said so when asked: 3D objects or 3D text, slow motion inside an edit (use Restyle), a logo or watermark placement, a new voice-over, stock footage, created or AI-generated footage. Background removal is the separate Remove background tool.

## Speech 1.9.0

- Word times from speech recognition (faster-whisper) are close, not exact. On clear test speech a word's start is off by about 60 ms on average; with loud background noise, about 100 to 135 ms. Whisper tends to start words a little early, so a highlight usually lights up just before the word, not after it.
- Takebloom now moves a word's start out of a pause when Whisper put it there (this was up to 0.8 s early). It only does this when there is a clear pause of at least 0.12 s. A word that runs straight on from the last one keeps Whisper's time, and some starts can still be 0.3 to 0.7 s early in noisy audio.
- With music or constant loud noise under the voice, there may be no clear pause. Then no start is moved.
- The Windows voice (SAPI) now gives true word times. Before 1.9.0 they were 1.5 times too big, so captions on a Windows voice ran later and later along each line. Voice lines made by older versions are made again once, because their saved times were wrong.
- The Kokoro voice still gives no word times of its own. Its lines are timed by Whisper (base.en), with the same limits as above.
- Karaoke captions now wait through any gap between two words' times. Before 1.9.0 such a gap made the later words in the line light up early. In a real test edit, every karaoke word lit up between 160 ms before and 26 ms after it was heard (57 ms on average).
- Faster batched speech recognition exists only as a test option of the helper (`--batch`). It was 13 to 28% faster on this laptop, but in one test it lost a word, and when its safety check throws a part away it throws away up to 30 s of speech. It is off; normal recognition is unchanged.
- Speaker detection (who is speaking) is not in 1.9.0.

## Speed 1.9.0

- Measured on one 55-second 1080x1920 test clip ("45 second reel, bold, remove mistakes", free AI off) on the release-test laptop (16 processor threads), old code and new code back to back. Other work was running on the PC at the same time, so the times move by tens of seconds from run to run. The whole edit took 8 min 53 s before and 6 min 08 s after. Your own times depend on the clip, the look and the PC.
- The cut, the transcript, the footage reading, the edit plan, the style plan and the cleaned voice are exactly the same files as before. The styled video matches the old one-worker style pass on the same plan: every frame is at least 42.8 dB PSNR (45.7 dB on average) with several workers, and at least 45.3 dB with one worker. The small difference is the video encoding, not the drawing.
- The style pass runs in two steps: first it follows your face and outline, then it draws the frames. Both steps run in several processes at once. Each tracking process needs about 0.5 GB and each drawing process about 0.8 GB at 1080x1920; 1.5 GB is always left free, and at most 6 of each run. With little free memory it falls back to one of each, which is still faster than before but not by much. More processes at once means the whole edit can use more memory in total (2.8 GB before, 3.9 GB after on the test PC), even though no single process is as big as before (1.1 GB before, 0.7 GB after).
- The style pass now reads the video twice (once to follow you, once to draw). On a very slow disk this costs a little time.
- Before 1.9.0, a style pass split across several workers started the camera from the centre again in every part and could start a part one frame early or late (the cut has small time gaps where pieces join). Both are fixed: one worker and many workers now give the same video.
- The final video is encoded with the fastest working hardware encoder. A quick test (about 15 to 30 seconds, once per FFmpeg version) picks it; the result is kept in `encoder.json` in the app folder. A hardware encoder is used only when it is at least 1.25 times faster and its picture is within 1 VMAF point of x264. If a hardware encode fails, the video is encoded again with x264 and that encoder is not used again on this PC. Only Intel Quick Sync was tested on a real PC; NVIDIA NVENC and AMD AMF settings are only checked by unit tests. To force one, set `render.encoder` in config.json (for example `libx264`).
- Reading the footage runs in up to 4 parts at once (same result as one part), and the voice clean-up and the voice analysis now start in the background earlier. The noise reduction itself still uses one processor core. Transcription speed is unchanged.

## Cut-out 1.9.0

- "Best quality" is optional. It uses BiRefNet lite (MIT licence). Its 214 MB model is downloaded only when you tick the download box. Its SHA-256 is checked; the publisher gives no checksum, so the check uses the value of the release file as downloaded on 17 Nov 2026.
- Best quality runs on the processor and is much slower than Fast. On the 13th-gen i7 test laptop, with other jobs running, one BiRefNet frame took about 17 to 32 seconds. A 6-second 1080x1920 clip took about 4 minutes (249 seconds; Fast: 23 seconds). Use it for short shots that need clean edges.
- To stay usable, BiRefNet runs only on 2 keyframes a second, at every shot change and usually on the last frame. Frames in between move the keyframe edge with optical flow. Fast moves between keyframes can smear the edge for a few frames; where the two keyframes disagree, MediaPipe decides, which can bring back MediaPipe's softer edge there.
- BiRefNet cuts out every main subject, not only people. A helmet or bag held up to the camera stays in the cut-out (Fast often drops it).
- The graphics card is not used: DirectML failed on the test PC's Intel graphics, and Takebloom does not install the DirectML package (it clashes with the voice package). If DirectML happens to be installed and fails, the processor is used.
- The time shown before a best-quality run comes only from speeds measured on this PC (the first run measures it). Before that, the hardware check shows a rough figure from the test laptop, marked as an estimate.
- Best quality needs a lot of memory: the helper peaked at about 6 to 6.5 GB (the model's fixed 1024 x 1024 input sets this floor). It is refused when less than 5 GB is free, and the hardware check warns on PCs with under 12 GB of memory.
- Only tested on a synthetic clip made from a public-domain still photo (camera pan, zoom and one hard cut), not on real footage of a moving person. Hair against a busy background, motion blur and several people were not tested.
- There is no spill (green or colour fringe) removal and no object picker; the whole main subject is kept.

## Learning from your edits 1.9.0

- Learning is counting and simple maths on this PC (a Beta score per choice, the same kind the taste model already used). No AI is trained and nothing is sent anywhere. Everything is kept in one file, `edit_signals.jsonl`, in the app's data folder. "Forget what you learned" (System page) deletes it, the taste scores and the keep or cut advice trained on your reviews. Films and projects are not touched.
- What counts: the choices your prompt or Fine-tune made, one-click refines (Faster, Calmer, More energy, Less zoom, No captions, No music, Try another look), the lines you tick or untick on the Transcript tab, covers you save, downloads, Share actions (Windows share, a site's page, Show in folder), stopping an edit, editing the same clips again with a different choice, restyle choices, and stars with notes. Simply watching a film counts nothing.
- Learned choices cover pace, captions, music mood, music on or off, motion, transitions, colour look and effects strength. Format, length, the story and the opening are not predicted.
- A learned choice is used only when your prompt and Fine-tune say nothing about it, no style template or "Edit like" sets it, and the evidence is strong: you chose it yourself at least once, chose or kept it in at least 2 of your last 6 edits, your latest own choice was the same, and its score is clearly ahead. Two edits with the same choice plus a download is usually enough; two choices alone are not. The "Takebloom will" card says each one with its real count. Anything you write or pick always wins.
- "Kept" means a choice Takebloom applied for you that you then downloaded, shared or rated 4 or 5 stars without changing it. Choices Takebloom made by itself never become "your taste" from downloads alone.
- Old evidence fades: each signal counts 0.85 to the power of the number of edits since it. The taste scores that pick Takebloom's own choices also fade a little (0.97) at every new edit, ratings included.
- Downloads are counted from the app's own download links. Saving the file another way (copying it from File Explorer) is not seen. A download and a share of the same film version count once.
- "Less zoom" asks for subtle motion and clean effects. In a footage edit the zooms come from the effects strength, so the motion setting on its own changes little.
- Keep or cut advice is retrained in the background after an edit finishes, once at least 2 different recordings have been reviewed and something new was reviewed. It uses the words of each reviewed project, so deleting a project removes its labels. The advice is shown only when it beats the built-in rules on recordings it was not trained on; it never cuts by itself. A model you trained by hand (`video-studio learn`) is kept unless it is off and the new one is on.
- Keep or cut retraining was run for real only on two short test clips (21 and 36 spoken words, 6 lines reviewed). That is far too little: the model trained but stayed off because it did not beat the built-in rules. How many reviews it needs before it helps is not known yet.
- A re-edit (review, refine) picks the choices Takebloom makes by itself again, so music or transitions you did not set can change after a re-edit. This was already so before 1.9.0; learning makes the new pick follow your taste more often, not always.
