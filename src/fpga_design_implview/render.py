"""Static pictures of the implemented design: PNG, PDF, SVG (matplotlib).

One page shows the placement, colored by hierarchy group; with routing data
a second page shows the interconnect usage per INT tile. A PDF gets both
pages, PNG/SVG get one file per page.
"""

import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from . import hier  # noqa: E402

THEMES = {
    "light": dict(
        surface="#fcfcfb", text="#0b0b0b", text2="#52514e", muted="#8a8984",
        unused="#e3e1db", int_tile="#f1f0ec", block="#ecebe6", other="#9b9993",
        context="#c9c7c0", cr="#a9a7a0", slr="#52514e",
        series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#6250d6", "#e34948"],
        seq=["#f1f0ec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
             "#256abf", "#184f95", "#0d366b"],
    ),
    "dark": dict(
        surface="#1a1a19", text="#f0efec", text2="#c3c2b7", muted="#8f8e88",
        unused="#2e2e2c", int_tile="#222221", block="#262624", other="#77766f",
        context="#44443f", cr="#5d5c57", slr="#c3c2b7",
        series=["#3987e5", "#d95926", "#199e70", "#c98500",
                "#d55181", "#008300", "#9085e9", "#e66767"],
        seq=["#222221", "#104281", "#184f95", "#256abf", "#3987e5",
             "#6da7ec", "#9ec5f4", "#cde2fb"],
    ),
}


def group_colors(theme, groups):
    """Highlight file colors where given, else the palette in order."""
    t = THEMES[theme]
    out = []
    k = 0
    for g in groups:
        own = g.color_dark if theme == "dark" and g.color_dark else g.color
        if own:
            out.append(own)
        elif g.name == hier.OTHER or k >= len(t["series"]):
            out.append(t["other"])
        else:
            out.append(t["series"][k])
            k += 1
    return out


def _verts(rects):
    r = np.asarray(rects, dtype=float)
    return np.stack([r[:, [0, 1]], r[:, [2, 1]], r[:, [2, 3]], r[:, [0, 3]]], axis=1)


def _rects(ax, rects, colors, z, **kw):
    if len(rects) == 0:
        return
    pc = PolyCollection(_verts(rects), facecolors=colors, edgecolors="none",
                        linewidths=0, antialiased=False, zorder=z, **kw)
    ax.add_collection(pc)


def _outline(ax, r, color, lw, z, ls="-"):
    ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fill=False,
                           edgecolor=color, linewidth=lw, linestyle=ls, zorder=z))


def _halo(color):
    return [pe.withStroke(linewidth=3, foreground=color)]


def block_label(tile_type):
    """HBM_DMAH_FT -> HBM, PSS2 -> PSS."""
    t = re.sub(r"(_FT|_L|_R)+$", "", tile_type)
    return t.split("_")[0].rstrip("0123456789") or tile_type


def _scale(lay):
    """Pixels per layout unit and dpi of the pictures."""
    long_side = max(lay.width, lay.height)
    return min(max(6.0, 2400.0 / long_side), 9000.0 / long_side), 200


def _figure(lay, theme, panel=True, panel_w=5.0):
    """Device axes on the left, information panel on the right (pax is None
    without the panel)."""
    ppu, dpi = _scale(lay)
    dev_w, dev_h = lay.width * ppu / dpi, lay.height * ppu / dpi
    margin = 0.25
    if not panel:
        panel_w = -margin
    fig_w, fig_h = dev_w + panel_w + 3 * margin, dev_h + 2 * margin
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    fig.patch.set_facecolor(THEMES[theme]["surface"])
    ax = fig.add_axes([margin / fig_w, margin / fig_h, dev_w / fig_w, dev_h / fig_h])
    ax.set_xlim(0, lay.width)
    ax.set_ylim(lay.height, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    if not panel:
        return fig, ax, None, dpi
    pax = fig.add_axes([(2 * margin + dev_w) / fig_w, margin / fig_h,
                        panel_w / fig_w, dev_h / fig_h])
    pax.axis("off")
    return fig, ax, pax, dpi


def _device_frame(ax, dev, lay, t, labels=True):
    """Clock regions, SLRs and siteless blocks."""
    fs = max(4.0, min(9.0, 1200.0 / max(lay.width, lay.height)))
    for tile, r in lay.block_rect.items():
        _rects(ax, [r], [t["block"]], 1)
        ax.text((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, block_label(dev.tile_type[tile]),
                ha="center", va="center", color=t["muted"], fontsize=fs * 2, zorder=6)
    for cr, r in lay.cr_rect:
        _outline(ax, r, t["cr"], 0.5, 5, ls=(0, (4, 3)))
        if labels:
            ax.text(r[0] + 0.6, r[1] + 0.6, cr.name, ha="left", va="top", color=t["muted"],
                    fontsize=fs, zorder=6, path_effects=_halo(t["surface"]))
    if len(lay.slr_rect) > 1:
        for name, r in lay.slr_rect:
            _outline(ax, r, t["slr"], 1.2, 5)
            ax.text(r[0] - 0.5, (r[1] + r[3]) / 2, name, ha="right", va="center",
                    rotation=90, color=t["text2"], fontsize=fs * 1.4, zorder=6)


def _sites(ax, lay, site_color, mask, z, overlay=None):
    r = lay.site_rect
    ok = mask & np.isfinite(r[:, 0])
    if overlay is not None:
        ok &= overlay
    idx = np.nonzero(ok)[0]
    _rects(ax, r[idx], [site_color[i] for i in idx], z)


def pblock_rects(dev, lay, pblocks):
    """Layout rectangles of each pblock's GRID_RANGES."""
    sidx = dev.site_index()
    crs = {cr.name: r for cr, r in lay.cr_rect}
    out = []
    for name, ranges in pblocks:
        rects = []
        for rng in ranges.split():
            a, _, b = rng.partition(":")
            b = b or a
            pts = []
            for n in (a, b):
                if n.startswith("CLOCKREGION_"):
                    r = crs.get(n[len("CLOCKREGION_"):])
                elif n in sidx:
                    r = lay.site_rect[sidx[n]]
                else:
                    r = None
                if r is not None and np.isfinite(r[0]):
                    pts.append(r)
            if pts:
                rects.append((min(p[0] for p in pts), min(p[1] for p in pts),
                              max(p[2] for p in pts), max(p[3] for p in pts)))
        out.append((name, rects))
    return out


def label_positions(lay, place, names, unit, fs=9.0, pad=0.0):
    """Where to name each group: the densest part of its sites, moved so the
    labels do not overlap (large groups pick first; a label that finds no free
    spot goes on its densest part anyway, unless its group is tiny, under 1% of
    the colored sites). unit is the size of one text point
    in layout units, pad the padding around the text in font sizes.
    Returns [(group index, x, y)]."""
    r = lay.site_rect
    used = np.nonzero(place.site_group >= 0)[0]
    used = used[np.isfinite(r[used, 0])]
    out = []
    if not len(used):
        return out
    cx = (r[used, 0] + r[used, 2]) / 2
    cy = (r[used, 1] + r[used, 3]) / 2
    sg = place.site_group[used]
    bins = 24
    placed = []
    groups = [gi for gi, g in enumerate(place.groups) if g.name != hier.OTHER and (sg == gi).any()]
    for gi in sorted(groups, key=lambda gi: -int((sg == gi).sum())):
        m = sg == gi
        h, xe, ye = np.histogram2d(cx[m], cy[m], bins=bins,
                                   range=[[0, lay.width], [0, lay.height]])
        w = (0.62 * len(names[gi]) + 2 * pad) * fs * unit
        ht = (1.4 + 2 * pad) * fs * unit
        steps = [(0, 0), (0, -ht), (0, ht), (-w / 2, 0), (w / 2, 0), (0, -2 * ht), (0, 2 * ht)]
        first = None
        spots = [k for k in np.argsort(h, axis=None)[::-1][:40] if h.flat[k] >= 1]
        for k in spots:
            i, j = np.unravel_index(k, h.shape)
            sel = m & (cx >= xe[i]) & (cx <= xe[i + 1]) & (cy >= ye[j]) & (cy <= ye[j + 1])
            bx, by = float(np.median(cx[sel])), float(np.median(cy[sel]))
            for dx, dy in steps:
                x = float(np.clip(bx + dx, w / 2, lay.width - w / 2))
                y = float(np.clip(by + dy, ht / 2, lay.height - ht / 2))
                first = first or (x, y)
                box = (x - w / 2, y - ht / 2, x + w / 2, y + ht / 2)
                if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                           for b in placed):
                    break
            else:
                continue
            break
        else:
            if m.sum() < 0.01 * len(used):
                continue
            x, y = first
            box = (x - w / 2, y - ht / 2, x + w / 2, y + ht / 2)
        placed.append(box)
        out.append((gi, x, y))
    return out
    cx = (r[used, 0] + r[used, 2]) / 2
    cy = (r[used, 1] + r[used, 3]) / 2
    bins = 24
    placed = []
    for gi, g in enumerate(place.groups):
        m = place.site_group[used] == gi
        if m.sum() < 8 or g.name == hier.OTHER:
            continue
        h, xe, ye = np.histogram2d(cx[m], cy[m], bins=bins,
                                   range=[[0, lay.width], [0, lay.height]])
        w = (0.62 * len(names[gi]) + 2 * pad) * fs * unit
        ht = (1.4 + 2 * pad) * fs * unit
        for k in np.argsort(h, axis=None)[::-1][:12]:
            i, j = np.unravel_index(k, h.shape)
            if h[i, j] < 2:
                break
            sel = m & (cx >= xe[i]) & (cx <= xe[i + 1]) & (cy >= ye[j]) & (cy <= ye[j + 1])
            x = float(np.clip(np.median(cx[sel]), w / 2, lay.width - w / 2))
            y = float(np.clip(np.median(cy[sel]), ht / 2, lay.height - ht / 2))
            box = (x - w / 2, y - ht / 2, x + w / 2, y + ht / 2)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                       for b in placed):
                placed.append(box)
                out.append((gi, x, y))
                break
    return out


# Font files of the group names (matplotlib cannot tell the weights of the
# Ubuntu files apart), then families with a real bold.
LABEL_FILES = {"pill": ("Ubuntu-M.ttf", "Ubuntu-B.ttf"), "text": ("Ubuntu-B.ttf",)}
LABEL_FAMILIES = ["Liberation Sans", "DejaVu Sans"]


def label_font(style, size):
    from matplotlib.font_manager import FontProperties, fontManager
    files = {os.path.basename(f.fname): f.fname for f in fontManager.ttflist}
    for name in LABEL_FILES[style]:
        if name in files:
            return FontProperties(fname=files[name], size=size)
    return FontProperties(family=LABEL_FAMILIES, weight="bold", size=size)


def _luminance(c):
    def lin(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in matplotlib.colors.to_rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_on(fill):
    """White or near-black text, whichever has more contrast on fill."""
    lum = _luminance(fill)
    return "#ffffff" if (1.05 / (lum + 0.05)) >= ((lum + 0.05) / 0.05) else "#111111"


def _group_labels(ax, lay, place, colors, t, ppu, dpi, size=10.0, style="pill"):
    """Group names on the device: rounded badges in the group color with a
    soft shadow ("pill"), or plain text with a halo ("text")."""
    names = hier.short_labels(place.groups)
    pad = 0.45 if style == "pill" else 0.0
    unit = dpi / 72.0 / ppu
    font = label_font(style, size)
    for gi, x, y in label_positions(lay, place, names, unit, size, pad):
        if style == "pill":
            txt = ax.text(x, y, names[gi], ha="center", va="center", fontproperties=font,
                          color=text_on(colors[gi]), zorder=8,
                          bbox=dict(boxstyle=f"round,pad={pad},rounding_size=0.9",
                                    fc=colors[gi], ec=t["surface"], lw=1.2))
            txt.get_bbox_patch().set_path_effects(
                [pe.withSimplePatchShadow(offset=(1.5, -1.5), shadow_rgbFace="#000000", alpha=0.3)])
        else:
            ax.text(x, y, names[gi], ha="center", va="center", color=t["text"],
                    fontproperties=font, zorder=8, path_effects=_halo(t["surface"]))


def _legend_rows(groups, colors):
    """(label, [colors], cells) per legend row; a highlight entry with many
    instances is one row with its shades."""
    rows, k = [], 0
    while k < len(groups):
        g = groups[k]
        j = k + 1
        while g.entry >= 0 and j < len(groups) and groups[j].entry == g.entry:
            j += 1
        if j - k > 6:
            lab = g.label.rsplit(" ", 1)[0] if " " in g.label else g.name
            rows.append((f"{lab} ({j - k})", colors[k:j], sum(x.cells for x in groups[k:j])))
        else:
            rows += [(x.label, [c], x.cells) for x, c in zip(groups[k:j], colors[k:j])]
        k = j
    return rows


def _panel(pax, dev, design, place, colors, t, page, stats=False):
    y = 1.0
    lh = 0.018

    def line(txt, size=10, color=None, weight="normal", dy=1.0, x=0.0, **kw):
        nonlocal y
        pax.text(x, y, txt, transform=pax.transAxes, ha="left", va="top", fontsize=size,
                 color=color or t["text"], fontweight=weight, **kw)
        y -= lh * dy * size / 10

    line(design.top or "design", 16, weight="bold", dy=1.6)
    fam = dev.info.get("family", "")
    line(f"{dev.part}" + (f"  ({fam})" if fam else ""), 11, color=t["text2"], dy=1.4)
    meta = "  ·  ".join(x for x in (design.tool(), design.info.get("date", "")) if x)
    if meta:
        line(meta, 9, color=t["text2"], dy=1.4)
    source = "  ·  ".join(x for x in (os.path.basename(design.source), dev.note) if x)
    if source:
        line(source, 9, color=t["muted"], dy=1.6)
    wns, whs = design.slack("wns"), design.slack("whs")
    if wns is not None or whs is not None:
        parts = []
        if wns is not None:
            parts.append(f"WNS {wns:+.3f} ns" + ("  (fails)" if wns < 0 else ""))
        if whs is not None:
            parts.append(f"WHS {whs:+.3f} ns" + ("  (fails)" if whs < 0 else ""))
        line("    ".join(parts), 10, dy=2.0)

    if stats:
        line("Utilization", 11, weight="bold", dy=1.5)
        for label, used, total in place.utilization:
            if not total:
                continue
            pct = 100.0 * used / total
            u = f"{used:,.1f}" if isinstance(used, float) and not used.is_integer() else f"{int(used):,}"
            pax.text(0.0, y, label, transform=pax.transAxes, ha="left", va="top",
                     fontsize=9, color=t["text2"])
            pax.text(0.62, y, f"{u} / {total:,}", transform=pax.transAxes, ha="right",
                     va="top", fontsize=9, color=t["text"], family="monospace")
            pax.text(0.80, y, f"{pct:5.1f}%", transform=pax.transAxes, ha="right", va="top",
                     fontsize=9, color=t["text"], family="monospace")
            y -= lh * 1.0
        y -= lh * 0.8

    if page == "routing":
        line("Routing", 11, weight="bold", dy=1.5)
        line("Each interconnect tile colors the logic", 9, color=t["text2"])
        line("next to it by how many of its PIPs", 9, color=t["text2"])
        line("the routed nets use.", 9, color=t["text2"], dy=1.6)
        return y

    line("Hierarchy", 11, weight="bold", dy=1.5)
    total = max(sum(g.cells for g in place.groups), 1)
    groups, cols = zip(*[(g, c) for g, c in zip(place.groups, colors)
                         if not (place.hide_other and g.name == hier.OTHER)]) or ((), ())
    for label, cs, cells in _legend_rows(list(groups), list(cols)):
        _swatch(pax, y, cs, t["surface"])
        pax.text(0.06, y, _elide(label, 40), transform=pax.transAxes, ha="left", va="top",
                 fontsize=9, color=t["text"])
        if stats:
            pax.text(0.98, y, f"{cells:,}  {100.0 * cells / total:4.1f}%",
                     transform=pax.transAxes, ha="right", va="top", fontsize=9,
                     color=t["text2"], family="monospace")
        y -= lh * 1.15
    _swatch(pax, y, t["unused"], t["surface"])
    pax.text(0.06, y, "unused site", transform=pax.transAxes, ha="left", va="top",
             fontsize=9, color=t["text2"])
    y -= lh * 1.6
    if stats:
        line("Cells per group; a site takes the color", 8, color=t["muted"])
        line("of most of the cells placed in it.", 8, color=t["muted"])
    else:
        line("A site takes the color of most of its cells.", 8, color=t["muted"])


def _elide(s, n):
    """Cut the middle of a long hierarchy path, keep both ends."""
    if len(s) <= n:
        return s
    parts = s.split("/")
    head, tail = parts[0], parts[-1]
    if len(head) + len(tail) + 3 <= n:
        return f"{head}/…/{tail}"
    return "…" + s[-(n - 1):]


def _swatch(pax, y, color, edge):
    """A color, or a list of colors side by side (shades of one entry)."""
    cs = color if isinstance(color, (list, tuple)) else [color]
    w = 0.04 / len(cs)
    for k, c in enumerate(cs):
        pax.add_patch(Rectangle((k * w, y - 0.011), w, 0.010, transform=pax.transAxes,
                                facecolor=c, edgecolor=edge if len(cs) == 1 else "none",
                                linewidth=0.5))


def placement_figure(dev, lay, design, place, theme="light", labels=True, stats=False,
                     panel=True, label_size=10.0, label_style="pill", pblocks=True):
    t = THEMES[theme]
    colors = group_colors(theme, place.groups)
    fig, ax, pax, dpi = _figure(lay, theme, panel)
    _device_frame(ax, dev, lay, t, labels)
    _rects(ax, lay.int_rect, [t["int_tile"]] * len(lay.int_rect), 1)

    site_color = [t["unused"]] * len(dev.sites)
    used = place.site_group >= 0
    for i in np.nonzero(used)[0]:
        site_color[i] = colors[place.site_group[i]]
    base = ~lay.site_overlay
    _sites(ax, lay, site_color, base & ~used, 2)
    _sites(ax, lay, site_color, base & used, 3)
    _sites(ax, lay, site_color, lay.site_overlay & used, 4)

    if pblocks and design.pblocks:
        fs = max(6.0, min(10.0, 1800.0 / max(lay.width, lay.height)))
        for name, rects in pblock_rects(dev, lay, design.pblocks):
            for r in rects:
                _outline(ax, r, t["text"], 1.0, 7)
            if rects:
                ax.text(rects[0][0] + 0.5, rects[0][3] - 0.5, name, ha="left", va="bottom",
                        fontsize=fs, color=t["text"], zorder=8, path_effects=_halo(t["surface"]))
    if labels:
        _group_labels(ax, lay, place, colors, t, *_scale(lay), label_size, label_style)
    if pax is not None:
        _panel(pax, dev, design, place, colors, t, "placement", stats)
    return fig, dpi


def _int_spans(dev, lay):
    """x extent of each INT tile's heat cell: out to the logic it serves,
    halfway to the next interconnect column at most."""
    cols = np.unique(dev.gx[lay.int_tiles])
    c = (lay.col_x[cols] + lay.col_x[cols + 1]) / 2
    left = np.concatenate([[-np.inf], (c[:-1] + c[1:]) / 2])
    right = np.concatenate([(c[:-1] + c[1:]) / 2, [np.inf]])
    left = np.maximum(left, c - 1.6)
    right = np.minimum(right, c + 1.6)
    k = np.searchsorted(cols, dev.gx[lay.int_tiles])
    r = lay.int_rect.copy()
    r[:, 0], r[:, 2] = left[k], right[k]
    return r


def routing_figure(dev, lay, design, place, theme="light", labels=True, stats=False,
                   panel=True, **_):
    t = THEMES[theme]
    fig, ax, pax, dpi = _figure(lay, theme, panel)
    _device_frame(ax, dev, lay, t, labels)
    tix = dev.tile_index()
    use = np.zeros(len(lay.int_tiles))
    pos = {ti: k for k, ti in enumerate(lay.int_tiles.tolist())}
    for name, n in design.pips.items():
        k = pos.get(tix.get(name, -1))
        if k is not None:
            use[k] = n
    top = float(np.percentile(use[use > 0], 99)) if (use > 0).any() else 1.0
    cmap = LinearSegmentedColormap.from_list("seq", t["seq"][1:])
    cols = cmap(np.clip(use / top, 0, 1))
    hot = use > 0

    used = place.site_group >= 0
    site_color = [t["unused"]] * len(dev.sites)
    _sites(ax, lay, site_color, ~lay.site_overlay, 2)
    _rects(ax, _int_spans(dev, lay)[hot], cols[hot], 3)
    if pax is None:
        return fig, dpi
    y = _panel(pax, dev, design, place, [], t, "routing", stats)

    cax = pax.inset_axes([0.0, y - 0.05, 0.6, 0.012])
    cb = fig.colorbar(matplotlib.cm.ScalarMappable(cmap=cmap, norm=matplotlib.colors.Normalize(0, top)),
                      cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=8, colors=t["text2"])
    cax.set_title(f"used PIPs per INT tile ({int(hot.sum()):,} tiles used,"
                  f" scale capped at the 99th percentile)", fontsize=8, color=t["text2"], loc="left")
    return fig, dpi


def write_outputs(paths, dev, lay, design, place, **opts):
    """Write each path by its extension (.png, .pdf, .svg). opts go to the
    figures: theme, labels, stats, panel, label_size, label_style."""
    pages = [("placement", lambda: placement_figure(dev, lay, design, place, **opts))]
    if design.pips:
        pages.append(("routing", lambda: routing_figure(dev, lay, design, place, **opts)))
    figs = {}

    def get(name, make):
        if name not in figs:
            figs[name] = make()
        return figs[name]

    written = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            with PdfPages(path) as pdf:
                for name, make in pages:
                    fig, dpi = get(name, make)
                    pdf.savefig(fig, facecolor=fig.get_facecolor())
            written.append(path)
        elif ext in (".png", ".svg"):
            for k, (name, make) in enumerate(pages):
                fig, dpi = get(name, make)
                out = path if k == 0 else f"{os.path.splitext(path)[0]}_{name}{ext}"
                fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor())
                written.append(out)
        else:
            raise ValueError(f"{path}: unknown output format (use .png, .pdf, .svg or .html)")
    for fig, _ in figs.values():
        plt.close(fig)
    return written
