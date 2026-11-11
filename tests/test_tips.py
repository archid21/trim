"""Tests for hooks/tips.py and hooks/session_tips.py (P-12).
Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPS = os.path.join(ROOT, "hooks", "tips.py")
HOOK = os.path.join(ROOT, "hooks", "session_tips.py")


class TipsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: v for k, v in os.environ.items() if k not in ("TRIM_TIPS",)}
        self.env["TRIM_HOME"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, TIPS, *args], capture_output=True, text=True, env=self.env, timeout=10
        )

    def hook(self, source, session_id="s1", env_extra=None, raw=None):
        env = dict(self.env, **(env_extra or {}))
        stdin = raw if raw is not None else json.dumps(
            {"hook_event_name": "SessionStart", "source": source, "session_id": session_id}
        )
        proc = subprocess.run(
            [sys.executable, HOOK], input=stdin, capture_output=True, text=True, env=env, timeout=10
        )
        self.assertEqual(proc.returncode, 0)
        if not proc.stdout.strip():
            return None
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        return out["additionalContext"]

    def test_list_shows_tip_with_token_cost_and_off_by_default(self):
        data = json.loads(self.cli("list").stdout)
        tip = {t["id"]: t for t in data["tips"]}["browsing"]
        self.assertFalse(tip["on"])
        self.assertGreater(tip["tokens"], 0)
        self.assertIn("scale: 0.5", tip["text"])
        self.assertIn("browser_batch", tip["text"])
        self.assertEqual(data["file"], os.path.join(self.tmp.name, "tips.json"))

    def test_nothing_added_when_no_tip_is_on(self):
        self.assertIsNone(self.hook("startup"))

    def test_tip_on_is_added_at_next_session(self):
        self.assertEqual(self.cli("set", "browsing", "on").returncode, 0)
        self.assertTrue(json.loads(self.cli("list").stdout)["tips"][0]["on"])
        text = self.hook("startup")
        self.assertIn("get_page_text", text)
        self.assertIn("scale: 0.5", text)

    def test_turning_off_restores_previous_context(self):
        before = self.hook("startup", "a")
        self.cli("set", "browsing", "on")
        self.assertIsNotNone(self.hook("startup", "b"))
        self.cli("set", "browsing", "off")
        self.assertEqual(self.hook("startup", "c"), before)

    def test_no_mid_session_change_after_compact(self):
        # Session s1 starts with the tip off; the user turns it on mid-session.
        self.assertIsNone(self.hook("startup", "s1"))
        self.cli("set", "browsing", "on")
        self.assertIsNone(self.hook("compact", "s1"))
        # A new session gets it.
        self.assertIsNotNone(self.hook("startup", "s2"))
        # Turning it off mid-session keeps it in s2 after a compact.
        self.cli("set", "browsing", "off")
        self.assertIsNotNone(self.hook("compact", "s2"))

    def test_clear_picks_up_current_choices(self):
        self.assertIsNone(self.hook("startup", "s1"))
        self.cli("set", "browsing", "on")
        self.assertIsNotNone(self.hook("clear", "s1"))

    def test_unknown_tip_rejected(self):
        proc = self.cli("set", "nope", "on")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "tips.json")))

    def test_off_switch(self):
        self.cli("set", "browsing", "on")
        self.assertIsNone(self.hook("startup", env_extra={"TRIM_TIPS": "off"}))

    def test_fails_open_on_bad_input_and_bad_file(self):
        self.assertIsNone(self.hook("startup", raw="not json"))
        with open(os.path.join(self.tmp.name, "tips.json"), "w") as f:
            f.write("{broken")
        self.assertIsNone(self.hook("startup"))

    def test_session_id_cannot_escape_folder(self):
        self.cli("set", "browsing", "on")
        self.hook("startup", session_id="../../evil")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp.name), "evil.json")))
        self.assertTrue(os.listdir(os.path.join(self.tmp.name, "sessions")))


if __name__ == "__main__":
    unittest.main()
