#!/usr/bin/env python3
"""trim P-4: clean up connector (MCP) tool results.

PostToolUse command hook for every MCP tool (`mcp__*`). It removes parts of a
text result that carry no information for Claude:

- base64 data: a data: URI, or a base64 run of BASE64_MIN characters or more,
  becomes "[trim: base64 data, N characters, in the full copy]";
- empty fields: JSON object fields that are null, "", [] or {} are removed
  (items in lists are kept, so positions do not change; false and 0 stay);
- repeated lines: REPEAT_MIN or more identical lines in a row become the line
  once plus "[trim: line repeated N times]".

The result changes only when its text is over MIN_CHARS characters and the
cleanup saves at least MIN_SAVING of it. Then the full original text goes to
~/.claude/trim/out/ (see fullcopy.py), and the cleaned result starts with a
line naming its path. A result may arrive as content blocks, a plain string, or
a dict with a "content" list. Image and other non-text blocks pass through as
they are. The hook replaces the result through `updatedMCPToolOutput` before Claude
reads it (research: 2026-11-10-mcp-output-hook.md).

Off switch: TRIM_TOOL_CLEANUP=off. The hook fails open: on any error it
prints nothing, and Claude gets the original result. See ADR 0007.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullcopy import save_full_copy  # noqa: E402
from savings import log_saving  # noqa: E402
from progress_runs import collapse_text  # noqa: E402

MIN_CHARS = 2000  # smaller results are left alone
MIN_SAVING = 0.10  # ... and so are results that would shrink less than this
BASE64_MIN = 1000  # shorter runs (IDs, page tokens) are kept
REPEAT_MIN = 3  # this many identical lines in a row are collapsed

# Text that only trim may write. In tool text, "[trim" becomes "(trim", so a
# connector cannot fake a trim header or note (for example a false
# "Full copy:" path).
MARKER = re.compile(r"\[(?=trim)", re.IGNORECASE)

# Invisible and direction-changing characters. The original JSON may carry
# them as \u escapes; json.dumps with ensure_ascii=False would turn them into
# raw characters that a reader cannot see, so they are escaped again.
HIDDEN = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")

DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/_=-]+")
BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}" % BASE64_MIN)


def is_off():
    return os.environ.get("TRIM_TOOL_CLEANUP", "").strip().lower() == "off"


def _base64_note(n):
    return "[trim: base64 data, %s characters, in the full copy]" % format(n, ",")


def _looks_base64(run):
    # Real base64 mixes upper case, lower case and digits.
    return (re.search(r"[A-Z]", run) and re.search(r"[a-z]", run)
            and re.search(r"[0-9]", run))


def strip_base64(text):
    text = DATA_URI.sub(lambda m: _base64_note(len(m.group(0))), text)
    return BASE64_RUN.sub(
        lambda m: _base64_note(len(m.group(0))) if _looks_base64(m.group(0)) else m.group(0),
        text,
    )


def collapse_repeats(text):
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        count = j - i + 1
        if count >= REPEAT_MIN and lines[i].strip():
            out.append("%s  [trim: line repeated %d times]" % (lines[i], count))
        else:
            out.extend(lines[i:j + 1])
        i = j + 1
    return "\n".join(out)


def clean_string(s):
    return collapse_text(collapse_repeats(strip_base64(MARKER.sub("(", s))))


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def clean_json(value):
    """Return a cleaned copy of a parsed JSON value."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = clean_json(v)
            if not _empty(v):
                out[MARKER.sub("(", k) if isinstance(k, str) else k] = v
        return out
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, str):
        return clean_string(value)
    return value


def clean_text(text):
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            indent = 1 if "\n" in stripped else None
            seps = None if indent else (",", ":")
            out = json.dumps(clean_json(parsed), indent=indent, separators=seps, ensure_ascii=False)
            return HIDDEN.sub(lambda m: "\\u%04x" % ord(m.group(0)), out)
    return clean_string(text)


def clean_blocks(blocks):
    """Return (new blocks, full original text, chars before, chars after), or None."""
    if not isinstance(blocks, list) or not blocks:
        return None
    if not all(isinstance(b, dict) for b in blocks):
        return None
    texts = [b.get("text") for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
    before = sum(len(t) for t in texts)
    if before <= MIN_CHARS:
        return None
    new_blocks = []
    after = 0
    for b in blocks:
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            t = clean_text(b["text"])
            after += len(t)
            new_blocks.append({**b, "text": t})
        else:
            new_blocks.append(b)
    if after > before * (1 - MIN_SAVING):
        return None
    full = "\n\n".join(texts)
    return new_blocks, full, before, after


def rewrite(tool_name, tool_response, session=None):
    """Return the new MCP output (list of blocks), or None when nothing changes."""
    if not str(tool_name).startswith("mcp__"):
        return None
    if isinstance(tool_response, str):
        tool_response = [{"type": "text", "text": tool_response}]
    elif isinstance(tool_response, dict) and isinstance(tool_response.get("content"), list):
        tool_response = tool_response["content"]
    cleaned = clean_blocks(tool_response)
    if cleaned is None:
        return None
    blocks, full, before, after = cleaned
    path = save_full_copy(full, suffix=".txt")
    log_saving("tool_cleanup", before, after, session=session, full_copy=path)
    header = {
        "type": "text",
        "text": "[trim] Cleaned: %s -> %s characters (base64 data, empty fields and repeated lines removed). Full copy: %s"
        % (format(before, ","), format(after, ","), path),
    }
    return [header] + blocks


def main():
    if is_off():
        return 0
    try:
        event = json.load(sys.stdin)
        new_output = rewrite(event.get("tool_name"), event.get("tool_response"), event.get("session_id"))
    except Exception:
        return 0
    if new_output is not None:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedMCPToolOutput": new_output,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
