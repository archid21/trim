#!/usr/bin/env python3
"""trim P-3: cap long test and build output.

Two modes in one file:

1. Hook (no arguments): a PreToolUse command hook for the Bash tool. When the
   command is one simple test or build command, it pipes the output into this
   script's cap mode:

       set -o pipefail; <command> 2>&1 | python3 <this file> cap

   A PostToolUse hook cannot replace Bash output, so the pipe must be added
   before the command runs (research: 2026-11-10-bash-output-hook.md).
   The command stays in the command line as it is, unquoted, so Claude Code's
   permission rules (allow, deny, auto mode) still see it. An earlier version
   quoted it inside a wrapper, which hid it from deny rules (ADR 0006).
2. Cap (`output_cap.py cap`, output on stdin): output of MAX_LINES lines or
   less comes back as it is. Longer output comes back capped: every failure
   line with a little context, every summary line (Ran N tests, OK, pytest's
   passed line, cargo's test result and others), then the last TAIL_LINES
   lines. The full copy
   goes to ~/.claude/trim/out/ (folder can be moved with TRIM_HOME; folder
   mode 700, file mode 600), and the capped result names its path. pipefail
   keeps the command's exit code.

Left alone: commands that are not tests or builds, background commands,
commands already piped into the cap, and any command with shell operators
(; & | < > ` ( ) { } or a newline), so no part of a compound command can be
hidden or changed.

Off switch: TRIM_OUTPUT_CAP=off. The hook fails open: on any error it prints
nothing, and the command runs unchanged. If capping fails, cap mode prints
the full output. See ADR 0006.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullcopy import save_full_copy  # noqa: E402
from progress_runs import collapse  # noqa: E402
from savings import log_saving  # noqa: E402

MAX_LINES = 150  # output longer than this is capped
MAX_CHARS = 20000  # ... and so is output with more characters than this
TAIL_LINES = 40  # last lines always kept
BEFORE, AFTER = 2, 12  # context kept around a failure line
FAIL_BUDGET = 300  # most failure lines (with context) kept; then lines only
LINE_CHARS = 500  # longer single lines are cut

SELF = os.path.abspath(__file__)

# Shell operators. A command with any of these is left alone.
OPERATORS = re.compile(r"[;&|<>`(){}\n\r]")

# Test and build commands, matched as a word in a simple command.
RUNNERS = re.compile(
    r"""(?x)(?:^|[\s/])(?:
        pytest|py\.test|tox|nox|jest|vitest|mocha|karma|rspec|phpunit|ctest
      | tsc|eslint|mvn|mvnw|gradle|gradlew|make|ninja|xcodebuild
      | python3?\ -m\ (?:pytest|unittest)
      | (?:npm|pnpm|yarn|bun)\ (?:run\ )?(?:test|build|lint|typecheck|check)
      | cargo\ (?:test|build|check|clippy|nextest)
      | go\ (?:test|build|vet)
      | dotnet\ (?:test|build)
      | swift\ (?:test|build)
      | bazel\ (?:test|build)
      | cmake\ --build
      | playwright\ test
      | cypress\ run
    )(?=$|\s)"""
)

# A line that reports a failure or an error.
FAILURE = re.compile(
    r"""(?x)
      ^(?:FAIL|ERROR|FAILED|ERRORS?)\b      # unittest, pytest summary
    | ^E\s{2,}                              # pytest assertion detail
    | ^---\ FAIL                            # go test
    | Traceback\ \(most\ recent\ call\ last\)
    | \b\w*(?:Error|Exception):             # AssertionError: ...
    | \berror(?:\[\w+\])?:                  # gcc, rustc, tsc
    | \bpanicked\ at\b                      # rust
    | ^\s*[✕✗×●]\s                          # jest, vitest
    | \bFAIL(?:ED|URE|URES)?\b
    | \berror\ TS\d+:                       # tsc
    | ^npm\ ERR!                             # npm
    | ^fatal:                                # git
    | ^\s*\d+:\d+\s+error\s                 # eslint
    | \*\*\*\ [^\n]{0,200}?\bError\ \d+                  # make
    | Segmentation\ fault|\bFATAL\b|command\ not\ found|undefined\ reference\ to
    """
)


# A test or build summary line. Kept even when it is not in the last lines:
# Python holds back print output in a pipe, so prints can come after the
# report and push the summary out of the tail.
SUMMARY = re.compile(
    r"""(?x)
      ^Ran\ \d+\ tests?\ in\b                  # unittest
    | ^OK\b                                   # unittest
    | ^=+\ .*\b(?:passed|failed|errors?|skipped|no\ tests\ ran)\b.*=+$   # pytest
    | ^\s*(?:Tests|Test\ Suites|Test\ Files|Snapshots):\s   # jest, vitest
    | ^\s*\d+\ (?:passing|failing|pending)\b     # mocha
    | ^\d+\ examples?,\ \d+\ failures?           # rspec
    | ^test\ result:                           # cargo
    | ^ok\s+\S+                                # go
    | ^PASS$                                   # go
    | ^Found\ \d+\ errors?\b                    # tsc
    | \bBUILD\ (?:SUCCESS|SUCCESSFUL|FAILED|FAILURE)\b   # maven, gradle
    """
)


def is_off():
    return os.environ.get("TRIM_OUTPUT_CAP", "").strip().lower() == "off"


# ---------------------------------------------------------------- hook mode


def rewrite(tool_name, tool_input):
    """Return the new Bash input, or None when nothing changes."""
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if tool_input.get("run_in_background") or "output_cap.py" in cmd:
        return None
    if OPERATORS.search(cmd) or not RUNNERS.search(cmd):
        return None
    piped = "set -o pipefail; %s 2>&1 | python3 %s cap" % (cmd.strip(), shlex.quote(SELF))
    return {**tool_input, "command": piped}


def hook_main():
    if is_off():
        return 0
    try:
        event = json.load(sys.stdin)
        new_input = rewrite(event.get("tool_name"), event.get("tool_input"))
    except Exception:
        return 0
    if new_input is not None:
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


# ----------------------------------------------------------------- capping



def is_failure(line):
    return bool(FAILURE.search(line[:LINE_CHARS]))  # bounded probe: no slow match on huge lines


def is_summary(line):
    return bool(SUMMARY.search(line[:LINE_CHARS]))


def _merge(ranges):
    merged = []
    for s, e in sorted(ranges):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _ranges_to_keep(lines):
    """Return sorted, merged (start, end) index ranges to keep (end exclusive)."""
    n = len(lines)
    hits = [i for i, line in enumerate(lines) if is_failure(line)]
    ranges = [(max(0, i - BEFORE), min(n, i + AFTER + 1)) for i in hits]
    if sum(e - s for s, e in _merge(ranges)) > FAIL_BUDGET:
        ranges = [(i, i + 1) for i in hits]  # every failure line, no context
    ranges += [(i, i + 1) for i, line in enumerate(lines) if is_summary(line)]
    ranges.append((max(0, n - TAIL_LINES), n))
    return _merge(ranges)


def _cut(line):
    if len(line) <= LINE_CHARS:
        return line
    return "%s ...[%s more characters]" % (line[:LINE_CHARS], format(len(line) - LINE_CHARS, ","))


def needs_cap(text):
    return len(text.splitlines()) > MAX_LINES or len(text) > MAX_CHARS


def _keeps(line):
    return is_failure(line) or is_summary(line)


def cap(text, path):
    """Return the capped version of text, naming the full copy at path."""
    lines = text.splitlines()
    kept = _ranges_to_keep(lines)
    failures = sum(1 for line in lines if is_failure(line))
    summaries = sum(1 for line in lines if is_summary(line))
    body = []
    pos = 0
    for s, e in kept:
        if s > pos:
            body.append("[trim] ... %s lines left out (lines %d-%d) ..." % (format(s - pos, ","), pos + 1, s))
        body.extend(_cut(line) for line in collapse(lines[s:e], _keeps))
        pos = e
    shown = sum(1 for line in body if not line.startswith("[trim] ... "))
    out = [
        "[trim] Output capped: %s lines, %s shown (%s failure or error lines and %s summary lines kept, then the last %d lines)."
        % (format(len(lines), ","), format(shown, ","), format(failures, ","), format(summaries, ","), TAIL_LINES),
        "[trim] Full output: %s" % path,
    ]
    return "\n".join(out + body) + "\n"


# ----------------------------------------------------------------- cap mode


def cap_main():
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if is_off() or not needs_cap(text):
        sys.stdout.write(text)
        return 0
    try:
        path = save_full_copy(text)
        capped = cap(text, path)
        sys.stdout.write(capped)
    except Exception:
        sys.stdout.write(text)  # fail open: full output
        return 0
    log_saving("output_cap", len(text), len(capped), full_copy=path)
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "cap":
        return cap_main()
    return hook_main()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
