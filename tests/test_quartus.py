"""A small compiled Quartus project, written as Quartus writes it (no Quartus
needed): settings, Fitter report, timing summary and post-fit netlist.

Grid, 12 columns x 10 rows (row 0 at the bottom), locations in use:

  col 0      a JTAG block, a PLL part, an ADC block the design leaves unused
  col 1..3   LABs                      col 4   M10K, rows 2 and 7
  col 5      DSP, rows 1, 3, 5 (two rows each)
  col 6      MLABs                     col 7   LABs
  col 8..10  the hard processor, rows 6..8
  col 11     a register of the periphery
  rows 0, 9  pads
"""

import gzip
import os

import numpy as np
import pytest

from fpga_design_implview import altera, quartus
from fpga_design_implview.cli import main
from fpga_design_implview.design import load_design
from fpga_design_implview.device import load_device
from fpga_design_implview.layout import build_layout
from fpga_design_implview.placement import build_placement, group_cells

ATOMS = [
    # location, atom type, name
    ("IOIBUF_X2_Y0_N1", "cyclonev_io_ibuf", "clk~input"),
    ("IOOBUF_X2_Y0_N19", "cyclonev_io_obuf", "led[0]~output"),
    ("IOIBUF_X5_Y0_N1", "cyclonev_io_ibuf", "rst_n~input"),
    ("IOOBUF_X3_Y9_N2", "cyclonev_io_obuf", "led[1]~output"),
    ("DDIOOUTCELL_X3_Y9_N4", "dffeas", "u_core|led_reg"),
    ("CLKCTRL_G4", "cyclonev_clkena", "clk~inputCLKENA0"),
    ("JTAG_X0_Y2_N3", "cyclonev_jtag", "altera_internal_jtag"),
    ("FRACTIONALPLL_X0_Y1_N0", "cyclonev_fractional_pll", "u_pll|gpll~FRACTIONAL_PLL"),
    ("ADCBLOCK_X0_Y7_N0", "cyclonev_adcblock", "~QUARTUS_CREATED_ADC1~"),
    ("LABCELL_X1_Y1_N0", "cyclonev_lcell_comb", "u_core|u_cnt|Add0~1"),
    ("FF_X1_Y1_N2", "dffeas", "u_core|u_cnt|count[0]"),
    ("LABCELL_X1_Y4_N3", "cyclonev_lcell_comb", "u_core|u_cnt|Add0~5"),
    ("LABCELL_X2_Y2_N0", "cyclonev_lcell_comb", "u_core|u_fsm|state~0"),
    ("FF_X3_Y6_N5", "dffeas", "u_core|u_fsm|state.IDLE"),
    ("M10K_X4_Y2_N0", "cyclonev_ram_block", "u_core|u_fifo|mem|ram_block1a0"),
    ("M10K_X4_Y7_N0", "cyclonev_ram_block", "u_core|u_fifo|mem|ram_block1a8"),
    ("DSP_X5_Y1_N0", "cyclonev_mac", "u_core|u_mul|Mult0~mac"),
    ("DSP_X5_Y3_N0", "cyclonev_mac", "u_core|u_mul|Mult1~mac"),
    ("DSP_X5_Y5_N0", "cyclonev_mac", "u_core|u_mul|Mult2~mac"),
    ("MLABCELL_X6_Y3_N0", "cyclonev_lcell_comb", "u_core|u_fifo|wr_ptr~0"),
    ("FF_X6_Y1_N1", "dffeas", "u_core|u_fifo|wr_ptr[0]"),
    ("LABCELL_X7_Y1_N0", "cyclonev_lcell_comb", "u_io|sync~0"),
    ("LABCELL_X7_Y3_N6", "cyclonev_lcell_comb", "~QUARTUS_CREATED_GND~I"),
    ("HPSINTERFACEHPS2FPGA_X8_Y6_N111", "cyclonev_hps_interface_hps2fpga", "u_hps|hps2fpga"),
    ("HPSPERIPHERALUART_X10_Y8_N111", "cyclonev_hps_peripheral_uart", "u_hps|uart0_inst"),
    ("FF_X11_Y5_N3", "dffeas", "u_io|pad_reg"),
]
PINS = {"clk": "M9", "led[0]": "AA2", "led[1]": "B7", "rst_n": "U7"}

FIT_RPT = """Fitter report for blinky
Tue Mar  5 10:19:00 2024
Quartus Prime Version 18.1.0 Build 625 09/12/2018 SJ Lite Edition


+----------------------------------------------------------------------------+
; Fitter Summary                                                             ;
+---------------------------------+------------------------------------------+
; Fitter Status                   ; Successful - Tue Mar  5 10:19:00 2024    ;
; Quartus Prime Version           ; 18.1.0 Build 625 09/12/2018 SJ Lite Edition ;
; Revision Name                   ; blinky                                   ;
; Top-level Entity Name           ; blinky_top                               ;
; Family                          ; Cyclone V                                ;
; Device                          ; 5CEBA4F23C7                              ;
; Logic utilization (in ALMs)     ; 9 / 18,480 ( < 1 % )                     ;
+---------------------------------+------------------------------------------+


+--------------------------------------------------------------------------------------+
; Fitter Resource Usage Summary                                                        ;
+-------------------------------------------------------------+----------------+-------+
; Resource                                                    ; Usage          ; %     ;
+-------------------------------------------------------------+----------------+-------+
; Logic utilization (ALMs needed / total ALMs on device)      ; 9 / 18,480     ; < 1 % ;
; ALMs needed [=A-B+C]                                        ; 9              ;       ;
;     [A] ALMs used in final placement [=a+b+c+d]             ; 11 / 18,480    ; < 1 % ;
;                                                             ;                ;       ;
; Difficulty packing design                                   ; Low            ;       ;
; Total LABs:  partially or completely used                   ; 8 / 1,848      ; < 1 % ;
;     -- Logic LABs                                           ; 8              ;       ;
; Dedicated logic registers                                   ; 4              ;       ;
;     -- By type:                                             ;                ;       ;
;         -- Primary logic registers                          ; 4 / 36,960     ; < 1 % ;
; I/O pins                                                    ; 4 / 224        ; 2 %   ;
;     -- Clock pins                                           ; 1 / 9          ; 11 %  ;
; M10K blocks                                                 ; 2 / 308        ; < 1 % ;
; Total MLAB memory bits                                      ; 0              ;       ;
; Total block memory bits                                     ; 4,096 / 3,153,920 ; < 1 % ;
; Total block memory implementation bits                      ; 20,480 / 3,153,920 ; < 1 % ;
; Total DSP Blocks                                            ; 3 / 66         ; 5 %   ;
; Fractional PLLs                                             ; 1 / 4          ; 25 %  ;
; Average interconnect usage (total/H/V)                      ; 0.1% / 0.1% / 0.1% ;   ;
+-------------------------------------------------------------+----------------+-------+


+--------------------------------------------------------------------------------------------+
; Input Pins                                                                                 ;
+-------+-------+----------+--------------+--------------+--------------+--------------------+
; Name  ; Pin # ; I/O Bank ; X coordinate ; Y coordinate ; Z coordinate ; I/O Standard       ;
+-------+-------+----------+--------------+--------------+--------------+--------------------+
; clk   ; M9    ; 3B       ; 2            ; 0            ; 0            ; 2.5 V              ;
; rst_n ; U7    ; 3A       ; 5            ; 0            ; 0            ; 2.5 V              ;
+-------+-------+----------+--------------+--------------+--------------+--------------------+


+---------------------------------------------------------------------------------------------+
; Output Pins                                                                                 ;
+--------+-------+----------+--------------+--------------+--------------+--------------------+
; Name   ; Pin # ; I/O Bank ; X coordinate ; Y coordinate ; Z coordinate ; I/O Standard       ;
+--------+-------+----------+--------------+--------------+--------------+--------------------+
; led[0] ; AA2   ; 3B       ; 2            ; 0            ; 17           ; 2.5 V              ;
; led[1] ; B7    ; 8A       ; 3            ; 9            ; 0            ; 2.5 V              ;
+--------+-------+----------+--------------+--------------+--------------+--------------------+
"""

STA_SUMMARY = """------------------------------------------------------------
Timing Analyzer Summary
------------------------------------------------------------

Type  : Slow 1100mV 85C Model Setup 'clk'
Slack : 1.250
TNS   : 0.000

Type  : Slow 1100mV 85C Model Hold 'clk'
Slack : 0.310
TNS   : 0.000

Type  : Slow 1100mV 0C Model Setup 'u_pll|gpll~PLL_OUTPUT_COUNTER|divclk'
Slack : -0.420
TNS   : -3.100

Type  : Fast 1100mV 0C Model Hold 'clk'
Slack : 0.180
TNS   : 0.000

Type  : Slow 1100mV 85C Model Minimum Pulse Width 'clk'
Slack : -9.000
TNS   : 0.000
"""


def netlist(vhdl=False, date="03/05/2024 10:20:30", atoms=ATOMS):
    c = "--" if vhdl else "//"
    out = [f'{c} VENDOR "Altera"', f'{c} PROGRAM "Quartus Prime"',
           f'{c} VERSION "Version 18.1.0 Build 625 09/12/2018 SJ Lite Edition"', "",
           f'{c} DATE "{date}"', "", f"{c} ", f"{c} Device: Altera 5CEBA4F23C7 Package FBGA484",
           f"{c} ", ""]
    out += ["ENTITY \tblinky_top IS", "    PORT (", "\tclk : IN std_logic", "\t);", "END blinky_top;"] \
        if vhdl else ["`timescale 1 ps/ 1 ps", "", "module blinky_top (", "\tclk,", "\tled);"]
    out += [f"{c} {port}\t=>  Location: PIN_{pin},\t I/O Standard: 2.5 V,\t Current Strength: Default"
            for port, pin in PINS.items()]
    for loc, ref, name in atoms:
        plain = name.isidentifier()
        out += ["", f"{c} Location: {loc}"]
        if vhdl:
            out += [(name if plain else f"\\{name}\\") + f" : {ref}", "PORT MAP (", "\tclk => gnd);"]
        else:
            out += [f"{ref} {name}(" if plain else f"{ref} \\{name} (", "\t.clk(gnd));"]
    return "\n".join(out) + "\n"


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "blinky"
    (d / "output_files").mkdir(parents=True)
    (d / "simulation" / "modelsim").mkdir(parents=True)
    (d / "blinky.qpf").write_text('QUARTUS_VERSION = "18.1"\n\nPROJECT_REVISION = "blinky"\n'
                                  'PROJECT_REVISION = "blinky_debug"\n')
    (d / "blinky.qsf").write_text(
        'set_global_assignment -name FAMILY "Cyclone V"\n'
        "set_global_assignment -name DEVICE 5CEBA4F23C7\n"
        "set_global_assignment -name TOP_LEVEL_ENTITY blinky_top\n"
        "set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files\n"
        'set_global_assignment -name EDA_OUTPUT_DATA_FORMAT "VERILOG HDL" -section_id eda_simulation\n')
    (d / "blinky_debug.qsf").write_text("set_global_assignment -name DEVICE 5CEBA4F23C7\n")
    (d / "output_files" / "blinky.fit.rpt").write_text(FIT_RPT)
    (d / "output_files" / "blinky.sta.summary").write_text(STA_SUMMARY)
    (d / "simulation" / "modelsim" / "blinky.vo").write_text(netlist())
    return d


@pytest.fixture
def fitted(project):
    return quartus.read(quartus.find_project(str(project)))


def test_find_project(project, tmp_path):
    p = quartus.find_project(str(project))
    assert (p.revision, os.path.basename(p.netlist)) == ("blinky", "blinky.vo")
    assert p.fit.endswith("blinky.fit.rpt") and p.sta.endswith("blinky.sta.summary")
    for name in ("blinky.qpf", "blinky.qsf", os.path.join("simulation", "modelsim", "blinky.vo")):
        q = quartus.find_project(str(project / name))
        assert (q.revision, q.netlist, q.fit) == (p.revision, str(project / "simulation" / "modelsim" / "blinky.vo"), p.fit)
    # a revision that is not compiled has no netlist; a Vivado run is not a project
    assert quartus.find_project(str(project / "blinky_debug.qsf")).netlist == ""
    assert quartus.find_project(str(tmp_path)) is None
    assert quartus.find_project(str(tmp_path / "top_routed.dcp")) is None


def test_newest_netlist_by_its_header(project):
    # a VHDL netlist of a later compilation wins, whatever the file times say
    vho = project / "simulation" / "modelsim" / "blinky.vho"
    vho.write_text(netlist(vhdl=True, date="03/06/2024 08:00:00"))
    os.utime(vho, (0, 0))
    assert quartus.find_project(str(project)).netlist == str(vho)
    vho.write_text(netlist(vhdl=True, date="03/04/2024 08:00:00"))
    assert quartus.find_project(str(project)).netlist.endswith("blinky.vo")


def test_read(fitted):
    assert fitted.info == {"part": "5CEBA4F23C7", "family": "Cyclone V", "top": "blinky_top",
                           "quartus": "18.1.0 Lite", "date": "2024-03-05 10:19:00",
                           "revision": "blinky", "wns": "-0.420", "whs": "0.180"}
    assert len(fitted.atoms) == len(ATOMS)
    assert ("LABCELL", 1, 4, 3, "cyclonev_lcell_comb", "u_core|u_cnt|Add0~5") in fitted.atoms
    assert ("JTAG", 0, 2, 3, "cyclonev_jtag", "altera_internal_jtag") in fitted.atoms
    assert ("CLKCTRL_G4", -1, -1, 0, "cyclonev_clkena", "clk~inputCLKENA0") in fitted.atoms
    assert fitted.pads[(2, 0, 17)] == ("AA2", "3B", "led[0]")
    assert fitted.utilization == [("ALM", 9, 18480), ("LAB", 8, 1848), ("I/O pin", 4, 224),
                                  ("M10K", 2, 308), ("Memory bit", 4096, 3153920),
                                  ("DSP", 3, 66), ("PLL", 1, 4)]
    assert fitted.warnings == []


def test_vhdl_netlist_and_no_reports(tmp_path):
    path = tmp_path / "blinky.vho"
    path.write_text(netlist(vhdl=True))
    info, atoms, ports = quartus.read_netlist(str(path))
    assert info == {"quartus": "18.1.0 Lite", "date": "2024-03-05 10:20:30",
                    "part": "5CEBA4F23C7", "top": "blinky_top"}
    assert ports == PINS
    assert ("FF", 1, 1, 2, "dffeas", "u_core|u_cnt|count[0]") in atoms
    assert ("JTAG", 0, 2, 3, "cyclonev_jtag", "altera_internal_jtag") in atoms
    # the netlist alone: no utilization, the pins from the port names
    fit = quartus.read(quartus.find_project(str(path)))
    assert fit.utilization == [] and "no Fitter report" in fit.warnings[0]
    assert fit.pads[(2, 0, 17)] == ("AA2", "", "led[0]")
    assert "wns" not in fit.info


def test_stale_netlist_and_missing_netlist(project):
    vo = project / "simulation" / "modelsim" / "blinky.vo"
    vo.write_text(netlist(date="03/01/2024 09:00:00"))
    fit = quartus.read(quartus.find_project(str(project)))
    assert "older than the Fitter report" in fit.warnings[0]
    vo.unlink()
    with pytest.raises(quartus.QuartusError, match="quartus_eda <project> -c blinky"):
        quartus.read(quartus.find_project(str(project)))
    vo.write_text("// no atoms here\nmodule blinky_top ();\nendmodule\n")
    with pytest.raises(quartus.QuartusError, match="not a post-fit netlist"):
        quartus.read(quartus.find_project(str(project)))


def test_slacks_from_the_report(tmp_path):
    rpt = tmp_path / "blinky.sta.rpt"
    rpt.write_text("""
+--------------------------------------+
; Slow 1200mV 85C Model Setup Summary  ;
+-------+--------+---------------------+
; Clock ; Slack  ; End Point TNS       ;
+-------+--------+---------------------+
; clk   ; -3.210 ; -45.200             ;
; clk2  ; 0.400  ; 0.000               ;
+-------+--------+---------------------+

+-------------------------------------+
; Fast 1200mV 0C Model Hold Summary   ;
+-------+-------+---------------------+
; Clock ; Slack ; End Point TNS       ;
+-------+-------+---------------------+
; clk   ; 0.187 ; 0.000               ;
+-------+-------+---------------------+
""")
    assert quartus.read_slacks(str(rpt)) == (-3.21, 0.187)
    (tmp_path / "none.sta.summary").write_text("Timing Analyzer Summary\n")
    assert quartus.read_slacks(str(tmp_path / "none.sta.summary")) == (None, None)


def test_map(fitted):
    m = altera.Map(fitted)
    t = m.tiles
    assert m.offgrid == ["CLKCTRL_G4"]
    assert (t[(1, 1)], t[(4, 2)], t[(5, 3)], t[(2, 0)], t[(0, 2)]) == ("LAB", "M10K", "DSP", "IO", "JTAG")
    # a column of MLABs, also where the design placed only a register
    assert t[(6, 3)] == t[(6, 1)] == t[(6, 2)] == "MLAB"
    # unused blocks inside the staircase around the placement, nothing outside
    rows = lambda col, kind: {y for (x, y), k in t.items() if x == col and k == kind}
    assert rows(1, "LAB") == {1, 2, 3, 4}
    assert rows(3, "LAB") == {1, 2, 3, 4, 5, 6}
    assert rows(4, "M10K") == {1, 2, 3, 4, 5, 6, 7}
    assert rows(7, "LAB") == {1, 2, 3}
    # DSP blocks are two rows tall: the rows in between belong to them
    assert rows(5, "DSP") == {1, 3, 5}
    assert all(m.null[(5, y)] == "NULL" for y in (2, 4, 6))
    # the hard processor: one tile and one site over its bounding box
    assert t[(8, 6)] == "HPS" and (10, 8) not in t
    assert sum(1 for k in m.null.values() if k == "HPS_NULL") == 8
    # a register of the periphery is not a LAB
    assert t[(11, 5)] == "FF"
    assert m.counts() == {"LAB": 20, "M10K": 7, "DSP": 3, "I/O pin": 4, "HPS": 1, "JTAG": 1,
                          "FRACTIONALPLL": 1, "ADCBLOCK": 1, "FF": 1}
    assert m.summary(fitted.utilization) == "LAB 20 of 1,848, M10K 7 of 308, DSP 3 of 66, I/O pin 4 of 224"
    assert m.summary([("Multiplier 9-bit", 2, 30)]) == "LAB 20, M10K 7, DSP 3 of 15, I/O pin 4"

    cells = {c[4]: c for c in m.cells}
    assert cells["u_core/u_cnt/count[0]"] == ("LAB_X1_Y1", "FF_N2", "dffeas", "FLOP_LATCH", "u_core/u_cnt/count[0]")
    assert cells["u_core/u_fifo/mem/ram_block1a8"][:2] == ("M10K_X4_Y7", "M10K_N0")
    assert cells["u_hps/uart0_inst"][:2] == ("HPS", "HPSPERIPHERALUART_N111")
    # pads by pin; the cells next to a pad go with it
    assert (cells["clk~input"][0], cells["led[0]~output"][0]) == ("PIN_M9", "PIN_AA2")
    assert cells["u_core/led_reg"][0] == cells["led[1]~output"][0] == "PIN_B7"
    assert m.sites["PIN_AA2"][1:4] == ("IO", "bank 3B", "PIN")
    # the ADC is on the map, unused; the cell Quartus made in a LAB is a cell
    assert "ADCBLOCK_X0_Y7" in m.sites and "~QUARTUS_CREATED_ADC1~" not in cells
    assert cells["~QUARTUS_CREATED_GND~I"][0] == "LAB_X7_Y3"


@pytest.fixture
def exported(fitted, tmp_path):
    des, dev = tmp_path / "blinky.ivd.gz", tmp_path / "blinky.ivdev.gz"
    altera.write(fitted, str(des), str(dev))
    return load_device(str(dev)), load_design(str(des))


def test_files_and_layout(exported):
    dev, des = exported
    assert (dev.part, dev.info["family"], dev.note) == ("5CEBA4F23C7", "Cyclone V", "map inferred from the placement")
    assert (dev.width, dev.height) == (12, 10)
    assert (des.top, des.tool(), des.slack("wns"), des.unplaced()) == ("blinky_top", "Quartus 18.1.0 Lite", -0.42, 0)
    assert des.utilization[1] == ("LAB", 8, 1848)
    lay = build_layout(dev)
    si = dev.site_index()
    rect = lambda name: lay.site_rect[si[name]]
    # every site is drawn; a plain grid, memory and DSP columns wider
    assert np.isfinite(lay.site_rect).all()
    assert (np.diff(lay.row_y) == 1.0).all()
    w = np.diff(lay.col_x)
    assert (w[1], w[4], w[5], w[9]) == (1.0, 1.5, 1.5, 1.0)
    # row 0 at the bottom; a DSP block is two rows tall, a M10K one
    assert rect("LAB_X1_Y1")[1] > rect("LAB_X1_Y4")[1]
    height = lambda name: rect(name)[3] - rect(name)[1]
    assert 0.8 < height("M10K_X4_Y2") < 1.0 and 1.7 < height("DSP_X5_Y3") < 2.0
    # the two pads of a tile share it, by Z; the processor covers its 3 x 3 box
    a, b = rect("PIN_M9"), rect("PIN_AA2")
    assert a[0] == b[0] and b[3] <= a[1]
    hps = rect("HPS")
    assert hps[2] - hps[0] > 2.4 and hps[3] - hps[1] > 2.4


def test_placement_and_report_utilization(exported):
    dev, des = exported
    groups, cg, _ = group_cells(des)
    p = build_placement(dev, des, groups, cg)
    assert p.missing == 0
    si = dev.site_index()
    assert p.site_group[si["LAB_X1_Y1"]] >= 0 and p.site_group[si["LAB_X1_Y2"]] == -1
    assert p.site_group[si["ADCBLOCK_X0_Y7"]] == -1
    # counted by the Fitter, not from the map
    assert p.utilization == des.utilization
    assert any(g.name.startswith("u_core/") for g in groups)


def test_cli(project, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IMPLVIEW_CACHE", str(tmp_path / "cache"))
    main(["render", str(project), "-O", str(tmp_path / "out"), "-o", "b.png", "-o", "b.html", "--stats"])
    io = capsys.readouterr()
    assert [os.path.basename(w) for w in io.out.split()] == ["b.png", "b.html"]
    assert "5CEBA4F23C7, top blinky_top, Quartus 18.1.0 Lite" in io.err
    assert "LAB 20 of 1,848, M10K 7 of 308, DSP 3 of 66, I/O pin 4 of 224" in io.err
    assert "1 cell on a location without coordinates (CLKCTRL_*): not drawn" in io.err
    page = (tmp_path / "out" / "b.html").read_text()
    assert "blinky_top" in page
    # the second time from the cache, without reading the project again
    monkeypatch.setattr(quartus, "read", lambda p: pytest.fail("read again"))
    main(["render", str(project / "blinky.qpf"), "-O", str(tmp_path / "out"), "-o", "c.svg"])
    assert capsys.readouterr().err == ""
    monkeypatch.undo()

    # export: the design, the device map next to it; render from the export
    monkeypatch.setenv("IMPLVIEW_CACHE", str(tmp_path / "cache"))
    os.makedirs(tmp_path / "x")
    main(["export", str(project), "-o", str(tmp_path / "x" / "blinky.ivd.gz")])
    assert sorted(os.listdir(tmp_path / "x")) == ["blinky.ivd.gz", "blinky.ivdev.gz"]
    with gzip.open(tmp_path / "x" / "blinky.ivd.gz", "rt") as f:
        assert f.readline() == "#implview-design\t1\n"
    capsys.readouterr()
    main(["render", str(tmp_path / "x" / "blinky.ivd.gz"), "-O", str(tmp_path / "out"), "-o", "d.pdf"])
    assert capsys.readouterr().out.split() == [str(tmp_path / "out" / "d.pdf")]

    main(["info", str(project)])
    out = capsys.readouterr().out
    assert "revision blinky" in out and "part        5CEBA4F23C7" in out and "quartus     18.1.0 Lite" in out
