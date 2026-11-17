"""Free stock B-roll: off without a key, only search words leave the PC, small HD copies, credits written.
Fully offline: the HTTP answers below are recorded in the shape the Pexels and Pixabay API documents give."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_studio import config, stock
from video_studio.understand import edit_args, understand

PEXELS = {
    "page": 1, "per_page": 15, "total_results": 2, "url": "https://www.pexels.com/search/videos/tea/",
    "videos": [
        {"id": 111, "width": 2160, "height": 3840, "url": "https://www.pexels.com/video/tea-leaves-111/",
         "image": "https://images.pexels.com/videos/111/x.jpg", "duration": 14,
         "user": {"id": 9, "name": "Asha Rao", "url": "https://www.pexels.com/@asha"},
         "video_files": [
             {"id": 1, "quality": "uhd", "file_type": "video/mp4", "width": 2160, "height": 3840, "fps": 25, "link": "https://videos.pexels.com/uhd.mp4"},
             {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1080, "height": 1920, "fps": 25, "link": "https://videos.pexels.com/hd.mp4"},
             {"id": 3, "quality": "sd", "file_type": "video/mp4", "width": 360, "height": 640, "fps": 25, "link": "https://videos.pexels.com/sd.mp4"},
             {"id": 4, "quality": "hls", "file_type": "video/mp4", "width": None, "height": None, "fps": None, "link": "https://x/hls"}],
         "video_pictures": [{"id": 1, "picture": "https://x/p.jpg", "nr": 0}]},
        {"id": 222, "width": 3840, "height": 2160, "url": "https://www.pexels.com/video/garden-222/", "duration": 30,
         "user": {"id": 8, "name": "Ravi", "url": "https://www.pexels.com/@ravi"},
         "video_files": [{"id": 5, "quality": "uhd", "file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://x/4k.mp4"}]},
    ]}

PIXABAY = {
    "total": 2, "totalHits": 2,
    "hits": [
        {"id": 125, "pageURL": "https://pixabay.com/videos/id-125/", "type": "film", "tags": "tea, garden, hills", "duration": 12,
         "videos": {"large": {"url": "https://cdn.pixabay.com/large.mp4", "width": 3840, "height": 2160, "size": 40000000, "thumbnail": ""},
                    "medium": {"url": "https://cdn.pixabay.com/medium.mp4", "width": 1920, "height": 1080, "size": 9000000, "thumbnail": ""},
                    "small": {"url": "https://cdn.pixabay.com/small.mp4", "width": 1280, "height": 720, "size": 4000000, "thumbnail": ""},
                    "tiny": {"url": "https://cdn.pixabay.com/tiny.mp4", "width": 960, "height": 540, "size": 2000000, "thumbnail": ""}},
         "views": 1, "downloads": 1, "likes": 1, "comments": 0, "user_id": 1281706, "user": "Coverr-Free-Footage", "userImageURL": ""},
        {"id": 126, "pageURL": "https://pixabay.com/videos/id-126/", "duration": 5,
         "videos": {"large": {"url": "", "width": 0, "height": 0, "size": 0}, "medium": {"url": "", "width": 0, "height": 0, "size": 0},
                    "small": {"url": "", "width": 0, "height": 0, "size": 0}, "tiny": {"url": "", "width": 0, "height": 0, "size": 0}},
         "user_id": 2, "user": "x"},
    ]}


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": self.tmp.name}, clear=False)
        self.env.start()
        os.environ.pop("PEXELS_API_KEY", None)
        os.environ.pop("PIXABAY_API_KEY", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Keys(Home):
    def test_no_key_means_nothing_is_searched(self):
        self.assertEqual(stock.keys(), {})
        self.assertFalse(stock.has_key())

    def test_keys_live_in_the_app_settings_and_are_never_shown(self):
        config.set_value("stock.pexels_key", "SECRET123")
        self.assertEqual(stock.keys(), {"pexels": "SECRET123"})
        self.assertEqual(config.shown("stock.pexels_key", config.get("stock.pexels_key")), "(set, hidden)")
        self.assertEqual(config.shown("stock.pixabay_key", ""), "")
        self.assertEqual(config.shown("defaults.fps", 30), 30)

    def test_environment_keys_work_too(self):
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "abc"}):
            self.assertEqual(stock.keys(), {"pixabay": "abc"})

    def test_config_list_prints_no_key(self):
        from video_studio import cli

        config.set_value("stock.pixabay_key", "TOPSECRET")
        lines = []
        with patch.object(cli.console, "info", side_effect=lambda s: lines.append(s)):
            cli.cmd_config(SimpleNamespace(cfg_cmd="list"))
        self.assertFalse(any("TOPSECRET" in x for x in lines))
        self.assertTrue(any("stock.pixabay_key" in x and "hidden" in x for x in lines))


class Prompt(Home):
    def test_without_a_key_the_card_says_how_to_add_one(self):
        r = understand("30 second reel with some stock footage of tea gardens")
        self.assertNotIn("stock", r["options"])
        self.assertTrue(any("free key" in c and "pexels.com/api" in c for c in r["cannot"]))
        self.assertFalse(any("Stock footage is not downloaded" in c for c in r["cannot"]))

    def test_with_a_key_it_is_a_supported_option(self):
        with patch.object(stock, "keys", return_value={"pexels": "k"}):
            r = understand("add b-roll please")
        self.assertTrue(r["options"]["stock"])
        self.assertIn("--stock", edit_args(r["options"], {}))
        self.assertTrue(any("Pexels" in s and "search words" in s for s in r["understood"]))
        self.assertEqual(r["cannot"], [])

    def test_ordinary_prompts_do_not_ask_for_stock(self):
        self.assertFalse(stock.wanted("make a fast reel about my tea shop", {}))
        self.assertTrue(stock.wanted("", {"stock": True}))
        self.assertTrue(stock.wanted("use some Pixabay clips", {}))


class Results(unittest.TestCase):
    def test_pexels_small_hd_copy_never_4k(self):
        r = stock.pexels_results(PEXELS)
        self.assertEqual(len(r), 1)                      # the 4K-only video is skipped
        self.assertEqual((r[0]["width"], r[0]["height"], r[0]["link"]), (1080, 1920, "https://videos.pexels.com/hd.mp4"))
        self.assertEqual(r[0]["author"], "Asha Rao")
        self.assertEqual(r[0]["page"], "https://www.pexels.com/video/tea-leaves-111/")

    def test_pixabay_medium_copy_and_credit(self):
        r = stock.pixabay_results(PIXABAY)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["link"], "https://cdn.pixabay.com/medium.mp4")
        self.assertEqual(r[0]["author_url"], "https://pixabay.com/users/Coverr-Free-Footage-1281706/")
        self.assertEqual(r[0]["licence"], "Pixabay Content Licence")

    def test_choose_matches_shape_and_length(self):
        cands = stock.pexels_results(PEXELS) + stock.pixabay_results(PIXABAY)
        self.assertEqual(stock.choose(cands, "portrait", 2.5, set())["site"], "Pexels")
        self.assertEqual(stock.choose(cands, "landscape", 2.5, set())["site"], "Pixabay")
        self.assertEqual(stock.choose(cands, "landscape", 2.5, {("Pixabay", 125)})["site"], "Pexels")
        self.assertIsNone(stock.choose(cands, "portrait", 14.0, set()))   # nothing long enough


class Network(Home):
    def test_pexels_search_sends_only_the_word_and_is_cached(self):
        seen = []

        def fake(req, timeout):
            seen.append(req)
            return FakeResponse(json.dumps(PEXELS).encode())

        with patch.object(stock, "_open", side_effect=fake):
            r1 = stock.search("pexels", "KEY1", "tea", "portrait")
            r2 = stock.search("pexels", "KEY1", "tea", "portrait")
        self.assertEqual(len(seen), 1)                    # the second search came from the 24-hour cache
        self.assertEqual(r1, r2)
        url = seen[0].full_url
        self.assertTrue(url.startswith("https://api.pexels.com/v1/videos/search?"))
        self.assertIn("query=tea", url)
        self.assertIn("orientation=portrait", url)
        self.assertEqual(seen[0].get_header("Authorization"), "KEY1")
        cached = (Path(self.tmp.name) / "stock" / "search" / "pexels_tea_portrait.json").read_text()
        self.assertNotIn("KEY1", cached)

    def test_errors_are_plain_and_never_show_the_key(self):
        def refused(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        with patch.object(stock, "_open", side_effect=refused):
            with self.assertRaises(stock.StockError) as ctx:
                stock.search("pixabay", "PIXKEY", "garden", "landscape")
        self.assertIn("refused the key", str(ctx.exception))
        self.assertNotIn("PIXKEY", str(ctx.exception))
        with patch.object(stock, "_open", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(stock.StockError) as ctx:
                stock.search("pexels", "K", "tea", "portrait")
        self.assertIn("could not be reached", str(ctx.exception))

    def test_pixabay_search_is_safe_and_film_only(self):
        seen = []
        with patch.object(stock, "_open", side_effect=lambda req, timeout: seen.append(req) or FakeResponse(json.dumps(PIXABAY).encode())):
            r = stock.search("pixabay", "PIXKEY", "tea garden", "landscape")
        self.assertEqual(len(r), 1)
        self.assertIn("safesearch=true", seen[0].full_url)
        self.assertIn("q=tea+garden", seen[0].full_url)

    def test_download_is_capped_and_cached(self):
        c = stock.pexels_results(PEXELS)[0]
        with patch.object(stock, "_open", return_value=FakeResponse(b"x" * 1000)) as op:
            p1 = stock.download(c)
            p2 = stock.download(c)
        self.assertEqual(op.call_count, 1)
        self.assertEqual(p1, p2)
        self.assertEqual(p1.stat().st_size, 1000)
        c2 = {**c, "id": 999}
        with patch.object(stock, "_open", return_value=FakeResponse(b"x" * 10, length=stock.MAX_BYTES + 1)):
            with self.assertRaises(stock.StockError):
                stock.download(c2)
        with patch.object(stock, "MAX_BYTES", 600), patch.object(stock, "_open", return_value=FakeResponse(b"y" * 2000, length=0)):
            with self.assertRaises(stock.StockError):
                stock.download({**c, "id": 998})
        self.assertEqual(list((Path(self.tmp.name) / "stock" / "clips").glob("*.part")), [])


LINES = [{"id": "u1", "t0": 0.0, "t1": 3.0, "en": "Welcome to my channel."},
         {"id": "u2", "t0": 3.0, "t1": 7.5, "en": "Today we walk through the tea garden in Darjeeling."},
         {"id": "u3", "t0": 7.5, "t1": 11.0, "en": "The tea leaves are picked by hand."},
         {"id": "u4", "t0": 11.0, "t1": 16.0, "en": "Every morning the pickers carry baskets up the hills."},
         {"id": "u5", "t0": 16.0, "t1": 19.0, "en": "Subscribe for more."}]


class Choosing(unittest.TestCase):
    def test_search_words_are_topic_words(self):
        words = [w["word"] for w in stock.search_words(LINES, "Tea Garden Walk", "reel with stock footage of tea gardens")]
        self.assertEqual(words[0], "tea")
        self.assertIn("garden", words)
        self.assertLessEqual(len(words), 4)
        for bad in ("stock", "footage", "reel", "welcome", "today"):
            self.assertNotIn(bad, words)

    def test_places_follow_the_b_roll_rules(self):
        picks = stock.search_words(LINES, "", "tea gardens")
        slots = stock.place(LINES, picks, busy=[], total=19.0)
        self.assertTrue(slots)
        for s in slots:
            self.assertGreaterEqual(s["t0"], 3.0)            # never over the opening line
            self.assertLessEqual(s["t1"], 16.0)              # never over the last line
            self.assertGreaterEqual(s["t1"] - s["t0"], 1.0)
        starts = sorted(s["t0"] for s in slots)
        self.assertTrue(all(b - a >= stock.GAP for a, b in zip(starts, starts[1:])))
        self.assertEqual(stock.place(LINES, picks, busy=[(0.0, 19.0)], total=19.0), [])   # all busy: nothing

    def test_credits_name_every_clip(self):
        used = [{"site": "Pexels", "id": 111, "page": "https://www.pexels.com/video/tea-leaves-111/", "author": "Asha Rao",
                 "author_url": "https://www.pexels.com/@asha", "licence": "Pexels Licence", "licence_url": "https://www.pexels.com/license/",
                 "word": "tea", "t0": 3.35, "t1": 6.15}]
        text = stock.credits_text(used)
        for part in ("Video by Asha Rao on Pexels", "tea-leaves-111", "@asha", "Pexels Licence", "3.4-6.2 s"):
            self.assertIn(part, text)


class FakeProject:
    def __init__(self, root: Path, brief: str, opts: dict):
        self.root = root
        self.build = root / "build"
        self.exports = root / "exports"
        self.build.mkdir(parents=True, exist_ok=True)
        self.data = {"brief": brief, "edit_options": opts}
        self.settings = {"quality": "draft"}
        segs = [{"start": ln["t0"], "end": ln["t1"], "text": ln["en"]} for ln in LINES]
        self.files = {"transcript.json": {"language": "en", "segments": segs, "words": []}}

    def read(self, name):
        return self.files.get(name)

    def write(self, name, data):
        self.files[name] = json.loads(json.dumps(data))


class Pass(Home):
    def manifest(self):
        pieces = [{"utt": ln["id"], "out_start": ln["t0"], "src_start": ln["t0"], "src_end": ln["t1"], "words": []} for ln in LINES]
        return {"fps": 30, "pieces": pieces}

    def plan(self):
        return {"title": "Tea", "utterances": [{"id": ln["id"], "start": ln["t0"], "end": ln["t1"], "text": ln["en"]} for ln in LINES]}

    def test_no_key_the_edit_carries_on_and_says_how(self):
        proj = FakeProject(Path(self.tmp.name) / "p", "stock footage of tea", {})
        m, video = self.manifest(), Path("cut.mp4")
        with patch.object(stock.console, "info") as info:
            self.assertEqual(stock.apply_pass(proj, m, self.plan(), video, {}, 1080, 1920, 19.0), video)
        self.assertEqual(m["stock"]["skipped"], "no key")
        self.assertTrue(any("free key" in str(c) for c in info.call_args_list))

    def test_not_asked_nothing_happens(self):
        proj = FakeProject(Path(self.tmp.name) / "p", "a calm reel", {})
        with patch.object(stock, "keys", return_value={"pexels": "k"}), patch.object(stock, "search") as s:
            stock.apply_pass(proj, self.manifest(), self.plan(), Path("cut.mp4"), {}, 1080, 1920, 19.0)
        s.assert_not_called()

    def test_with_a_key_clips_are_placed_and_credited(self):
        proj = FakeProject(Path(self.tmp.name) / "p", "tea garden reel with stock footage", {})
        m = self.manifest()
        clip = Path(self.tmp.name) / "clip.mp4"
        clip.write_bytes(b"x")
        with patch.object(stock, "keys", return_value={"pexels": "k"}), \
                patch.object(stock, "search", return_value=stock.pexels_results(PEXELS)) as s, \
                patch.object(stock, "download", return_value=clip), \
                patch("video_studio.engines.ff.duration", return_value=14.0), \
                patch("video_studio.broll.apply", return_value=Path("with_stock.mp4")) as ap:
            out = stock.apply_pass(proj, m, self.plan(), Path("cut.mp4"), {}, 1080, 1920, 19.0)
        self.assertEqual(out, Path("with_stock.mp4"))
        self.assertEqual(s.call_args[0][3], "portrait")       # the reel's shape
        cuts = ap.call_args[0][1]
        self.assertEqual([c["t0"] for c in cuts], [3.35])     # one Pexels clip in the fake answer: never used twice
        self.assertEqual(m["stock"]["words"], ["tea", "baskets"])
        self.assertTrue(all(c["stock"] for c in m["broll"]))  # your pictures will keep clear of it
        credits = (proj.exports / "credits.txt").read_text(encoding="utf-8")
        self.assertIn("Asha Rao", credits)
        self.assertIn("https://www.pexels.com/video/tea-leaves-111/", credits)

    def test_an_unreadable_clip_never_stops_the_edit(self):
        proj = FakeProject(Path(self.tmp.name) / "p", "tea reel with stock footage", {})
        m = self.manifest()
        clip = Path(self.tmp.name) / "clip.mp4"
        clip.write_bytes(b"x")
        with patch.object(stock, "keys", return_value={"pexels": "k"}),                 patch.object(stock, "search", return_value=stock.pexels_results(PEXELS)),                 patch.object(stock, "download", return_value=clip),                 patch("video_studio.engines.ff.duration", return_value=14.0),                 patch("video_studio.broll.apply", side_effect=RuntimeError("bad clip")):
            out = stock.apply_pass(proj, m, self.plan(), Path("cut.mp4"), {}, 1080, 1920, 19.0)
        self.assertEqual(out, Path("cut.mp4"))
        self.assertFalse((proj.exports / "credits.txt").exists())
        self.assertEqual(m["broll"], [])


if __name__ == "__main__":
    unittest.main()
