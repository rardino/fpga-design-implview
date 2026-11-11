"""Versal: a small synthetic device with the Versal names and structure.

Grid, 10 columns x 12 rows (row 0 at the top), three clock regions:

  rows 0..2   X0Y2, AI Engine array, no interconnect: AIE tiles in cols 0
              and 2, an AIE interface tile at (6, 2)
  rows 3..9   X0Y1, fabric: GT quad in col 0, INT in cols 1, 4 and 6, CLE in
              col 2, an interface tile with only IRI_QUAD sites in col 3, a
              DSP tile (two DSP58 and a DSP58_CPLX) and a URAM tile in col 5
  rows 10..11 X0Y0, bottom row, no interconnect: an XPIO tile (two IOB sites
              of two pads each) and an XPLL
  cols 7, 8   NULL from top to bottom, but for a CPM configuration frame tile
              (CFRM_CPM_TILE, siteless) at (7, 2)
  col 9       terminations (TERM_R), siteless
"""

import gzip

import numpy as np
import pytest


def _lines(part, w, h, tiles, regions):
    """Device file lines: tiles {(x, y): (type, [(site, site type)])}, the
    rest NULL; regions [(name, row of the grid top, bottom)], full width."""
    out = ["#implview-device\t1", f"part\t{part}", "architecture\tversal", "family\tversaltest"]
    grid = {(x, y): tiles.get((x, y), ("NULL", [])) for x in range(w) for y in range(h)}
    index = {}
    for (x, y), (ty, ss) in sorted(grid.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        index[(x, y)] = len(index)
        out.append(f"T\t{ty}_X{x}Y{h - 1 - y}\t{ty}\t{x}\t{y}\t{len(ss)}\t0")
    for (x, y), (ty, ss) in grid.items():
        cr = next(n for n, y0, y1 in regions if y0 <= y <= y1)
        for s, st in ss:
            out.append(f"S\t{index[(x, y)]}\t{s}\t{st}\t{cr}\t{int(st == 'XPIOB')}")
    for k, (n, y0, y1) in enumerate(regions):
        out.append(f"R\t{n}\t{len(regions) - 1 - k}\t0\t0\t{y0}\t{w - 1}\t{y1}\tSLR0")
    out.append("L\tSLR0\t0\t1\t0")
    return out


def _device_lines():
    t = {}
    t[0, 2] = ("AIE_TILE", [("AIE_CORE_X0Y0", "AIE_CORE"), ("AIE_MEMGRP_X0Y0", "AIE_MEMGRP")])
    t[2, 2] = ("AIE_TILE", [("AIE_CORE_X1Y0", "AIE_CORE"), ("AIE_MEMGRP_X1Y0", "AIE_MEMGRP")])
    t[6, 2] = ("AIE_INTF_B0_CORE", [("AIE_PL_X0Y0", "AIE_PL")])
    t[7, 2] = ("CFRM_CPM_TILE", [])
    t[0, 9] = ("GTY_QUAD_SINGLE", [("GTY_QUAD_X0Y0", "GTY_QUAD")])
    for y in range(3, 10):
        n = 9 - y
        t[1, y] = t[4, y] = t[6, y] = ("INT", [])
        t[2, y] = ("CLE_W_CORE", [(f"SLICE_X0Y{n}", "SLICEL")])
        t[3, y] = ("INTF_LOCF_TL_TILE", [(f"IRI_QUAD_X0Y{4 * n + k}", "IRI_QUAD_EVEN")
                                         for k in range(4)])
    t[5, 9] = ("DSP_ROCF_B_TILE", [("DSP_X0Y0", "DSP58_PRIMARY"), ("DSP_X0Y1", "DSP58_PRIMARY"),
                                   ("DSP58_CPLX_X0Y0", "DSP58_CPLX")])
    t[5, 5] = ("URAM_DELAY_ROCF_TL_TILE", [("URAM288_X0Y0", "URAM288"),
                                           ("URAM_CAS_DLY_X0Y0", "URAM_CAS_DLY")])
    t[2, 11] = ("XPIO_NIBBLE_SC", [("IOB_X0Y0", "XPIOB"), ("IOB_X0Y1", "XPIOB")])
    t[5, 11] = ("CMT_XPLL", [("XPLL_X0Y0", "XPLL")])
    for y in range(12):
        t[9, y] = ("TERM_R", [])
    out = _lines("xcvtest-pkg-1", 10, 12, t, [("X0Y2", 0, 2), ("X0Y1", 3, 9), ("X0Y0", 10, 11)])
    out.append("B\tSLICEL\tA6LUT:LUT6\tB6LUT:LUT6\tA5LUT:LUT5\tAFF:REG_INIT\tAFF2:REG_INIT")
    out.append("B\tXPIOB\tDIFFRXTX:XPIOB_DIFFRXTX\tIOB_M:XPIOB_IOB_M\tIOB_S:XPIOB_IOB_S"
               "\tPAD_M:XPIOB_PAD_M\tPAD_S:XPIOB_PAD_S")
    out.append("K\tAK7\tIOB_X0Y0")
    out.append("K\tAK8\tIOB_X0Y0")
    return out


def _design_lines():
    out = ["#implview-design\t1", "part\txcvtest-pkg-1", "top\tvtop", "vivado\t2026.1",
           "wns\t0.5", "whs\t0.01"]
    for site, bel, ref, grp, name in [
            ("SLICE_X0Y1", "A6LUT", "LUT6", "LUT", "u_a/lut0"),
            ("SLICE_X0Y1", "AFF", "FDRE", "FLOP_LATCH", "u_a/ff0"),
            ("DSP_X0Y0", "DSP_M_DATA", "DSP_M_DATA58", "DSP", "u_m/dsp/m"),
            ("IOB_X0Y0", "IOB_M", "IBUF", "IO", "din_IBUF_inst"),
            ("IOB_X0Y0", "IOB_S", "OBUF", "IO", "dout_OBUF_inst")]:
        out.append(f"U\t{site}\t{bel}\t{ref}\t{grp}\t{name}")
    out.append("O\tIOB_X0Y0\tdin\tdin_IBUF_inst")
    out.append("O\tIOB_X0Y0\tdout\tdout_OBUF_inst")
    return out


def _write(path, lines):
    with gzip.open(path, "wt") as f:
        f.write("\n".join(lines) + "\n")
    return path


@pytest.fixture
def versal(tmp_path):
    from fpga_design_implview.design import load_design
    from fpga_design_implview.device import load_device
    from fpga_design_implview.layout import build_layout
    dev = load_device(_write(tmp_path / "xcvtest-pkg-1.ivdev.gz", _device_lines()))
    des = load_design(_write(tmp_path / "vtop.ivd.gz", _design_lines()))
    return dev, des, build_layout(dev)


def _rect(dev, lay, site):
    return lay.site_rect[dev.site_index()[site]]


def test_interface_registers_are_not_drawn(versal):
    dev, _, lay = versal
    names = [s.name for s in dev.sites]
    for n, r in zip(names, lay.site_rect):
        assert np.isfinite(r[0]) == (not n.startswith("IRI_QUAD")), n
    w = np.diff(lay.col_x)
    assert w[3] < w[1] < w[2]           # the interface column is a sliver


def test_tiles_stay_in_a_clock_region_without_interconnect(versal):
    dev, _, lay = versal
    # the AIE interface tile does not take the NULL columns down to the bottom
    assert _rect(dev, lay, "AIE_PL_X0Y0")[3] <= lay.row_y[3]
    assert _rect(dev, lay, "IOB_X0Y0")[1] >= lay.row_y[10]
    assert _rect(dev, lay, "XPLL_X0Y0")[1] >= lay.row_y[10]


def test_configuration_frame_is_a_block(versal):
    from fpga_design_implview.render import block_label
    dev, _, lay = versal
    assert [dev.tile_type[t] for t in lay.block_rect] == ["CFRM_CPM_TILE"]
    assert block_label("CFRM_CPM_TILE") == "CPM"
    assert block_label("CFRM_CPIPE_TERM_CPM5_CORE") == "CPM"
    assert block_label("HBM_DMAH_FT") == "HBM" and block_label("PSS2") == "PSS"


@pytest.mark.parametrize("tile_type, block", [("TERM_R", False), ("RBRK_CFRM_CORE", False),
                                              ("CFRM_PMC_VR_TILE", True)])
def test_terminations_and_breaks_are_not_blocks(tmp_path, tile_type, block):
    from fpga_design_implview.device import load_device
    from fpga_design_implview.layout import build_layout
    # a CLE column, an INT column, then NULL with the siteless tile at the top
    t = {(2, 0): (tile_type, [])}
    for y in range(6):
        t[0, y] = ("CLE_W_CORE", [(f"SLICE_X0Y{5 - y}", "SLICEL")])
        t[1, y] = ("INT", [])
    dev = load_device(_write(tmp_path / "d.ivdev.gz", _lines("xcvt-1", 6, 6, t, [("X0Y0", 0, 5)])))
    assert bool(build_layout(dev).block_rect) == block


def test_pins_of_a_site(versal):
    dev, _, _ = versal
    assert dev.pins["IOB_X0Y0"] == "AK7 AK8"


def test_utilization(versal):
    from fpga_design_implview.placement import build_placement, group_cells
    dev, des, _ = versal
    groups, cg, _ = group_cells(des)
    p = build_placement(dev, des, groups, cg)
    util = {k: (u, t) for k, u, t in p.utilization}
    assert util["DSP"] == (1, 2)        # DSP58_CPLX is not a DSP of its own
    assert util["URAM"] == (0, 1)       # nor URAM_CAS_DLY a URAM
    assert util["IOB"] == (2, 4)        # pads: two per site, both in use
    assert util["GT quad"] == (0, 1)
    assert util["MMCM/PLL"] == (0, 1)
    assert util["LUT"] == (1, 7 * 2) and util["FF"] == (1, 7 * 2)


def test_html_scale_fits_large_devices(versal, tmp_path):
    from types import SimpleNamespace

    from test_design import _page_meta

    from fpga_design_implview.html import Q, _quant, write_html
    from fpga_design_implview.placement import build_placement, group_cells
    assert _quant(SimpleNamespace(width=500.0, height=900.0)) == Q
    q = _quant(SimpleNamespace(width=547.0, height=1311.0))
    assert q < Q and 1311 * q <= 65535
    dev, des, lay = versal
    groups, cg, _ = group_cells(des)
    write_html(str(tmp_path / "v.html"), dev, lay, des, build_placement(dev, des, groups, cg))
    meta = _page_meta((tmp_path / "v.html").read_text())
    assert meta["q"] == Q and "AK7 AK8" in meta["pins"].values()
    assert [b[0] for b in meta["blocks"]] == ["CPM"]
