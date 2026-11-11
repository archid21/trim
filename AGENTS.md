Project map (start here): `docs/project/README.md`.
Record each decision as one ADR in `docs/decisions` (gitignored; private): copy the format of the latest `NNNN-*.md`, take the next number, and add a row to `docs/decisions/README.md`.
Research stays private, in its own local git repo outside this one. Do not copy research text into this repo.
Handoff note: `docs/project/HANDOFF.md` (gitignored); overwrite it at wrap-up.

## Ground rules

- Show every time in Pacific time, next to any other time zone.
- Commit locally. Never push; we push to GitHub together.
- Explain in simple words. Say what a technical term means the first time.
- Before you build, show the plan and wait for the user's OK, unless the user says otherwise.
- Scope: if a feature changes what Claude reads or loads, it is trim. Model, effort and cache are rightsize. Plan and handoff are whatsnext.
- Design rules for trim: `docs/project/README.md`.
- Browsing: read pages with `get_page_text`, `find` or `read_page` (interactive filter) first. Take a screenshot only for a visual check, at `scale: 0.5`, or `zoom` on one region.

<!-- whatsnext:start -->
## whatsnext

`whatsnext` below is `python3 <plugin>/skills/whatsnext/scripts/whatsnext.py`.

- At the start of a session read the resume note (`whatsnext progress resume-note`), or the active task's progress file in `docs/project/progress/`.
- Keep the progress file current: `whatsnext progress next "<text>"` sets the next action; `whatsnext progress stamp` records the state.
- The plan is `docs/project/PLAN.md`. When an item is done, prove it: `whatsnext plan done <id> --verify "<cmd>"`.
<!-- whatsnext:end -->
