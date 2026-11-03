import gzip
import os
import zipfile

import pytest

from implview import hier
from implview.cli import main
from implview.dcp import dcp_info, find_dcp
from implview.design import Cell
from implview.placement import build_placement, group_cells


def cells(*names):
    return [Cell(n, "S", "A6LUT", "LUT6", "LUT") for n in names]


def test_auto_groups_walk_through_wrappers():
    cs = cells(*[f"top/core/a/x{i}" for i in range(50)], *[f"top/core/b/x{i}" for i in range(30)],
               *[f"top/core/c/d/x{i}" for i in range(20)])
    groups, cg = hier.auto_groups(cs, max_groups=8)
    assert [g.label for g in groups] == ["a", "b", "c/d"] or [g.name for g in groups] == \
        ["top/core/a", "top/core/b", "top/core/c/d"]
    assert [g.cells for g in groups] == [50, 30, 20]
    assert cg[0] == 0 and cg[-1] == 2


def test_auto_groups_split_the_largest_first():
    big = [f"core/dsp/m{k}/x{i}" for k in range(10) for i in range(100)]
    small = [f"io/p{k}/x{i}" for k in range(5) for i in range(20)]
    groups, _ = hier.auto_groups(cells(*big, *small), max_groups=8)
    names = [g.name for g in groups]
    assert len([n for n in names if n != hier.OTHER]) <= 8
    assert sum(n.startswith("core/dsp/m") for n in names) >= 6
    assert "io" in names


def test_pattern_and_depth_groups():
    cs = cells("a/x/1", "a/y/2", "b/3", "c")
    groups, cg = hier.pattern_groups(cs, ["a/x", "b*"])
    assert [g.name for g in groups] == ["a/x", "b*", hier.OTHER]
    assert cg == [0, 2, 1, 2]
    groups, cg = hier.depth_groups(cs, 1)
    assert {g.name for g in groups} == {"a", "b", ""}


def test_short_labels_keep_generic_parents():
    gs = [hier.Group("x/xbar/inst", "x/xbar/inst"), hier.Group("x/xbar/inst/s00", "x/xbar/inst/s00")]
    assert hier.short_labels(gs) == ["xbar/inst", "s00"]


def test_placement_and_utilization(loaded):
    dev, des, lay = loaded
    groups, cg = group_cells(des)
    p = build_placement(dev, des, groups, cg)
    assert p.missing == 0
    si = dev.site_index()
    assert p.site_group[si["SLICE_X0Y1"]] >= 0
    assert p.site_group[si["SLICE_X0Y5"]] == -1
    util = {k: (u, t) for k, u, t in p.utilization}
    assert util["LUT"][0] == 4                  # four LUT positions used
    assert util["FF"][0] == 2
    assert util["BRAM"] == (1.5, 2)             # one RAMB36 + one RAMB18
    assert util["IOB"][0] == 1


def test_outputs(loaded, tmp_path):
    from implview.html import write_html
    from implview.render import write_outputs
    dev, des, lay = loaded
    groups, cg = group_cells(des)
    p = build_placement(dev, des, groups, cg)
    out = write_outputs([str(tmp_path / "a.png"), str(tmp_path / "a.pdf"), str(tmp_path / "a.svg")],
                        dev, lay, des, p)
    # routing data gives a second page
    assert sorted(os.path.basename(x) for x in out) == ["a.pdf", "a.png", "a.svg",
                                                         "a_routing.png", "a_routing.svg"]
    for x in out:
        assert os.path.getsize(x) > 1000
    with open(tmp_path / "a.png", "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    write_html(str(tmp_path / "a.html"), dev, lay, des, p)
    page = (tmp_path / "a.html").read_text()
    assert "DecompressionStream" in page and "testtop" in page


def test_dcp_info_and_find(tmp_path):
    run = tmp_path / "proj" / "proj.runs" / "impl_1"
    run.mkdir(parents=True)
    xml = ('<?xml version="1.0"?><Checkpoint Version="26"><PRODUCT Name="Vivado v2026.1 (64-bit)"/>'
           '<Part Name="xcvu37p-fsvh2892-2L-e"/><Top Name="mytop"/></Checkpoint>')
    for name in ("mytop_opt.dcp", "mytop_routed.dcp"):
        with zipfile.ZipFile(run / name, "w") as z:
            z.writestr("dcp.xml", xml)
    dcp = find_dcp(str(tmp_path / "proj"))
    assert dcp.endswith("mytop_routed.dcp")
    assert find_dcp(str(run)) == dcp
    info = dcp_info(dcp)
    assert info == {"part": "xcvu37p-fsvh2892-2L-e", "top": "mytop",
                    "product": "Vivado v2026.1 (64-bit)", "vivado": "2026.1"}
    with pytest.raises(FileNotFoundError):
        find_dcp(str(tmp_path / "nothing"))


def test_cli_render_from_export(files, tmp_path, monkeypatch, capsys):
    dev, des = files
    monkeypatch.setenv("IMPLVIEW_CACHE", str(tmp_path / "cache"))
    os.makedirs(tmp_path / "cache" / "devices")
    with gzip.open(dev) as a, open(tmp_path / "cache" / "devices" / "xc7test-pkg-1.ivdev.gz", "wb") as b:
        b.write(gzip.compress(a.read()))
    main(["render", str(des), "-O", str(tmp_path / "out"), "--theme", "dark"])
    written = capsys.readouterr().out.split()
    assert [os.path.basename(w) for w in written] == ["testtop.png", "testtop_routing.png", "testtop.pdf"]
