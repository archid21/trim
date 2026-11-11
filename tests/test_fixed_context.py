"""Tests for hooks/fixed_context.py (P-10). Run: python3 -m unittest tests.test_fixed_context"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "hooks", "fixed_context.py")
UUID = "75632a2d-6abf-4038-b123-b7357c3ae27d"


def attachment(a):
    return {"type": "attachment", "attachment": a}


def tool_use(name, **inp):
    return {"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}


def listings(skill_content, skill_names, desktop=False):
    drive = UUID if desktop else "claude_ai_Google_Drive"
    return [
        attachment({"type": "skill_listing", "content": skill_content, "names": skill_names,
                    "skillCount": len(skill_names), "isInitial": True}),
        attachment({"type": "agent_listing_delta", "isInitial": True,
                    "addedTypes": ["general-purpose", "ember", "wren", "toolkit:helper", "hooky:watcher"],
                    "addedLines": ["- general-purpose: built in", "- ember: " + "e" * 270,
                                   "- wren: " + "w" * 100, "- toolkit:helper: " + "h" * 135,
                                   "- hooky:watcher: " + "x" * 54],
                    "builtInTypes": ["general-purpose"]}),
        attachment({"type": "deferred_tools_delta", "addedNames": [
            "WebFetch", "mcp__content-optimizer__score", "mcp__content-optimizer__check",
            "mcp__%s__search_files" % drive, "mcp__%s__copy_file" % drive,
            "mcp__ccd_session__start", "mcp__Claude_Browser__navigate"]}),
        attachment({"type": "mcp_instructions_delta", "addedNames": ["content-optimizer"],
                    "addedBlocks": ["## content-optimizer\n" + "c" * 250]}),
    ]


class FixedContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.claude = os.path.join(t, "claude")
        self.home = os.path.join(t, "trimhome")
        self.project = os.path.realpath(os.path.join(t, "proj"))
        os.makedirs(self.project)
        self.tdir = os.path.join(self.claude, "projects",
                                 "".join(c if c.isalnum() or c == "-" else "-" for c in self.project))
        os.makedirs(self.tdir)
        # plugins: toolkit (no hooks), hooky (hooks), trim (self)
        plugins = {}
        for name, hooks in (("toolkit", False), ("hooky", True), ("trim", True)):
            root = os.path.join(t, "plugins", name)
            os.makedirs(os.path.join(root, "hooks"))
            if hooks:
                self.write_json(os.path.join(root, "hooks", "hooks.json"), {})
            plugins[name + "@m"] = [{"installPath": root}]
        self.write_json(os.path.join(self.claude, "plugins", "installed_plugins.json"),
                        {"version": 2, "plugins": plugins})
        self.write_json(os.path.join(self.claude, "settings.json"),
                        {"enabledPlugins": {k: True for k in plugins}})
        self.settings = os.path.join(self.project, ".claude", "settings.local.json")
        # skills: two plain, one plugin skill; "budget" listing has a name-only line
        self.full = "- alpha: " + "a" * 400 + "\n- beta: " + "b" * 200 + "\n- toolkit:lint: " + "l" * 100
        self.names = ["alpha", "beta", "toolkit:lint"]

    def tearDown(self):
        self.tmp.cleanup()

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def session(self, i, records, age_days=0):
        path = os.path.join(self.tdir, "s%d.jsonl" % i)
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        when = time.time() - age_days * 86400 - (100 - i)
        os.utime(path, (when, when))

    def sessions(self, n=5, content=None, desktop=False, extra=()):
        for i in range(n):
            recs = listings(content or self.full, self.names, desktop=desktop and i == n - 1)
            if i == 0:
                recs += list(extra)
            self.session(i, recs)

    def run_cli(self, *_):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TRIM_") and k != "CLAUDE_PROJECT_DIR"}
        env.update(TRIM_HOME=self.home, TRIM_CLAUDE_DIR=self.claude)
        cmd = [sys.executable, SCRIPT, "--project", self.project]
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def ids(self, out):
        return {i["id"]: i for i in out["items"]}

    # --- the list ---

    def test_list_shows_unused_items_with_tokens(self):
        self.sessions(extra=[tool_use("Agent", subagent_type="wren"),
                             tool_use("Skill", skill="beta"),
                             tool_use("mcp__claude_ai_Google_Drive__search_files", query="x")])
        out = self.run_cli("scan")
        items = self.ids(out)
        self.assertEqual(out["window_days"], 90)
        self.assertEqual(out["sessions"], 5)
        # ember: "- ember: " (9) + 270 + newline = 280 chars / 2.7 = 104 tokens
        self.assertEqual(items["agent:ember"]["tokens"], 104)
        # content-optimizer: two names (31 + 31 chars incl. newline) + 270-char block
        self.assertEqual(items["mcp:content-optimizer"]["tokens"], round((31 + 31 + 270) / 2.7))
        self.assertIn("plugin:toolkit@m", items)
        self.assertGreater(items["plugin:toolkit@m"]["tokens"], 0)
        self.assertEqual(items["plugin:toolkit@m"]["parts"], {"agents": 1, "skills": 1, "mcp_servers": 0})
        # skills are never listed; the summary points to /skill-doctor
        self.assertFalse([i for i in out["items"] if i["kind"] == "skill"])
        self.assertIn("/skill-doctor", out["skills"]["note"])
        self.assertFalse(out["skills"]["listing_at_budget"])
        for i in out["items"]:
            self.assertIsInstance(i["tokens"], int)
        # used, built-in, app and self items are never suggested
        for gone in ("agent:wren", "agent:general-purpose", "skill:beta",
                     "mcp:claude_ai_Google_Drive", "mcp:ccd_session", "mcp:Claude_Browser",
                     "plugin:trim@m", "agent:toolkit:helper", "skill:toolkit:lint"):
            self.assertNotIn(gone, items)
        # a plugin with hooks is listed apart, with a note
        hooky = out["plugins_with_hooks"][0]
        self.assertEqual(hooky["id"], "plugin:hooky@m")
        self.assertIn("hooks", hooky["note"])

    def test_skill_budget_is_explained_and_plugin_skills_count_zero(self):
        at_budget = "- alpha: " + "a" * 400 + "\n- beta\n- toolkit:lint: " + "l" * 100
        self.sessions(content=at_budget)
        out = self.run_cli("scan")
        self.assertTrue(out["skills"]["listing_at_budget"])
        self.assertIn("saves 0 tokens", out["skills"]["note"])
        toolkit = self.ids(out)["plugin:toolkit@m"]
        self.assertEqual(toolkit["tokens"], round((len("- toolkit:helper: ") + 135 + 1) / 2.7))
        self.assertIn("budget", toolkit["note"])



    def test_too_few_sessions_suggests_nothing(self):
        self.sessions(n=3)
        out = self.run_cli("scan")
        self.assertEqual(out["items"], [])
        self.assertIn("needs 5", out["note"])
        self.assertIsNotNone(out["overview"])

    def test_old_sessions_are_outside_the_window(self):
        self.sessions()
        self.session(9, [tool_use("Agent", subagent_type="ember")], age_days=120)
        self.assertIn("agent:ember", self.ids(self.run_cli("scan")))

    def test_item_removed_mid_session_is_not_listed(self):
        self.sessions(extra=[attachment({"type": "agent_listing_delta", "isInitial": False,
                                         "addedTypes": [], "addedLines": [],
                                         "removedTypes": ["ember"]})])
        # the removal is in the oldest session; put one in the latest too
        self.session(4, listings(self.full, self.names) + [
            attachment({"type": "agent_listing_delta", "isInitial": False, "addedTypes": [],
                        "addedLines": [], "removedTypes": ["ember"]}),
            attachment({"type": "skill_listing", "isInitial": False, "content": "- gamma: g",
                        "names": ["gamma"]})])
        out = self.run_cli("scan")
        self.assertNotIn("agent:ember", self.ids(out))
        self.assertIn("agent:wren", self.ids(out))
        parts = {r["part"]: r["detail"] for r in out["overview"]["parts"]}
        self.assertEqual(parts["skill listing"], "4 skills")

    def test_desktop_uuid_connector_is_the_same_as_the_cli_name(self):
        # last session is the desktop app (UUID name); use was under the CLI name
        self.sessions(desktop=True, extra=[tool_use("mcp__claude_ai_Google_Drive__copy_file")])
        items = self.ids(self.run_cli("scan"))
        self.assertNotIn("mcp:claude_ai_Google_Drive", items)
        self.assertNotIn("mcp:" + UUID, items)

    # --- diagnose only ---

    def test_never_writes_settings_or_state(self):
        self.sessions()
        before = sorted(os.walk(self.tmp.name))
        self.run_cli("scan")
        self.assertFalse(os.path.exists(self.settings))
        self.assertFalse(os.path.exists(self.home))
        self.assertEqual(sorted(os.walk(self.tmp.name)), before)

    def test_each_item_says_where_to_turn_it_off(self):
        os.makedirs(os.path.join(self.claude, "agents"))
        open(os.path.join(self.claude, "agents", "ember.md"), "w").close()
        self.write_json(os.path.join(self.project, ".mcp.json"),
                        {"mcpServers": {"content-optimizer": {"command": "x"}}})
        self.sessions()
        out = self.run_cli("scan")
        items = self.ids(out)
        self.assertIn("~/.claude/agents/ember.md", items["agent:ember"]["how_to_turn_off"])
        self.assertIn("Agent(wren)", items["agent:wren"]["how_to_turn_off"])
        self.assertIn(".mcp.json", items["mcp:content-optimizer"]["how_to_turn_off"])
        self.assertIn("claude.ai", items["mcp:claude_ai_Google_Drive"]["how_to_turn_off"])
        self.assertIn("claude plugin disable toolkit@m --scope local",
                      items["plugin:toolkit@m"]["how_to_turn_off"])
        self.assertIn("/plugin", out["plugins_with_hooks"][0]["how_to_turn_off"])

    def test_overview_shows_tokens_per_part(self):
        self.sessions()
        ov = self.run_cli("scan")["overview"]
        parts = {r["part"]: r["tokens"] for r in ov["parts"]}
        self.assertEqual(parts["skill listing"], round(len(self.full) / 2.7))
        self.assertEqual(ov["total"], sum(parts.values()))


if __name__ == "__main__":
    unittest.main()
