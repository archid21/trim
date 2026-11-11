#!/usr/bin/env python3
"""trim P-10: show what fills this project's fixed context, and what it has not used.

Diagnose only. trim never changes settings or files here; for each unused item
it says where to turn it off in Claude Code (ADR 0011).

  python3 fixed_context.py [--project DIR]     prints JSON

What it reads: the project's transcripts in <claude dir>/projects/<slug>/. Each
one holds the session's skill, agent, tool and MCP listings, CLAUDE.md files and
SessionStart hook output as attachments, and the tool calls that show use.

Output:
- `overview`: tokens per request for each part of the fixed context (latest session).
- `items`: agent types, MCP servers and plugins with no use in the window
  (90 days, or cleanupPeriodDays if lower), largest first, each with `tokens`
  and `how_to_turn_off`.
- `plugins_with_hooks`: unused plugins that also run hooks (turning one off stops them).
- `skills`: a pointer to Claude Code's /skill-doctor and /skills.
Tokens are estimates: characters / 2.7 (measured in the P-10 probe).
"""
import glob
import json
import os
import re
import sys
import time

WINDOW_DAYS = 90  # user's choice (ADR 0011); capped at cleanupPeriodDays
MIN_SESSIONS = 5  # fewer sessions in the window: no unused list
CHARS_PER_TOKEN = 2.7  # measured in the P-10 probe
UUID_SERVER = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# MCP servers that belong to the desktop app itself: never listed as unused.
APP_SERVERS = {"visualize", "terminal", "scheduled-tasks", "mcp-registry"}
APP_SERVER_PREFIXES = ("ccd_", "Claude_")
SELF_PLUGIN = "trim"
LISTING_TYPES = ("skill_listing", "agent_listing_delta", "deferred_tools_delta",
                 "mcp_instructions_delta", "instructions")


# ---------- paths and helpers ----------

def claude_dir():
    return os.environ.get("TRIM_CLAUDE_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def home_dir():
    return os.path.dirname(claude_dir()) if os.environ.get("TRIM_CLAUDE_DIR") else os.path.expanduser("~")


def project_slug(project):
    return re.sub(r"[^A-Za-z0-9-]", "-", project)


def transcripts_dir(project):
    base = os.path.join(claude_dir(), "projects")
    for p in (os.path.realpath(project), project):
        d = os.path.join(base, project_slug(p))
        if os.path.isdir(d):
            return d
    return os.path.join(base, project_slug(os.path.realpath(project)))


def tokens(chars):
    return int(round(chars / CHARS_PER_TOKEN))


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def window_days():
    user = read_json(os.path.join(claude_dir(), "settings.json"), {}) or {}
    days = user.get("cleanupPeriodDays") if isinstance(user, dict) else None
    if isinstance(days, int) and 0 < days < WINDOW_DAYS:
        return days
    return WINDOW_DAYS


# ---------- reading transcripts ----------

def _records(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def _tool_uses(d):
    msg = d.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        yield ("text", content)
        return
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            yield ("tool", b)
        elif b.get("type") == "text":
            yield ("text", b.get("text", ""))


COMMAND = re.compile(r"<command-name>/?([^<\s]+)</command-name>")


def scan_transcripts(project, days):
    """Return (listing, use, sessions, server_tools) for the window."""
    d = transcripts_dir(project)
    cutoff = time.time() - days * 86400
    mains = [p for p in glob.glob(os.path.join(d, "*.jsonl")) if os.path.getmtime(p) >= cutoff]
    subs = [p for p in glob.glob(os.path.join(d, "*", "subagents", "*.jsonl"))
            if os.path.getmtime(p) >= cutoff]
    use = {"skill": set(), "agent": set(), "mcp": set(), "command": set()}
    server_tools = {}
    listing = {}
    for path in sorted(mains, key=os.path.getmtime, reverse=True) + subs:
        found = {}
        for rec in _records(path):
            a = rec.get("attachment")
            if rec.get("type") == "attachment" and isinstance(a, dict):
                t = a.get("type")
                if t == "deferred_tools_delta":
                    for n in a.get("addedNames") or []:
                        if isinstance(n, str) and n.startswith("mcp__") and n.count("__") >= 2:
                            srv, tool = n.split("__")[1], n.split("__", 2)[2]
                            server_tools.setdefault(srv, set()).add(tool)
                if t in LISTING_TYPES:
                    found.setdefault("_seq", []).append(a)
                    found.setdefault(t, a)
                elif (t == "hook_additional_context" and a.get("hookEvent") == "SessionStart"
                      and not found.get("_answered")):
                    found.setdefault("hooks", []).extend(
                        c for c in a.get("content") or [] if isinstance(c, str))
                continue
            if rec.get("type") == "assistant":
                found["_answered"] = True
            for kind, x in _tool_uses(rec):
                if kind == "text":
                    for m in COMMAND.findall(x if isinstance(x, str) else ""):
                        use["command"].add(m)
                    continue
                name, inp = x.get("name") or "", x.get("input") or {}
                if name == "Skill":
                    use["skill"].add(str(inp.get("skill") or "").lstrip("/"))
                elif name in ("Agent", "Task"):
                    use["agent"].add(inp.get("subagent_type") or "general-purpose")
                elif name.startswith("mcp__") and name.count("__") >= 2:
                    srv, tool = name.split("__")[1], name.split("__", 2)[2]
                    use["mcp"].add(srv)
                    server_tools.setdefault(srv, set()).add(tool)
        if not listing and path in mains and "skill_listing" in found and "agent_listing_delta" in found:
            listing = merge_listings(found)
    return listing, use, len(mains), server_tools


def _merge_lines(base, a, names_key, lines_key, removed_key):
    """Apply one *_delta attachment to a {name: line} map, in place."""
    for n in a.get(removed_key) or []:
        base.pop(n, None)
    names = a.get(names_key) or []
    lines = a.get(lines_key) or [None] * len(names)
    for n, line in zip(names, lines):
        base[n] = line if line is not None else n


def merge_listings(found):
    """The listings as they stood at the end of the session: the first full listing
    plus every later change (agents removed mid-session, skills added, and so on)."""
    agents, tools, blocks, skills = {}, {}, {}, None
    builtin = {"builtInTypes": [], "builtInPluginTypes": []}
    for a in found.get("_seq", []):
        t = a.get("type")
        if t == "agent_listing_delta":
            _merge_lines(agents, a, "addedTypes", "addedLines", "removedTypes")
            for k in builtin:
                builtin[k] = a.get(k) or builtin[k]
        elif t == "deferred_tools_delta":
            _merge_lines(tools, a, "addedNames", "addedLines", "removedNames")
        elif t == "mcp_instructions_delta":
            _merge_lines(blocks, a, "addedNames", "addedBlocks", "removedNames")
        elif t == "skill_listing":
            if skills is None or a.get("isInitial"):
                skills = {"type": t, "content": a.get("content") or "", "names": list(a.get("names") or [])}
            else:  # a later listing adds skills
                skills["content"] = (skills["content"] + "\n" + (a.get("content") or "")).strip("\n")
                skills["names"] += [n for n in a.get("names") or [] if n not in skills["names"]]
    out = {k: v for k, v in found.items() if not k.startswith("_") and k not in LISTING_TYPES}
    out["skill_listing"] = skills or {}
    out["agent_listing_delta"] = dict(builtin, addedTypes=list(agents), addedLines=list(agents.values()))
    out["deferred_tools_delta"] = {"addedNames": list(tools), "addedLines": list(tools.values())}
    out["mcp_instructions_delta"] = {"addedNames": list(blocks), "addedBlocks": list(blocks.values())}
    if "instructions" in found:
        out["instructions"] = found["instructions"]
    return out


def connector_aliases(server_tools):
    """Map a desktop-app UUID server name to its CLI name (claude_ai_<Name>) by tool names."""
    named = {s: t for s, t in server_tools.items() if s.startswith("claude_ai_")}
    alias = {}
    for s, tools in server_tools.items():
        if not UUID_SERVER.fullmatch(s):
            continue
        best, score = None, 0.0
        for n, ntools in named.items():
            j = len(tools & ntools) / float(len(tools | ntools) or 1)
            if j > score:
                best, score = n, j
        if best and score >= 0.6:
            alias[s] = best
    return alias


# ---------- plugins ----------

def installed_plugins(project):
    data = read_json(os.path.join(claude_dir(), "plugins", "installed_plugins.json"), {}) or {}
    plugins = data.get("plugins", {}) if isinstance(data, dict) else {}
    enabled = {}
    for path in (os.path.join(claude_dir(), "settings.json"),
                 os.path.join(project, ".claude", "settings.json"),
                 os.path.join(project, ".claude", "settings.local.json")):
        s = read_json(path, {}) or {}
        if isinstance(s, dict):
            enabled.update(s.get("enabledPlugins") or {})
    out = {}
    for key, installs in plugins.items():
        if not enabled.get(key):
            continue
        inst = installs[-1] if isinstance(installs, list) and installs else {}
        root = inst.get("installPath") or ""
        has_hooks = os.path.isfile(os.path.join(root, "hooks", "hooks.json"))
        manifest = read_json(os.path.join(root, ".claude-plugin", "plugin.json"), {}) or {}
        if manifest.get("hooks"):
            has_hooks = True
        out[key] = {"name": key.split("@")[0], "has_hooks": has_hooks}
    return out


def _server_plugin(server, plugin_names):
    for p in plugin_names:
        if server.startswith("plugin_%s_" % p) or server.startswith("plugin:%s:" % p):
            return p
    return None


# ---------- the list ----------

def _skill_lines(listing):
    """Map each skill name to its listing line. A name can hold a colon (plugin:skill),
    so names come from the attachment's `names`; a line is "- name" or "- name: text"."""
    names = sorted(listing.get("names") or [], key=len, reverse=True)
    lines, last = {}, None
    for line in (listing.get("content") or "").split("\n"):
        name = None
        if line.startswith("- "):
            body = line[2:]
            name = next((n for n in names if body == n or body.startswith(n + ":")), None)
        if name:
            lines[name], last = line, name
        elif last:  # a description that runs over more than one line
            lines[last] += "\n" + line
    return lines


def _name_only(name, line):
    return line.strip() == "- " + name


# ---------- where to turn an item off ----------

def mcp_config_place(project, server):
    if server in (read_json(os.path.join(project, ".mcp.json"), {}) or {}).get("mcpServers", {}):
        return "%s/.mcp.json" % project
    cfg = read_json(os.path.join(home_dir(), ".claude.json"), {}) or {}
    if server in (cfg.get("projects", {}).get(project, {}) or {}).get("mcpServers", {}):
        return "~/.claude.json (this project)"
    if server in (cfg.get("mcpServers") or {}):
        return "~/.claude.json (all projects)"
    return None


def how_to(kind, name, project, plugins):
    if kind == "plugin":
        return ("/plugin, or in a terminal: `claude plugin disable %s --scope local` "
                "(this project only) or `claude plugin uninstall %s` (everywhere)" % (name, name))
    if kind == "agent":
        if ":" in name:
            p = name.split(":", 1)[0]
            key = next((k for k, v in plugins.items() if v["name"] == p), p)
            return ("comes from plugin %s; plugin agents cannot be turned off one at a time. "
                    "Turn off the plugin in /plugin, or deny it for this project with "
                    "`\"permissions\": {\"deny\": [\"Agent(%s)\"]}` in .claude/settings.local.json"
                    % (key, name))
        for folder, label in ((os.path.join(project, ".claude", "agents"), ".claude/agents"),
                              (os.path.join(claude_dir(), "agents"), "~/.claude/agents")):
            if os.path.isfile(os.path.join(folder, name + ".md")):
                return ("remove or move %s/%s.md (all sessions that read that folder), or deny it "
                        "for this project with `\"permissions\": {\"deny\": [\"Agent(%s)\"]}` "
                        "in .claude/settings.local.json" % (label, name, name))
        return ("deny it for this project with `\"permissions\": {\"deny\": [\"Agent(%s)\"]}` "
                "in .claude/settings.local.json" % name)
    if kind == "mcp":
        if name.startswith("claude_ai_") or UUID_SERVER.fullmatch(name):
            return ("claude.ai connector: turn it off in /mcp (or the Connectors menu in the "
                    "desktop app), or remove it on claude.ai under Settings > Connectors "
                    "(every app and project)")
        place = mcp_config_place(project, name)
        return "/mcp to turn it off" + ("; it is set up in %s" % place if place else "")
    return None


# ---------- the report ----------

def build(project):
    project = os.path.realpath(project)
    days = window_days()
    listing, use, sessions, server_tools = scan_transcripts(project, days)
    result = {"project": project, "window_days": days, "sessions": sessions,
              "overview": overview(listing), "items": [], "plugins_with_hooks": [],
              "skills": None, "note": None}
    if sessions < MIN_SESSIONS or not listing:
        result["note"] = ("Only %d sessions in the last %d days; trim needs %d before it "
                          "calls anything unused." % (sessions, days, MIN_SESSIONS))
        return result
    plugins = installed_plugins(project)
    pnames = {v["name"]: k for k, v in plugins.items()}
    used_prefixes = set()
    for kind in ("skill", "agent", "command"):
        for n in use[kind]:
            if ":" in n:
                used_prefixes.add(n.split(":", 1)[0])
    for srv in use["mcp"]:
        p = _server_plugin(srv, pnames)
        if p:
            used_prefixes.add(p)

    # agents
    agents = listing["agent_listing_delta"]
    builtin = set(agents.get("builtInTypes") or []) | set(agents.get("builtInPluginTypes") or [])
    agent_items = {}
    for name, line in zip(agents.get("addedTypes") or [], agents.get("addedLines") or []):
        if name in builtin or name in use["agent"]:
            continue
        agent_items[name] = len(line) + 1

    # MCP servers
    alias = connector_aliases(server_tools)
    reverse = {}
    for u, n in alias.items():
        reverse.setdefault(n, set()).add(u)
    dt = listing.get("deferred_tools_delta") or {}
    srv_chars = {}
    for n in dt.get("addedNames") or []:
        if isinstance(n, str) and n.startswith("mcp__") and n.count("__") >= 2:
            s = n.split("__")[1]
            srv_chars[s] = srv_chars.get(s, 0) + len(n) + 1
    mi = listing.get("mcp_instructions_delta") or {}
    for s, block in zip(mi.get("addedNames") or [], mi.get("addedBlocks") or []):
        srv_chars[s] = srv_chars.get(s, 0) + len(block)
    used_mcp = set(use["mcp"])
    for s in list(used_mcp):
        used_mcp |= {alias.get(s, s)} | reverse.get(s, set())
    mcp_items = {}
    for s, chars in srv_chars.items():
        if s in APP_SERVERS or s.startswith(APP_SERVER_PREFIXES) or s in used_mcp:
            continue
        display = alias.get(s, s)
        names = sorted({s, display} | reverse.get(display, set()))
        item = mcp_items.setdefault(display, {"chars": 0, "names": names})
        item["chars"] += chars

    # skills
    sl = listing["skill_listing"]
    lines = _skill_lines(sl)
    at_budget = any(_name_only(n, line) for n, line in lines.items())
    used_skills = use["skill"] | use["command"]
    skill_items = {n: len(line) + 1 for n, line in lines.items() if n not in used_skills}

    # plugins: unused when none of its parts was used
    for key, info in sorted(plugins.items()):
        p = info["name"]
        if p == SELF_PLUGIN or p in used_prefixes:
            continue
        a = {n: c for n, c in agent_items.items() if n.startswith(p + ":")}
        k = {n: c for n, c in skill_items.items() if n.startswith(p + ":")}
        m = {n: v for n, v in mcp_items.items() if _server_plugin(n, [p])}
        if not (a or k or m):
            continue
        chars = sum(a.values()) + sum(v["chars"] for v in m.values()) + (0 if at_budget else sum(k.values()))
        item = {"id": "plugin:" + key, "kind": "plugin", "name": key, "tokens": tokens(chars),
                "parts": {"agents": len(a), "skills": len(k), "mcp_servers": len(m)},
                "note": "skills part counts 0 tokens: the skill listing is at its budget" if at_budget and k else None}
        for n in a:
            agent_items.pop(n)
        for n in k:
            skill_items.pop(n)
        for n in m:
            mcp_items.pop(n)
        if info["has_hooks"]:
            item["note"] = "; ".join(x for x in (
                "has hooks: turning it off also stops its hooks (and its hook output, "
                "which this estimate does not count)", item["note"]) if x)
            result["plugins_with_hooks"].append(item)
        else:
            result["items"].append(item)

    for n, c in agent_items.items():
        result["items"].append({"id": "agent:" + n, "kind": "agent", "name": n,
                                "tokens": tokens(c), "note": None})
    for n, v in mcp_items.items():
        result["items"].append({"id": "mcp:" + n, "kind": "mcp", "name": n,
                                "tokens": tokens(v["chars"]), "names": v["names"], "note": None})
    result["skills"] = {
        "unused": len(skill_items), "listing_at_budget": at_budget,
        "note": "Skills: run /skill-doctor to see unused skills, and /skills to turn them off."
                + (" The skill listing is at its size budget now, so turning skills off saves "
                   "0 tokens; other skills get their descriptions back." if at_budget else "")}
    for item in result["items"] + result["plugins_with_hooks"]:
        item["how_to_turn_off"] = how_to(item["kind"], item["name"], project, plugins)
    result["items"].sort(key=lambda i: -i["tokens"])
    result["plugins_with_hooks"].sort(key=lambda i: -i["tokens"])
    return result


def overview(listing):
    """Tokens per request for each part of the fixed context, from the latest session."""
    if not listing:
        return None
    sl = listing.get("skill_listing") or {}
    ag = listing.get("agent_listing_delta") or {}
    dt = listing.get("deferred_tools_delta") or {}
    mi = listing.get("mcp_instructions_delta") or {}
    ins = listing.get("instructions") or {}
    parts = [
        ("skill listing", len(sl.get("content") or ""), "%d skills" % len(sl.get("names") or [])),
        ("agent listing", sum(len(x) + 1 for x in ag.get("addedLines") or []),
         "%d agent types" % len(ag.get("addedTypes") or [])),
        ("deferred tool names", sum(len(x) + 1 for x in dt.get("addedLines") or dt.get("addedNames") or []),
         "%d tools" % len(dt.get("addedNames") or [])),
        ("MCP server instructions", sum(len(x) for x in mi.get("addedBlocks") or []),
         "%d server(s)" % len(mi.get("addedNames") or [])),
        ("CLAUDE.md and memory files", sum(len(f.get("content") or "") for f in ins.get("files") or []),
         ", ".join(f.get("path", "?") for f in ins.get("files") or [])),
        ("SessionStart hook output", sum(len(x) for x in listing.get("hooks") or []),
         "%d hook outputs" % len(listing.get("hooks") or [])),
    ]
    rows = [{"part": n, "tokens": tokens(c), "detail": d} for n, c, d in parts]
    return {"parts": rows, "total": sum(r["tokens"] for r in rows),
            "note": "Not counted: Claude Code's own system prompt and loaded (not deferred) tool definitions."}


def main(argv):
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if argv[:1] == ["--project"] and len(argv) > 1:
        project = argv[1]
    print(json.dumps(build(project), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
