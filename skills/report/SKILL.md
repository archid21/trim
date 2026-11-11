---
name: report
description: Show how many input tokens trim saved, per feature (all time, last 30 days, last 7 days) and per recent session. Reads trim's own savings log; changes nothing.
---

# trim report

Report only. Show what trim saved. Never change settings or files.

`sv.py` below means `python3 <this skill's base directory>/../../hooks/savings.py`.

1. Run `sv.py`. It prints JSON: `log`, `since`, `features` (each with `saves`, `tokens_saved` (all time), `last_7_days`, `last_30_days`, `assumed`), `total`, `sessions` (the last 5: `session`, `saves`, `tokens_saved`, `last`), `sessions_note`, `note` and `estimate`.
2. If `note` is set, say it and stop.
3. Show a table, largest `tokens_saved` first: feature, saves, tokens saved (all time), last 30 days, last 7 days. Mark a feature with `assumed: true` as "assumed" (browser_scale uses a fixed figure per screenshot). Add a total row. Then a second small table of `sessions`: session (first 8 characters), saves, tokens saved, last activity (Pacific time next to UTC); say `sessions_note` in one line.
4. Say once: `estimate`. Say `since` as the date the log starts (Pacific time next to UTC). Say the log cleans itself: lines older than 90 days are folded into daily totals and dropped, so all-time totals stay, and the log is capped at 1 MB. Say how to turn logging off: `TRIM_SAVINGS=off`.
5. Say these are input tokens trim kept out of the conversation, so the money saved is a share of the subscription or real API cost, depending on the user's plan.

Rules:
- Do not edit or delete the log or the totals file (`savings-totals.json`).
- The full copies named in the log are in `~/.claude/trim/out/` and are deleted after 7 days.
