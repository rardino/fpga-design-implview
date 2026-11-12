"""Device map of an Altera part and the design on it, from Quartus locations.

A Quartus location is a kind of atom and a position on the device grid:
LABCELL_X12_Y7_N0 is cell 0 of the LAB in column 12, row 7, row 0 at the
bottom. Each grid position is a tile, as on the Vivado tile grid:

- LAB, MLAB: the logic cells and registers of one LAB, on the site LAB_X12_Y7;
- M9K, M10K, DSP, ...: one block, some kinds several rows tall;
- IO: one site per pad, named after its pin (PIN_R8);
- anything else (PLL parts, JTAG, ...) under its own kind, the parts of the
  hard processor together in one block.

Quartus cannot list the resources of a device, so the map is inferred from the
placement and holds only what the placement shows:

- every location the design uses;
- the unused blocks of the columns it uses (a column holds one kind of block),
  inside the outline of the placement: where blocks are in use both to the
  left and to the right, above and below (a staircase around what is used, so
  a corner of the die the design leaves empty is left out, and so is a corner
  the die does not have);
- the pads of the pins in use.

Columns the design does not touch stay empty.
"""

import gzip
import math
import os
import re
from collections import defaultdict

from .quartus import BUFFER_N

FORMAT = 1
MAP = 1                 # version of the inference, part of the cache key

REGISTERS = {"FF", "LCFF"}
LOGIC = {"LCCOMB", "LABCELL", "MLABCELL", "LC"} | REGISTERS
DSP = {"DSP", "DSPMULT", "DSPOUT", "MPDSP"}
RAM_RE = re.compile(r"M\d+K$|M512$|MRAM$")
MAX_PITCH = 4           # rows of the tallest block told from the placement
CREATED = "~QUARTUS_CREATED_"   # atoms Quartus adds on blocks the design leaves unused


def _core(kinds):
    """Type of the tile that holds these kinds of atoms, None for a block of
    its own kind."""
    if kinds & BUFFER_N.keys():
        return "IO"
    ram = sorted(k for k in kinds if RAM_RE.match(k))
    if ram:
        return ram[0]
    if kinds & DSP:
        return "DSP"
    if "MLABCELL" in kinds:
        return "MLAB"
    if kinds & LOGIC:
        return "LAB"
    return None


def _group(kind):
    if kind in REGISTERS:
        return "FLOP_LATCH"
    if kind in LOGIC:
        return "LUT"
    if RAM_RE.match(kind):
        return "BLOCKRAM"
    if kind in DSP:
        return "DSP"
    if kind.startswith(("IO", "DDIO")):
        return "IO"
    return "OTHERS"


def _pitch(columns):
    """Rows from one block to the next, from the rows used in each column."""
    g, n = 0, 0
    for ys in columns:
        ys = sorted(ys)
        n += len(ys)
        for a, b in zip(ys, ys[1:]):
            g = math.gcd(g, b - a)
    return g if n >= 3 and 1 < g <= MAX_PITCH else 1


class Map:
    """Tiles, sites and placed cells of a fitted design."""

    def __init__(self, fit):
        self.tiles = {}         # (x, y) -> tile type
        self.sites = {}         # site name -> ((x, y) of its tile, type, bank, prefix, sx, sy)
        self.cells = []         # (site, bel, atom type, group, name)
        self.null = {}          # (x, y) -> NULL kind: the rest of a block larger than its tile
        self.merged = {}        # (x, y) of a part of the hard processor -> (x, y) of its tile
        atoms = [a for a in fit.atoms if a[1] >= 0]
        self.offgrid = [a[0] for a in fit.atoms if a[1] < 0]    # locations without coordinates
        kinds = defaultdict(set)
        for kind, x, y, _n, _ref, _name in atoms:
            kinds[(x, y)].add(kind)
        self._classify(kinds)
        self._hard_blocks(kinds)
        self._fill()
        self._place(atoms, fit.pads)

    def _classify(self, kinds):
        core = {xy: _core(ks) for xy, ks in kinds.items()}
        xs, ys = {x for x, _ in kinds}, {y for _, y in kinds}
        edge_x, edge_y = (min(xs), max(xs)), (min(ys), max(ys))
        mlab = {x for (x, _), t in core.items() if t == "MLAB"}     # a column of MLABs
        for (x, y), t in core.items():
            if t == "LAB" and x in mlab:
                t = "MLAB"
            elif t == "LAB" and kinds[(x, y)] <= REGISTERS and (x in edge_x or y in edge_y):
                t = None                # a register of the periphery, not a LAB
            self.tiles[(x, y)] = t or min(kinds[(x, y)])

    def _hard_blocks(self, kinds):
        """The parts of the hard processor become one tile over their bounding
        box, if nothing else is inside it."""
        hps = sorted(xy for xy, ks in kinds.items() if all(k.startswith("HPS") for k in ks))
        if len(hps) < 2:
            return
        x0, x1 = min(x for x, _ in hps), max(x for x, _ in hps)
        y0, y1 = min(y for _, y in hps), max(y for _, y in hps)
        box = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        if any(xy in self.tiles and xy not in hps for xy in box):
            return
        for xy in box:
            self.tiles.pop(xy, None)
            self.null[xy] = "HPS_NULL"
        del self.null[hps[0]]
        self.tiles[hps[0]] = "HPS"
        self.merged = dict.fromkeys(hps, hps[0])
        self._site("HPS", hps[0], "HPS", "HPS")

    def _fill(self):
        """Unused blocks of the columns in use."""
        cols = defaultdict(lambda: defaultdict(list))       # x -> type -> rows
        for (x, y), t in self.tiles.items():
            if t in ("LAB", "MLAB", "DSP") or RAM_RE.match(t):
                cols[x][t].append(y)
        by_type = defaultdict(list)
        for types in cols.values():
            for t, ys in types.items():
                by_type[t].append(ys)
        pitch = {t: 1 if t in ("LAB", "MLAB") else _pitch(c) for t, c in by_type.items()}
        span = {x: (min(min(ys) for ys in types.values()), max(max(ys) for ys in types.values()))
                for x, types in cols.items()}
        for x, types in cols.items():
            # rows with blocks in use at or left of the column and at or right of it
            left = [s for k, s in span.items() if k <= x]
            right = [s for k, s in span.items() if k >= x]
            lo = max(min(s[0] for s in left), min(s[0] for s in right))
            hi = min(max(s[1] for s in left), max(s[1] for s in right))
            for t, ys in types.items():
                p = pitch[t]
                for y in range(lo, hi + 1):
                    if (y - ys[0]) % p == 0 and (x, y) not in self.tiles and (x, y) not in self.null:
                        self.tiles[(x, y)] = t
        for (x, y), t in list(self.tiles.items()):
            for k in range(1, pitch.get(t, 1)):
                if (x, y + k) not in self.tiles:
                    self.null.setdefault((x, y + k), "NULL")

    def _site(self, name, xy, type_, prefix, bank="", sx=None, sy=None):
        """A site of the tile at xy; sx, sy order the sites of one tile."""
        if name not in self.sites:
            self.sites[name] = (xy, type_, bank, prefix, xy[0] if sx is None else sx,
                                xy[1] if sy is None else sy)
        return name

    def _place(self, atoms, pins):
        for (x, y), t in self.tiles.items():
            if t in ("LAB", "MLAB"):
                self._site(f"LAB_X{x}_Y{y}", (x, y), t, "LAB")
            elif t == "DSP" or RAM_RE.match(t):
                self._site(f"{t}_X{x}_Y{y}", (x, y), t, t)
        # pads: one site each, stacked in the tile by their Z coordinate
        pads = defaultdict(dict)
        for kind, x, y, n, _ref, _name in atoms:
            if kind in BUFFER_N:
                z = n - BUFFER_N[kind]
                pin, bank, _port = pins.get((x, y, z), ("", "", ""))
                name = f"PIN_{pin}" if pin else f"IOPAD_X{x}_Y{y}_N{z}"
                pads[(x, y)][z] = self._site(name, (x, y), "IO", "PIN",
                                             f"bank {bank}" if bank else "", 0, z)
        for kind, x, y, n, ref, name in atoms:
            xy = self.merged.get((x, y), (x, y))
            t = self.tiles[xy]
            if t == "HPS":
                site = "HPS"
            elif t == "IO":
                site = pads[xy][min(pads[xy], key=lambda z: abs(n - 1 - z))]
            elif t in ("LAB", "MLAB") and kind in LOGIC:
                site = f"LAB_X{x}_Y{y}"
            elif (t == "DSP" and kind in DSP) or (t == kind and RAM_RE.match(t)):
                site = f"{t}_X{x}_Y{y}"
            else:
                site = self._site(f"{kind}_X{x}_Y{y}", xy, kind, kind)
            if name.startswith(CREATED) and kind not in LOGIC:
                continue                # the block is there, the design does not use it
            self.cells.append((site, f"{kind}_N{n}", ref, _group(kind), name.replace("|", "/")))

    def counts(self):
        """Sites of the map by kind, as the Fitter report names them."""
        n = defaultdict(int)
        for _xy, type_, *_ in self.sites.values():
            n[{"MLAB": "LAB", "IO": "I/O pin"}.get(type_, type_)] += 1
        return dict(n)

    def summary(self, utilization):
        """The blocks on the map next to what the Fitter report says the device
        has: "LAB 1,287 of 1,395, M9K 66 of 66"."""
        counts = self.counts()
        total = {label: t for label, _used, t in utilization}
        if "DSP" not in total and "Multiplier 9-bit" in total:
            total["DSP"] = total["Multiplier 9-bit"] // 2       # two to a block
        parts = []
        for label in ["LAB"] + sorted(k for k in counts if RAM_RE.match(k)) + ["DSP", "I/O pin"]:
            if counts.get(label):
                parts.append(f"{label} {counts[label]:,}"
                             + (f" of {total[label]:,}" if total.get(label) else ""))
        return ", ".join(parts)

    def device_lines(self, info):
        w = max(x for x, _ in self.tiles) + 1
        top = max(max(y for _, y in self.tiles), max((y for _, y in self.null), default=0))
        blocks = sorted({t for t in self.tiles.values() if t == "DSP" or RAM_RE.match(t)})
        out = [f"#implview-device\t{FORMAT}", f"part\t{info.get('part', '')}",
               f"family\t{info.get('family', '')}", "map\tinferred", "wide\t" + " ".join(blocks)]
        per_tile = defaultdict(list)
        for name, s in self.sites.items():
            per_tile[s[0]].append(name)
        index = {}
        for y in range(top, -1, -1):
            for x in range(w):
                t = self.tiles.get((x, y)) or self.null.get((x, y), "EMPTY")
                index[(x, y)] = len(index)
                out.append(f"T\t{t}_X{x}_Y{y}\t{t}\t{x}\t{top - y}\t{len(per_tile[(x, y)])}\t0")
        for name, (xy, type_, bank, prefix, sx, sy) in self.sites.items():
            out.append(f"S\t{index[xy]}\t{name}\t{type_}\t{bank}\t{int(prefix == 'PIN')}"
                       f"\t{prefix}\t{sx}\t{sy}")
        return out

    def design_lines(self, fit):
        info = fit.info
        out = [f"#implview-design\t{FORMAT}", f"part\t{info.get('part', '')}",
               f"top\t{info.get('top', '')}"]
        out += [f"{k}\t{info[k]}" for k in ("date", "quartus", "wns", "whs") if info.get(k)]
        out.append(f"primitives\t{len(self.cells)}")
        out += [f"Z\t{label}\t{used}\t{total}" for label, used, total in fit.utilization]
        out += ["U\t" + "\t".join(c) for c in self.cells]
        return out


def write(fit, design_path, device_path):
    """Write the design and the device file of a fitted design. Returns the
    map they were written from."""
    m = Map(fit)
    for path, lines in ((device_path, m.device_lines(fit.info)), (design_path, m.design_lines(fit))):
        tmp = f"{path}.tmp{os.getpid()}"
        try:
            with gzip.open(tmp, "wt") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return m
