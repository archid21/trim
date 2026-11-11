#!/usr/bin/env python3
"""trim P-2: take browser screenshots at half size.

PreToolUse command hook for the desktop app's browser tools
(mcp__Claude_Browser__computer and mcp__Claude_Browser__browser_batch).
A screenshot with no `scale` gets `scale: 0.5`. Clicks still land, because
the tool keeps reporting the full-size coordinate frame (P-1, Q2).

Left alone:
- a screenshot that already sets `scale` (Claude can ask for `scale: 1`),
- `zoom` and every other action.

Off switch: set the environment variable TRIM_BROWSER_SCALE=off.
The hook fails open: on any error it prints nothing, and the call goes out
unchanged. See ADR 0004.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from savings import log_saving  # noqa: E402

# Assumed saving per screenshot: about 673 tokens full size minus about 168 at
# half size (width x height / 750, an estimate from P-1, not measured).
ASSUMED_TOKENS = 505
SCALE = 0.5


def _is_computer(name):
    return name == "computer" or str(name).endswith("__computer")


def _scale_screenshot(inp):
    """Return a copy of a computer-tool input with scale added, or None."""
    if not isinstance(inp, dict) or inp.get("action") != "screenshot":
        return None
    if "scale" in inp:
        return None
    return {**inp, "scale": SCALE}


def rewrite(tool_name, tool_input):
    """Return the new tool input, or None when nothing changes."""
    if not isinstance(tool_input, dict):
        return None
    if _is_computer(tool_name):
        return _scale_screenshot(tool_input)
    if str(tool_name).endswith("__browser_batch"):
        actions = tool_input.get("actions")
        if not isinstance(actions, list):
            return None
        changed = False
        new_actions = []
        for item in actions:
            new_inp = None
            if isinstance(item, dict) and _is_computer(item.get("name")):
                new_inp = _scale_screenshot(item.get("input"))
            if new_inp is None:
                new_actions.append(item)
            else:
                new_actions.append({**item, "input": new_inp})
                changed = True
        return {**tool_input, "actions": new_actions} if changed else None
    return None


def _count_scaled(old, new):
    """How many screenshots the rewrite gave a scale."""
    if isinstance(new, dict) and isinstance(new.get("actions"), list):
        olds = old.get("actions", []) if isinstance(old, dict) else []
        return sum(
            1 for a, b in zip(olds, new["actions"])
            if isinstance(b, dict) and isinstance(b.get("input"), dict)
            and b["input"].get("scale") == SCALE and "scale" not in (a.get("input") or {})
        )
    return 1 if isinstance(new, dict) and new.get("scale") == SCALE else 0


def main():
    if os.environ.get("TRIM_BROWSER_SCALE", "").strip().lower() == "off":
        return 0
    try:
        event = json.load(sys.stdin)
        new_input = rewrite(event.get("tool_name"), event.get("tool_input"))
    except Exception:
        return 0
    if new_input is not None:
        shots = _count_scaled(event.get("tool_input"), new_input)
        if shots:
            log_saving("browser_scale", tokens_saved=ASSUMED_TOKENS * shots,
                       session=event.get("session_id"), assumed=True)
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": new_input,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
