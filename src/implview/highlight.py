"""Manual highlighting from a TOML file.

    [[highlight]]
    path = "u_core/u_fft"               # hierarchy path: * inside one level, ** any depth
    color = "#2a78d6"                   # hex or color name; omit for the next palette color
    label = "FFT"                       # legend name (default: the path)

    [[highlight]]
    path = "u_core/gen_ch[*].u_filter"  # brackets are literal: generate loop instances
    color = ["#9ee6c4", "#0b4f37"]      # several instances get shades between these
    label = "filter"                    # legend: "filter 0", "filter 1"

    [options]
    others = "grey"                     # cells not listed: "grey" or "hide"
    theme = "dark"                      # also stats = true, labels = false

A path that matches several instances gives one group per instance, with shades
of the color (one color: lighter to darker, a list: from the first to the last),
unless gradient = false. A path that matches no instance is tried on cell names.
When paths overlap, the most specific one holds the cell.
"""

import colorsys
import re
from dataclasses import dataclass, field

from matplotlib.colors import to_hex, to_rgb

from .hier import OTHER, Group, _cell_parent, _parent

ENTRY_KEYS = {"path", "color", "color_dark", "label", "gradient"}
OPTION_KEYS = {"others": ("grey", "hide"), "theme": ("light", "dark"), "stats": None, "labels": None}


@dataclass
class Entry:
    path: str
    color: object = None        # str, [str, ...] or None
    color_dark: object = None
    label: str = ""
    gradient: bool = True


@dataclass
class Config:
    entries: list = field(default_factory=list)
    options: dict = field(default_factory=dict)


def compile_glob(pat):
    """* matches inside one hierarchy level, ** across levels, ? one character;
    everything else is literal (generate names have brackets)."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**", i):
            out.append("(.*)")
            i += 2
        elif pat[i] == "*":
            out.append("([^/]*)")
            i += 1
        elif pat[i] == "?":
            out.append("([^/])")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out))


def _color(c, where):
    try:
        return to_hex(c)
    except ValueError:
        raise ValueError(f"{where}: unknown color {c!r}") from None


def load_config(path):
    import tomllib
    with open(path, "rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
    unknown = set(raw) - {"highlight", "options"}
    if unknown:
        raise ValueError(f"{path}: unknown section(s) {', '.join(sorted(unknown))}")
    cfg = Config()
    for k, e in enumerate(raw.get("highlight", [])):
        where = f"{path}: highlight #{k + 1}"
        bad = set(e) - ENTRY_KEYS
        if bad:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(bad))}")
        if not isinstance(e.get("path"), str) or not e["path"]:
            raise ValueError(f"{where}: needs a path")
        for key in ("color", "color_dark"):
            c = e.get(key)
            if c is not None:
                for x in (c if isinstance(c, list) else [c]):
                    _color(x, where)
        cfg.entries.append(Entry(e["path"], e.get("color"), e.get("color_dark"),
                                 e.get("label", ""), e.get("gradient", True)))
    for k, v in raw.get("options", {}).items():
        if k not in OPTION_KEYS:
            raise ValueError(f"{path}: unknown option {k!r}")
        allowed = OPTION_KEYS[k]
        if allowed and v not in allowed:
            raise ValueError(f"{path}: option {k} must be one of {', '.join(allowed)}")
        if allowed is None and not isinstance(v, bool):
            raise ValueError(f"{path}: option {k} must be true or false")
        cfg.options[k] = v
    return cfg


def shades(spec, n):
    """n colors from a color (lighter to darker around it) or a list (from the
    first to the last, through the ones in between)."""
    if isinstance(spec, list) and len(spec) == 1:
        spec = spec[0]
    if isinstance(spec, list):
        stops = [to_rgb(c) for c in spec]
        if n == 1:
            return [to_hex(stops[0])]
        out = []
        for k in range(n):
            f = k / (n - 1) * (len(stops) - 1)
            a = min(int(f), len(stops) - 2)
            w = f - a
            out.append(to_hex([x + (y - x) * w for x, y in zip(stops[a], stops[a + 1])]))
        return out
    if n == 1:
        return [to_hex(spec)]
    h, l, s = colorsys.rgb_to_hls(*to_rgb(spec))
    # the light end must still stand out from the grey of the unused sites
    hi = min(0.72, l + 0.18)
    lo = max(0.25, hi - 0.36)
    return [to_hex(colorsys.hls_to_rgb(h, hi + (lo - hi) * k / (n - 1), s)) for k in range(n)]


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _outermost(paths):
    keep = set(paths)
    out = []
    for p in paths:
        q = _parent(p)
        while q and q not in keep:
            q = _parent(q)
        if not q:
            out.append(p)
    return out


def manual_groups(cells, entries, palette, palette_dark):
    """Groups from the entries, in file order, then OTHER. Returns
    (groups, cell group index of each cell, entries that matched nothing)."""
    paths = set()
    for c in cells:
        p = _cell_parent(c.name)
        while p and p not in paths:
            paths.add(p)
            p = _parent(p)
    paths = sorted(paths, key=_natural)

    groups, by_path, by_cell, unmatched = [], {}, {}, []
    slot = 0
    for ei, e in enumerate(entries):
        rx = compile_glob(e.path)
        inst = _outermost([p for p in paths if rx.fullmatch(p)])
        cell_level = not inst
        if cell_level:
            names = [c.name for c in cells if rx.fullmatch(c.name)]
            if not names:
                unmatched.append(e.path)
                continue
            members = [names]
        else:
            members = [[p] for p in inst] if e.gradient and len(inst) > 1 else [inst]
        if e.color is None:
            base, base_dark = palette[slot % len(palette)], palette_dark[slot % len(palette_dark)]
            slot += 1
        else:
            base, base_dark = e.color, e.color_dark or e.color
        light = shades(base, len(members))
        dark = shades(e.color_dark, len(members)) if e.color_dark else shades(base_dark, len(members))
        for k, mem in enumerate(members):
            if len(members) > 1:
                caps = [x for x in rx.fullmatch(mem[0]).groups() if x]
                label = f"{e.label} {','.join(caps)}" if e.label else mem[0]
                name = mem[0]
            else:
                label = e.label or e.path
                name = e.path
            gi = len(groups)
            groups.append(Group(name, label, color=light[k], color_dark=dark[k], entry=ei))
            for m in mem:
                (by_cell if cell_level else by_path).setdefault(m, gi)

    other = len(groups)
    cache = {}
    cell_group = []
    for c in cells:
        g = by_cell.get(c.name)
        if g is None:
            p = _cell_parent(c.name)
            if p not in cache:
                q = p
                while q and q not in by_path:
                    q = _parent(q)
                cache[p] = by_path.get(q, other)
            g = cache[p]
        cell_group.append(g)
    n_other = 0
    for g in cell_group:
        if g == other:
            n_other += 1
        else:
            groups[g].cells += 1
    if n_other:
        groups.append(Group(OTHER, OTHER, n_other))
    return groups, cell_group, unmatched


def write_config(path, groups, colors, top=""):
    """Highlight file with the given groups and colors, to edit by hand."""
    lines = [f"# implview highlight file{f', from the automatic groups of {top}' if top else ''}",
             "# path: * inside one hierarchy level, ** any depth; brackets are literal", ""]
    for g, c in zip(groups, colors):
        if g.name in (OTHER, ""):
            continue
        lines += ["[[highlight]]", f"path = {_toml_str(g.name)}", f"color = {_toml_str(c)}", ""]
    lines += ["[options]", 'others = "grey"     # or "hide"', ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
