"""Tests for hooks/savings.py (P-9). Run: python3 -m unittest tests.test_savings"""
import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")
sys.path.insert(0, HOOKS)
import savings  # noqa: E402


def ts(days_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def line(days_ago, feature="output_cap", tokens=100, assumed=False):
    return json.dumps({"v": 1, "ts": ts(days_ago), "session": None, "feature": feature,
                       "before": None, "after": None, "tokens_saved": tokens,
                       "assumed": assumed, "full_copy": None}, separators=(",", ":"))


class SavingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.get(k) for k in ("TRIM_HOME", "TRIM_SAVINGS")}
        os.environ["TRIM_HOME"] = self.tmp.name
        os.environ.pop("TRIM_SAVINGS", None)
        self.addCleanup(self.restore)
        self.path = savings.log_path()

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def lines(self):
        with open(self.path) as f:
            return f.read().splitlines()

    def test_line_has_format_and_private_mode(self):
        savings.log_saving("output_cap", 2700, 270, session="s1", full_copy="/x/y.log")
        e = json.loads(self.lines()[0])
        self.assertEqual((e["v"], e["feature"], e["tokens_saved"], e["session"]), (1, "output_cap", 900, "s1"))
        self.assertFalse(e["assumed"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_no_saving_logs_nothing(self):
        savings.log_saving("output_cap", 100, 100)
        savings.log_saving("output_cap", 100, 200)
        self.assertFalse(os.path.exists(self.path))

    def test_off_switch(self):
        os.environ["TRIM_SAVINGS"] = "off"
        savings.log_saving("output_cap", 2700, 270)
        self.assertFalse(os.path.exists(self.path))

    def test_failed_write_does_not_raise(self):
        os.environ["TRIM_HOME"] = os.path.join(self.tmp.name, "file")
        open(os.environ["TRIM_HOME"], "w").close()  # a file where the folder should be
        savings.log_saving("output_cap", 2700, 270)

    def test_prune_drops_old_lines_when_file_is_big(self):
        with open(self.path, "w") as f:
            f.write(line(120) + "\n")
            f.write(line(100) + "\n")
            f.write(line(1) + "\n")
            f.write("not json\n")
            f.write(("x" * 100 + "\n") * 1100)  # past PRUNE_BYTES
        savings.log_saving("output_cap", 2700, 270)
        entries = [json.loads(x) for x in self.lines()]
        self.assertEqual(len(entries), 2)  # the 1-day-old line and the new one
        self.assertTrue(os.path.getsize(self.path) < savings.PRUNE_BYTES)

    def test_prune_caps_size_keeping_newest(self):
        old_max = savings.MAX_BYTES
        savings.MAX_BYTES = 1000
        self.addCleanup(setattr, savings, "MAX_BYTES", old_max)
        with open(self.path, "w") as f:
            for i in range(50):  # one second apart, oldest first
                f.write(line(2 - i / 86400.0, tokens=i + 1) + "\n")
        savings.prune(self.path)
        entries = [json.loads(x) for x in self.lines()]
        self.assertTrue(os.path.getsize(self.path) <= 1000)
        self.assertEqual(entries[-1]["tokens_saved"], 50)

    def test_report_sums_per_feature_and_window(self):
        with open(self.path, "w") as f:
            f.write(line(1, "output_cap", 1000) + "\n")
            f.write(line(10, "output_cap", 500) + "\n")
            f.write(line(40, "tool_cleanup", 200) + "\n")
            f.write(line(1, "browser_scale", 505, assumed=True) + "\n")
        r = savings.report()
        oc = r["features"]["output_cap"]
        self.assertEqual((oc["saves"], oc["tokens_saved"], oc["last_7_days"], oc["last_30_days"]), (2, 1500, 1000, 1500))
        self.assertTrue(r["features"]["browser_scale"]["assumed"])
        self.assertEqual(r["total"]["tokens_saved"], 2205)
        self.assertEqual(r["total"]["last_30_days"], 2005)

    def test_report_with_no_log(self):
        r = savings.report()
        self.assertEqual(r["total"]["tokens_saved"], 0)
        self.assertTrue(r["note"])

    def test_pruned_lines_move_to_totals_and_all_time_survives(self):
        with open(self.path, "w") as f:
            f.write(line(120, "output_cap", 1000) + "\n")
            f.write(line(100, "browser_scale", 505, assumed=True) + "\n")
            f.write(line(1, "output_cap", 200) + "\n")
            f.write(("x" * 100 + "\n") * 1100)
        before = savings.report()["total"]["tokens_saved"]
        savings.log_saving("tool_cleanup", 2700, 0)  # 1,000 tokens; triggers the prune
        self.assertEqual(len(self.lines()), 2)  # the 1-day-old line and the new one
        t = savings.read_totals()
        self.assertEqual(sum(r["tokens_saved"] for d in t["days"].values() for r in d.values()), 1505)
        r = savings.report()
        self.assertEqual(r["total"]["tokens_saved"], before + 1000)
        self.assertEqual(r["features"]["output_cap"]["tokens_saved"], 1200)
        self.assertTrue(r["features"]["browser_scale"]["assumed"])
        self.assertEqual(r["features"]["output_cap"]["last_30_days"], 200)  # old lines are not recent
        self.assertEqual(stat.S_IMODE(os.stat(savings.totals_path()).st_mode), 0o600)

    def test_folding_twice_does_not_double_count(self):
        with open(self.path, "w") as f:
            f.write(line(120, "output_cap", 1000) + "\n")
        savings.prune(self.path)
        with open(self.path, "w") as f:  # the same old line comes back (crash between the two writes)
            f.write(line(120, "output_cap", 1000) + "\n")
        savings.prune(self.path)
        self.assertEqual(savings.report()["features"]["output_cap"]["tokens_saved"], 1000)

    def test_size_cap_drops_also_go_to_totals(self):
        old_max = savings.MAX_BYTES
        savings.MAX_BYTES = 1000
        self.addCleanup(setattr, savings, "MAX_BYTES", old_max)
        with open(self.path, "w") as f:
            for i in range(50):
                f.write(line(2, tokens=10) + "\n")
        savings.prune(self.path)
        self.assertEqual(savings.report()["total"]["tokens_saved"], 500)

    def test_report_lists_recent_sessions(self):
        with open(self.path, "w") as f:
            for sid, d, tok in (("a", 5, 100), ("b", 1, 300), ("a", 4, 50), ("c", 2, 10)):
                e = json.loads(line(d, tokens=tok)); e["session"] = sid
                f.write(json.dumps(e) + "\n")
            f.write(line(1, tokens=70) + "\n")  # no session id
        r = savings.report()
        self.assertEqual([s["session"] for s in r["sessions"]], ["b", "c", "a"])
        self.assertEqual((r["sessions"][2]["saves"], r["sessions"][2]["tokens_saved"]), (2, 150))
        self.assertEqual(r["total"]["tokens_saved"], 530)  # the no-session line still counts

    def test_report_script_prints_json_and_changes_nothing(self):
        with open(self.path, "w") as f:
            f.write(line(1, tokens=300) + "\n")
        before = open(self.path).read()
        out = subprocess.run([sys.executable, os.path.join(HOOKS, "savings.py")],
                             capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(json.loads(out.stdout)["total"]["tokens_saved"], 300)
        self.assertEqual(open(self.path).read(), before)


class HookIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {**os.environ, "TRIM_HOME": self.tmp.name}
        self.env.pop("TRIM_SAVINGS", None)

    def run_hook(self, name, stdin, *args):
        return subprocess.run([sys.executable, os.path.join(HOOKS, name), *args], input=stdin,
                              capture_output=True, text=True, env=self.env)

    def entries(self):
        p = os.path.join(self.tmp.name, "savings.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(x) for x in open(p).read().splitlines()]

    def test_output_cap_logs(self):
        text = "".join("line %d\n" % i for i in range(400)) + "FAILED test_x\nRan 400 tests\n"
        self.run_hook("output_cap.py", text, "cap")
        e = self.entries()
        self.assertEqual([x["feature"] for x in e], ["output_cap"])
        self.assertTrue(e[0]["full_copy"])

    def test_output_cap_short_output_logs_nothing(self):
        self.run_hook("output_cap.py", "ok\n", "cap")
        self.assertEqual(self.entries(), [])

    def test_browser_scale_logs_assumed_per_screenshot(self):
        ev = {"tool_name": "mcp__Claude_Browser__browser_batch", "session_id": "s9",
              "tool_input": {"actions": [
                  {"name": "computer", "input": {"action": "screenshot"}},
                  {"name": "computer", "input": {"action": "screenshot", "scale": 1}},
                  {"name": "computer", "input": {"action": "screenshot"}}]}}
        self.run_hook("browser_scale.py", json.dumps(ev))
        e = self.entries()
        self.assertEqual((len(e), e[0]["tokens_saved"], e[0]["assumed"], e[0]["session"]), (1, 1010, True, "s9"))

    def test_browser_scale_no_change_logs_nothing(self):
        ev = {"tool_name": "mcp__Claude_Browser__computer",
              "tool_input": {"action": "screenshot", "scale": 1}}
        self.run_hook("browser_scale.py", json.dumps(ev))
        self.assertEqual(self.entries(), [])

    def test_hooks_still_work_when_log_is_off(self):
        self.env["TRIM_SAVINGS"] = "off"
        ev = {"tool_name": "mcp__Claude_Browser__computer", "tool_input": {"action": "screenshot"}}
        out = self.run_hook("browser_scale.py", json.dumps(ev))
        self.assertIn('"scale": 0.5', out.stdout)
        self.assertEqual(self.entries(), [])


if __name__ == "__main__":
    unittest.main()
