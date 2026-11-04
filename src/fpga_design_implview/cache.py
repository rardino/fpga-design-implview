"""Exports are cached: devices by part, designs by checkpoint and options."""

import hashlib
import os


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
