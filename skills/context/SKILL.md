---
name: context
description: Show what fills this project's fixed context (tokens per part), list the agent types, connectors and plugins not used here in 90 days, and say where in Claude Code to turn each one off. Changes nothing.
---

# trim context

Diagnose only. Show the user what Claude Code loads into every session of this project, what this project has not used in 90 days, and where to turn each item off. Never change settings or files.

`fc.py` below means `python3 <this skill's base directory>/../../hooks/fixed_context.py`.

1. Run `fc.py`. It prints JSON: `window_days`, `sessions`, `overview` (`parts` with `part`, `tokens`, `detail`; `total`; `note`), `items` (each with `id`, `kind`, `name`, `tokens`, `note`, `how_to_turn_off`), `plugins_with_hooks`, `skills` and `note`.
2. Show the overview as a short table: part, tokens per request, detail. Say once: tokens are estimates (characters / 2.7), paid on every request, mostly as cheap cache reads. Add the overview `note`.
3. If `note` is set (too few sessions), say it and stop.
4. Show the unused items as a table, largest first: name, kind, tokens per request, where to turn it off (`how_to_turn_off`). Then:
   - `plugins_with_hooks`: list them apart, with the `note` (turning one off also stops its hooks).
   - `skills`: say its `note` in one line.
5. Close with: the total tokens per request of the listed items, and that trim changed nothing. Changes made in Claude Code settings apply from the next session.

Rules:
- Do not run the commands or edit the settings in `how_to_turn_off` yourself. If the user asks you to, treat it as a separate request, and say which file or setting changes and that it applies from the next session.
- An item used in another project can still be listed here; say that the list is for this project only.
