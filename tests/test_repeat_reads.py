"""Tests for hooks/repeat_reads.py (P-5). Run: python3 -m unittest tests.test_repeat_reads"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "repeat_reads.py")

SESSION = "sess-1"
BIG = "line of source code\n" * 400  # 8,000 bytes


def run_hook(event=None, home=None, env_extra=None, raw=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRIM_")}
    if home:
        env["TRIM_HOME"] = home
    env.update(env_extra or {})
    stdin = raw if raw is not None else json.dumps(event)
    proc = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True,
                          text=True, env=env, timeout=10)
    return proc.returncode, proc.stdout, proc.stderr


class RepeatReadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.file = os.path.join(self.tmp.name, "big.txt")
        self.write(BIG)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, path=None):
        with open(path or self.file, "w") as f:
            f.write(text)

    def event(self, name, path=None, **extra):
        ev = {"session_id": SESSION, "hook_event_name": name, "tool_name": "Read",
              "cwd": self.tmp.name, "tool_input": {"file_path": path or self.file}}
        ev.update(extra)
        return ev

    def send(self, ev, **kw):
        """Return the reason when the Read was stopped (exit 2), else None (exit 0, no output)."""
        code, out, err = run_hook(ev, home=self.home, **kw)
        self.assertEqual(out, "")
        if code == 2:
            return err
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        return None

    def post(self, path=None, **extra):
        return self.send(self.event("PostToolUse", path, **extra))

    def pre(self, path=None, **extra):
        return self.send(self.event("PreToolUse", path, **extra))

    def assertDenied(self, reason):
        self.assertIsNotNone(reason, "Read was not stopped")
        self.assertTrue(reason.startswith("[trim]"))
        self.assertNotIn("\n", reason)
        return reason

    def state_file(self):
        return os.path.join(self.home, "reads", SESSION + ".json")

    def test_second_read_of_unchanged_file_is_denied(self):
        """The P-5 done test."""
        self.assertIsNone(self.pre())  # first read: nothing recorded yet
        self.post()
        reason = self.assertDenied(self.pre())
        self.assertIn(os.path.realpath(self.file), reason)
        self.assertIn("UTC", reason)

    def test_third_read_after_pointer_is_allowed_and_then_recorded_again(self):
        self.post()
        self.assertDenied(self.pre())
        self.assertIsNone(self.pre())  # escape hatch
        self.post()  # the allowed read finished and is recorded afresh
        self.assertDenied(self.pre())

    def test_read_after_file_changed_is_allowed(self):
        self.post()
        self.write(BIG + "one more line\n")
        self.assertIsNone(self.pre())

    def test_read_after_precompact_is_allowed(self):
        self.post()
        self.send({"session_id": SESSION, "hook_event_name": "PreCompact"})
        self.assertFalse(os.path.exists(self.state_file()))
        self.assertIsNone(self.pre())

    def test_read_after_session_start_compact_or_clear_is_allowed(self):
        for source in ("compact", "clear"):
            self.post()
            self.send({"session_id": SESSION, "hook_event_name": "SessionStart", "source": source})
            self.assertIsNone(self.pre(), source)

    def test_session_start_startup_keeps_state(self):
        self.post()
        self.send({"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"})
        self.assertDenied(self.pre())

    def test_different_offset_limit_or_pages_is_allowed(self):
        self.post()
        for extra in ({"offset": 10}, {"limit": 50}, {"pages": "1-2"}):
            ev = self.event("PreToolUse")
            ev["tool_input"].update(extra)
            self.assertIsNone(self.send(ev), extra)
        self.assertDenied(self.pre())  # same arguments still match

    def test_same_arguments_with_offset_match(self):
        ev = self.event("PostToolUse")
        ev["tool_input"].update({"offset": 5, "limit": 100})
        self.send(ev)
        ev = self.event("PreToolUse")
        ev["tool_input"].update({"offset": 5, "limit": 100})
        self.assertDenied(self.send(ev))

    def test_subagent_is_never_denied_and_nothing_recorded(self):
        agent = {"agent_id": "a1", "agent_type": "Explore"}
        self.post(**agent)
        self.assertFalse(os.path.exists(self.state_file()))
        self.assertIsNone(self.pre(**agent))
        # A main-conversation record is not used for a subagent read either.
        self.post()
        self.assertIsNone(self.pre(**agent))
        self.assertDenied(self.pre())

    def test_small_file_is_allowed(self):
        small = os.path.join(self.tmp.name, "small.txt")
        self.write("x" * 1999, small)
        self.post(small)
        self.assertIsNone(self.pre(small))
        self.write("x" * 2000, small)
        self.post(small)
        self.assertDenied(self.pre(small))

    def test_off_switch(self):
        env = {"TRIM_REPEAT_READS": "off"}
        self.send(self.event("PostToolUse"), env_extra=env)
        self.assertFalse(os.path.exists(self.state_file()))
        self.post()
        self.assertIsNone(self.send(self.event("PreToolUse"), env_extra=env))

    def test_state_file_and_folder_are_private(self):
        self.post()
        self.assertEqual(stat.S_IMODE(os.stat(self.state_file()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.state_file())).st_mode), 0o700)

    def test_other_tools_and_sessions_are_ignored(self):
        self.post()
        ev = self.event("PreToolUse")
        ev["tool_name"] = "Grep"
        self.assertIsNone(self.send(ev))
        ev = self.event("PreToolUse")
        ev["session_id"] = "other"
        self.assertIsNone(self.send(ev))

    def test_unsafe_session_id_does_nothing(self):
        for sid in ("../evil", "a/b", "", None, 7):
            ev = self.event("PostToolUse")
            ev["session_id"] = sid
            self.send(ev)
        self.assertFalse(os.path.exists(os.path.join(self.home, "reads")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil.json")))

    def test_missing_file_is_allowed(self):
        gone = os.path.join(self.tmp.name, "gone.txt")
        self.post(gone)
        self.assertIsNone(self.pre(gone))

    def test_relative_path_uses_cwd(self):
        self.post("big.txt")
        self.assertDenied(self.pre(self.file))

    def test_bad_input_exits_zero_with_no_output(self):
        for raw in ("", "not json", "[1, 2]", "null", '{"hook_event_name": "PreToolUse", "tool_name": "Read"}',
                    '{"hook_event_name": "PreToolUse", "tool_name": "Read", "session_id": "s", "tool_input": []}'):
            code, out, err = run_hook(raw=raw, home=self.home)
            self.assertEqual(code, 0, raw)
            self.assertEqual(out + err, "", raw)


if __name__ == "__main__":
    unittest.main()
