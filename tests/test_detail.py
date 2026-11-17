"""--detail export: LUT equations from INIT, cell/net/route records, the viewer data."""

import gzip
import random

import pytest

from fpga_design_implview import lut
from fpga_design_implview.design import load_design
from fpga_design_implview.placement import build_placement, group_cells

from conftest import H, _design_lines, _write
from test_design import _page_meta


# ---- LUT equations

def _same(init, n):
    eq = lut.equation(init, n)
    for k in range(1 << n):
        assert lut.eval_sop(eq, n, k) == bool((init >> k) & 1), (hex(init), n, eq, k)
    return eq


def test_parse_init():
    assert lut.parse_init("8'hB8") == (0xB8, 8)
    assert lut.parse_init("64'h0000_0000_FFFF_0000") == (0xFFFF0000, 64)
    assert lut.parse_init("4'b0110") == (6, 4)
    assert lut.parse_init("0x8") == (8, 4)


def test_mux_lut3_real_fixture():
    # Vivado LUT3 8'hB8 (the 2:1 mux of the Artix-7 test design): minterms 3, 4, 5, 7
    assert lut.minterms(0xB8, 3) == [3, 4, 5, 7]
    eq = _same(0xB8, 3)
    assert eq in ("(I0 & I1) | (~I1 & I2)", "(~I1 & I2) | (I0 & I1)")


def test_constants_and_simple_gates():
    assert lut.equation(0, 4) == "0"
    assert lut.equation((1 << 16) - 1, 4) == "1"
    assert lut.equation(0x2, 1) == "I0"
    assert lut.equation(0x1, 1) == "~I0"
    assert lut.equation(0x8, 2) == "I0 & I1"
    assert _same(0x6, 2).count("|") == 1                    # xor: two terms
    assert lut.equation(0xAAAAAAAAAAAAAAAA, 6) == "I0"      # depends on I0 only


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_random_inits_are_equivalent(n):
    rnd = random.Random(n)
    for _ in range(40 if n < 6 else 12):
        _same(rnd.getrandbits(1 << n), n)


def test_lut_inputs_only_plain_luts():
    assert lut.lut_inputs("LUT6") == 6 and lut.lut_inputs("LUT1") == 1
    for ref in ("SRL16E", "SRLC32E", "RAMD32", "CFGLUT5", "LUT6_2", "FDRE"):
        assert lut.lut_inputs(ref) is None


def test_physical_init_follows_the_pin_map():
    pm = lut.parse_pinmap("I0:A6 I1:A4 I2:A3")
    assert pm == {0: 6, 1: 4, 2: 3}
    phys = lut.physical_init(0xB8, 3, pm)
    for k in range(64):
        a = [(k >> (j - 1)) & 1 for j in range(1, 7)]       # a[0] = A1
        lk = a[5] | (a[3] << 1) | (a[2] << 2)
        assert (phys >> k) & 1 == (0xB8 >> lk) & 1
    # identity map: the INIT replicated over the unused pins
    assert lut.physical_init(0x6, 2, {0: 1, 1: 2}) & 0xF == 0x6
    assert lut.physical_init(0x6, 2, {0: 1, 1: 2}) == int("6" * 16, 16)


# ---- export records

DETAIL = [
    # cells: 0 lut0, 1 ff0 (order of the U records)
    "C\t0\tINIT=64'hAAAAAAAAAAAAAAAA\tPINMAP=I0:A3 I1:A1",
    "C\t1\tINIT=1'b0\tIS_C_INVERTED=1'b0",
    "C\t6\tRAM_MODE=TDP\tREAD_WIDTH_A=18",
    "W\t0\tclk_BUFG\tGLOBAL_CLOCK\t2\t1",
    "Q\t0\t9:O\t1:C\t4:C",
    f"G\t0\tCLK_BUFG_X2Y0 INT_L_X1Y{H - 1 - 2}\tINT_L_X1Y{H - 1 - 2} CLBLM_L_X2Y{H - 1 - 2}",
    "I\t0\tCLK_BUFG_X2Y0/CLK_BUFG.A->>B",
    "W\t1\tu_top/u_a/q\tSIGNAL\t1\t2",
    "Q\t1\t1:Q\t0:I0",
    "W\t2\tdin_IBUF\tSIGNAL\t1\t1",
    "Q\t2\t@din\t5:I0",
    "H\t3\t2\t20000\t1",
]


@pytest.fixture
def detailed(tmp_path, files):
    des = tmp_path / "testtop.ivd.gz"
    _write(des, _design_lines() + DETAIL)
    return files[0], des


def test_detail_records(detailed):
    d = load_design(detailed[1])
    assert d.detail[0]["INIT"] == "64'hAAAAAAAAAAAAAAAA"
    assert d.detail[0]["PINMAP"] == "I0:A3 I1:A1"
    assert d.detail[6]["RAM_MODE"] == "TDP"
    assert [n.name for n in d.nets] == ["clk_BUFG", "u_top/u_a/q", "din_IBUF"]
    clk = d.nets[0]
    assert clk.is_clock and clk.fanout == 2 and clk.routed == 1
    assert clk.pins[0] == "9:O" and len(clk.segs) == 2 and clk.pips
    assert d.nets[1].routed == 2 and not d.nets[1].segs        # over the cap
    assert d.net_summary == {"nets": 3, "routed": 2, "cap": 20000, "pips": 1}
    pn = d.pin_nets()
    assert pn[(1, "C")] == 0 and pn[(0, "I0")] == 1 and pn[(5, "I0")] == 2
    assert "C" not in d.info and "W" not in d.info


def test_html_detail(detailed, tmp_path):
    from fpga_design_implview.device import load_device
    from fpga_design_implview.html import write_html
    from fpga_design_implview.layout import build_layout
    dev, des = load_device(detailed[0]), load_design(detailed[1])
    lay = build_layout(dev)
    groups, cg, _ = group_cells(des)
    write_html(str(tmp_path / "d.html"), dev, lay, des, build_placement(dev, des, groups, cg))
    meta = _page_meta((tmp_path / "d.html").read_text())
    det = meta["detail"]
    luts = [c for c in det["cells"].values() if c.get("k") == "lut"]
    assert any(c.get("eq") == "I0" and c["pm"] == {"I0": "A3", "I1": "A1"} for c in luts)
    ffs = [c for c in det["cells"].values() if c.get("k") == "ff"]
    assert any(["C", 0] in c.get("n", []) for c in ffs)       # clock pin -> net 0
    assert [n[0] for n in det["nets"]] == ["clk_BUFG", "u_top/u_a/q", "din_IBUF"]
    assert det["nets"][2][6] == "@din"                          # port driver
    assert meta["arrays"]["nseg"][2] == 2 * 4                   # two segments, 4 coords
    # the setup path goes ff0/Q -> lut0/I2 ...: no net on I2 here, but ff0/C has one
    assert len(det["pnets"]) == len(meta["tpaths"])


def test_html_without_detail_has_no_detail(loaded, tmp_path):
    from fpga_design_implview.html import write_html
    dev, des, lay = loaded
    groups, cg, _ = group_cells(des)
    write_html(str(tmp_path / "a.html"), dev, lay, des, build_placement(dev, des, groups, cg))
    assert _page_meta((tmp_path / "a.html").read_text())["detail"] is None


def test_detail_option_in_the_cache_key(tmp_path, monkeypatch):
    import zipfile
    from fpga_design_implview import cli
    run = tmp_path / "impl_1"
    run.mkdir()
    xml = ('<?xml version="1.0"?><Checkpoint Version="26"><PRODUCT Name="Vivado v2026.1 (64-bit)"/>'
           '<Part Name="xc7test-pkg-1"/><Top Name="t"/></Checkpoint>')
    with zipfile.ZipFile(run / "t_routed.dcp", "w") as z:
        z.writestr("dcp.xml", xml)
    monkeypatch.setenv("IMPLVIEW_CACHE", str(tmp_path / "cache"))
    calls = []
    monkeypatch.setattr(cli, "_atomic_run", lambda outputs, args, a, v: calls.append(args))
    monkeypatch.setattr(cli, "load_design", lambda p: (_ for _ in ()).throw(SystemExit(0)))
    for argv in (["export", str(run), "--detail", "--nets", "7"], ["export", str(run)]):
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert "-detail" in calls[0] and calls[0][calls[0].index("-nets") + 1] == "7"
    assert "-detail" not in calls[1]
    assert calls[0][2] != calls[1][2]                           # separate cache entries


# ---- crosscheck helpers

def test_parse_fasm():
    from fpga_design_implview.crosscheck import parse_fasm
    f = parse_fasm("A_X1Y1.SLICEL_X0.ALUT.INIT[31:0] = 32'b1010\n"
                   "A_X1Y1.SLICEL_X0.ALUT.INIT[63:32] = 32'b1\n"
                   "INT_L_X0Y0.EE2BEG0.LOGIC_OUTS_L4\n# comment\n"
                   "A_X1Y1.SLICEL_X0.AFF.ZINI\n")
    assert f["A_X1Y1.SLICEL_X0.ALUT.INIT"] == 0b1010 | (1 << 32)
    assert f["INT_L_X0Y0.EE2BEG0.LOGIC_OUTS_L4"] == 1 and f["A_X1Y1.SLICEL_X0.AFF.ZINI"] == 1


def test_find_db_maps_vivado_part_names(tmp_path):
    from fpga_design_implview.crosscheck import find_db
    (tmp_path / "artix7" / "xc7a35tcsg324-1").mkdir(parents=True)
    fam, part = find_db("artix7", "xc7a35ticsg324-1L", str(tmp_path))
    assert part == "xc7a35tcsg324-1" and fam.endswith("artix7")
    assert find_db("zynquplus", "xczu2cg-sbva484-1-e", str(tmp_path)) == (None, None)


def test_compare_pips_and_luts(tmp_path, detailed):
    from fpga_design_implview.crosscheck import compare
    from fpga_design_implview.device import load_device
    fam = tmp_path / "db"
    fam.mkdir()
    (fam / "segbits_clk_bufg.db").write_text("CLK_BUFG.B.A 1_1\n")
    dev, des = load_device(detailed[0]), load_design(detailed[1])
    rep = compare(dev, des, {"CLK_BUFG_X2Y0.B.A": 1}, str(fam))
    assert rep["pips"]["vivado_pips_with_bits"] == 1
    assert rep["pips"]["vivado_found_in_fasm_pct"] == 100.0
    # lut0 (I0 on A3, INIT = I0): in FASM nothing -> a mismatch is reported
    assert rep["lut_init"]["compared"] >= 1 and rep["lut_init"]["mismatches"]
