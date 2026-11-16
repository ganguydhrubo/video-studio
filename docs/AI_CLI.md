# AI CLI integration

The studio never needs an AI to work: the local editor brain drafts every edit on its own, in any language. Since 1.3.0 an **AI editor reviews that draft by default**, using a free provider:

1. The rule-based story editor drafts the cut on this PC.
2. The AI editor gets every line (what was heard, its English meaning, how strongly it was said) plus the draft, and returns its own cut with a reason per change and a note of what footage would show each line (used to find B-roll).
3. The answer is checked before it is trusted (`story.accept_review`): unknown or fumbled lines are dropped, the opening line must stand alone and must not be a line the speech model mangled, everything after the opening returns to recorded order, a cut that is too thin is rejected, and an over-long cut is trimmed by story value. If the answer fails, the draft is used.

**Free first.** With `ai.provider = auto` (the default) only free providers are tried: OpenCode free models, then Ollama, Gemini, Qwen. Paid plans (Claude, Codex) are used only when you pick one (`--ai claude`, `ai use claude`); set `ai.paid_fallback true` to allow them as a fallback. `--no-ai` or `ai use none` keeps everything on this PC.

| Provider | Detected via | Sign-in | Headless call used | Cost |
|---|---|---|---|---|
| OpenCode | `opencode --version` | none for the free models | `opencode run --pure -m <free model> --format json`, prompt on stdin | Free models, no account |
| Claude Code | `claude auth status --json` | `video-studio ai login claude` → `claude` login flow | `claude -p --output-format json --no-session-persistence` + tools disabled, prompt on stdin | Included in your Claude plan |
| OpenAI Codex | `codex login status` | `codex login` | `codex exec --skip-git-repo-check --sandbox read-only --ephemeral` | Included in ChatGPT plan |
| Gemini CLI | `gemini --version` + credentials file exists (never read) | `gemini` | `gemini -p ... --output-format json --approval-mode plan` | Free tier |
| Qwen Code | `qwen --version` + credentials file exists | `qwen` | `qwen -p ...` | Varies |
| Ollama | `ollama list` | none (local) | `ollama run <model>` | Free, fully offline |

- **Choosing a provider:** `video-studio ai use claude`, `ai fallback claude codex ollama`, or `--ai codex` for a single project. `--no-ai` (or `--ai none`) uses local rules only.
- **Health:** a failed call is remembered and shown in `ai status` and `doctor` (with the fix) until a later call succeeds. The Director then falls back to the next provider automatically.
- **Gemini personal login:** as of Nov 2026, Gemini CLI 0.65 rejects personal OAuth accounts as "ineligible" and points to Antigravity. The free fix is to create a key at aistudio.google.com and set `GEMINI_API_KEY` in your user environment.
- **Privacy:**
  - Only the transcript or script text and your brief are sent to the provider you chose; video frames are sent only with `validate --ai-review`.
  - No tokens are read, stored or forwarded.
  - Replies are cached in `%LOCALAPPDATA%\VideoStudio\cache\ai`, and the call budget defaults to 4 per project.
