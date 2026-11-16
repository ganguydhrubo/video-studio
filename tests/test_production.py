"""1.8.0 production features: hardware assessment for any PC, the template/look/transition library, photos to
video, restyle, AI clip gating, the server job commands and the taskbar watcher. No renders, no network."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-test-"))

from video_studio import library, machine, photos, restyle  # noqa: E402
from video_studio.util import ToolError  # noqa: E402


def fake_pc(ram=16.0, threads=16, gpus=None, cuda=False, encoders=None, free=8.0, disk=200.0, segmentation=True):
    return {"cpu": {"name": "Test CPU", "cores": threads // 2, "threads": threads}, "gpus": gpus or [], "ram_total_gb": ram,
            "ram_free_gb": free, "disk_free_gb": disk, "encoders": {"working": encoders or []}, "cuda": {"cuda": cuda},
            "models": {"tools": {"ffmpeg": True, "blender": False}, "packages": {}, "segmentation": segmentation},
            "speed": {"measured": False}}


NVIDIA_24 = [{"name": "NVIDIA GeForce RTX 4090", "vendor": "nvidia", "discrete": True, "vram_gb": 24.0}]
NVIDIA_8 = [{"name": "NVIDIA GeForce RTX 4060", "vendor": "nvidia", "discrete": True, "vram_gb": 8.0}]
AMD_16 = [{"name": "AMD Radeon RX 7800 XT", "vendor": "amd", "discrete": True, "vram_gb": 16.0}]


class AnyPC(unittest.TestCase):
    def test_tiers_follow_the_real_hardware(self):
        self.assertEqual(machine.tier(fake_pc(ram=4, threads=4)), "basic")
        self.assertEqual(machine.tier(fake_pc(ram=8)), "standard")
        self.assertEqual(machine.tier(fake_pc(ram=16)), "plus")
        self.assertEqual(machine.tier(fake_pc(ram=16, gpus=AMD_16)), "gpu")
        self.assertEqual(machine.tier(fake_pc(ram=32, gpus=NVIDIA_24)), "workstation")

    def test_generation_is_offered_only_where_the_card_can_run_it(self):
        def feat(s, key):
            return {f["key"]: f for f in machine.assess(s)["features"]}[key]["status"]
        self.assertEqual(feat(fake_pc(), "gen_text_to_video"), "cloud_only")
        self.assertEqual(feat(fake_pc(gpus=AMD_16), "gen_text_to_video"), "cloud_only")  # needs NVIDIA CUDA
        self.assertEqual(feat(fake_pc(gpus=NVIDIA_8, cuda=True), "gen_text_to_video"), "ready")
        self.assertEqual(feat(fake_pc(gpus=NVIDIA_8, cuda=True), "gen_image_to_video"), "cloud_only")  # 12 GB needed
        self.assertEqual(feat(fake_pc(gpus=NVIDIA_24, cuda=False), "gen_image_to_video"), "install")
        self.assertEqual(feat(fake_pc(ram=32, gpus=NVIDIA_24, cuda=True), "gen_image_to_video"), "ready")

    def test_check_refuses_what_the_pc_cannot_do_and_warns_about_limits(self):
        self.assertEqual(machine.check("generate", s=fake_pc())["level"], "stop")
        basic = machine.check("edit", short_side=2160, fps=60, s=fake_pc(ram=6, threads=4))
        self.assertEqual(basic["level"], "warn")
        self.assertTrue(any("720p" in m for m in basic["messages"]))
        self.assertTrue(any("fps" in m for m in basic["messages"]))
        self.assertEqual(machine.check("edit", short_side=1080, s=fake_pc())["level"], "ok")

    def test_time_estimate_depends_on_the_kind_of_job(self):
        def minutes(task):
            msg = next(m for m in machine.check(task, seconds=600, s=fake_pc())["messages"] if m.startswith("Expected"))
            return msg
        self.assertNotEqual(minutes("photos"), minutes("edit"))

    def test_low_disk_space_stops_a_long_job(self):
        r = machine.check("edit", seconds=3600, s=fake_pc(disk=3))
        self.assertEqual(r["level"], "stop")

    def test_working_encoder_is_reported(self):
        a = machine.assess(fake_pc(encoders=["h264_nvenc"], gpus=NVIDIA_8))
        self.assertEqual(a["encoder"], "h264_nvenc")


class Library(unittest.TestCase):
    def test_every_transition_is_a_real_ffmpeg_xfade_name(self):
        for family in library.TRANSITION_FAMILIES:
            names = library.transitions(family)
            self.assertTrue(names, family)
            self.assertTrue(set(names) <= set(library.XFADE), family)
        self.assertNotIn("squeezev", library.XFADE)

    def test_luts_are_valid_cube_files(self):
        for key in library.GRADES:
            path = library.lut_file(key)
            lines = Path(path).read_text().splitlines()
            size = int(next(x for x in lines if x.startswith("LUT_3D_SIZE")).split()[1])
            rows = [x for x in lines if x and x[0].isdigit()]
            self.assertEqual(len(rows), size ** 3, key)
            self.assertTrue(all(0.0 <= float(v) <= 1.0 for v in rows[size ** 3 // 2].split()), key)
        self.assertEqual(library.grade_filter(None), "")
        self.assertIn("lut3d=", library.grade_filter("teal_orange"))

    def test_template_never_overrides_the_persons_own_choice(self):
        merged, brief = library.apply_template("product_ad", {"aspect": "1:1", "transition": None}, "my brief")
        self.assertEqual(merged["aspect"], "1:1")
        self.assertTrue(merged.get("transition"))
        self.assertIn("my brief", brief)

    def test_catalog_counts_match(self):
        cat = library.catalog()
        self.assertEqual(cat["counts"]["templates"], len(library.TEMPLATES))
        self.assertGreaterEqual(cat["counts"]["templates"], 20)
        self.assertGreaterEqual(cat["counts"]["looks"], 15)


class Photos(unittest.TestCase):
    def test_frame_sizes_keep_even_numbers(self):
        self.assertEqual(photos.size_for("9:16"), (1080, 1920))
        self.assertEqual(photos.size_for("9:16", 720), (720, 1280))
        w, h = photos.size_for("4:5", 481)
        self.assertEqual((w % 2, h % 2), (0, 0))

    def test_default_picture_length_lands_on_the_music_bar(self):
        d = photos.plan_durations(4, None, 120.0, None, 0.5)
        self.assertEqual(len(d), 4)
        self.assertAlmostEqual(d[0] % 2.0, 0.0)  # one bar at 120 bpm is 2 s
        total = photos.plan_durations(5, None, 120.0, 20.0, 0.5)
        self.assertAlmostEqual(sum(total) - 0.5 * 4, 20.0)

    def test_camera_moves_and_filters(self):
        for move in photos.MOVES:
            z, x, y = photos.move_expr(move, 90)
            self.assertTrue(z and x and y)
        self.assertIn("zoompan", photos.clip_filter(720, 1280, 30, 90, "zoom_in"))
        self.assertIn("boxblur", photos.still_filter(720, 1280))

    def test_collect_keeps_order_and_skips_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("b.jpg", "a.png", "notes.txt"):
                Path(tmp, n).write_bytes(b"x")
            self.assertEqual([p.name for p in photos.collect([tmp])], ["a.png", "b.jpg"])

    def test_no_pictures_is_a_plain_error(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ToolError):
            photos.run([tmp], Path(tmp) / "out")


class Restyle(unittest.TestCase):
    def test_speed_changes_chain_atempo_within_its_range(self):
        self.assertEqual(restyle._atempo(4.0), "atempo=2.0,atempo=2.0000")
        self.assertEqual(restyle._atempo(0.25), "atempo=0.5,atempo=0.5000")
        self.assertEqual(restyle._atempo(1.5), "atempo=1.5000")

    def test_frame_shape_fill_crop_and_same_shape(self):
        fill = restyle.video_filter(src_w=1920, src_h=1080, w=1080, h=1920, fit="fill")
        crop = restyle.video_filter(src_w=1920, src_h=1080, w=1080, h=1920, fit="crop")
        same = restyle.video_filter(src_w=1920, src_h=1080, w=1280, h=720)
        self.assertIn("boxblur", fill)
        self.assertNotIn("boxblur", crop)
        self.assertIn("crop=1080:1920", crop)
        self.assertNotIn("crop", same)

    def test_slow_motion_and_steadying(self):
        g = restyle.video_filter(src_w=1280, src_h=720, w=None, h=None, speed=0.5, smooth_slow=True, stab=Path("s.trf"),
                                 grade="noir", sharpen=True, denoise=True)
        for part in ("minterpolate", "vidstabtransform", "setpts=PTS/0.5000", "lut3d", "unsharp", "hqdn3d"):
            self.assertIn(part, g)

    def test_bad_speed_and_missing_file_are_plain_errors(self):
        with self.assertRaises(ToolError):
            restyle.run("nope.mp4", Path(tempfile.mkdtemp()))
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            name = f.name
        with self.assertRaises(ToolError):
            restyle.run(name, Path(tempfile.mkdtemp()), speed=9)
        os.unlink(name)


class Generate(unittest.TestCase):
    def test_refuses_with_cloud_advice_on_a_pc_without_a_big_card(self):
        from video_studio import generate
        with patch.object(generate, "readiness", return_value={"local": False, "status": "cloud_only", "reason": "No NVIDIA card.", "vram_gb": 0}), \
                patch.object(generate, "_call") as helper:
            with self.assertRaises(ToolError) as err:
                generate.run_generation("tea gardens", Path(tempfile.mkdtemp()))
        helper.assert_not_called()
        self.assertIn("cloud", err.exception.hint)

    def test_small_cards_use_smaller_frames_and_wan_frame_counts(self):
        from video_studio import generate
        calls = {}

        def fake_call(cmd, timeout, **kw):
            calls.update(kw)
            return {"ok": True, "output": kw["dest"], "seconds": 1}
        with patch.object(generate, "readiness", return_value={"local": True, "status": "ready", "reason": "", "vram_gb": 8.0}), \
                patch.object(generate, "_call", side_effect=fake_call):
            generate.run_generation("tea gardens", Path(tempfile.mkdtemp()), aspect="9:16", seconds=8)
        self.assertEqual((calls["width"], calls["height"]), generate.LOW_VRAM_SHAPES["9:16"])
        self.assertEqual((calls["frames"] - 1) % 4, 0)
        self.assertLessEqual(calls["frames"], 5 * 24 + 1)  # capped at 5 s below 24 GB


class CommandsAndServer(unittest.TestCase):
    def test_new_commands_parse(self):
        from video_studio import cli
        p = cli.build_parser()
        a = p.parse_args(["photos", "a.jpg", "b.jpg", "--seconds", "2", "--grade", "noir", "--transition", "glitch", "--template", "travel"])
        self.assertEqual((a.cmd, a.images, a.grade), ("photos", ["a.jpg", "b.jpg"], "noir"))
        a = p.parse_args(["restyle", "v.mp4", "--speed", "0.5", "--steady", "--background", "blur"])
        self.assertTrue(a.steady)
        a = p.parse_args(["cutout", "v.mp4", "--background", "transparent", "--mov"])
        self.assertTrue(a.mov)
        for argv in (["generate", "a shot"], ["machine", "--json"], ["library"], ["update", "rollback"]):
            self.assertTrue(callable(p.parse_args(argv).func))

    def test_server_builds_each_quick_job(self):
        from video_studio import studio
        with tempfile.TemporaryDirectory() as tmp:
            pic, vid = Path(tmp, "a.jpg"), Path(tmp, "v.mp4")
            pic.write_bytes(b"x")
            vid.write_bytes(b"x")
            root = Path(tmp, "proj")
            a = studio._quick_args("photos", {"files": [str(pic)], "titles": "One"}, {"grade": "noir", "narrate": True}, root, "P")
            self.assertEqual(a[0], "photos")
            self.assertIn("--titles-file", a)
            self.assertIn("--narrate", a)
            a = studio._quick_args("restyle", {"files": [str(vid), str(pic)]}, {"background": "image", "speed": "2"}, root, "R")
            self.assertEqual(a[a.index("--background-image") + 1], str(pic))
            a = studio._quick_args("cutout", {"files": [str(vid)]}, {"background": "colour", "colour": "#112233"}, root, "C")
            self.assertEqual(a[a.index("--colour") + 1], "#112233")
            self.assertIn("error", studio._quick_args("generate", {}, {}, root, "G"))
            self.assertIn("error", studio._quick_args("photos", {"files": []}, {}, root, "P"))

    def test_update_actions_are_refused_while_running_from_source(self):
        from video_studio import studio
        r = studio.update_action("install", {})
        self.assertIn("error", r)
        self.assertNotIn("exit", r)
        self.assertIn("error", studio.update_action("rollback", {}))
        self.assertIn("error", studio.update_action("nonsense", {}))


class Taskbar(unittest.TestCase):
    def test_watch_is_harmless_away_from_windows(self):
        from video_studio import taskbar
        with patch.object(taskbar.os, "name", "posix"):
            self.assertEqual(taskbar.watch(first_wait=0, poll=0), 0)

    def test_watch_brands_new_windows_and_puts_the_icon_back(self):
        from video_studio import taskbar
        seen = [[11], [11], []]
        with patch.object(taskbar, "find_windows", side_effect=lambda: seen.pop(0) if seen else []), \
                patch.object(taskbar, "brand") as brand, patch.object(taskbar, "icon_kept", return_value=False), \
                patch.object(taskbar, "set_icon") as set_icon, patch.object(taskbar.time, "sleep"), \
                patch.object(taskbar.ctypes, "windll", create=True):
            if os.name != "nt":
                self.skipTest("Windows only")
            self.assertEqual(taskbar.watch(first_wait=0, poll=0), 1)
        brand.assert_called_once_with(11)
        set_icon.assert_called_once_with(11)

    def test_launcher_keeps_the_window_branded(self):
        from video_studio import launcher
        with patch.object(launcher, "launch", return_value="http://127.0.0.1:7860/"), \
                patch("video_studio.taskbar.watch", return_value=1) as watch:
            launcher.main()
        watch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
