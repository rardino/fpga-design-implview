"""Group the placed cells by design hierarchy, for coloring.

auto_groups() walks down the hierarchy from the top, always splitting the
largest group into its submodules (the largest ones first when there are not
enough colors for all), until the colors run out. Single-child wrappers are
walked through for free, so a design whose top only instantiates one core is
grouped inside that core.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

OTHER = "(other)"
TOP = "(top)"


@dataclass
class Group:
    name: str               # hierarchy path ("" for the top level), or OTHER
    label: str              # what the legend shows
    cells: int = 0
    color: str = None       # set by a highlight file, else the palette
    color_dark: str = None
    entry: int = -1         # highlight entry it comes from


def _parent(path):
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _cell_parent(name):
    return name.rsplit("/", 1)[0] if "/" in name else ""


def _subtree_counts(cells):
    counts = Counter()
    for c in cells:
        p = _cell_parent(c.name)
        counts[p] += 1
    sub = Counter()
    for p, n in counts.items():
        while True:
            sub[p] += n
            if not p:
                break
            p = _parent(p)
    return sub


def _assign(cells, paths):
    """Index into paths of the deepest path that holds each cell, -1 if none."""
    pos = {p: i for i, p in enumerate(paths)}
    cache = {}
    out = []
    for c in cells:
        p = _cell_parent(c.name)
        if p not in cache:
            q = p
            while q not in pos and q:
                q = _parent(q)
            cache[p] = pos.get(q, -1)
        out.append(cache[p])
    return out


def _finish(cells, paths, labels, min_share):
    """Groups sorted by size; the small ones and the unassigned go to OTHER."""
    idx = _assign(cells, paths)
    n = Counter(idx)
    total = max(len(cells), 1)
    keep = [i for i in range(len(paths)) if n[i] and n[i] / total >= min_share]
    keep.sort(key=lambda i: -n[i])
    groups = [Group(paths[i], labels[i], n[i]) for i in keep]
    remap = {old: new for new, old in enumerate(keep)}
    other = len(groups)
    cell_group = [remap.get(i, other) for i in idx]
    n_other = sum(1 for g in cell_group if g == other)
    if n_other:
        groups.append(Group(OTHER, OTHER, n_other))
    return groups, cell_group


def _label(path, base):
    if not path:
        return TOP
    if base and path.startswith(base + "/"):
        return path[len(base) + 1:]
    return path


def auto_groups(cells, max_groups=8, min_share=0.005):
    sub = _subtree_counts(cells)
    total = max(len(cells), 1)
    children = defaultdict(list)
    for p in sub:
        if p:
            children[_parent(p)].append(p)
    big = lambda p: sub[p] / total >= min_share

    selected = {""}
    while True:
        own = {g: sub[g] - sum(sub[k] for k in children[g] if k in selected) for g in selected}
        nsig = sum(1 for g in selected if own[g] / total >= min_share)
        g = max((g for g in selected if any(big(k) and k not in selected for k in children[g])),
                key=lambda g: own[g], default=None)
        if g is None:
            break
        kids = sorted((k for k in children[g] if big(k) and k not in selected),
                      key=lambda k: -sub[k])
        rest = own[g] - sum(sub[k] for k in kids)
        if nsig + len(kids) - (rest / total < min_share) > max_groups:
            # not enough colors for all of them: split off the largest ones
            kids = kids[:max_groups - nsig]
        if not kids:
            break
        selected.update(kids)

    # Labels relative to the deepest wrapper that holds everything selected.
    paths = sorted(selected)
    real = [p for p in paths if p and sub[p] - sum(sub[k] for k in children[p] if k in selected) > 0]
    base = _common_prefix(real)
    return _finish(cells, paths, [_label(p, base) for p in paths], min_share)


def _common_prefix(paths):
    if not paths:
        return ""
    parts = [p.split("/") for p in paths]
    common = []
    for level in zip(*parts):
        if len(set(level)) != 1:
            break
        common.append(level[0])
    # keep at least the last level of each path in its label
    if len(common) >= min(len(p) for p in parts):
        common = common[:-1]
    return "/".join(common)


GENERIC = {"inst", "i", "u0", "top", "core", "impl", "wrapper", "inst_i", "gen"}


def _generic(name):
    """Instance names that say nothing on their own (IP wrappers)."""
    return name.lower() in GENERIC or len(name) <= 2


def short_labels(groups):
    """Shortest path suffix that tells each group apart (for the map)."""
    paths = [g.label.split("/") for g in groups]
    out = []
    for g, p in zip(groups, paths):
        if g.name in (OTHER, "") or g.label == TOP:
            out.append(g.label)
            continue
        n = 1
        while n < len(p) and (sum(1 for q in paths if q[-n:] == p[-n:]) > 1
                              or _generic(p[-n])):
            n += 1
        out.append("/".join(p[-n:]))
    return out


def depth_groups(cells, depth, max_groups=8, min_share=0.005):
    """Group by the hierarchy path cut at a fixed depth."""
    paths = sorted({"/".join(_cell_parent(c.name).split("/")[:depth]) for c in cells})
    groups, cell_group = _finish(cells, paths, [_label(p, "") for p in paths], min_share)
    return _cap(groups, cell_group, max_groups)


def _cap(groups, cell_group, max_groups):
    """Fold everything past max_groups into OTHER."""
    real = [g for g in groups if g.name != OTHER]
    if len(real) <= max_groups:
        return groups, cell_group
    keep = real[:max_groups]
    other = len(keep)
    remap = {i: (i if i < max_groups else other) for i in range(len(groups))}
    cell_group = [remap[g] for g in cell_group]
    n_other = sum(1 for g in cell_group if g == other)
    return keep + [Group(OTHER, OTHER, n_other)], cell_group
