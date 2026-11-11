# Project map

Goal: Lower cost without lower quality. trim cuts what Claude reads (input tokens) in Claude Code, and keeps the full text in a file that Claude can open.
Scope: In: smaller tool results (screenshots, command output, connector output, repeat reads, pastes, web pages, subagent reports), a check of the fixed context that shows what the project has not used and where to turn it off, and Haiku notes for large results that are not code. Out: model and effort choice and the prompt cache (rightsize's job), the plan and handoffs (whatsnext's job), and edits to tool results that are already in the conversation.

trim acts; rightsize measures and reports. trim writes its savings to a shared file that `/rightsize report` can read.

One line per record. Follow the link; do not add content here.

- Vocabulary: `CONTEXT.md`
- Features (update at each release): `docs/FEATURES.md`
- Plan (build order): `docs/project/PLAN.md`
- Decisions: `docs/decisions/README.md`
- Ground rules: `AGENTS.md`
- Tests: `python3 -m unittest discover -s tests`.

## Design rules

- Keep the full text. Each time trim shortens a result, it saves the full result to a file and tells Claude the path.
- Never shorten or summarize source files. An edit needs the exact text.
- Never edit a tool result that is already in the conversation, and never change tools or other fixed context in the middle of a chat. Both throw away the prompt cache.
- Fixed context: diagnose only. trim shows tokens per part and what the project has not used in 90 days, and says where in Claude Code to turn each item off. trim never changes settings or files; Claude Code's own commands (`/mcp`, `/plugin`, `/skills`) do that.
- Measure before you build. Count real tokens first; Claude Code already defers many tool definitions.
- trim is the author's own design. Other people's mods can prompt an idea; their code, names and UI stay out.
- Research and decision records stay private: research in a separate local repo, decisions in `docs/decisions/` (gitignored).
