#!/usr/bin/env python3
"""trim P-12: add the tips the user turned on to Claude's context.

SessionStart command hook. It prints the tips as `additionalContext`.

No mid-session change: on a new session (startup or clear) it saves the list
of tips it added under the session id. After a compact in that same session,
it adds that saved list again, not the current choices. So a tip turned on or
off during a session applies only at the next session.

Off switch: set the environment variable TRIM_TIPS=off.
The hook fails open: on any error it prints nothing.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tips  # noqa: E402

KEEP_DAYS = 30


def _snapshot_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))[:128]
    return os.path.join(tips.home(), "sessions", f"{safe}.json")


def _prune_snapshots():
    folder = os.path.join(tips.home(), "sessions")
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.endswith(".json") and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


def tip_ids_for(event):
    source = event.get("source")
    session_id = event.get("session_id")
    if source == "compact" and session_id:
        try:
            with open(_snapshot_path(session_id), encoding="utf-8") as f:
                saved = json.load(f).get("tips")
            if isinstance(saved, list):
                return [t for t in saved if t in tips.TIPS]
        except (OSError, ValueError, AttributeError):
            pass
    ids = tips.enabled_ids(tips.load_choices())
    if session_id:
        tips._write_json(_snapshot_path(session_id), {"tips": ids})
        _prune_snapshots()
    return ids


def main():
    if os.environ.get("TRIM_TIPS", "").strip().lower() == "off":
        return 0
    try:
        event = json.load(sys.stdin)
        text = tips.render(tip_ids_for(event if isinstance(event, dict) else {}))
    except Exception:
        return 0
    if text:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
