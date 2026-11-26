"""An open-flow (Yosys + nextpnr-xilinx) implementation as an implview design
file, to draw it with the same viewer and compare it with the Vivado build.

Cells and their BELs come from nextpnr's --write JSON (NEXTPNR_BEL), the
routing usage per tile from its FASM (the pip features of each tile).
"""

import gzip
import json
import re
from collections import Counter

KINDS = (  # nextpnr cell type pattern -> (REF_NAME, PRIMITIVE_GROUP)
    (r"SLICE_LUTX", ("LUT", "LUT")),
    (r"SLICE_FFX", ("FDRE", "FLOP_LATCH")),
    (r"CARRY4", ("CARRY4", "CARRY")),
    (r"F7MUX|F8MUX|MUXF7|MUXF8", ("MUXF7", "MUXFX")),
    (r"RAMB", ("RAMB", "BLOCKRAM")),
    (r"DSP", ("DSP48E1", "ARITHMETIC")),
    (r"BUFG|BUFH|BUFR|MMCM|PLL", ("BUFGCTRL", "CLOCK")),
    (r"IOB|PAD|IBUF|OBUF|ILOGIC|OLOGIC|IDELAY", ("IOB", "IO")),
)


def _kind(t):
    for pat, v in KINDS:
        if re.search(pat, t):
            return v
    return (t, "OTHERS")


def fasm_pips_per_tile(fasm_text):
    """Routing features (TILE.DST.SRC in an interconnect tile) per tile."""
    count = Counter()
    for line in fasm_text.splitlines():
        name = line.split("#", 1)[0].split("=", 1)[0].strip()
        parts = name.split(".")
        if len(parts) == 3 and re.match(r"INT_[LR]_X\d+Y\d+$", parts[0]):
            count[parts[0]] += 1
    return count


def write_design(placed_json, fasm_text, part, out, top="", tool="nextpnr-xilinx"):
    """Write an .ivd.gz from nextpnr results; returns the utilization Counter."""
    with open(placed_json) as f:
        d = json.load(f)
    mods = d.get("modules", {})
    name, mod = next(((k, v) for k, v in mods.items() if v.get("attributes", {}).get("top")),
                     next(iter(mods.items())))
    lines = ["#implview-design\t1", f"part\t{part}", f"top\t{top or name}", f"nextpnr\t{tool}"]
    util = Counter()
    n = 0
    for cname, c in mod.get("cells", {}).items():
        bel = c.get("attributes", {}).get("NEXTPNR_BEL", "")
        if "/" not in bel:
            continue
        site, belname = bel.split("/", 1)
        ref, grp = _kind(c.get("type", ""))
        util[grp] += 1
        lines.append(f"U\t{site}\t{belname.split('/')[-1]}\t{ref}\t{grp}\t{cname}")
        n += 1
    lines.append(f"primitives\t{n}")
    for t, k in sorted(fasm_pips_per_tile(fasm_text).items()):
        lines.append(f"P\t{t}\t{k}")
        util["pips"] += k
    with gzip.open(out, "wt") as f:
        f.write("\n".join(lines) + "\n")
    return util


def vivado_util(des):
    """The same counts from a Vivado export (pips from the -detail pip lists)."""
    u = Counter(c.group for c in des.cells)
    return u


def slices_used(cells):
    return len({c.site for c in cells if c.site.startswith("SLICE")})
