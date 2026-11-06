import colorsys
from pathlib import Path

import pytest
from matplotlib.colors import to_rgb

from fpga_design_implview import hier
from fpga_design_implview.cli import main
from fpga_design_implview.design import Cell
from fpga_design_implview.highlight import Entry, compile_glob, load_config, manual_groups, shades
from fpga_design_implview.placement import build_placement, group_cells
from fpga_design_implview.render import THEMES, _legend_rows, group_colors, placement_figure

PAL, PAL_DARK = THEMES["light"]["series"], THEMES["dark"]["series"]


def cells(*names):
    return [Cell(n, "S", "A6LUT", "LUT6", "LUT") for n in names]


def test_glob():
    rx = compile_glob("core/gen_ch[*].u_filter")
    assert rx.fullmatch("core/gen_ch[0].u_filter")
    assert rx.fullmatch("core/gen_ch[12].u_filter").groups() == ("12",)
    assert not rx.fullmatch("core/gen_ch[0].u_mixer")
    assert not compile_glob("core/*").fullmatch("core/a/b")
    assert compile_glob("core/**").fullmatch("core/a/b")
    assert compile_glob("**/sync_?").fullmatch("x/y/sync_3")


def test_shades():
    one = shades("#2a78d6", 4)
    light = [colorsys.rgb_to_hls(*to_rgb(c))[1] for c in one]
    assert len(set(one)) == 4 and light == sorted(light, reverse=True)
    two = shades(["#ffffff", "#000000"], 3)
    assert two == ["#ffffff", "#808080", "#000000"]
    assert shades("red", 1) == ["#ff0000"]


def test_manual_groups():
    cs = cells("core/dsp/a", "core/dsp/fir/b", "core/gen[2].u/c", "core/gen[10].u/d",
               "core/gen[1].u/e", "io/x/f", "io/x/rx_reg", "top_ff")
    entries = [Entry("core/dsp", "#2a78d6", label="dsp"), Entry("core/dsp/fir", "red"),
               Entry("core/gen[*].u", ["#ffffff", "#000000"], label="gen"),
               Entry("io/x/rx_*"), Entry("nothing/here")]
    groups, cg, unmatched = manual_groups(cs, entries, PAL, PAL_DARK)
    names = [g.label for g in groups]
    # generate instances in natural order, one shade each, labeled by index
    assert names == ["dsp", "core/dsp/fir", "gen 1", "gen 2", "gen 10", "io/x/rx_*", hier.OTHER]
    assert [g.color for g in groups[2:5]] == ["#ffffff", "#808080", "#000000"]
    # the most specific path holds the cell, whatever the order in the file
    assert cg[0] == 0 and cg[1] == 1
    assert cg[2] == 3 and cg[3] == 4 and cg[4] == 2
    # a path matching no instance is tried on cell names; uncolored ones get the palette
    assert cg[6] == 5 and groups[5].color == PAL[0]
    assert cg[5] == cg[7] == 6
    assert unmatched == ["nothing/here"]


def test_load_config(tmp_path):
    p = tmp_path / "h.toml"
    p.write_text('[[highlight]]\npath = "a/b"\ncolor = "orange"\nlabel = "B"\n'
                 '[[highlight]]\npath = "a/c[*]"\ncolor = ["#fff", "#000"]\ngradient = false\n'
                 '[options]\nothers = "hide"\ntheme = "dark"\nstats = true\n')
    cfg = load_config(p)
    assert [e.path for e in cfg.entries] == ["a/b", "a/c[*]"]
    assert cfg.entries[1].gradient is False
    assert cfg.options == {"others": "hide", "theme": "dark", "stats": True}
    for bad in ('[[highlight]]\npath = "a"\ncolour = "red"\n',
                '[[highlight]]\npath = "a"\ncolor = "notacolor"\n',
                '[[highlight]]\ncolor = "red"\n',
                '[options]\nothers = "maybe"\n'):
        p.write_text(bad)
        with pytest.raises(ValueError):
            load_config(p)


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.toml")),
                         ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_example_highlight_files(path):
    assert load_config(path).entries


def test_hide_other_and_stats(loaded):
    dev, des, lay = loaded
    groups, cg, _ = group_cells(des, entries=[Entry("u_top/u_a", "#123456")])
    shown = build_placement(dev, des, groups, cg)
    hidden = build_placement(dev, des, groups, cg, hide_other=True)
    si = dev.site_index()
    assert shown.site_group[si["SLICE_X0Y2"]] == 1          # u_b: OTHER, grey
    assert hidden.site_group[si["SLICE_X0Y2"]] == -1        # not colored
    assert hidden.site_group[si["SLICE_X0Y1"]] == 0
    assert hidden.utilization == shown.utilization          # still counted
    assert group_colors("light", groups)[0] == "#123456"

    def texts(stats):
        fig, _ = placement_figure(dev, lay, des, shown, stats=stats)
        return " ".join(t.get_text() for ax in fig.axes for t in ax.texts)

    assert "Utilization" not in texts(False) and "%" not in texts(False)
    assert "Utilization" in texts(True)


def test_long_loops_are_one_legend_row():
    groups = [hier.Group(f"g[{k}]", f"buf {k}", 1, entry=0) for k in range(10)] + \
             [hier.Group("x", "x", 5, entry=1)]
    rows = _legend_rows(groups, [f"#0000{k:02x}" for k in range(11)])
    assert [(r[0], len(r[1]), r[2]) for r in rows] == [("buf (10)", 10, 10), ("x", 1, 5)]


def test_cli_highlight_file(files, tmp_path, monkeypatch, capsys):
    import gzip
    import os
    dev, des = files
    monkeypatch.setenv("IMPLVIEW_CACHE", str(tmp_path / "cache"))
    os.makedirs(tmp_path / "cache" / "devices")
    with gzip.open(dev) as a, open(tmp_path / "cache" / "devices" / "xc7test-pkg-1.ivdev.gz", "wb") as b:
        b.write(gzip.compress(a.read()))
    # automatic groups saved as a highlight file, then used to render
    saved = tmp_path / "auto.toml"
    main(["render", str(des), "-O", str(tmp_path / "a"), "-o", "a.png", "--save-highlight", str(saved)])
    cfg = load_config(saved)
    assert cfg.entries and all(e.color for e in cfg.entries)
    main(["render", str(des), "-O", str(tmp_path / "b"), "-o", "b.png", "-H", str(saved), "--stats"])
    assert capsys.readouterr().out.split()[-2].endswith("b.png")


def test_no_panel_and_labels(loaded):
    from fpga_design_implview.render import label_positions, routing_figure, text_on
    dev, des, lay = loaded
    groups, cg, _ = group_cells(des)
    p = build_placement(dev, des, groups, cg)
    fig, _ = placement_figure(dev, lay, des, p, panel=False, label_size=30)
    assert len(fig.axes) == 1
    texts = [t for t in fig.axes[0].texts if t.get_bbox_patch() is not None]
    assert texts and all(t.get_fontsize() == 30 for t in texts)
    fig, _ = routing_figure(dev, lay, des, p, panel=False)
    assert len(fig.axes) == 1
    # every group gets a name, even with labels much larger than the device
    names = hier.short_labels(groups)
    got = {gi for gi, _, _ in label_positions(lay, p, names, unit=1.0, fs=40)}
    assert got == {gi for gi, g in enumerate(groups)
                   if g.name != hier.OTHER and (p.site_group == gi).any()}
    assert text_on("#ffd400") == "#111111" and text_on("#0f3d8a") == "#ffffff"


def test_label_options(tmp_path):
    p = tmp_path / "h.toml"
    p.write_text('[options]\npanel = false\nlabel_size = 14\nlabel_style = "text"\n')
    assert load_config(p).options == {"panel": False, "label_size": 14, "label_style": "text"}
    for bad in ('[options]\nlabel_size = "big"\n', '[options]\nlabel_size = 0\n',
                '[options]\nlabel_style = "neon"\n', '[options]\npanel = 1\n'):
        p.write_text(bad)
        with pytest.raises(ValueError):
            load_config(p)
