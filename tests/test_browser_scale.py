"""Tests for hooks/browser_scale.py (P-2). Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "browser_scale.py")
COMPUTER = "mcp__Claude_Browser__computer"
BATCH = "mcp__Claude_Browser__browser_batch"


def run_hook(tool_name, tool_input, env_extra=None, raw=None):
    env = {k: v for k, v in os.environ.items() if k != "TRIM_BROWSER_SCALE"}
    env.update(env_extra or {})
    stdin = raw if raw is not None else json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
    )
    proc = subprocess.run(
        [sys.executable, HOOK], input=stdin, capture_output=True, text=True, env=env, timeout=10
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, out


class BrowserScaleTest(unittest.TestCase):
    def test_screenshot_gets_half_scale(self):
        code, out = run_hook(COMPUTER, {"action": "screenshot", "tabId": "t1"})
        self.assertEqual(code, 0)
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["updatedInput"], {"action": "screenshot", "tabId": "t1", "scale": 0.5})

    def test_explicit_scale_left_alone(self):
        self.assertEqual(run_hook(COMPUTER, {"action": "screenshot", "scale": 1}), (0, None))

    def test_click_and_zoom_left_alone(self):
        self.assertEqual(run_hook(COMPUTER, {"action": "left_click", "coordinate": [10, 20]}), (0, None))
        self.assertEqual(run_hook(COMPUTER, {"action": "zoom", "region": [0, 0, 100, 100]}), (0, None))

    def test_batch_screenshots_scaled_others_kept(self):
        actions = [
            {"name": "navigate", "input": {"url": "http://localhost:8765"}},
            {"name": "computer", "input": {"action": "left_click", "ref": "ref_1"}},
            {"name": "computer", "input": {"action": "screenshot"}},
            {"name": "computer", "input": {"action": "screenshot", "scale": 0.8}},
        ]
        code, out = run_hook(BATCH, {"actions": actions})
        self.assertEqual(code, 0)
        new = out["hookSpecificOutput"]["updatedInput"]["actions"]
        self.assertEqual(new[0], actions[0])
        self.assertEqual(new[1], actions[1])
        self.assertEqual(new[2]["input"], {"action": "screenshot", "scale": 0.5})
        self.assertEqual(new[3], actions[3])

    def test_batch_without_screenshot_unchanged(self):
        actions = [{"name": "navigate", "input": {"url": "back"}}]
        self.assertEqual(run_hook(BATCH, {"actions": actions}), (0, None))

    def test_off_switch(self):
        self.assertEqual(
            run_hook(COMPUTER, {"action": "screenshot"}, env_extra={"TRIM_BROWSER_SCALE": "off"}),
            (0, None),
        )

    def test_bad_input_fails_open(self):
        self.assertEqual(run_hook(None, None, raw="not json"), (0, None))
        self.assertEqual(run_hook(COMPUTER, "oops"), (0, None))


if __name__ == "__main__":
    unittest.main()
