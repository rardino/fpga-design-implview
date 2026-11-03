import numpy as np

from conftest import H


def rect_of(dev, lay, site):
    return lay.site_rect[dev.site_index()[site]]


def tile_rect(dev, lay, tile_type, gx, gy):
    for t, r in lay.tile_rect.items():
        if dev.tile_type[t] == tile_type and dev.gx[t] == gx and dev.gy[t] == gy:
            return r
    raise KeyError((tile_type, gx, gy))


def test_load(loaded):
    dev, des, lay = loaded
    assert dev.part == "xc7test-pkg-1"
    assert dev.width == 12 and dev.height == H
    assert dev.pins["IOB_X0Y4"] == "A1"
    assert des.top == "testtop" and des.slack("wns") == 0.123 and des.slack("whs") == -0.010


def test_tall_tiles_claim_the_null_points_above(loaded):
    dev, _, lay = loaded
    # BRAM at row 4 covers rows 0..4, the one at row 9 rows 5..9
    assert tile_rect(dev, lay, "BRAM_L", 4, 4)[1] == lay.row_y[0]
    assert tile_rect(dev, lay, "BRAM_L", 4, 9)[1] == lay.row_y[5]
    assert tile_rect(dev, lay, "BRAM_L", 4, 9)[3] == lay.row_y[10]
    # IOB pairs are two rows tall
    r = tile_rect(dev, lay, "LIOB", 0, 3)
    assert (r[1], r[3]) == (lay.row_y[2], lay.row_y[4])


def test_hard_block_takes_its_null_area(loaded):
    dev, _, lay = loaded
    r = tile_rect(dev, lay, "PSS2", 8, 7)
    assert r == (lay.col_x[6], lay.row_y[0], lay.col_x[12], lay.row_y[8])
    # the PS7 site gets most of it, the pads a strip on the left
    ps = rect_of(dev, lay, "PS7_X0Y0")
    pad = rect_of(dev, lay, "IOPAD_X1Y0")
    assert pad[2] <= ps[0]
    assert (ps[2] - ps[0]) * (ps[3] - ps[1]) > 0.5 * (r[2] - r[0]) * (r[3] - r[1])


def test_widths_and_rows(loaded):
    dev, _, lay = loaded
    w = np.diff(lay.col_x)
    assert w[2] == 2.0                  # two slices side by side
    assert w[1] < w[2] and w[3] < w[2]  # INT columns are narrow
    h = np.diff(lay.row_y)
    assert h[H - 1] < h[0]              # clock row without interconnect is thin


def test_sites(loaded):
    dev, _, lay = loaded
    names = [s.name for s in dev.sites]
    # TIEOFF is never drawn, everything else is
    for n, r in zip(names, lay.site_rect):
        assert np.isfinite(r[0]) == (not n.startswith("TIEOFF")), n
    # slices of one tile side by side, Y0 at the bottom
    a, b = rect_of(dev, lay, "SLICE_X0Y1"), rect_of(dev, lay, "SLICE_X1Y1")
    assert a[2] <= b[0] and a[1] == b[1]
    assert rect_of(dev, lay, "SLICE_X0Y1")[1] > rect_of(dev, lay, "SLICE_X0Y2")[1]
    # RAMB36 covers its two RAMB18 and is drawn only when used
    big = rect_of(dev, lay, "RAMB36_X0Y0")
    lo, hi = rect_of(dev, lay, "RAMB18_X0Y0"), rect_of(dev, lay, "RAMB18_X0Y1")
    assert lay.site_overlay[dev.site_index()["RAMB36_X0Y0"]]
    assert big[1] <= hi[1] and big[3] >= lo[3] and hi[3] <= lo[1]


def test_clock_regions(loaded):
    dev, _, lay = loaded
    (cr, r), = lay.cr_rect
    assert cr.name == "X0Y0" and r == (0.0, 0.0, lay.width, lay.height)
