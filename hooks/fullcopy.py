"""trim: save the full copy of a tool result that trim made smaller.

Shared by output_cap.py (P-3) and tool_cleanup.py (P-4). Full copies go to
~/.claude/trim/out/ (folder can be moved with TRIM_HOME). The folder is mode
700, each file is mode 600, created new with a random name and no symlink
follow. Copies older than KEEP_DAYS are deleted. See ADR 0006.
"""
import datetime
import os
import secrets
import time

KEEP_DAYS = 7


def trim_home():
    return os.environ.get("TRIM_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "trim"
    )


def _clean_old(folder):
    limit = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            if name.endswith((".log", ".txt")) and os.path.getmtime(p) < limit:
                os.remove(p)
    except OSError:
        pass


def save_full_copy(text, suffix=".log"):
    """Write text to a new private file in the out folder; return its path."""
    folder = os.path.join(trim_home(), "out")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    _clean_old(folder)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(folder, "%s-%s%s" % (stamp, secrets.token_hex(4), suffix))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path
