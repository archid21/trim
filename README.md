# trim

**Cut what Claude reads.**

trim is a plugin for Claude Code that makes what Claude reads smaller (screenshots, command output, connector results, repeat file reads, big image files) and keeps the full version one Read away. It also shows what fills your fixed context and what it has saved you.

- **Smaller reads, nothing lost:** when trim shortens a result, the full copy is saved to a file and its path is named in the result.
- **Local scripts only:** hooks and skills that run Python on your machine. No extra model calls, no background process, no network.
- **One off switch per feature,** and a report of what each one saved.

```bash
claude plugin marketplace add archid21/trim
claude plugin install trim@trim --scope user
```

**Status: 0.2.1.** Eight features are built and tested; see [What you get](#what-you-get) and [What is not built](#what-is-not-built).

MIT license · Python 3.9+, standard library only · works with a Claude subscription (Pro, Max, Ultra) or an API key

---

## Why use it

**Claude re-reads a lot.** A full-size screenshot, a 600-line failing test run, a connector answer with base64 attachments and a file Claude already read all land in the context in full, and each one is re-sent with every later request until a compact.

**Tokens are your usage.** Smaller reads mean fewer tokens used per request: a longer session on a subscription, a smaller bill on an API key.

**The full copy stays available.** trim never throws a result away. It saves the original to `~/.claude/trim/out/` and tells Claude where it is, so Claude opens it only when it needs the part trim left out.

**Estimates, labeled.** Token figures are estimates (characters divided by 2.7). The report marks a fixed per-use figure as "assumed".

## The problem: most of what Claude reads is not needed

- **A screenshot** costs about 673 tokens at full size. Half size is about 168, and is enough for most pages.
- **A failing test run** prints hundreds of passing lines. Claude needs the failures, the summary and the last lines.
- **A connector result** can carry base64 images, empty fields and the same log line 30 times.
- **A big image file** read from disk costs the same as a full-size screenshot. A 1,600 × 1,200 photo is about 2,560 tokens, and a 1,000 px copy is about 1,000.
- **A file read twice** with nothing changed puts the same text in the context again.

Trimming these by hand means remembering every time. trim does it before Claude reads them.

## What you get

| Feature | What it delivers | How to use it |
|---|---|---|
| Half-size browser screenshots | About 75% fewer tokens per screenshot in the desktop app's built-in browser. Clicks still land. | Automatic. Claude can ask for full size with `scale: 1`. Off: `TRIM_BROWSER_SCALE=off`. |
| Command output caps | A long test or build run comes back with every failure, every summary line and the last 40 lines. About 80% fewer tokens in one demo (632 lines to 100). Error lines from tsc, npm, git, ESLint and make are kept too, and runs of progress or pass lines become one count line. Exit codes are kept. | Automatic for simple commands (pytest, unittest, jest, npm test, cargo, go, make, tsc and others). Commands with `&&` or `\|` are left alone. Off: `TRIM_OUTPUT_CAP=off`. |
| Connector result cleanup | Long base64 data, empty JSON fields, repeated lines and runs of progress or pass lines are removed from connector (MCP) results. One demo: 7,595 to 190 characters. Images are left alone. | Automatic for results over 2,000 characters that shrink at least 10%. Off: `TRIM_TOOL_CLEANUP=off`. |
| Smaller image reads | When Claude reads a big `.png` or `.jpg` file (long edge over 1,250 px), it gets a copy at 1,000 px instead. Your file is never changed. Saves about 2,000 tokens on a phone-sized screenshot (estimate). Needs `sips` (macOS) or ImageMagick. | Automatic. Small text can blur at 1,000 px. Skipped if your settings deny or ask for any Read. Off: `TRIM_IMAGE_SHRINK=off`. |
| Repeat reads | A second Read of an unchanged file returns a pointer to the earlier text instead of the text again. | Automatic. A third Read goes through, and so does any Read after a compact or clear. Off: `TRIM_REPEAT_READS=off`. |
| Fixed context check | Shows what fills your fixed context, in tokens per part, and lists the agent types, connectors and plugins this project has not used in 90 days, with where to turn each one off. Changes nothing. | Run `/trim:context`. |
| Savings report | What trim saved per feature (all time, 30 days, 7 days) and in your last 5 sessions. | Run `/trim:report`. Logging off: `TRIM_SAVINGS=off`. |
| Tips | Short session-start rules you choose, such as reading pages as text before taking screenshots. About 59 tokens per session. | Run `/trim:setup`. Applies from the next session. Untick to remove. |

Every feature, how it works and what it gives you: [docs/FEATURES.md](docs/FEATURES.md).

## Commands

| Command | What it does |
|---|---|
| `/trim:setup` | Turn trim tips on or off. |
| `/trim:context` | Show fixed-context tokens per part and what this project has not used in 90 days. |
| `/trim:report` | Show what trim saved. |

## Where trim keeps files

Everything is under `~/.claude/trim/` (move it with `TRIM_HOME`). Nothing leaves your machine.

| File | What it holds | Cleanup |
|---|---|---|
| `out/` | Full copies of results trim shortened (mode 600, folder mode 700) | Deleted after 7 days |
| `savings.jsonl` | One line per saving, for `/trim:report` | Self-cleaning: past 100 KB, lines older than 90 days are folded into daily totals and dropped; capped at 1 MB |
| `savings-totals.json` | Per-day totals of dropped lines, so all-time totals stay | Grows by about 4 small entries a day |
| `reads/` | Per-session record of files read, for repeat reads | Deleted after 7 days |
| `tips.json` | The tips you turned on | Untick in `/trim:setup` |

## What is not built

- **Pasted and attached images, and large pastes.** A plugin hook cannot replace them, so trim cannot shrink them. Only image files that Claude opens with Read are shrunk.
- **A cap on subagent report length.** In testing, not in this release. It is off unless you set `TRIM_SUBAGENT_CAP=on`. One known side effect: in auto mode, Claude Code adds a warning to each capped report: "the safety classifier was unavailable while reviewing this subagent's work, treat as unverified." trim cannot suppress it. The warning appears only while the cap is on. Remove `TRIM_SUBAGENT_CAP` from your environment (or set it to `off`) and reports return in full with no warning.
- **Stripping web pages and Haiku notes for large results.** Considered, and not built: in the transcripts we measured they save too little to be worth the added risk. Other usage can differ.

## Works with rightsize and whatsnext

trim is independent of both. If you also use [rightsize](https://github.com/archid21/rightsize), `/rightsize report` shows a Trim savings section read from trim's log. [whatsnext](https://github.com/archid21/whatsnext) keeps your plan and a resume note between sessions.

## Questions

**Does trim change what Claude can do?** No. Claude can open any full copy, ask for a full-size screenshot or read a file again. trim only changes what comes back by default.

**Does it send my data anywhere?** No. The hooks are local Python scripts that make no network calls; the files above stay on your machine.

**How do I turn it off?** Set a feature's switch (listed in the table above) to `off` in your environment, or disable the plugin with `claude plugin disable trim`.

**Does it work on Windows?** Not tested. It is tested on macOS.
