"""Device resource map: tiles on the Vivado tile grid, their sites and BELs.

Read back from the gzip'ed TSV written by `implview::export_device`
(tcl/implview.tcl), one file per part, cached by `implview.cache`; or by
`implview.altera` for a Quartus design, on the grid of its locations.
"""

import gzip
import re
from dataclasses import dataclass, field

import numpy as np

SITE_NAME_RE = re.compile(r"^(.*)_X(\d+)Y(\d+)$")


@dataclass
class Site:
    name: str
    type: str
    tile: int               # index into Device.tile_*
    clock_region: str
    bonded: bool
    prefix: str             # name without the _X#Y# suffix (SLICE, RAMB18, ...)
    x: int                  # site X/Y from the name, -1 if the name has none
    y: int


@dataclass
class ClockRegion:
    name: str
    row: int
    col: int
    gx0: int                # grid bounds, inclusive (top left .. bottom right)
    gy0: int
    gx1: int
    gy1: int
    slr: str


@dataclass
class Device:
    part: str = ""
    info: dict = field(default_factory=dict)
    tile_name: list = field(default_factory=list)
    tile_type: list = field(default_factory=list)
    gx: np.ndarray = None
    gy: np.ndarray = None
    tile_nsites: np.ndarray = None
    tile_slr: np.ndarray = None
    sites: list = field(default_factory=list)
    bels: dict = field(default_factory=dict)        # site type -> [(bel, bel type)]
    clock_regions: list = field(default_factory=list)
    slrs: list = field(default_factory=list)        # (name, index, is_master, config order)
    pins: dict = field(default_factory=dict)        # site name -> package pins, space separated

    @property
    def name(self):
        return self.info.get("device", self.part)

    @property
    def note(self):
        """What the reader of a picture has to know about the map."""
        return "map inferred from the placement" if self.info.get("map") == "inferred" else ""

    @property
    def width(self):
        return int(self.gx.max()) + 1

    @property
    def height(self):
        return int(self.gy.max()) + 1

    def site_index(self):
        return {s.name: i for i, s in enumerate(self.sites)}

    def tile_index(self):
        return {n: i for i, n in enumerate(self.tile_name)}

    def tile_sites(self):
        """Site indices of each tile (list of lists)."""
        out = [[] for _ in self.tile_name]
        for i, s in enumerate(self.sites):
            out[s.tile].append(i)
        return out


def parse_site_name(name):
    m = SITE_NAME_RE.match(name)
    if not m:
        return name, -1, -1
    return m.group(1), int(m.group(2)), int(m.group(3))


def load_device(path):
    dev = Device()
    gx, gy, ns, slr = [], [], [], []
    with gzip.open(path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        if header[0] != "#implview-device":
            raise ValueError(f"{path}: not an implview device file")
        for line in f:
            r = line.rstrip("\n").split("\t")
            k = r[0]
            if k == "T":
                dev.tile_name.append(r[1])
                dev.tile_type.append(r[2])
                gx.append(int(r[3]))
                gy.append(int(r[4]))
                ns.append(int(r[5]))
                slr.append(int(r[6]) if r[6] else 0)
            elif k == "S":
                if len(r) > 8:          # the name does not say where the site goes
                    prefix, x, y = r[6], int(r[7]), int(r[8])
                else:
                    prefix, x, y = parse_site_name(r[2])
                dev.sites.append(Site(r[2], r[3], int(r[1]), r[4], r[5] == "1", prefix, x, y))
            elif k == "B":
                dev.bels[r[1]] = [tuple(b.split(":", 1)) for b in r[2:] if b]
            elif k == "R":
                dev.clock_regions.append(ClockRegion(
                    r[1], int(r[2]), int(r[3]), int(r[4]), int(r[5]), int(r[6]), int(r[7]), r[8]))
            elif k == "L":
                dev.slrs.append((r[1], int(r[2]), r[3] == "1", int(r[4]) if r[4] else 0))
            elif k == "K":          # a Versal IO site has two pads, a GT many
                dev.pins[r[2]] = f"{dev.pins[r[2]]} {r[1]}" if r[2] in dev.pins else r[1]
            elif k == "part":
                dev.part = r[1]
            else:
                dev.info[k] = r[1] if len(r) > 1 else ""
    dev.gx = np.array(gx, dtype=np.int32)
    dev.gy = np.array(gy, dtype=np.int32)
    dev.tile_nsites = np.array(ns, dtype=np.int32)
    dev.tile_slr = np.array(slr, dtype=np.int32)
    return dev
