"""Tests for hooks/progress_runs.py (P-14). Run: python3 -m unittest tests.test_progress_runs"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

from tests.test_output_cap import full_copy_path, run_cap  # noqa: E402
from tests.test_tool_cleanup import run_hook  # noqa: E402


def install_log():
    lines = ["Resolving dependencies"]
    lines += ["Downloading package-%03d (1.2 MB)" % i for i in range(60)]
    lines += ["Compiling crate-%03d v0.1.%d" % (i, i) for i in range(45)]
    lines += ["error: linker `cc` failed"]
    lines += ["Compiling extra-%d" % i for i in range(5)]
    lines += ["Finished in 41s"]
    return lines


def test_log():
    lines = ["PASS src/a%03d.test.ts" % i for i in range(70)]
    lines += ["FAIL src/broken.test.ts"]
    lines += ["PASS src/b%03d.test.ts" % i for i in range(30)]
    lines += ["Tests: 1 failed, 100 passed"]
    return lines


class CollapseTest(unittest.TestCase):
    def test_runs_become_one_line_each(self):
        import progress_runs
        out = progress_runs.collapse(install_log())
        text = "\n".join(out)
        self.assertIn('[trim: 60 "Downloading" lines collapsed]', text)
        self.assertIn('[trim: 45 "Compiling" lines collapsed]', text)
        self.assertNotIn("package-030", text)
        self.assertIn("Resolving dependencies", text)  # a run of one stays
        self.assertIn("error: linker `cc` failed", text)
        self.assertIn('[trim: 5 "Compiling" lines collapsed]', text)
        self.assertIn("Finished in 41s", text)

    def test_a_failure_between_passes_survives(self):
        import progress_runs
        text = "\n".join(progress_runs.collapse(test_log()))
        self.assertIn('[trim: 70 "PASS" lines collapsed]', text)
        self.assertIn("FAIL src/broken.test.ts", text)
        self.assertIn('[trim: 30 "PASS" lines collapsed]', text)

    def test_short_runs_and_mixed_lines_are_left_alone(self):
        import progress_runs
        lines = ["Downloading a", "Downloading b", "Compiling x", "Downloading c", "done"]
        self.assertEqual(progress_runs.collapse(lines), lines)

    def test_verbose_unittest_ok_lines_collapse(self):
        import progress_runs
        lines = ["test_%03d (t.T) ... ok" % i for i in range(40)]
        out = progress_runs.collapse(lines)
        self.assertEqual(len(out), 1)
        self.assertIn("40", out[0])


class ToolCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_connector_install_log_collapses_and_full_copy_opens(self):
        """The P-14 done test, connector side."""
        original = "\n".join(install_log())
        code, out = run_hook([{"type": "text", "text": original}], home=self.home)
        self.assertEqual(code, 0)
        blocks = out["hookSpecificOutput"]["updatedMCPToolOutput"]
        header, body = blocks[0]["text"], blocks[1]["text"]
        self.assertIn('[trim: 60 "Downloading" lines collapsed]', body)
        self.assertIn("error: linker `cc` failed", body)
        self.assertNotIn("package-030", body)
        path = header.split("Full copy: ", 1)[1]
        with open(path) as f:
            self.assertEqual(f.read(), original)


class OutputCapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_capped_bash_output_collapses_runs_in_the_kept_lines(self):
        """The P-14 done test, Bash side."""
        original = "\n".join(["noise %d" % i for i in range(300)] + test_log()) + "\n"
        proc = run_cap(original, self.home)
        self.assertIn("FAIL src/broken.test.ts", proc.stdout)
        self.assertIn('"PASS" lines collapsed]', proc.stdout)
        self.assertLess(len(proc.stdout.splitlines()), 40)
        with open(full_copy_path(self, proc.stdout)) as f:
            self.assertEqual(f.read(), original)


if __name__ == "__main__":
    unittest.main()
