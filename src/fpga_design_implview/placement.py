"""What the design uses of the device: sites, groups, utilization."""

import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import hier

LUT_BEL_RE = re.compile(r"^([A-H])[56]LUT$")
FF_BEL_RE = re.compile(r"^[A-H]5?FF2?$")

# Utilization rows: label, site name prefix test, weight per used site.
RESOURCES = [
    ("SLICE", lambda p: p == "SLICE"),
    ("BRAM", lambda p: p in ("RAMB36", "RAMB18")),
    ("URAM", lambda p: p.startswith("URAM")),
    ("DSP", lambda p: p.startswith("DSP")),
    ("IOB", lambda p: p == "IOB" or p.startswith("IOB")),
    ("GT", lambda p: p.startswith("GT") and "CHANNEL" in p),
    ("BUFG", lambda p: p.startswith("BUFG")),
    ("MMCM/PLL", lambda p: p.startswith(("MMCM", "PLL"))),
]


@dataclass
class Placement:
    groups: list                    # hier.Group, color order
    cell_group: list                # group index of each design cell
    cell_site: np.ndarray           # site index of each cell, -1 if not in the device
    site_group: np.ndarray          # group shown on each site, -1 not colored
    site_used: np.ndarray           # site holds cells (for the utilization)
    site_cells: dict = field(default_factory=dict)  # site index -> [cell indices]
    utilization: list = field(default_factory=list)  # (label, used, total)
    missing: int = 0                # cells on sites the device file does not have
    hide_other: bool = False        # OTHER cells are not colored


def group_cells(design, max_groups=8, entries=None, depth=None):
    """(groups, cell group of each cell, highlight paths that matched nothing)."""
    if entries:
        from .highlight import manual_groups
        from .render import THEMES
        return manual_groups(design.cells, entries, THEMES["light"]["series"],
                             THEMES["dark"]["series"])
    if depth is not None:
        return (*hier.depth_groups(design.cells, depth, max_groups), [])
    return (*hier.auto_groups(design.cells, max_groups), [])


def build_placement(dev, design, groups, cell_group, hide_other=False):
    """A site shows the group of most of its cells; listed groups win over
    OTHER, and with hide_other a site with only OTHER cells is not colored."""
    sidx = dev.site_index()
    cell_site = np.array([sidx.get(c.site, -1) for c in design.cells], dtype=np.int64)
    site_cells = {}
    for i, s in enumerate(cell_site.tolist()):
        if s >= 0:
            site_cells.setdefault(s, []).append(i)
    other = next((k for k, g in enumerate(groups) if g.name == hier.OTHER), -1)
    site_group = np.full(len(dev.sites), -1, np.int32)
    site_used = np.zeros(len(dev.sites), bool)
    for s, cells in site_cells.items():
        n = Counter(cell_group[i] for i in cells)
        if len(n) > 1:
            n.pop(other, None)
        g = n.most_common(1)[0][0]
        site_used[s] = True
        site_group[s] = -1 if hide_other and g == other else g
    # pads of ports on sites without cells (N side of a differential pair),
    # colored like the cell the port connects to
    if design.pads:
        cell_index = {c.name: i for i, c in enumerate(design.cells)}
        for site, _port, cell in design.pads:
            s, i = sidx.get(site, -1), cell_index.get(cell)
            if s < 0 or s in site_cells or i is None:
                continue
            g = cell_group[i]
            site_used[s] = True
            site_group[s] = -1 if hide_other and g == other else g
    p = Placement(groups, cell_group, cell_site, site_group, site_used, site_cells,
                  missing=int((cell_site < 0).sum()), hide_other=hide_other)
    p.utilization = utilization(dev, design, p)
    return p


def utilization(dev, design, p):
    rows = []
    prefix = [s.prefix for s in dev.sites]
    used = p.site_used
    # LUT and FF from the BELs, so the count is the same on every family
    luts, ffs = set(), 0
    for c, s in zip(design.cells, p.cell_site.tolist()):
        if s < 0 or prefix[s] != "SLICE":
            continue
        m = LUT_BEL_RE.match(c.bel)
        if m:
            luts.add((s, m.group(1)))
        elif FF_BEL_RE.match(c.bel):
            ffs += 1
    nslice = sum(1 for x in prefix if x == "SLICE")
    lut_per = _bels_per_slice(dev, LUT_BEL_RE, letters=True)
    ff_per = _bels_per_slice(dev, FF_BEL_RE)
    if nslice:
        rows.append(("LUT", len(luts), nslice * lut_per))
        rows.append(("FF", ffs, nslice * ff_per))
    for label, test in RESOURCES:
        idx = [i for i, x in enumerate(prefix) if test(x)]
        if not idx:
            continue
        if label == "BRAM":
            n36 = sum(1 for i in idx if prefix[i] == "RAMB36")
            u36 = {i for i in idx if prefix[i] == "RAMB36" and used[i]}
            u18 = sum(1 for i in idx if prefix[i] == "RAMB18" and used[i])
            rows.append((label, len(u36) + u18 / 2, n36))
        else:
            rows.append((label, int(used[idx].sum()), len(idx)))
    return rows


def _bels_per_slice(dev, rx, letters=False):
    for st, bels in dev.bels.items():
        if st.startswith("SLICE"):
            names = [b for b, _ in bels if rx.match(b)]
            return len({n[0] for n in names}) if letters else len(names)
    return 0
