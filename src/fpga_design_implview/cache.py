"""Exports are cached: devices by part, designs by checkpoint and options.

Vivado writes them gzip'ed, or as plain text when its Tcl has no zlib; the
cache keeps them gzip'ed and the readers take both.
"""

import gzip
import hashlib
import os
import shutil


def root():
    base = os.environ.get("IMPLVIEW_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "implview")
    return base


def device_path(part):
    d = os.path.join(root(), "devices")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{part}.ivdev.gz")


def design_path(dcp, *opts):
    st = os.stat(dcp)
    key = f"{os.path.abspath(dcp)}|{st.st_mtime_ns}|{st.st_size}|{'|'.join(map(str, opts))}"
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = os.path.join(root(), "designs")
    os.makedirs(d, exist_ok=True)
    name = os.path.splitext(os.path.basename(dcp))[0]
    return os.path.join(d, f"{name}-{h}.ivd.gz")


def is_gzip(path):
    with open(path, "rb") as f:
        return f.read(2) == b"\x1f\x8b"


def open_export(path):
    """An export file as text, gzip'ed or plain."""
    return gzip.open(path, "rt") if is_gzip(path) else open(path)


def compress(path):
    """Gzip a plain export in place."""
    if is_gzip(path):
        return
    tmp = path + ".gz"
    try:
        with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
