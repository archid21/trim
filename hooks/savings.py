"""trim P-9: the savings log, and the report built from it.

Each trim hook that makes a result smaller (or a call cheaper) appends one JSON
line to ~/.claude/trim/savings.jsonl (folder can be moved with TRIM_HOME).
`/trim:report` reads the file, and so does `/rightsize report` (ADR 0012).

Line format (v 1): {"v":1,"ts":"2026-11-10T23:55:00Z","session":"...|null",
"feature":"output_cap|tool_cleanup|repeat_reads|browser_scale","before":N|null,
"after":N|null,"tokens_saved":N,"assumed":false,"full_copy":"path|null"}
before and after are characters. tokens_saved is an estimate: (before - after)
/ 2.7. `assumed: true` means a fixed figure, not measured on that call.

The log cleans itself: when the file passes PRUNE_BYTES, the next write drops
lines older than KEEP_DAYS, and then the oldest lines until the file is under
MAX_BYTES. So the file stays small with no cron job or manual step.

Before a line is dropped, it is added to savings-totals.json: per UTC day and
feature, `saves`, `tokens_saved` and `assumed`. `through` is the newest line
folded in, so folding the same line twice cannot happen. The report adds the
totals file and the log, so "all time" survives the pruning. The totals file
keeps no session ids or paths, and grows by about 4 small entries per day.

Off switch: TRIM_SAVINGS=off. log_saving never raises: a failed write must not
break the hook that called it.

  python3 savings.py [--days N]     prints the report as JSON
"""
import datetime
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullcopy import trim_home  # noqa: E402

CHARS_PER_TOKEN = 2.7  # measured in the P-10 probe
KEEP_DAYS = 90
PRUNE_BYTES = 100_000  # check for old lines once the file is this big
MAX_BYTES = 1_000_000  # hard cap, newest lines kept
SESSIONS_SHOWN = 5
FEATURES = ("output_cap", "tool_cleanup", "repeat_reads", "browser_scale")


def is_off():
    return os.environ.get("TRIM_SAVINGS", "").strip().lower() == "off"


def log_path():
    return os.path.join(trim_home(), "savings.jsonl")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse(line):
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _ts(entry):
    try:
        return datetime.datetime.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except (KeyError, TypeError, ValueError):
        return None


def totals_path():
    return os.path.join(trim_home(), "savings-totals.json")


def read_totals(path=None):
    """Return the totals file as {"through": ts|None, "days": {day: {feature: row}}}."""
    try:
        with open(path or totals_path(), encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        t = None
    if not isinstance(t, dict) or t.get("v") != 1 or not isinstance(t.get("days"), dict):
        return {"through": None, "days": {}}
    return {"through": t.get("through"), "days": t["days"]}


def _write_private(path, text):
    """Write a file new (mode 600) and rename it in."""
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _fold(dropped):
    """Add dropped log entries to the totals file (only those newer than `through`)."""
    totals = read_totals()
    old_through = totals["through"] or ""
    through = old_through
    days = totals["days"]
    for e in dropped:
        if e["ts"] <= old_through:
            continue
        row = days.setdefault(e["ts"][:10], {}).setdefault(
            e.get("feature", "other"), {"saves": 0, "tokens_saved": 0, "assumed": False})
        row["saves"] += 1
        row["tokens_saved"] += e["tokens_saved"]
        row["assumed"] = row["assumed"] or bool(e.get("assumed"))
        through = max(through, e["ts"])
    _write_private(totals_path(), json.dumps(
        {"v": 1, "through": through or None, "days": days}, separators=(",", ":")))


def prune(path, now=None):
    """Fold, then drop, lines older than KEEP_DAYS, then the oldest lines past MAX_BYTES."""
    now = now or _now()
    limit = now - datetime.timedelta(days=KEEP_DAYS)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    kept = []
    dropped = []
    for line in lines:
        entry = _parse(line)
        ts = _ts(entry) if entry else None
        if ts is None:
            continue  # not a valid line: dropped, nothing to count
        if ts >= limit:
            kept.append((line, entry))
        elif entry.get("v") == 1 and isinstance(entry.get("tokens_saved"), int):
            dropped.append(entry)
    while kept and sum(len(x[0]) + 1 for x in kept) > MAX_BYTES:
        e = kept.pop(0)[1]
        if e.get("v") == 1 and isinstance(e.get("tokens_saved"), int):
            dropped.append(e)
    while dropped and kept and kept[0][1]["ts"] == max(e["ts"] for e in dropped):
        # Same second as the newest dropped line: `through` would hide it from the
        # report, so fold it too.
        e = kept.pop(0)[1]
        if e.get("v") == 1 and isinstance(e.get("tokens_saved"), int):
            dropped.append(e)
    if dropped:
        _fold(dropped)  # totals first: a failure here leaves the log untouched
    kept = [x[0] for x in kept]
    _write_private(path, "".join(x + "\n" for x in kept))


def log_saving(feature, before=None, after=None, tokens_saved=None, session=None,
               full_copy=None, assumed=False):
    """Append one line to the savings log. Never raises."""
    if is_off():
        return
    try:
        if tokens_saved is None:
            if before is None or after is None:
                return
            tokens_saved = round((before - after) / CHARS_PER_TOKEN)
        if tokens_saved <= 0:
            return
        entry = {
            "v": 1,
            "ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session": session if isinstance(session, str) else None,
            "feature": feature,
            "before": before,
            "after": after,
            "tokens_saved": int(tokens_saved),
            "assumed": bool(assumed),
            "full_copy": full_copy,
        }
        path = log_path()
        folder = os.path.dirname(path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        if os.path.getsize(path) > PRUNE_BYTES:
            prune(path)
    except Exception:
        return


def read_log(path=None):
    """Return the valid entries of the log, oldest first."""
    try:
        with open(path or log_path(), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        e = _parse(line)
        if e and e.get("v") == 1 and isinstance(e.get("tokens_saved"), int) and _ts(e):
            out.append(e)
    return out


def report(entries=None, now=None, totals=None):
    """Sum per feature (all time, last 30 and 7 days) and per session.

    All time = the totals file (lines already pruned) + the log lines newer than it.
    """
    now = now or _now()
    entries = read_log() if entries is None else entries
    totals = read_totals() if totals is None else totals
    through = totals["through"] or ""
    features = {}

    def row(name):
        return features.setdefault(name, {"saves": 0, "tokens_saved": 0, "assumed": False,
                                          "last_7_days": 0, "last_30_days": 0})

    first = None
    for day, by_feature in totals["days"].items():
        try:
            d = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        first = d if first is None else min(first, d)
        age = (now - d).total_seconds() / 86400 - 1  # a day's totals end 24 h after its start
        for name, t in by_feature.items():
            r = row(name)
            r["saves"] += t.get("saves", 0)
            r["tokens_saved"] += t.get("tokens_saved", 0)
            r["assumed"] = r["assumed"] or bool(t.get("assumed"))
            if age <= 7:
                r["last_7_days"] += t.get("tokens_saved", 0)
            if age <= 30:
                r["last_30_days"] += t.get("tokens_saved", 0)
    sessions = {}
    for e in entries:
        if e["ts"] <= through:
            continue  # already in the totals file
        t = _ts(e)
        first = t if first is None else min(first, t)
        r = row(e.get("feature", "other"))
        r["saves"] += 1
        r["tokens_saved"] += e["tokens_saved"]
        r["assumed"] = r["assumed"] or bool(e.get("assumed"))
        age = (now - t).total_seconds() / 86400
        if age <= 7:
            r["last_7_days"] += e["tokens_saved"]
        if age <= 30:
            r["last_30_days"] += e["tokens_saved"]
        sid = e.get("session")
        if isinstance(sid, str):
            s = sessions.setdefault(sid, {"session": sid, "saves": 0, "tokens_saved": 0,
                                          "assumed": False, "last": e["ts"]})
            s["saves"] += 1
            s["tokens_saved"] += e["tokens_saved"]
            s["assumed"] = s["assumed"] or bool(e.get("assumed"))
            s["last"] = max(s["last"], e["ts"])
    total = {k: sum(r[k] for r in features.values())
             for k in ("saves", "tokens_saved", "last_7_days", "last_30_days")}
    recent = sorted(sessions.values(), key=lambda s: s["last"], reverse=True)[:SESSIONS_SHOWN]
    return {
        "log": log_path(),
        "since": first.strftime("%Y-%m-%dT%H:%M:%SZ") if first else None,
        "features": features,
        "total": total,
        "sessions": recent,
        "sessions_note": "Last %d sessions with a logged saving, from the log (about the last %d days). Command output caps carry no session id, so they are in the feature rows but not here." % (SESSIONS_SHOWN, KEEP_DAYS),
        "note": None if features else "No savings logged yet.",
        "estimate": "Tokens are estimates (characters / 2.7). Rows with assumed=true use a fixed figure per call, not a measurement.",
    }


def main(argv):
    json.dump(report(), sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
