#!/usr/bin/env python3
"""trim P-6: read big image files at a smaller size.

PreToolUse command hook, matcher `Read`. When Claude reads a .png or .jpg file
whose long edge is over SHRINK_ABOVE pixels and that would save at least
MIN_SAVED_TOKENS tokens, the hook makes a copy with a long
edge of TARGET pixels in trim's private out folder and points the Read at the
copy (`updatedInput.file_path`). The original file is never changed.

Pasted or attached images cannot be changed by a plugin hook (research:
2026-11-10-attached-images.md), so only image files that Claude opens with Read
are handled. Needs `sips` (macOS) or ImageMagick; with neither the hook does
nothing. Image cost grows with pixel count (about width x height / 750
tokens), so the saving is an estimate and is logged as assumed (ADR 0017).

Safety (ADR 0020): the hook does nothing when any settings file denies Read
or asks for Read (a rewritten path could dodge the rule), and it decodes only files that start
with a PNG or JPEG signature, with the decoder named, so file content cannot
pick an ImageMagick coder.

Off switch: TRIM_IMAGE_SHRINK=off. Fails open: on any error it prints nothing
and the Read goes through as it is.
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullcopy import new_copy_path, trim_home  # noqa: E402
from savings import log_saving  # noqa: E402

SHRINK_ABOVE = 1250  # long edge in pixels; at or under this the image is left alone
TARGET = 1000  # long edge of the copy
TOKEN_PIXELS = 750  # about this many pixels per token
MIN_SAVED_TOKENS = 500  # smaller estimated savings are not worth the lost detail
TIMEOUT = 4
EXTENSIONS = (".png", ".jpg", ".jpeg")


def is_off():
    return os.environ.get("TRIM_IMAGE_SHRINK", "").strip().lower() == "off"


def _settings_files(cwd):
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    files = [os.path.join(config, "settings.json")]
    for base in ("/Library/Application Support/ClaudeCode", "/etc/claude-code"):
        files.append(os.path.join(base, "managed-settings.json"))
        drop_in = os.path.join(base, "managed-settings.d")
        if os.path.isdir(drop_in):
            files += [os.path.join(drop_in, n) for n in sorted(os.listdir(drop_in)) if n.endswith(".json")]
    if isinstance(cwd, str) and cwd:
        folder = os.path.realpath(cwd)
        while True:  # the project folder and every folder above it
            files += [os.path.join(folder, ".claude", "settings.json"),
                      os.path.join(folder, ".claude", "settings.local.json")]
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    return files


def read_is_denied(cwd):
    """True when any settings file has a Read deny or ask rule, or cannot be read."""
    for path in _settings_files(cwd):
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                perms = json.load(f).get("permissions") or {}
            rules = (perms.get("deny") or []) + (perms.get("ask") or [])
            if any(isinstance(r, str) and (r == "Read" or r.startswith("Read(")) for r in rules):
                return True
        except Exception:
            return True  # fail closed
    return False


def image_kind(path):
    """Return 'png' or 'jpeg' from the file's first bytes, else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return None


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)


def image_size(path, kind):
    """Return (width, height) or None."""
    if shutil.which("sips"):
        out = _run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path]).stdout
        nums = re.findall(r"pixel(?:Width|Height): (\d+)", out)
        if len(nums) == 2:
            return int(nums[0]), int(nums[1])
        return None
    tool = shutil.which("magick") or shutil.which("identify")
    if tool:
        cmd = [tool, "identify"] if os.path.basename(tool) == "magick" else [tool]
        out = _run(cmd + ["-format", "%w %h", "--", "%s:%s[0]" % (kind, path)]).stdout.split()
        if len(out) == 2 and all(x.isdigit() for x in out):
            return int(out[0]), int(out[1])
    return None


def resize(src, dest, kind):
    if shutil.which("sips"):
        _run(["sips", "-Z", str(TARGET), "--out", dest, src])
    else:
        tool = shutil.which("magick") or shutil.which("convert")
        if not tool:
            return
        _run([tool, "%s:%s" % (kind, src), "-resize", "%dx%d>" % (TARGET, TARGET), dest])


def shrink(path, session=None):
    """Return the path of a smaller copy, or None when the image is left alone."""
    if not isinstance(path, str) or not path.lower().endswith(EXTENSIONS):
        return None
    if not os.path.isabs(path) or not os.path.isfile(path):
        return None  # sips has no "--", so a path must never start with "-"
    out_dir = os.path.realpath(os.path.join(trim_home(), "out"))
    if os.path.realpath(path).startswith(out_dir + os.sep):
        return None  # already a trim copy
    kind = image_kind(path)
    if not kind:
        return None
    size = image_size(path, kind)
    if not size or max(size) <= SHRINK_ABOVE:
        return None
    scale = TARGET / float(max(size))
    new_pixels = round(size[0] * scale) * round(size[1] * scale)
    saved = (size[0] * size[1] - new_pixels) / TOKEN_PIXELS
    if saved < MIN_SAVED_TOKENS:
        return None  # for example a wide, short banner
    dest = new_copy_path(os.path.splitext(path)[1].lower())
    try:
        resize(path, dest, kind)
        if not os.path.isfile(dest) or os.path.getsize(dest) == 0:
            raise OSError("no copy made")
        os.chmod(dest, 0o600)
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        return None
    log_saving("image_shrink", tokens_saved=saved, assumed=True, session=session, full_copy=dest)
    return dest


def main():
    if is_off():
        return 0
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") != "Read":
            return 0
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return 0
        if read_is_denied(event.get("cwd")):
            return 0
        copy = shrink(tool_input.get("file_path"), event.get("session_id"))
        if not copy:
            return 0
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": dict(tool_input, file_path=copy),
        }}, sys.stdout)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
