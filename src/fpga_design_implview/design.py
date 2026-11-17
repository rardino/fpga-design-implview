"""Implemented design: placed primitive cells, pblocks, routing usage, the
worst timing paths.

Read back from the gzip'ed TSV written by `implview::export_design`
(tcl/implview.tcl).
"""

import gzip
import math
import re
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
class TimingPath:
    check: str              # setup or hold
    slack: float | None
    requirement: float | None
    datapath: float | None  # data path delay, logic and net parts
    logic: float | None
    net: float | None
    levels: int | None
    skew: float | None
    start_clock: str
    end_clock: str
    group: str
    startpoint: str
    endpoint: str
    nodes: list = field(default_factory=list)   # (pin or port, site), in path order
    report: str = ""                            # report_timing of the path


def _num(v):
    try:
        x = float(v)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


_UNESC = {"n": "\n", "t": "\t", "\\": "\\"}


def _unesc(s):
    return re.sub(r"\\(.)", lambda m: _UNESC.get(m.group(1), m.group(0)), s)


def _path(r):
    r = r + [""] * (16 - len(r))
    lv = _num(r[8])
    return TimingPath(r[2], _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6]), _num(r[7]),
                      int(lv) if lv is not None else None, _num(r[9]),
                      r[10], r[11], r[12], r[13], r[14])


@dataclass
class Net:
    name: str
    type: str               # SIGNAL, GLOBAL_CLOCK, ... (Vivado net TYPE)
    fanout: int
    routed: int             # 0 not routed, 1 route exported, 2 routed but over the -nets cap
    pins: list = field(default_factory=list)    # driver first: "cell_index:PIN" or "@port"
    segs: list = field(default_factory=list)    # (tile, tile) routing segments
    pips: list = field(default_factory=list)    # Vivado pip names

    @property
    def is_clock(self):
        return "CLOCK" in self.type


@dataclass
class Design:
    part: str = ""
    top: str = ""
    info: dict = field(default_factory=dict)
    utilization: list = field(default_factory=list)  # (label, used, total) when the tool reports it
    cells: list = field(default_factory=list)
    pblocks: list = field(default_factory=list)     # (name, grid ranges)
    pads: list = field(default_factory=list)        # (site, port, a cell on its net)
    pips: dict = field(default_factory=dict)        # tile name -> used pips
    paths: list = field(default_factory=list)       # TimingPath, setup then hold
    source: str = ""                                # checkpoint the export came from
    detail: dict = field(default_factory=dict)      # cell index -> {property: value} (-detail)
    nets: list = field(default_factory=list)        # Net (-detail)
    net_summary: dict = field(default_factory=dict)

    def pin_nets(self):
        """(cell index, pin) -> net index, from the net pin lists."""
        out = {}
        for ni, n in enumerate(self.nets):
            for p in n.pins:
                c, _, pin = p.partition(":")
                if c.isdigit():
                    out[(int(c), pin)] = ni
        return out

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

    def tool(self):
        """The tool that implemented the design, with its version."""
        if self.info.get("nextpnr"):
            return self.info["nextpnr"]
        for key, name in (("vivado", "Vivado"), ("quartus", "Quartus")):
            if self.info.get(key):
                return f"{name} {self.info[key]}"
        return ""


def load_design(path):
    d = Design()
    paths = {}
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
            elif k == "Z":
                d.utilization.append((r[1], int(r[2]), int(r[3])))
            elif k == "X":
                d.pblocks.append((r[1], r[2] if len(r) > 2 else ""))
            elif k == "O":
                d.pads.append((r[1], r[2], r[3]))
            elif k == "T":
                paths[r[1]] = _path(r)
            elif k == "N" and r[1] in paths:
                paths[r[1]].nodes.append((r[2], r[3] if len(r) > 3 else ""))
            elif k == "Y" and r[1] in paths:
                paths[r[1]].report = _unesc(r[2]) if len(r) > 2 else ""
            elif k == "C":
                d.detail[int(r[1])] = dict(_unesc(x).split("=", 1) for x in r[2:] if "=" in x)
            elif k == "W":
                d.nets.append(Net(r[2], r[3], int(r[4]), int(r[5])))
            elif k == "Q":
                d.nets[int(r[1])].pins = [x for x in r[2:]]
            elif k == "G":
                d.nets[int(r[1])].segs = [tuple(x.split(" ", 1)) for x in r[2:] if " " in x]
            elif k == "I":
                d.nets[int(r[1])].pips = r[2:]
            elif k == "H":
                d.net_summary = {"nets": int(r[1]), "routed": int(r[2]), "cap": int(r[3]),
                                 "pips": int(r[4]) if len(r) > 4 else 0}
            elif k == "part":
                d.part = r[1]
            elif k == "top":
                d.top = r[1]
            else:
                d.info[k] = r[1] if len(r) > 1 else ""
    d.paths = list(paths.values())
    return d
