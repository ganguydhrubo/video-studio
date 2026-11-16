import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import studio
from video_studio.util import resource_path

HTML = resource_path("studio_ui", "index.html").read_text(encoding="utf-8")
SCRIPT = HTML.split("<script>")[1].split("</script>")[0]


class FakeJob:
    made = []

    def __init__(self, kind, args, root, title):
        self.id, self.kind, self.args, self.root, self.title, self.status = "job" + str(len(FakeJob.made)), kind, args, root, title, "running"
        FakeJob.made.append(self)

    def start(self):
        self.started = True


class CinematicInterface(unittest.TestCase):
    def test_browse_stays_a_real_file_input_and_the_old_picker_is_never_called(self):
        self.assertIn('<input type="file" id="fileInput" class="file-chooser"', HTML)
        self.assertNotIn("/api/pick-files", HTML)
        self.assertIn('xhr.open("POST", "/api/upload")', SCRIPT)
        self.assertIn('"/api/local-file"', SCRIPT)

    def test_opening_can_be_skipped_and_respects_reduced_motion(self):
        self.assertIn('id="skipIntro"', HTML)
        self.assertIn("prefers-reduced-motion", HTML)
        self.assertRegex(SCRIPT, r"if \(seen \|\| STILL \|\| location\.search\.includes\(\"nointro\"\)\) el\.hidden = true")
        self.assertIn("setTimeout(finish, 3300)", SCRIPT)

    def test_every_real_stage_has_a_friendly_name_a_milestone_and_a_vignette(self):
        for stage in ("ingest", "transcribe", "sense", "plan", "cut", "render", "validate", "export", "analyze", "storyboard", "produce"):
            for table in ("FRIENDLY_STAGE", "STAGE_HELP", "WINS", "WIN_GLYPH"):
                body = re.search(table + r" = \{(.*?)\};", SCRIPT, re.S).group(1)
                self.assertRegex(body, r"\b" + stage + r":", f"{table} lacks {stage}")
        self.assertIn('EDIT_STAGES = ["ingest","transcribe","sense","plan","cut","render","validate","export"]', SCRIPT)

    def test_progress_is_counted_from_state_and_never_invented(self):
        self.assertIn("${done.length} of ${stages.length}", SCRIPT)
        for fake in ("% complete", "time remaining", "minutes left", " XP"):
            self.assertNotIn(fake, HTML)

    def test_raw_errors_are_replaced_with_recovery_text(self):
        self.assertNotIn("signal is aborted", HTML)
        self.assertNotIn("r.statusText", SCRIPT)
        self.assertIn("Your work is safe", SCRIPT)
        self.assertIn("Your clips and text are still here", SCRIPT)

    def test_polling_is_chained_so_requests_cannot_overlap(self):
        self.assertNotIn("setInterval(", SCRIPT)
        self.assertIn("pollTimer = setTimeout(loop, 1000)", SCRIPT)
        for card in ("busyCard.ai", "busyCard.pc", "busyCard.engines", "projectsBusy"):
            self.assertIn(card, SCRIPT)

    def test_six_providers_have_bundled_icons_and_separate_status_facts(self):
        icons = SCRIPT.split("const AI_ICONS=")[1].split("\n")[0]
        for key in ("opencode", "claude", "codex", "gemini", "qwen", "ollama"):
            self.assertIn(f'"{key}":', icons)
        for label in ("Installed", "Signed in", "Sign-in needed", "Worked in your edits", "Not yet proven in an edit", "Checking"):
            self.assertIn(label, SCRIPT)

    def test_no_remote_assets_are_loaded(self):
        self.assertIsNone(re.search(r"""(src|href)=["']https?://""", HTML))
        self.assertTrue(resource_path("assets", "fonts", "InstrumentSerif-Italic.ttf").is_file())

    def test_resume_continues_from_the_first_unfinished_stage_and_reuses_a_running_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project.json").write_text(json.dumps({"id": "t1", "name": "Demo", "kind": "edit", "settings": {}}), encoding="utf-8")
            FakeJob.made.clear()
            with patch.object(studio, "Job", FakeJob), patch.dict(studio.JOBS, {}, clear=True):
                first = studio.refine({"root": str(root), "action": "resume"})
                self.assertEqual(FakeJob.made[0].args, ["resume", "--project", str(root)])
                self.assertTrue(FakeJob.made[0].started)
                again = studio.refine({"root": str(root), "action": "resume"})
                self.assertEqual(first["job"], again["job"])
                self.assertEqual(len(FakeJob.made), 1)


if __name__ == "__main__":
    unittest.main()
