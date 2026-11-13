"""Tests for hooks/output_cap.py (P-3). Run: python3 -m unittest tests.test_output_cap"""
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "output_cap.py")


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRIM_")}
    env.update(extra or {})
    return env


def run_hook(tool_input, tool_name="Bash", env_extra=None, raw=None):
    stdin = raw if raw is not None else json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
    )
    proc = subprocess.run(
        [sys.executable, HOOK], input=stdin, capture_output=True, text=True,
        env=clean_env(env_extra), timeout=10,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, out


def new_command(cmd):
    code, out = run_hook({"command": cmd})
    assert code == 0 and out, "hook did not rewrite %r" % cmd
    return out["hookSpecificOutput"]["updatedInput"]["command"]


def run_shell(cmd, home, env_extra=None):
    """Run a command line the way the Bash tool does (bash -c)."""
    extra = {"TRIM_HOME": home}
    extra.update(env_extra or {})
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                          env=clean_env(extra), timeout=120)


def run_cap(text, home, env_extra=None):
    extra = {"TRIM_HOME": home}
    extra.update(env_extra or {})
    return subprocess.run([sys.executable, HOOK, "cap"], input=text, capture_output=True,
                          text=True, env=clean_env(extra), timeout=30)


def write_suite(folder, passing=300, failing=3):
    """A unittest suite with many noisy passing tests and a few failures."""
    lines = ["import unittest", "", "class T(unittest.TestCase):"]
    for i in range(passing):
        lines.append("    def test_ok_%03d(self):\n        print('noise line %d')\n        self.assertTrue(True)" % (i, i))
    for i in range(failing):
        lines.append("    def test_bad_%d(self):\n        self.assertEqual(%d, %d)" % (i, i, i + 100))
    with open(os.path.join(folder, "test_suite.py"), "w") as f:
        f.write("\n".join(lines) + "\n")


def full_copy_path(testcase, stdout):
    for line in stdout.splitlines():
        if line.startswith("[trim] Full output: "):
            return line[len("[trim] Full output: "):]
    testcase.fail("no full output path in:\n" + stdout[:2000])


class HookTest(unittest.TestCase):
    def test_test_command_is_piped_and_stays_visible(self):
        code, out = run_hook({"command": "python3 -m unittest discover -s tests", "timeout": 60000})
        self.assertEqual(code, 0)
        new = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(new["timeout"], 60000)
        self.assertTrue(new["command"].startswith(
            "set -o pipefail; python3 -m unittest discover -s tests 2>&1 | python3 "))
        self.assertTrue(new["command"].endswith("output_cap.py cap"))

    def test_common_runners_are_piped(self):
        for cmd in ["pytest -q", "npm test", "npm run build", "cargo test", "go test ./...",
                    "make", "npx tsc --noEmit", "pnpm lint", "FOO=$HOME pytest tests/a.py"]:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(run_hook({"command": cmd})[1])

    def test_other_commands_left_alone(self):
        for cmd in ["ls -la", "git status", "cat Makefile", "echo pytest-like", "grep -r maker ."]:
            with self.subTest(cmd=cmd):
                self.assertEqual(run_hook({"command": cmd}), (0, None))

    def test_commands_with_shell_operators_left_alone(self):
        """No part of a compound command may be hidden from permission rules."""
        for cmd in ["make --version; rm -f x", "cd app && npm test", "pytest || true",
                    "pytest | tail -5", "pytest > out.txt", "pytest `echo -q`", "pytest $(echo -q)",
                    "(pytest)", "{ pytest; }", "pytest &", "pytest\nrm x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(run_hook({"command": cmd}), (0, None))

    def test_background_piped_and_other_tools_left_alone(self):
        self.assertEqual(run_hook({"command": "pytest", "run_in_background": True}), (0, None))
        self.assertEqual(run_hook({"command": "pytest 2>&1 | python3 /x/output_cap.py cap"}), (0, None))
        self.assertEqual(run_hook({"command": "pytest"}, tool_name="Read"), (0, None))

    def test_off_switch_and_bad_input_fail_open(self):
        self.assertEqual(run_hook({"command": "pytest"}, env_extra={"TRIM_OUTPUT_CAP": "off"}), (0, None))
        self.assertEqual(run_hook(None, raw="not json"), (0, None))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.suite = os.path.join(self.tmp.name, "suite")
        os.makedirs(self.suite)

    def tearDown(self):
        self.tmp.cleanup()

    def test_long_failing_run_is_capped_with_every_failure_and_path(self):
        """The P-3 done test: hook rewrite, then the rewritten command in bash."""
        write_suite(self.suite)
        cmd = "python3 -m unittest discover -s %s -v" % shlex.quote(self.suite)
        direct = run_shell(cmd + " 2>&1", self.home)
        self.assertGreater(len(direct.stdout.splitlines()), 600)

        proc = run_shell(new_command(cmd), self.home)
        self.assertEqual(proc.returncode, 1)  # the command's own exit code
        out = proc.stdout
        self.assertLess(len(out.splitlines()), 150)
        for i in range(3):
            self.assertIn("FAIL: test_bad_%d" % i, out)
            self.assertIn("AssertionError: %d != %d" % (i, i + 100), out)
        self.assertIn("FAILED (failures=3)", out)
        path = full_copy_path(self, out)
        with open(path) as f:
            saved = f.read()
        self.assertEqual(len(saved.splitlines()), len(direct.stdout.splitlines()))
        self.assertIn("noise line 150", saved)
        self.assertNotIn("noise line 150", out)

    def test_long_passing_run_keeps_its_summary(self):
        """Buffered prints come after the report; the summary must still be kept."""
        write_suite(self.suite, passing=300, failing=0)
        cmd = "python3 -m unittest discover -s %s -v" % shlex.quote(self.suite)
        proc = run_shell(new_command(cmd), self.home)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout
        self.assertIn("[trim] Output capped", out)
        self.assertRegex(out, r"(?m)^Ran 300 tests in ")
        self.assertRegex(out, r"(?m)^OK$")
        self.assertIn("noise line 299", out)  # the buffered prints are the tail

    def test_summary_lines_of_other_runners_kept(self):
        summaries = ["===== 120 passed, 2 skipped in 3.10s =====", "Tests:       12 passed, 12 total",
                     "test result: ok. 40 passed; 0 failed", "ok  \texample.com/pkg\t0.012s",
                     "  42 passing (1s)", "BUILD SUCCESSFUL in 4s"]
        text = "".join(s + "\n" + "filler\n" * 100 for s in summaries)
        out = run_cap(text, self.home).stdout
        for s in summaries:
            self.assertIn(s + "\n", out)

    def test_full_copy_is_private(self):
        proc = run_cap("line\n" * 500, self.home)
        path = full_copy_path(self, proc.stdout)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        second = full_copy_path(self, run_cap("line\n" * 500, self.home).stdout)
        self.assertNotEqual(path, second)

    def test_short_output_unchanged_and_exit_code_kept(self):
        cmd = new_command("make -s -f /dev/null nothing-to-make")
        proc = run_shell(cmd, self.home)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("[trim]", proc.stdout)
        self.assertIn("nothing-to-make", proc.stdout)
        self.assertFalse(os.path.isdir(os.path.join(self.home, "out")))

    def test_many_failures_keep_every_failure_line(self):
        text = "".join("FAIL: case_%d\ndetail %d\n" % (i, i) for i in range(1, 401))
        proc = run_cap(text, self.home)
        for i in (1, 200, 400):
            self.assertIn("FAIL: case_%d\n" % i, proc.stdout)
        full_copy_path(self, proc.stdout)

    def test_errors_in_other_tools_output_survive_the_cut_middle(self):
        """P-13: error lines of tsc, npm, git and eslint in the middle are kept."""
        errors = [
            "src/app.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.",
            "npm ERR! code ELIFECYCLE",
            "fatal: not a git repository (or any of the parent directories): .git",
            "  12:5  error  'x' is assigned a value but never used  no-unused-vars",
            "make: *** [all] Error 2",
            "Segmentation fault (core dumped)",
        ]
        lines = ["filler %d" % i for i in range(1200)]
        for n, e in enumerate(errors):
            lines[100 + n * 150] = e
        proc = run_cap("\n".join(lines) + "\n", self.home)
        for e in errors:
            self.assertIn(e, proc.stdout)
        self.assertNotIn("filler 300\n", proc.stdout)  # still capped
        full_copy_path(self, proc.stdout)

    def test_words_like_error_in_ordinary_lines_do_not_flood_the_output(self):
        lines = ["Compiling error_handler.rs (module %d)" % i for i in range(1200)]
        lines += ["no_errors_found = true"] * 100
        proc = run_cap("\n".join(lines) + "\n", self.home)
        self.assertLess(len(proc.stdout.splitlines()), 150)

    def test_huge_line_of_repeated_markers_is_matched_fast(self):
        import time
        text = "ok\n" * 300 + "*** " * 200000 + "\n"
        start = time.time()
        run_cap(text, self.home)
        self.assertLess(time.time() - start, 10)

    def test_off_switch_passes_full_output(self):
        proc = run_cap("x\n" * 500, self.home, {"TRIM_OUTPUT_CAP": "off"})
        self.assertEqual(len(proc.stdout.splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
