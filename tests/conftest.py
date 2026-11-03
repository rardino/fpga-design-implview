"""A small synthetic device and design in the export format (no Vivado needed).

Grid, 12 columns x 12 rows (row 0 at the top):

  col 0      IOB pairs: LIOB tiles on odd rows, NULL above each
  col 1      INT
  col 2      CLB, two slices per tile (SLICE_X0, SLICE_X1)
  col 3      INT
  col 4      BRAM every 5 rows (rows 4 and 9), NULL above, RAMB36 + 2 RAMB18
  col 5      DSP every 5 rows
  col 6..11  rows 0..7: a hard block area of PCIE_NULL with one PSS tile at
             (8, 7) that holds a PS7 site and 10 IOPAD sites; rows 8..11: CLBs
  row 11     clock row (no INT): a BUFG tile in col 2
"""

import gzip

import pytest

W, H = 12, 12


def _device_lines():
    tiles = {}
    sites = []

    def tile(x, y, ty, ss=()):
        tiles[(x, y)] = (f"{ty}_X{x}Y{H - 1 - y}", ty, list(ss))

    for y in range(H):
        for x in range(W):
            tile(x, y, "NULL")
    for y in range(H - 1):
        if y % 2 == 1:
            tile(0, y, "LIOB", [(f"IOB_X0Y{2 * (H - 1 - y)}", "IOB33M"),
                                (f"IOB_X0Y{2 * (H - 1 - y) + 1}", "IOB33S")])
        tile(1, y, "INT_L", [(f"TIEOFF_X0Y{y}", "TIEOFF")])
        tile(2, y, "CLBLM_L", [(f"SLICE_X0Y{H - 2 - y}", "SLICEM"), (f"SLICE_X1Y{H - 2 - y}", "SLICEL")])
        tile(3, y, "INT_R", [(f"TIEOFF_X1Y{y}", "TIEOFF")])
    for y in (4, 9):
        k = 0 if y == 9 else 1
        tile(4, y, "BRAM_L", [(f"RAMB36_X0Y{k}", "RAMBFIFO36E1"), (f"RAMB18_X0Y{2 * k}", "FIFO18E1"),
                              (f"RAMB18_X0Y{2 * k + 1}", "RAMB18E1")])
        tile(5, y, "DSP_L", [(f"DSP48_X0Y{2 * k}", "DSP48E1"), (f"DSP48_X0Y{2 * k + 1}", "DSP48E1")])
    for y in range(8):
        for x in range(6, W):
            tile(x, y, "PCIE_NULL")
    tile(8, 7, "PSS2", [("PS7_X0Y0", "PS7")] + [(f"IOPAD_X1Y{i}", "IOPAD") for i in range(10)])
    for y in range(8, H - 1):
        for x in range(6, W, 2):
            tile(x, y, "INT_L")
            tile(x + 1, y, "CLBLL_L", [(f"SLICE_X{x}Y{H - 2 - y}", "SLICEL")])
    tile(2, H - 1, "CLK_BUFG", [(f"BUFGCTRL_X0Y{i}", "BUFGCTRL") for i in range(4)])

    out = ["#implview-device\t1", "part\txc7test-pkg-1", "family\ttest", "device\txc7test"]
    index = {}
    for (x, y), (name, ty, ss) in sorted(tiles.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        index[(x, y)] = len(index)
        out.append(f"T\t{name}\t{ty}\t{x}\t{y}\t{len(ss)}\t0")
    for (x, y), (name, ty, ss) in tiles.items():
        for s, st in ss:
            out.append(f"S\t{index[(x, y)]}\t{s}\t{st}\tX0Y0\t{int(st.startswith('IOB'))}")
            sites.append(s)
    out.append("B\tSLICEL\tA6LUT:LUT6\tB6LUT:LUT6\tC6LUT:LUT6\tD6LUT:LUT6\tA5LUT:LUT5\tAFF:REG_INIT\tBFF:REG_INIT\tA5FF:FF_INIT")
    out.append(f"R\tX0Y0\t0\t0\t0\t0\t{W - 1}\t{H - 1}\tSLR0")
    out.append("L\tSLR0\t0\t1\t0")
    out.append("K\tA1\tIOB_X0Y4")
    return out


DESIGN_CELLS = [
    # site, bel, ref, group, name
    ("SLICE_X0Y1", "A6LUT", "LUT6", "LUT", "u_top/u_a/lut0"),
    ("SLICE_X0Y1", "AFF", "FDRE", "FLOP_LATCH", "u_top/u_a/ff0"),
    ("SLICE_X1Y1", "B6LUT", "LUT6", "LUT", "u_top/u_a/sub/lut1"),
    ("SLICE_X0Y2", "A6LUT", "LUT6", "LUT", "u_top/u_b/lut0"),
    ("SLICE_X0Y3", "BFF", "FDRE", "FLOP_LATCH", "u_top/u_b/ff0"),
    ("SLICE_X6Y1", "A6LUT", "LUT6", "LUT", "u_top/u_c/lut0"),
    ("RAMB36_X0Y0", "RAMB36", "RAMB36E1", "BLOCKRAM", "u_top/u_b/ram"),
    ("RAMB18_X0Y3", "RAMB18E1", "RAMB18E1", "BLOCKRAM", "u_top/u_a/ram18"),
    ("PS7_X0Y0", "PS7", "PS7", "PS", "u_ps/ps7"),
    ("IOB_X0Y4", "PAD", "IBUF", "IO", "clk_IBUF_inst"),
]


def _design_lines():
    out = ["#implview-design\t1", "part\txc7test-pkg-1", "top\ttesttop", "vivado\t2026.1",
           "date\t2026-11-03 10:00:00", "wns\t0.123", "whs\t-0.010"]
    for site, bel, ref, grp, name in DESIGN_CELLS:
        out.append(f"U\t{site}\t{bel}\t{ref}\t{grp}\t{name}")
    out.append("X\tpb_a\tSLICE_X0Y1:SLICE_X1Y3")
    out.append(f"P\tINT_L_X1Y{H - 1 - 2}\t40")
    out.append(f"P\tINT_R_X3Y{H - 1 - 2}\t10")
    return out


def _write(path, lines):
    with gzip.open(path, "wt") as f:
        f.write("\n".join(lines) + "\n")


@pytest.fixture
def files(tmp_path):
    dev = tmp_path / "xc7test-pkg-1.ivdev.gz"
    des = tmp_path / "testtop.ivd.gz"
    _write(dev, _device_lines())
    _write(des, _design_lines())
    return dev, des


@pytest.fixture
def loaded(files):
    from implview.design import load_design
    from implview.device import load_device
    from implview.layout import build_layout
    dev, des = load_device(files[0]), load_design(files[1])
    return dev, des, build_layout(dev)
