"""UltraScale and Zynq UltraScale+: small synthetic devices with their names."""

import numpy as np
import pytest
from test_versal import _write


def _device(path, w, h, tiles, regions):
    """tiles {(x, y): (type, [(site, site type, bonded)])}, the rest NULL;
    regions [(name, x0, y0, x1, y1)], row 0 at the top."""
    from fpga_design_implview.device import load_device
    out = ["#implview-device\t1", "part\txczutest-pkg-1", "architecture\tzynquplus"]
    index = {}
    for y in range(h):
        for x in range(w):
            ty, ss = tiles.get((x, y), ("NULL", []))
            index[x, y] = len(index)
            out.append(f"T\t{ty}_X{x}Y{h - 1 - y}\t{ty}\t{x}\t{y}\t{len(ss)}\t0")
    for (x, y), (ty, ss) in tiles.items():
        cr = next((n for n, x0, y0, x1, y1 in regions if x0 <= x <= x1 and y0 <= y <= y1), "")
        for s, st, b in ss:
            out.append(f"S\t{index[x, y]}\t{s}\t{st}\t{cr}\t{int(b)}")
    for n, x0, y0, x1, y1 in regions:
        out.append(f"R\t{n}\t{n[3:]}\t{n[1]}\t{x0}\t{y0}\t{x1}\t{y1}\tSLR0")
    out.append("L\tSLR0\t0\t1\t0")
    return load_device(_write(path, out))


def _column(tiles, x, ty, prefix, rows, n=1):
    for y in rows:
        tiles[x, y] = (ty, [(f"{prefix}_X{x}Y{11 - y}", prefix, False)] * n if prefix else [])


@pytest.fixture
def zuplus(tmp_path):
    """12 x 12. Columns 0..5: the PS, three clock regions without interconnect
    (PS8 at the bottom of the middle one, VCU at the bottom of the lowest);
    column 6: interface tiles, but a clock row tile with BUFG_PS at row 2;
    then fabric, and a GT quad at the bottom right."""
    from fpga_design_implview.design import load_design
    from fpga_design_implview.layout import build_layout
    t = {(0, 7): ("PSS_ALTO", [("PS8_X0Y0", "PS8", False)]),
         (0, 11): ("VCU_VCU_FT", [("VCU_X0Y0", "VCU", False)])}
    _column(t, 6, "INT_INTF_LEFT_TERM_PSS", "", range(12))
    t[6, 2] = ("RCLK_INTF_LEFT_TERM_DA6_FT", [(f"BUFG_PS_X0Y{k}", "BUFG_PS", False)
                                              for k in range(3)])
    for x in (7, 9):
        _column(t, x, "INT", "", range(12))
    for x in (8, 10):
        for y in range(12):
            t[x, y] = ("CLEL_R", [(f"SLICE_X{x}Y{11 - y}", "SLICEL", False)])
    for y in range(8):
        t[11, y] = ("CLEL_R", [(f"SLICE_X11Y{11 - y}", "SLICEL", False)])
    t[11, 11] = ("GTH_QUAD_RIGHT", [(f"GTHE4_CHANNEL_X0Y{k}", "GTHE4_CHANNEL", k < 2)
                                    for k in range(4)]
                 + [("GTHE4_COMMON_X0Y0", "GTHE4_COMMON", True)]
                 + [(f"BUFG_GT_X0Y{k}", "BUFG_GT", False) for k in range(2)]
                 + [(f"BUFG_GT_SYNC_X0Y{k}", "BUFG_GT_SYNC", False) for k in range(2)])
    regions = [(f"X{c}Y{2 - r}", x0, 4 * r, x1, 4 * r + 3)
               for r in range(3) for c, (x0, x1) in enumerate([(0, 5), (6, 11)])]
    dev = _device(tmp_path / "zu.ivdev.gz", 12, 12, t, regions)
    des = load_design(_write(tmp_path / "zu.ivd.gz", [
        "#implview-design\t1", "part\txczutest-pkg-1", "top\tsoc", "vivado\t2026.1",
        "U\tPS8_X0Y0\tPS8\tPS8\tOTHERS\tsoc/ps/inst/PS8_i",
        "U\tBUFG_PS_X0Y0\tBUFG_PS\tBUFG_PS\tCLOCK\tsoc/ps/inst/buffer_pl_clk_0",
        "U\tBUFG_GT_SYNC_X0Y0\tBUFG_GT_SYNC\tBUFG_GT_SYNC\tCLOCK\tsoc/gt/sync"]))
    return dev, des, build_layout(dev)


def _rect(dev, lay, site):
    return lay.site_rect[dev.site_index()[site]]


def _tile(dev, lay, site):
    return lay.tile_rect[dev.sites[dev.site_index()[site]].tile]


def test_ps_and_vcu_fill_their_area(zuplus):
    dev, _, lay = zuplus
    ps, vcu = _tile(dev, lay, "PS8_X0Y0"), _tile(dev, lay, "VCU_X0Y0")
    assert ps[1] == lay.row_y[0] and ps[3] == lay.row_y[8]      # three regions tall
    assert vcu[1] == lay.row_y[8] and vcu[3] == lay.row_y[12]
    assert ps[2] == vcu[2] == lay.col_x[6]                      # out to the fabric
    # the clock row next to the PS does not cut it in strips
    assert _rect(dev, lay, "BUFG_PS_X0Y0")[0] >= lay.col_x[6]


def test_large_sites_are_labeled(zuplus, tmp_path):
    from test_design import _page_meta

    from fpga_design_implview.html import write_html
    from fpga_design_implview.placement import build_placement, group_cells
    from fpga_design_implview.render import site_labels
    dev, des, lay = zuplus
    names = [n for n, _ in site_labels(dev, lay)]
    assert {"PS8", "VCU"} <= set(names) and "BUFG_PS" not in names and "SLICE" not in names
    assert "PS8" not in [n for n, _ in site_labels(dev, lay, {"PS8"})]    # a group says it
    groups, cg, _ = group_cells(des)
    write_html(str(tmp_path / "z.html"), dev, lay, des, build_placement(dev, des, groups, cg))
    assert [n for n, _ in _page_meta((tmp_path / "z.html").read_text())["site_labels"]] == names


def test_utilization(zuplus):
    from fpga_design_implview.placement import build_placement, group_cells
    dev, des, _ = zuplus
    groups, cg, _ = group_cells(des)
    util = {k: (u, t) for k, u, t in build_placement(dev, des, groups, cg).utilization}
    assert util["BUFG"] == (1, 5)       # BUFG_GT_SYNC is not a clock buffer
    assert util["GT"] == (0, 2)         # bonded channels
    assert util["VCU"] == (0, 1)
    assert "PCIe" not in util and "RF ADC" not in util


def test_rfsoc_ps_outside_the_clock_regions(tmp_path):
    """8 x 8: the PS8 tile in column 1, left of the clock regions; the PS area
    is the lower half of columns 0..4, fabric above it and to the right."""
    from fpga_design_implview.layout import build_layout
    t = {(1, 7): ("PSS_ALTO", [("PS8_X0Y0", "PS8", False)])}
    for x in (3, 5, 7):
        _column(t, x, "INT", "", range(8) if x > 3 else range(4))
    for x in (2, 4, 6):
        for y in (range(8) if x > 4 else range(4)):
            t[x, y] = ("CLEL_R", [(f"SLICE_X{x}Y{7 - y}", "SLICEL", False)])
    regions = [("X0Y1", 2, 0, 4, 3), ("X1Y1", 5, 0, 7, 3), ("X0Y0", 2, 4, 4, 7), ("X1Y0", 5, 4, 7, 7)]
    dev = _device(tmp_path / "rf.ivdev.gz", 8, 8, t, regions)
    lay = build_layout(dev)
    ps = _tile(dev, lay, "PS8_X0Y0")
    assert ps[1] == lay.row_y[4] and ps[2] == lay.col_x[5]


def test_io_bank_and_clocking_column(tmp_path):
    """4 x 20: an HP IO bank (column 0) and an UltraScale XIPHY column with
    the MMCM and seven kinds of small sites (column 2), INT in 1 and 3."""
    from fpga_design_implview.layout import build_layout

    def sites(prefix, n, st=None):
        return [(f"{prefix}_X0Y{k}", st or prefix, False) for k in range(n)]
    t = {(0, 19): ("HPIO_L", sites("IOB", 8, "HPIOB") + sites("HPIOBDIFFINBUF", 8)
                   + sites("HPIOBDIFFOUTBUF", 8) + sites("HPIO_VREF_SITE", 1)),
         (2, 19): ("XIPHY_L", sum((sites(p, 8) for p in (
             "BITSLICE_RX_TX", "BITSLICE_TX", "BITSLICE_CONTROL", "BUFCE_ROW", "BUFGCE",
             "BUFGCTRL", "PLL_SELECT_SITE")), []) + sites("MMCM", 1, "MMCME3_ADV")
                   + sites("RIU_OR", 1))}
    for x in (1, 3):
        for y in range(20):
            t[x, y] = ("INT", [])
    dev = _device(tmp_path / "io.ivdev.gz", 4, 20, t, [("X0Y0", 0, 0, 3, 19)])
    lay = build_layout(dev)
    iob = np.array([_rect(dev, lay, f"IOB_X0Y{k}") for k in range(8)])
    vref = _rect(dev, lay, "HPIO_VREF_SITE_X0Y0")
    assert vref[2] - vref[0] < (iob[:, 2] - iob[:, 0]).min()   # a strip, not the bank
    assert iob[:, 1].min() < lay.row_y[1] and iob[:, 3].max() > lay.row_y[19]
    mmcm, riu = _rect(dev, lay, "MMCM_X0Y0"), _rect(dev, lay, "RIU_OR_X0Y0")
    tile = lay.col_x[3] - lay.col_x[2]
    assert mmcm[2] - mmcm[0] > 0.3 * tile and mmcm[3] - mmcm[1] > 0.9 * lay.height
    assert riu[0] >= mmcm[2]
