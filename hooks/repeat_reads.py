#!/usr/bin/env python3
"""trim P-5: point to an unchanged file instead of reading it again.

One command hook for four events:

- PostToolUse (Read): remember the read. The key is the real path plus offset,
  limit and pages; the value is the SHA-256 of the file on disk, its size and
  the time. Files under MIN_BYTES are not remembered (a pointer saves little).
- PreToolUse (Read): if the same read is remembered and the file is unchanged,
  stop the Read (exit code 2, reason on stderr) and tell Claude its text is
  still in the conversation. The
  entry is then marked `pointed`. If Claude Reads it again anyway, the Read
  goes through (the escape hatch) and PostToolUse remembers it afresh.
- PreCompact, and SessionStart with source "compact" or "clear": forget all
  reads of the session, because the text is no longer in the conversation.
- Anything from a subagent (the event has `agent_id`): do nothing. A subagent
  has its own conversation and cannot see the main one.

State is kept per session in <TRIM_HOME>/reads/<session_id>.json (default
~/.claude/trim/reads/): folder mode 700, file mode 600. Entries older than
KEEP_DAYS days are deleted.

Off switch: TRIM_REPEAT_READS=off. The hook fails open: on any error it prints
nothing and exits 0, so Claude gets a normal read. See ADR 0009 and 0010.
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullcopy import trim_home  # noqa: E402
from savings import log_saving  # noqa: E402

MIN_BYTES = 2000  # smaller files are not remembered
KEEP_DAYS = 7  # state files older than this are deleted
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def is_off():
    return os.environ.get("TRIM_REPEAT_READS", "").strip().lower() == "off"


def reads_dir():
    return os.path.join(trim_home(), "reads")


def state_path(session_id):
    """Path of the session's state file, or None when the id is not safe."""
    if not isinstance(session_id, str) or not SAFE_ID.fullmatch(session_id):
        return None
    return os.path.join(reads_dir(), session_id + ".json")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    """Write the state file new (mode 600) in a mode 700 folder, then rename it in."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    _clean_old(folder)
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _clean_old(folder):
    limit = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            if name.endswith((".json", ".tmp")) and os.path.getmtime(p) < limit:
                os.remove(p)
    except OSError:
        pass


def forget(session_id):
    path = state_path(session_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def read_key(event):
    """Return the key string for a Read event, or None."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    if not os.path.isabs(file_path):
        file_path = os.path.join(str(event.get("cwd") or os.getcwd()), file_path)
    real = os.path.realpath(file_path)
    return json.dumps(
        [real, tool_input.get("offset"), tool_input.get("limit"), tool_input.get("pages")]
    ), real


def file_fingerprint(real):
    """Return (sha256 hex, size in bytes) of the file now, or None when unreadable."""
    try:
        h = hashlib.sha256()
        size = 0
        with open(real, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
    except OSError:
        return None
    return h.hexdigest(), size


def record(event):
    path = state_path(event.get("session_id"))
    keyed = read_key(event)
    if path is None or keyed is None:
        return
    key, real = keyed
    fp = file_fingerprint(real)
    if fp is None or fp[1] < MIN_BYTES:
        return
    state = load_state(path)
    state[key] = {
        "sha256": fp[0],
        "size": fp[1],
        "time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_state(path, state)


def check(event):
    """Return the reason text when the read should be stopped, else None."""
    path = state_path(event.get("session_id"))
    keyed = read_key(event)
    if path is None or keyed is None:
        return None
    key, real = keyed
    state = load_state(path)
    entry = state.get(key)
    if not isinstance(entry, dict) or entry.get("pointed"):
        return None
    fp = file_fingerprint(real)
    if fp is None or fp[0] != entry.get("sha256"):
        return None
    entry["pointed"] = True
    save_state(path, state)
    reason = (
        "[trim] %s is unchanged since your earlier Read with the same arguments "
        "(at %s UTC); its text is still in this conversation above, so use that. "
        "If you cannot find it, Read it again and trim will let it through."
        % (real, str(entry.get("time", "an earlier time")).replace("T", " ").rstrip("Z"))
    )
    log_saving("repeat_reads", fp[1], len(reason), session=event.get("session_id"))
    return reason


def handle(event):
    if not isinstance(event, dict) or "agent_id" in event:
        return None
    name = event.get("hook_event_name")
    if name == "PreCompact":
        forget(event.get("session_id"))
    elif name == "SessionStart":
        if event.get("source") in ("compact", "clear"):
            forget(event.get("session_id"))
    elif event.get("tool_name") == "Read":
        if name == "PostToolUse":
            record(event)
        elif name == "PreToolUse":
            return check(event)
    return None


def main():
    if is_off():
        return 0
    try:
        output = handle(json.load(sys.stdin))
    except Exception:
        return 0
    if output is None:
        return 0
    # Stop the Read with exit code 2 and the reason on stderr. Claude Code
    # always shows this to Claude as "PreToolUse:Read hook error: <reason>",
    # and other plugins (rightsize, its ADR 0049) read that prefix as a hook
    # refusal, not a failed call. A JSON permissionDecision "deny" may reach
    # Claude without the prefix. See ADR 0010.
    sys.stderr.write(output)
    return 2


if __name__ == "__main__":
    sys.exit(main())
