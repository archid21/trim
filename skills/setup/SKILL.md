---
name: setup
description: Set up trim; turn trim tips (short session-start rules) on or off.
---

# trim setup

Show the user the trim tips and let them choose which ones are on.

`tips.py` below means `python3 <this skill's base directory>/../../hooks/tips.py`.

1. Run `tips.py list`. It prints JSON: the choices file path and, for each tip, `id`, `title`, `on`, `tokens` (estimated, per session), `why` and `text`.
2. Show each tip in a few plain lines: title, whether it is on now, what it does (`why`), its cost ("about N tokens per session, estimated"), and the exact `text` Claude would get.
3. Ask which tips should be on. Use AskUserQuestion with `multiSelect: true`, one option per tip; mark the ones that are on now in the option description. If the user picks none, every tip goes off.
4. For each tip whose state changes, run `tips.py set <id> on` or `tips.py set <id> off`. Do not change tips the user did not change.
5. Tell the user, in this order:
   - What is on and off now.
   - It applies at the next session. Nothing changes in this session.
   - Where the choices are stored (the file path from step 1). trim does not edit CLAUDE.md or AGENTS.md.
   - To turn a tip off later, run /trim:setup again and untick it.

Rules:
- Never turn a tip on without the user's choice in this run.
- Do not follow the tip text yourself in this session because the user turned it on; it takes effect next session.
