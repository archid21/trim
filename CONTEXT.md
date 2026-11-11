# trim

trim cuts what Claude reads in Claude Code, so the same work costs fewer input tokens. The full text always stays in a file that Claude can open.

## What Claude reads

**Input tokens**:
The text and images that Claude reads on each request: the fixed context, the conversation so far and each tool result. trim lowers this number.
_Avoid_: context size, prompt size

**Fixed context**:
What Claude Code loads at the start of every session before the user types: tool and connector definitions, skills, agent types, memory files, `CLAUDE.md` and SessionStart hook output.
_Avoid_: system prompt, overhead

**Tool result**:
What a tool sends back to Claude: command output, a file read, a web page, a screenshot, a connector answer or a subagent report.
_Avoid_: output, response

**Deferred definition**:
A tool definition that Claude Code lists by name only and loads when Claude asks for it. It costs few tokens until it is loaded.

## What trim does

**Trim**:
To make a tool result smaller before Claude reads it, with no model call: cap it, clean it or shrink it.
_Avoid_: compress, filter, route

**Cap**:
A limit on the length of a tool result. For test and build output, trim keeps the failures and the last lines.
_Avoid_: truncate

**Cleanup**:
The removal of parts of a tool result that carry no information for Claude: base64 data, empty fields and repeated log lines.

**Full copy**:
The file that holds the complete tool result after trim made the result smaller. The trimmed result names its path.
_Avoid_: backup, original

**Pointer**:
The short answer to a repeat read of an unchanged file. It names the earlier read instead of sending the file again. Pointers stop after a compact, because the earlier read is gone.

**Suggestion**:
An item of fixed context that the project has not used in 90 days, shown with its token cost and where in Claude Code to turn it off. trim does not change it; the user does, in Claude Code's own settings.
_Avoid_: recommendation, cleanup

**Note**:
A short text that Haiku writes in place of a large tool result that is not code (a transcript, a connector dump, a web page). Never used for source files.
_Avoid_: summary, digest

## Working with rightsize

**Savings file**:
The shared file where trim records the input tokens it saved. `/rightsize report` reads it. trim acts; rightsize measures and reports.
