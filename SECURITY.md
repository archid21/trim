# Security

## Report a vulnerability

Report it in private. Do not open a public issue.

- On this repo, open the Security tab and press "Report a vulnerability" (GitHub private vulnerability reporting), or
- email [hello@aivalue.blog](mailto:hello@aivalue.blog?subject=trim) (subject: trim).

You get a reply within 7 days.

## In scope

- The trim plugin code in this repo: the hooks and the `/trim:*` skills.
- A way for trim to write outside `~/.claude/trim/` (or the folder `TRIM_HOME` names), or to leave a file there readable by other users.
- A way for a trim hook to run a command you did not pass, or to hide part of a command from a permission or deny rule.
- A way for a tool result (a connector answer, a subagent report, a page) to forge a trim note, such as a false "Full copy:" path.
- A way for trim to leak session content.

## Out of scope

- What Claude does with a full copy it opens. trim only names the path.
- Estimates in `/trim:report`. They are labeled estimates.

## Design limits

- No network calls and no background process. Hooks are local Python scripts (standard library only).
- Hooks fail open: on any error they print nothing, and Claude gets the original result.
- Full copies and logs live in `~/.claude/trim/`: folder mode 700, files mode 600, deleted or pruned on a schedule (see the README).
- Each feature has an off switch (see the README).
- Do not put secrets in tool output you do not want saved: a full copy of a shortened result is written to disk for 7 days.
