"""What a checkpoint is, without Vivado: dcp.xml inside the zip names the part."""

import glob
import os
import re
import xml.etree.ElementTree as ET
import zipfile

# Checkpoints of an implementation run, most complete first.
STAGES = ("_routed.dcp", "_postroute_physopt.dcp", "_physopt.dcp", "_placed.dcp", "_opt.dcp")


def dcp_info(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("dcp.xml"))
    info = {}
    for tag, key in (("Part", "part"), ("Top", "top"), ("PRODUCT", "product")):
        e = root.find(tag)
        if e is not None:
            info[key] = e.get("Name", "")
    m = re.search(r"v(\d{4}\.\d+)", info.get("product", ""))
    info["vivado"] = m.group(1) if m else ""
    return info


def find_dcp(path):
    """The checkpoint to show for a path: a .dcp, a run directory (impl_1)
    or a project directory (its impl_1 run)."""
    if os.path.isfile(path):
        return path
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    runs = [path]
    runs += sorted(glob.glob(os.path.join(path, "*.runs", "impl_*")))
    runs += sorted(glob.glob(os.path.join(path, "impl_*")))
    for run in runs:
        for stage in STAGES:
            hits = sorted(glob.glob(os.path.join(run, "*" + stage)), key=os.path.getmtime)
            if hits:
                return hits[-1]
    raise FileNotFoundError(f"no implemented checkpoint (*_routed.dcp, ...) under {path}")
