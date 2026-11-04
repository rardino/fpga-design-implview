"""Implemented design: placed primitive cells, pblocks, routing usage.

Read back from the gzip'ed TSV written by `implview::export_design`
(tcl/implview.tcl).
"""

import gzip
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Cell:
    name: str
    site: str
    bel: str
    ref: str                # REF_NAME (LUT6, FDRE, RAMB36E2, ...)
    group: str              # PRIMITIVE_GROUP (LUT, FLOP_LATCH, BLOCKRAM, ...)


@dataclass
class Design:
    part: str = ""
    top: str = ""
    info: dict = field(default_factory=dict)
    cells: list = field(default_factory=list)
    pblocks: list = field(default_factory=list)     # (name, grid ranges)
    pads: list = field(default_factory=list)        # (site, port, a cell on its net)
    pips: dict = field(default_factory=dict)        # tile name -> used pips
    source: str = ""                                # checkpoint the export came from

    def slack(self, key):
        v = self.info.get(key, "")
        try:
            return float(v)
        except ValueError:
            return None

    def group_counts(self):
        return Counter(c.group for c in self.cells)

    def unplaced(self):
        """Primitive cells without a site, None for exports that do not say."""
        try:
            return max(int(self.info["primitives"]) - len(self.cells), 0)
        except (KeyError, ValueError):
            return None


def load_design(path):
    d = Design()
    with gzip.open(path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        if header[0] != "#implview-design":
            raise ValueError(f"{path}: not an implview design file")
        for line in f:
            r = line.rstrip("\n").split("\t")
            k = r[0]
            if k == "U":
                d.cells.append(Cell(r[5], r[1], r[2], r[3], r[4]))
            elif k == "P":
                d.pips[r[1]] = int(r[2])
            elif k == "X":
                d.pblocks.append((r[1], r[2] if len(r) > 2 else ""))
            elif k == "O":
                d.pads.append((r[1], r[2], r[3]))
            elif k == "part":
                d.part = r[1]
            elif k == "top":
                d.top = r[1]
            else:
                d.info[k] = r[1] if len(r) > 1 else ""
    return d
