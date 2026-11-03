# Implemented design viewer for Vivado

`implview` draws what a Vivado implementation uses of the device: every site of the
part (slices, block RAM, DSP, URAM, IO, GT, clocking, hard blocks) on the tile grid,
lit up when the placed design uses it and colored by design hierarchy. It reads the
part from the checkpoint, so the same command works for any design and device.

Outputs:

| File      | What                                                                 |
|-----------|----------------------------------------------------------------------|
| `.png`    | placement picture, plus `_routing.png` with the interconnect usage    |
| `.pdf`    | both pages, vector (zoom in to see single slices)                    |
| `.svg`    | like the PNGs, vector                                                |
| `.html`   | interactive viewer in one self-contained file (pan, zoom, search)    |

Each picture has the part, Vivado version, WNS/WHS, a utilization table (LUT, FF,
SLICE, BRAM, URAM, DSP, IOB, GT, BUFG, MMCM/PLL) and the hierarchy legend.
Clock regions, SLRs and pblocks are outlined.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .          # numpy, matplotlib
```

Vivado is needed to export a design (it is the only thing that reads checkpoints);
drawing works without it.

## Usage

```sh
implview render path/to/impl_1                  # run dir, project dir or .dcp
implview render top_routed.dcp -o top.png -o top.pdf -o top.html -O pictures
implview render top_routed.dcp --theme dark --no-routing
```

Without `-o` it writes `<top>.png` and `<top>.pdf` in the current directory.

The first run on a checkpoint opens it in Vivado in batch mode and exports the
placement (and the routing usage, unless `--no-routing`); the first run on a part
also exports the device. Both are cached in `~/.cache/implview` (`$IMPLVIEW_CACHE`),
devices by part and designs by checkpoint path and time, so later renders take
seconds. `--refresh` exports again.

| Command                      | What                                                     |
|------------------------------|----------------------------------------------------------|
| `implview render INPUT`      | pictures and viewer                                      |
| `implview export INPUT [-o]` | only the export (design `.ivd.gz`, device in the cache)  |
| `implview device PART`       | only the device of a part, without a design              |
| `implview info INPUT`        | part, top, Vivado version, cache status                  |
| `implview tcl`               | path of `implview.tcl`, for use inside Vivado            |

### Which Vivado

`--vivado` or `$IMPLVIEW_VIVADO` give the command (a path, or a command with its
arguments); otherwise `vivado` on the PATH, or the install under `~/opt/xilinx`,
`~/Xilinx`, `/opt/Xilinx`, `/tools/Xilinx` whose version wrote the checkpoint (older
versions cannot open newer checkpoints). Opening a checkpoint needs a license for its
device.

```sh
export IMPLVIEW_VIVADO=/tools/Xilinx/2026.1/Vivado/bin/vivado
```

### From a build script

To skip opening the checkpoint again, export at the end of the implementation run, in
the same Vivado session (source the file by its path, `implview tcl` prints it; do not
call `implview` from inside Vivado, its environment breaks Python):

```tcl
source /path/to/fpga-design-implview/src/implview/tcl/implview.tcl
implview::export_all $out/implview -routing     ;# <top>.ivd.gz and <part>.ivdev.gz
```

then `implview render $out/implview/<top>.ivd.gz`. The device file is looked up next
to the design file first, then in the cache.

### Colors

By default the hierarchy is split from the top, always splitting the largest group
(wrappers with one child are walked through), until the 8 colors are used; cells of
small modules go to "(other)". A site takes the color of most of its cells. Options:

- `-g PATH` (repeatable): one color per hierarchy path, glob allowed, in the given
  order: `-g u_core/u_fft -g 'u_core/gen_ch*' -g u_io`
- `--depth N`: hierarchy cut at depth N, largest groups first
- `--max-groups N` (up to 8)

### The viewer

Scroll to zoom, drag to pan, `F` to fit. Hover a site for its cells (name, primitive,
BEL), click it to pin the list; click a cell to highlight its whole module. The search
box matches cell names (`*` and `?` as wildcards) and dims everything else. Groups and
layers (unused sites, routing usage, clock regions, pblocks, labels) can be toggled,
light/dark switched, the current view saved as PNG. The URL hash sets the start view:
`viewer.html#q=u_core/u_fft/*&theme=dark&layer=routing&site=SLICE_X10Y20`.

## How it works

`tcl/implview.tcl` exports two gzip'ed tab-separated files:

- device (`*.ivdev.gz`): every tile (type, grid position), its sites (type, clock
  region), the BELs of each site type, clock regions, SLRs, package pins;
- design (`*.ivd.gz`): every placed primitive (site, BEL, primitive, hierarchical
  name), pblocks, WNS/WHS, and the used PIPs per tile (constant nets left out).

The picture is laid out on Vivado's tile grid (`layout.py`). The grid has one point per
tile, so grid columns get a width from what they hold (site columns full, interconnect
narrow, the rest thin) and rows without interconnect (clock rows) are thin. Tiles taller
than one grid point sit at the bottom of a run of NULL tiles: each claims the NULL
points above it up to its clock region edge. Tiles next to a larger NULL area (Zynq PS,
GT quads, IO banks) grow into it; siteless tiles in front of a large one (the HBM
stacks) are drawn as labeled boxes. Inside a tile, sites are grouped by name prefix and
placed by the X/Y of their names; a RAMB36 is drawn over its two RAMB18 when used.

The geometry is the tile grid, not the die to scale.

## Status

Tried on a Zynq-7000 (xc7z020) and a Virtex UltraScale+ HBM part (xcvu37p, three SLRs).
UltraScale, Zynq UltraScale+ and Versal parts are not tried yet. Nets are not drawn,
only how much each interconnect tile is used.

Tests: `.venv/bin/pip install -e .[test] && .venv/bin/pytest` (no Vivado needed, they
use a small synthetic device).
