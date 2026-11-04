"""Running Vivado in batch mode to export devices and designs."""

import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

TCL_DIR = os.path.join(os.path.dirname(__file__), "tcl")
INSTALL_ROOTS = ("~/opt/xilinx", "~/Xilinx", "/opt/Xilinx", "/tools/Xilinx", "/opt/xilinx",
                 "/tools/xilinx", "/opt/amd", "/tools/amd")


class VivadoError(RuntimeError):
    pass


def installed():
    """(version, path to bin/vivado) of the installs found, oldest first."""
    found = {}
    for root in INSTALL_ROOTS:
        root = os.path.expanduser(root)
        for pat in ("*/Vivado/bin/vivado", "Vivado/*/bin/vivado"):
            for p in glob.glob(os.path.join(root, pat)):
                m = re.search(r"(\d{4}\.\d+)", p)
                if m:
                    found[m.group(1)] = p
    return sorted(found.items(), key=lambda kv: tuple(int(x) for x in kv[0].split(".")))


def command(cmd=None, version=""):
    """The command that runs Vivado, as a list.

    --vivado or IMPLVIEW_VIVADO win (a path, or a command with its arguments).
    Otherwise: vivado on the PATH, or the newest install found, preferring the
    version that wrote the checkpoint (older versions cannot open newer
    checkpoints).
    """
    cmd = cmd or os.environ.get("IMPLVIEW_VIVADO")
    if cmd:
        return shlex.split(cmd)
    inst = installed()
    for v, p in inst:
        if v == version:
            return [p]
    on_path = shutil.which("vivado")
    if on_path:
        return [on_path]
    if inst:
        return [inst[-1][1]]
    raise VivadoError("Vivado not found: put it on the PATH, or set IMPLVIEW_VIVADO / --vivado")


def run(args, cmd=None, version="", quiet=False):
    """Run batch.tcl with args; prints the implview lines and errors."""
    exe = command(cmd, version)
    script = os.path.join(TCL_DIR, "batch.tcl")
    with tempfile.TemporaryDirectory(prefix="implview-") as tmp:
        full = exe + ["-mode", "batch", "-nojournal", "-nolog", "-notrace",
                      "-source", script, "-tclargs"] + [str(a) for a in args]
        log = []
        p = subprocess.Popen(full, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace")
        for line in p.stdout:
            log.append(line)
            if not quiet and (line.startswith(("implview:", "ERROR:")) or "license" in line.lower()):
                sys.stderr.write(re.sub(r"\.tmp\d+", "", line))
        p.wait()
    if p.returncode != 0:
        tail = "".join(log[-30:])
        raise VivadoError(f"Vivado failed (exit {p.returncode}): {' '.join(exe)}\n{tail}")
