"""trim P-14: collapse runs of progress and pass lines into one line each.

A run is RUN_MIN or more lines in a row of the same kind: `Downloading ...`,
`Compiling ...`, `PASS ...`, `ok ...`, a check mark, or a verbose test line
ending in `... ok`. Each run becomes one line:

    [trim: 60 "Downloading" lines collapsed]

A line the caller calls a failure never joins a run, and a run of one or two
lines stays as it is, so nothing that carries information is lost. The full
text stays in the full copy that the caller names (ADR 0015).
"""
import re

RUN_MIN = 3
PROBE_CHARS = 200  # kind is read from the start of the line only

_WORDS = (
    "Downloading|Compiling|Fetching|Installing|Resolving|Building|Unpacking|"
    "Collecting|Updating|Checking|Linking|Extracting|Preparing"
)
_START = re.compile(r"^\s*(%s)\b" % _WORDS)
_PASS = re.compile(r"^\s*PASS\b")
_OK = re.compile(r"^ok\s+\S+")
_TICK = re.compile(r"^\s*[✓✔]\s")
_VERBOSE_OK = re.compile(r"\.\.\. ok$")


def kind(line):
    """Return the kind of a progress or pass line, or None."""
    probe = line[:PROBE_CHARS]
    m = _START.match(probe)
    if m:
        return m.group(1)
    if _PASS.match(probe):
        return "PASS"
    if _OK.match(probe):
        return "ok"
    if _TICK.match(probe):
        return "✓"
    if _VERBOSE_OK.search(line.rstrip()[-20:]):
        return "... ok"
    return None


def collapse(lines, is_failure=None):
    """Return lines with each run of RUN_MIN or more same-kind lines made one line."""
    out = []
    i = 0
    n = len(lines)
    while i < n:
        k = kind(lines[i])
        if k is None or (is_failure and is_failure(lines[i])):
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < n and kind(lines[j]) == k and not (is_failure and is_failure(lines[j])):
            j += 1
        if j - i >= RUN_MIN:
            out.append('[trim: %d "%s" lines collapsed]' % (j - i, k))
        else:
            out.extend(lines[i:j])
        i = j
    return out


def collapse_text(text, is_failure=None):
    return "\n".join(collapse(text.split("\n"), is_failure))
