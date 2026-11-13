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


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)


def image_size(path):
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
        out = _run(cmd + ["-format", "%w %h", path + "[0]"]).stdout.split()
        if len(out) == 2 and all(x.isdigit() for x in out):
            return int(out[0]), int(out[1])
    return None


def resize(src, dest):
    if shutil.which("sips"):
        _run(["sips", "-Z", str(TARGET), src, "--out", dest])
    else:
        tool = shutil.which("magick") or shutil.which("convert")
        if not tool:
            return
        _run([tool, src, "-resize", "%dx%d>" % (TARGET, TARGET), dest])


def shrink(path, session=None):
    """Return the path of a smaller copy, or None when the image is left alone."""
    if not isinstance(path, str) or not path.lower().endswith(EXTENSIONS):
        return None
    if not os.path.isfile(path):
        return None
    out_dir = os.path.realpath(os.path.join(trim_home(), "out"))
    if os.path.realpath(path).startswith(out_dir + os.sep):
        return None  # already a trim copy
    size = image_size(path)
    if not size or max(size) <= SHRINK_ABOVE:
        return None
    scale = TARGET / float(max(size))
    new_pixels = round(size[0] * scale) * round(size[1] * scale)
    saved = (size[0] * size[1] - new_pixels) / TOKEN_PIXELS
    if saved < MIN_SAVED_TOKENS:
        return None  # for example a wide, short banner
    dest = new_copy_path(os.path.splitext(path)[1].lower())
    try:
        resize(path, dest)
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
