"""Geometry of the device: where each tile and site is drawn.

The Vivado tile grid (GRID_POINT_X/Y) is the backbone: one grid column per
tile column, one grid row per tile row, row 0 at the top of the die. Grid
points are not all the same size on silicon, so columns and rows get a width
from what they hold:

- columns with sites (CLB, BRAM, DSP, IO, ...) are full width,
- interconnect (INT) columns are narrow, everything else (interface, break,
  terminations) is a sliver;
- rows with interconnect are full height, clock/break rows are thin.

Tiles that are taller than one grid point (BRAM, DSP, IOB pairs, GT quads,
hard blocks) sit on one grid point with NULL tiles above them. Each tile
claims the free NULL points above it in its column, up to the edge of its
clock region; tiles that are still next to a NULL area (the Zynq PS, GT
quads, IO banks) then grow into it. The tiles of a clock region without
interconnect (on Versal the AI Engine array and the bottom row of XPIO,
memory controllers and clocking) stay inside it.

Inside a tile, sites are grouped by the prefix of their name (SLICE, RAMB18,
ILOGIC, ...) and laid out on a small grid from the X/Y of their names.

A Quartus map (implview.altera) has no interconnect tiles: all its rows and
columns are full size, the columns of the tile types it lists as wide (memory,
DSP) one and a half.
"""

import math
import re
from dataclasses import dataclass, field

import numpy as np

INT_TYPE_RE = re.compile(r"INT(_[LR])?$")

# Sites that are never drawn, by the start of their name: no cell is placed
# on them. Constant sources in every INT/DSP tile; on Versal the interconnect
# input registers of the interface tiles (IRI_QUAD, turned on by the router)
# and the leaf clock, clock delay and test sites of the clock rows.
IGNORED_PREFIXES = ("TIEOFF", "IRI_QUAD", "BUFDIV_LEAF", "GCLK_DELAY", "RCLK")

# Siteless tiles that are never drawn as hard blocks: terminations and row or
# column breaks next to an empty area (Versal).
NOT_BLOCKS = ("TERM_", "RBRK_", "CBRK_")

# A big site that covers the smaller ones of the same tile: the smaller ones
# are drawn as the base layer, the big one on top only when used.
OVERLAYS = {"RAMB36": "RAMB18"}

COL_W_LOGIC = 1.0
COL_W_INT = 0.4
COL_W_THIN = 0.12
ROW_H_FULL = 1.0
ROW_H_THIN = 0.35
PAD = 0.08          # gap around each site, fraction of the site size
MIN_RUN = 3         # a NULL area this many grid points wide is absorbed by its tile
WIDE_SUBCOLS = 1.5  # width, in site columns, of the tile types a device file calls wide
BLOCK_MIN_AREA = 0.005  # siteless hard blocks: fraction of the device area


def is_null_type(t):
    return t == "NULL" or t.endswith("_NULL")


def is_int_type(t):
    return bool(INT_TYPE_RE.fullmatch(t))


@dataclass
class Layout:
    width: float
    height: float
    col_x: np.ndarray               # left edge of each grid column (+ right edge)
    row_y: np.ndarray               # top edge of each grid row (+ bottom edge)
    tile_rect: dict                 # tile index -> (x0, y0, x1, y1), drawn tiles
    site_rect: np.ndarray           # (nsites, 4) x0 y0 x1 y1, NaN if not drawn
    site_overlay: np.ndarray        # bool, drawn only when used
    int_tiles: np.ndarray           # tile indices of the interconnect tiles
    int_rect: np.ndarray            # (n, 4)
    block_rect: dict = field(default_factory=dict)  # tile index -> rect, siteless hard blocks
    cr_rect: list = field(default_factory=list)     # (ClockRegion, (x0, y0, x1, y1))
    slr_rect: list = field(default_factory=list)    # (name, (x0, y0, x1, y1))

    def grid_rect(self, gx0, gy0, gx1, gy1):
        """Rectangle covering grid points gx0..gx1, gy0..gy1 (inclusive)."""
        return (float(self.col_x[gx0]), float(self.row_y[gy0]),
                float(self.col_x[gx1 + 1]), float(self.row_y[gy1 + 1]))


def _drawable_sites(dev):
    """Per tile, the indices of the sites that are drawn."""
    out = [[] for _ in dev.tile_name]
    for i, s in enumerate(dev.sites):
        if not s.prefix.startswith(IGNORED_PREFIXES):
            out[s.tile].append(i)
    return out


def _tile_subcols(dev, sites):
    """How many site columns a tile wants (for the column width)."""
    groups = {}
    for i in sites:
        s = dev.sites[i]
        groups.setdefault(s.prefix, set()).add(s.x)
    for big, small in OVERLAYS.items():
        if big in groups and small in groups:
            del groups[big]
    if len(groups) > 3:
        return 1
    return sum(len(xs) for xs in groups.values())


def _free(owner, ok, ys, xs):
    sub = owner[ys, xs] == -1
    return (sub & ok[ys, xs]).all() if ok is not None else sub.all()


def _free_run(owner, ok, y0, y1, x, step, W):
    """Number of fully free grid columns next to column x, rows y0..y1."""
    n = 0
    x += step
    while 0 <= x < W and _free(owner, ok, slice(y0, y1 + 1), x):
        n += 1
        x += step
    return n


def _components(free, kind):
    """4-connected components of the free grid points of the same kind."""
    H, W = free.shape
    comp = np.full((H, W), -1, np.int64)
    boxes = []
    ys, xs = np.nonzero(free)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if comp[sy, sx] >= 0:
            continue
        c = len(boxes)
        k = kind[sy, sx]
        comp[sy, sx] = c
        stack = [(sy, sx)]
        x0 = x1 = sx
        y0 = y1 = sy
        while stack:
            y, x = stack.pop()
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < H and 0 <= nx < W and free[ny, nx] \
                        and comp[ny, nx] < 0 and kind[ny, nx] == k:
                    comp[ny, nx] = c
                    stack.append((ny, nx))
        boxes.append((x0, y0, x1, y1))
    return comp, boxes


def _edge_labels(comp, rect, H, W):
    x0, y0, x1, y1 = rect
    edge = []
    if x0 > 0:
        edge.append(comp[y0:y1 + 1, x0 - 1])
    if x1 < W - 1:
        edge.append(comp[y0:y1 + 1, x1 + 1])
    if y0 > 0:
        edge.append(comp[y0 - 1, x0:x1 + 1])
    if y1 < H - 1:
        edge.append(comp[y1 + 1, x0:x1 + 1])
    return set(np.concatenate(edge).tolist()) - {-1} if edge else set()


def _absorb(owner, comp, boxes, t, rect, H, W, bound=None):
    """Grow over the bounding boxes of the NULL areas next to the tile, as long
    as nothing else is inside them (and they are inside bound, if given).
    Returns None if nothing was taken."""
    r = rect
    tried = set()
    while True:
        for c in sorted(_edge_labels(comp, r, H, W) - tried):
            tried.add(c)
            bx0, by0, bx1, by1 = boxes[c]
            u = (min(bx0, r[0]), min(by0, r[1]), max(bx1, r[2]), max(by1, r[3]))
            if bound and (u[0] < bound[0] or u[1] < bound[1]
                          or u[2] > bound[2] or u[3] > bound[3]):
                continue
            sub = owner[u[1]:u[3] + 1, u[0]:u[2] + 1]
            if ((sub == -1) | (sub == -2) | (sub == t)).all():
                r = u
                break
        else:
            break
    if r == rect or (r[2] - r[0] + 1) < MIN_RUN or (r[3] - r[1] + 1) < MIN_RUN:
        return None
    return r


def _grow(owner, ok, x0, y0, x1, y1, H, W):
    """Grow the rectangle over free grid points (where ok, if given), one
    side at a time."""
    changed = True
    while changed:
        changed = False
        if x0 > 0 and _free(owner, ok, slice(y0, y1 + 1), x0 - 1):
            x0 -= 1
            changed = True
        if x1 < W - 1 and _free(owner, ok, slice(y0, y1 + 1), x1 + 1):
            x1 += 1
            changed = True
        if y0 > 0 and _free(owner, ok, y0 - 1, slice(x0, x1 + 1)):
            y0 -= 1
            changed = True
        if y1 < H - 1 and _free(owner, ok, y1 + 1, slice(x0, x1 + 1)):
            y1 += 1
            changed = True
    return x0, y0, x1, y1


def _place_sites(dev, sites, rect, out, overlay):
    """Lay the sites of one tile out inside its rectangle."""
    x0, y0, x1, y1 = rect
    groups = {}
    for i in sites:
        groups.setdefault(dev.sites[i].prefix, []).append(i)

    over = {}
    for big, small in OVERLAYS.items():
        if big in groups and small in groups:
            over[big] = groups.pop(big)

    names = sorted(groups)
    w, h = x1 - x0, y1 - y0
    cells = {}
    big_area = w * h >= 16 and len(names) > 1
    singles = [n for n in names if len(groups[n]) == 1]
    many = [n for n in names if len(groups[n]) >= 8]
    if big_area and singles and many:
        # Large hard block with many small sites (PS7 and its MIO pads):
        # the small ones go in strips on the left, the block gets the rest.
        strip = min(0.12 * w, 1.0)
        for k, n in enumerate(many):
            cells[n] = (x0 + k * strip, y0, x0 + (k + 1) * strip, y1)
        rest = [n for n in names if n not in many]
        _split(cells, rest, (x0 + len(many) * strip, y0, x1, y1), groups, dev)
    else:
        _split(cells, names, rect, groups, dev)

    for n, cell in cells.items():
        _grid(dev, groups[n], cell, out)
    for n, idx in over.items():
        _grid(dev, idx, rect, out)
        overlay[idx] = True


def _split(cells, names, rect, groups, dev):
    """Give each prefix group a part of rect: side by side, or a grid if many."""
    x0, y0, x1, y1 = rect
    if not names:
        return
    if len(names) <= 3:
        weights = [len({dev.sites[i].x for i in groups[n]}) for n in names]
        tot = sum(weights)
        x = x0
        for n, wt in zip(names, weights):
            nx = x + (x1 - x0) * wt / tot
            cells[n] = (x, y0, nx, y1)
            x = nx
        return
    nc = math.ceil(math.sqrt(len(names)))
    nr = math.ceil(len(names) / nc)
    cw, ch = (x1 - x0) / nc, (y1 - y0) / nr
    for k, n in enumerate(names):
        c, r = k % nc, k // nc
        cells[n] = (x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch)


def _grid(dev, idx, rect, out):
    """Sites of one prefix on an nx * ny grid from their names (Y0 at the bottom)."""
    x0, y0, x1, y1 = rect
    xs = sorted({dev.sites[i].x for i in idx})
    ys = sorted({dev.sites[i].y for i in idx})
    if len(xs) * len(ys) > 4 * len(idx):
        # Sparse names (one site per X and Y): stack them in name order.
        order = sorted(idx, key=lambda i: (dev.sites[i].y, dev.sites[i].x))
        pos = {i: (0, k) for k, i in enumerate(order)}
        nx, ny = 1, len(order)
    else:
        xi = {v: k for k, v in enumerate(xs)}
        yi = {v: k for k, v in enumerate(ys)}
        pos = {i: (xi[dev.sites[i].x], yi[dev.sites[i].y]) for i in idx}
        nx, ny = len(xs), len(ys)
    sw, sh = (x1 - x0) / nx, (y1 - y0) / ny
    p = PAD * min(sw, sh)
    for i, (cx, cy) in pos.items():
        top = y1 - (cy + 1) * sh
        out[i] = (x0 + cx * sw + p, top + p, x0 + (cx + 1) * sw - p, top + sh - p)


def build_layout(dev):
    W, H = dev.width, dev.height
    ntiles = len(dev.tile_name)
    types = dev.tile_type
    null_t = np.array([is_null_type(t) for t in types])
    int_t = np.array([is_int_type(t) for t in types])
    dsites = _drawable_sites(dev)
    drawable = np.array([bool(s) for s in dsites])

    # Row heights, then column widths from the tiles in full rows (the sites
    # of the clock rows must not widen every column). A map without interconnect
    # tiles (Quartus) is a plain grid: every row and column full size.
    plain_grid = not int_t.any()
    row_full = np.full(H, plain_grid)
    np.logical_or.at(row_full, dev.gy[int_t], True)
    row_h = np.where(row_full, ROW_H_FULL, ROW_H_THIN)
    col_logic = np.zeros(W, bool)
    col_int = np.zeros(W, bool)
    col_sub = np.ones(W)
    in_full = drawable & row_full[dev.gy]
    np.logical_or.at(col_logic, dev.gx[in_full], True)
    np.logical_or.at(col_int, dev.gx[int_t], True)
    wide = set(dev.info.get("wide", "").split())
    for t in np.nonzero(in_full)[0]:
        g = dev.gx[t]
        col_sub[g] = max(col_sub[g], _tile_subcols(dev, dsites[t]),
                         WIDE_SUBCOLS if types[t] in wide else 1)
    col_w = np.where(col_logic | plain_grid, COL_W_LOGIC * np.clip(col_sub, 1, 2),
                     np.where(col_int, COL_W_INT, COL_W_THIN))
    col_x = np.concatenate([[0.0], np.cumsum(col_w)])
    row_y = np.concatenate([[0.0], np.cumsum(row_h)])

    # Tile extents on the grid. Each tile claims the NULL points above it in
    # its column; owner holds the claims (-1 free NULL, -2 other tiles).
    owner = np.full((H, W), -1, np.int64)
    tidx = np.arange(ntiles)
    owner[dev.gy, dev.gx] = np.where(null_t, -1, np.where(drawable, tidx, -2))
    # Kind of each NULL point: plain NULL is filler above tall tiles, other
    # kinds (PCIE_NULL) mark the area of a hard block. Missing points are NULL.
    null_id = {"NULL": 0}
    nt = np.zeros((H, W), np.int64)
    nt[dev.gy, dev.gx] = [null_id.setdefault(ty, len(null_id)) if n else -1
                          for ty, n in zip(types, null_t)]

    # A tall tile never spans two clock regions.
    cr_id = np.full((H, W), -1, np.int64)
    cr_box = []
    for k, cr in enumerate(dev.clock_regions):
        cr_box.append((min(cr.gx0, cr.gx1), min(cr.gy0, cr.gy1),
                       max(cr.gx0, cr.gx1), max(cr.gy0, cr.gy1)))
        cr_id[cr_box[k][1]:cr_box[k][3] + 1, cr_box[k][0]:cr_box[k][2] + 1] = k
    # Clock regions without interconnect (on Versal the AI Engine array and
    # the bottom row of XPIO, memory controllers and clocking): their tiles
    # do not grow out of them.
    cr_int = set(cr_id[dev.gy[int_t], dev.gx[int_t]].tolist())
    ext = {}
    for t in np.nonzero(drawable)[0]:
        x, y = int(dev.gx[t]), int(dev.gy[t])
        y0 = y
        while y0 > 0 and owner[y0 - 1, x] == -1 and nt[y0 - 1, x] == 0 \
                and cr_id[y0 - 1, x] == cr_id[y, x]:
            y0 -= 1
        owner[y0:y, x] = t
        ext[t] = (x, y0, x, y)

    # Tiles next to a NULL area take all of it when nothing else is in its
    # bounding box (the PS of a Zynq, a PCIe block); otherwise they grow over
    # the NULL points next to them if those are wide enough (GT quads, IO).
    comp, boxes = _components(owner == -1, nt)
    plain = nt == 0
    inside = {}
    for t, (x0, y0, x1, y1) in ext.items():
        k = int(cr_id[y1, x0])
        bound, ok = None, plain
        if k >= 0 and k not in cr_int:
            if k not in inside:
                inside[k] = plain & (cr_id == k)
            bound, ok = cr_box[k], inside[k]
        r = _absorb(owner, comp, boxes, t, (x0, y0, x1, y1), H, W, bound)
        if r is None and max(_free_run(owner, ok, y0, y1, x0, -1, W),
                             _free_run(owner, ok, y0, y1, x0, 1, W)) >= MIN_RUN:
            r = _grow(owner, ok, x0, y0, x1, y1, H, W)
        if r is not None:
            gx0, gy0, gx1, gy1 = r
            sub = owner[gy0:gy1 + 1, gx0:gx1 + 1]
            sub[(sub == -1) | (sub == -2)] = t
            ext[t] = r

    # Tiles without sites in front of a large NULL area are hard blocks with
    # no placeable sites (the HBM stacks): drawn as plain boxes.
    blocks = {}
    min_area = BLOCK_MIN_AREA * col_x[-1] * row_y[-1]
    free = owner == -1
    near = np.zeros((H, W), bool)
    near[1:, :] |= free[:-1, :]
    near[:-1, :] |= free[1:, :]
    near[:, 1:] |= free[:, :-1]
    near[:, :-1] |= free[:, 1:]
    cand = ~drawable & ~null_t & ~int_t & near[dev.gy, dev.gx] \
        & np.array([not t.startswith(NOT_BLOCKS) for t in types])
    for t in np.nonzero(cand)[0]:
        x, y = int(dev.gx[t]), int(dev.gy[t])
        if owner[y, x] != -2:
            continue
        owner[y, x] = -1
        gx0, gy0, gx1, gy1 = _grow(owner, None, x, y, x, y, H, W)
        area = (col_x[gx1 + 1] - col_x[gx0]) * (row_y[gy1 + 1] - row_y[gy0])
        if area >= min_area:
            owner[gy0:gy1 + 1, gx0:gx1 + 1] = t
            blocks[t] = (gx0, gy0, gx1, gy1)
        else:
            owner[y, x] = -2

    lay = Layout(
        width=float(col_x[-1]), height=float(row_y[-1]), col_x=col_x, row_y=row_y,
        tile_rect={}, site_rect=np.full((len(dev.sites), 4), np.nan),
        site_overlay=np.zeros(len(dev.sites), bool),
        int_tiles=np.nonzero(int_t)[0], int_rect=None)
    for t, (gx0, gy0, gx1, gy1) in ext.items():
        rect = lay.grid_rect(gx0, gy0, gx1, gy1)
        lay.tile_rect[t] = rect
        _place_sites(dev, dsites[t], rect, lay.site_rect, lay.site_overlay)

    for t, (gx0, gy0, gx1, gy1) in blocks.items():
        lay.block_rect[t] = lay.grid_rect(gx0, gy0, gx1, gy1)

    it = lay.int_tiles
    lay.int_rect = np.stack([col_x[dev.gx[it]], row_y[dev.gy[it]],
                             col_x[dev.gx[it] + 1], row_y[dev.gy[it] + 1]], axis=1)

    slr_box = {}
    for cr in dev.clock_regions:
        r = lay.grid_rect(min(cr.gx0, cr.gx1), min(cr.gy0, cr.gy1),
                          max(cr.gx0, cr.gx1), max(cr.gy0, cr.gy1))
        lay.cr_rect.append((cr, r))
        b = slr_box.get(cr.slr)
        slr_box[cr.slr] = r if b is None else (min(b[0], r[0]), min(b[1], r[1]),
                                               max(b[2], r[2]), max(b[3], r[3]))
    lay.slr_rect = sorted(slr_box.items())
    return lay
