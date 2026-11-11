#!/usr/bin/env python3
"""trim tips: short rules the user can turn on in /trim:setup (P-12).

A tip that is on is added to Claude's context when a session starts, by the
SessionStart hook in hooks/session_tips.py. Turning a tip on or off never
changes a session that is already running; it applies at the next session.

Choices live in trim's own file, not in the user's CLAUDE.md:
    ~/.claude/trim/tips.json   (folder can be moved with TRIM_HOME)

Command line, used by the /trim:setup skill:
    python3 tips.py list            all tips as JSON: id, title, on, tokens, text
    python3 tips.py set <id> on|off
"""
import json
import os
import sys
import tempfile

TIPS = {
    "browsing": {
        "title": "Text-first browsing",
        "why": (
            "Page text costs far fewer tokens than a screenshot. Putting the "
            "half-size scale in batch screenshots also stops auto mode from "
            "refusing batches that trim would otherwise change."
        ),
        "text": (
            "Browsing: read pages with get_page_text, find or read_page "
            "(filter: interactive) first. Take a screenshot only for a visual "
            "check. In every screenshot, including inside browser_batch, set "
            "scale: 0.5; use zoom to see one region in detail."
        ),
    },
}

HEADER = "trim tips (turned on by the user in /trim:setup):"


def home():
    return os.environ.get("TRIM_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "trim"
    )


def choices_path():
    return os.path.join(home(), "tips.json")


def estimate_tokens(text):
    """Rough estimate: about 4 characters per token."""
    return max(1, round(len(text) / 4))


def load_choices():
    """Return {tip_id: bool}. A missing or broken file means every tip is off."""
    try:
        with open(choices_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    tips = data.get("tips") if isinstance(data, dict) else None
    if not isinstance(tips, dict):
        return {}
    return {k: v is True for k, v in tips.items() if k in TIPS}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def save_choices(choices):
    _write_json(choices_path(), {"version": 1, "tips": dict(sorted(choices.items()))})


def enabled_ids(choices):
    return [tip_id for tip_id in TIPS if choices.get(tip_id)]


def render(tip_ids):
    """The context text for these tips, or '' when there are none."""
    lines = [f"- {TIPS[t]['text']}" for t in tip_ids if t in TIPS]
    return "\n".join([HEADER] + lines) if lines else ""


def listing():
    choices = load_choices()
    return [
        {
            "id": tip_id,
            "title": tip["title"],
            "on": bool(choices.get(tip_id)),
            "tokens": estimate_tokens(tip["text"]),
            "why": tip["why"],
            "text": tip["text"],
        }
        for tip_id, tip in TIPS.items()
    ]


def main(argv):
    if len(argv) == 1 and argv[0] == "list":
        print(json.dumps({"file": choices_path(), "tips": listing()}, indent=2))
        return 0
    if len(argv) == 3 and argv[0] == "set" and argv[2] in ("on", "off"):
        if argv[1] not in TIPS:
            print(f"unknown tip: {argv[1]} (known: {', '.join(TIPS)})", file=sys.stderr)
            return 2
        choices = load_choices()
        choices[argv[1]] = argv[2] == "on"
        save_choices(choices)
        print(f"{argv[1]}: {argv[2]} (applies at the next session)")
        return 0
    print("usage: tips.py list | tips.py set <id> on|off", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
