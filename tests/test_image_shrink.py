"""Tests for hooks/image_shrink.py (P-6). Run: python3 -m unittest tests.test_image_shrink"""
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "image_shrink.py")
HAS_RESIZER = bool(shutil.which("sips") or shutil.which("magick") or shutil.which("convert"))


def make_png(path, w, h):
    """A valid RGB PNG with colored rows, written with the standard library only."""
    rows = [b"\x00" + bytes(((y * 255) // h, 90, (y * 7) % 256)) * w for y in range(h)]
    raw = b"".join(rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def run_hook(tool_input, home, tool_name="Read", env_extra=None, raw=None, path_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRIM_")}
    env["TRIM_HOME"] = home
    if path_env is not None:
        env["PATH"] = path_env
    env.update(env_extra or {})
    stdin = raw if raw is not None else json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": tool_name,
        "session_id": "sess-1", "tool_input": tool_input,
    })
    proc = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True,
                          text=True, env=env, timeout=20)
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, out


@unittest.skipUnless(HAS_RESIZER, "no image resizer (sips or ImageMagick) on this machine")
class ImageShrinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.pics = os.path.join(self.tmp.name, "pics")
        os.makedirs(self.pics)

    def tearDown(self):
        self.tmp.cleanup()

    def pic(self, name, w, h):
        p = os.path.join(self.pics, name)
        make_png(p, w, h)
        return p

    def test_large_image_read_points_at_a_smaller_copy(self):
        """The P-6 done test (image half)."""
        big = self.pic("big.png", 1600, 1200)
        before = open(big, "rb").read()
        code, out = run_hook({"file_path": big}, self.home)
        self.assertEqual(code, 0)
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        new_path = spec["updatedInput"]["file_path"]
        self.assertNotEqual(new_path, big)
        self.assertTrue(new_path.startswith(os.path.join(self.home, "out")))
        self.assertEqual(max(png_size(new_path)), 1000)
        self.assertEqual(png_size(new_path), (1000, 750))
        self.assertEqual(stat.S_IMODE(os.stat(new_path).st_mode), 0o600)
        self.assertEqual(open(big, "rb").read(), before)  # original untouched
        log = [json.loads(l) for l in open(os.path.join(self.home, "savings.jsonl"))]
        self.assertEqual(log[-1]["feature"], "image_shrink")
        self.assertTrue(log[-1]["assumed"])
        self.assertGreater(log[-1]["tokens_saved"], 500)
        self.assertEqual(log[-1]["full_copy"], new_path)

    def test_other_input_fields_are_kept(self):
        big = self.pic("big2.png", 1600, 1200)
        code, out = run_hook({"file_path": big, "offset": 3}, self.home)
        updated = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["offset"], 3)

    def test_small_image_left_alone(self):
        small = self.pic("small.png", 1200, 900)  # long edge at or under the floor
        self.assertEqual(run_hook({"file_path": small}, self.home), (0, None))
        self.assertFalse(os.path.exists(os.path.join(self.home, "out")))

    def test_wide_short_banner_is_left_alone(self):
        banner = self.pic("banner.png", 1400, 214)  # over the edge floor, saves only about 200 tokens
        self.assertEqual(run_hook({"file_path": banner}, self.home), (0, None))

    def test_not_an_image_or_missing_or_a_folder(self):
        txt = os.path.join(self.pics, "a.txt")
        open(txt, "w").write("hi")
        for target in (txt, os.path.join(self.pics, "nope.png"), self.pics, ""):
            self.assertEqual(run_hook({"file_path": target}, self.home), (0, None))

    def test_other_tools_ignored(self):
        big = self.pic("big3.png", 1600, 1200)
        self.assertEqual(run_hook({"file_path": big}, self.home, tool_name="Edit"), (0, None))

    def test_a_trim_copy_is_not_shrunk_again(self):
        big = self.pic("big4.png", 1600, 1200)
        _, out = run_hook({"file_path": big}, self.home)
        copy = out["hookSpecificOutput"]["updatedInput"]["file_path"]
        self.assertEqual(run_hook({"file_path": copy}, self.home), (0, None))

    def test_off_switch_and_bad_input_fail_open(self):
        big = self.pic("big5.png", 1600, 1200)
        self.assertEqual(run_hook({"file_path": big}, self.home, env_extra={"TRIM_IMAGE_SHRINK": "off"}), (0, None))
        self.assertEqual(run_hook(None, self.home, raw="not json"), (0, None))
        self.assertEqual(run_hook(None, self.home, raw=json.dumps(
            {"tool_name": "Read", "tool_input": "x"})), (0, None))

    def test_no_resizer_on_path_leaves_the_read_alone(self):
        big = self.pic("big6.png", 1600, 1200)
        self.assertEqual(run_hook({"file_path": big}, self.home, path_env="/nonexistent"), (0, None))

    def test_a_broken_image_leaves_the_read_alone(self):
        bad = os.path.join(self.pics, "bad.png")
        open(bad, "wb").write(b"not a png at all" * 1000)
        self.assertEqual(run_hook({"file_path": bad}, self.home), (0, None))

    def test_old_image_copies_are_deleted(self):
        out_dir = os.path.join(self.home, "out")
        os.makedirs(out_dir)
        old = os.path.join(out_dir, "20200101-000000-aaaaaaaa.png")
        make_png(old, 10, 10)
        past = time.time() - 8 * 86400
        os.utime(old, (past, past))
        run_hook({"file_path": self.pic("big7.png", 1600, 1200)}, self.home)
        self.assertFalse(os.path.exists(old))


class RegisteredTest(unittest.TestCase):
    def test_hook_is_registered_on_read_next_to_repeat_reads(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            cfg = json.load(f)
        reads = [g for g in cfg["hooks"]["PreToolUse"] if g["matcher"] == "Read"]
        commands = " ".join(h["command"] for g in reads for h in g["hooks"])
        self.assertIn("image_shrink.py", commands)
        self.assertIn("repeat_reads.py", commands)


if __name__ == "__main__":
    unittest.main()
