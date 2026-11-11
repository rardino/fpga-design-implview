"""Interactive viewer: one self-contained HTML file.

The device geometry and the placement are packed as typed arrays (gzip,
base64) next to a gzip'ed JSON with the names; the page inflates them with
DecompressionStream and draws on a canvas. Pan/zoom, hover and click for the
cells of a site, search by cell name, group and layer toggles, light/dark, the
worst timing paths drawn over the sites they go through.
"""

import base64
import gzip
import json
import math
import os

import numpy as np

from . import hier
from .render import (THEMES, _int_spans, block_label, group_colors, label_positions, pblock_rects,
                     site_labels, text_on)

Q = 64          # layout units are stored as uint16 in 1/Q steps, fewer on large devices


class _Pack:
    def __init__(self):
        self.parts = []
        self.size = 0
        self.index = {}

    def add(self, name, arr, kind):
        dtype = {"u1": "u1", "i1": "i1", "u2": "<u2", "i4": "<i4", "f4": "<f4"}[kind]
        b = np.ascontiguousarray(arr, dtype=dtype).tobytes()
        self.index[name] = (kind, self.size, int(np.asarray(arr).size))
        pad = (-len(b)) % 4
        self.parts.append(b + b"\0" * pad)
        self.size += len(b) + pad

    def blob(self):
        return b"".join(self.parts)


def _b64gz(data):
    return base64.b64encode(gzip.compress(data, 6)).decode("ascii")


def _quant(lay):
    """Steps per layout unit: the far edge of the device must fit in uint16."""
    return min(Q, int(65535 // math.ceil(max(lay.width, lay.height))))


def _q(r, q):
    return [int(round(v * q)) for v in r]


def write_html(path, dev, lay, design, place):
    pk = _Pack()
    q = _quant(lay)
    drawn = np.nonzero(np.isfinite(lay.site_rect[:, 0]))[0]
    pos = np.full(len(dev.sites), -1, np.int64)
    pos[drawn] = np.arange(len(drawn))
    pk.add("rect", np.round(lay.site_rect[drawn] * q).astype(np.int64).ravel(), "u2")
    pk.add("group", place.site_group[drawn], "i1")
    pk.add("overlay", lay.site_overlay[drawn], "u1")

    prefixes, types, crs = {}, {}, {}
    pre, typ, sx, sy, scr = [], [], [], [], []
    odd = {}
    for k, i in enumerate(drawn.tolist()):
        s = dev.sites[i]
        pre.append(prefixes.setdefault(s.prefix, len(prefixes)))
        typ.append(types.setdefault(s.type, len(types)))
        scr.append(crs.setdefault(s.clock_region, len(crs)))
        if s.x < 0 or s.name != f"{s.prefix}_X{s.x}Y{s.y}":
            odd[k] = s.name
            sx.append(65535)
            sy.append(65535)
        else:
            sx.append(s.x)
            sy.append(s.y)
    pk.add("prefix", pre, "u2")
    pk.add("type", typ, "u2")
    pk.add("sx", sx, "u2")
    pk.add("sy", sy, "u2")
    pk.add("cr", scr, "u2")

    # cells, sorted by site, with a start index per site
    order = [c for c in range(len(design.cells)) if place.cell_site[c] >= 0
             and pos[place.cell_site[c]] >= 0]
    order.sort(key=lambda c: pos[place.cell_site[c]])
    paths, refs, bels = {}, {}, {}
    c_path, c_ref, c_bel, c_group, leaves = [], [], [], [], []
    counts = np.zeros(len(drawn) + 1, np.int64)
    for c in order:
        cell = design.cells[c]
        parent, _, leaf = cell.name.rpartition("/")
        c_path.append(paths.setdefault(parent, len(paths)))
        c_ref.append(refs.setdefault(cell.ref, len(refs)))
        c_bel.append(bels.setdefault(cell.bel, len(bels)))
        c_group.append(place.cell_group[c])
        leaves.append(leaf)
        counts[pos[place.cell_site[c]] + 1] += 1
    pk.add("cstart", np.cumsum(counts), "i4")
    pk.add("cpath", c_path, "i4")
    pk.add("cref", c_ref, "u2")
    pk.add("cbel", c_bel, "u2")
    pk.add("cgroup", c_group, "u1")

    # routing usage per INT tile, spread over the logic next to it
    heat = {}
    if design.pips:
        tix = dev.tile_index()
        use = np.zeros(len(lay.int_tiles))
        ipos = {ti: k for k, ti in enumerate(lay.int_tiles.tolist())}
        for name, n in design.pips.items():
            k = ipos.get(tix.get(name, -1))
            if k is not None:
                use[k] = n
        hot = use > 0
        spans = _int_spans(dev, lay)[hot]
        spans[:, 0] = np.maximum(spans[:, 0], 0)
        spans[:, 2] = np.minimum(spans[:, 2], lay.width)
        pk.add("heat_rect", np.round(spans * q).astype(np.int64).ravel(), "u2")
        pk.add("heat_val", np.minimum(use[hot], 65535), "u2")
        heat = {"n": int(hot.sum()), "top": float(np.percentile(use[hot], 99)) if hot.any() else 1.0}

    sidx = dev.site_index()

    def spos(site):
        i = sidx.get(site, -1)
        return int(pos[i]) if i >= 0 else -1

    tpaths = [{"k": t.check, "sl": t.slack, "rq": t.requirement, "dp": t.datapath, "lg": t.logic,
               "nt": t.net, "lv": t.levels, "sk": t.skew, "sc": t.start_clock, "ec": t.end_clock,
               "gr": t.group, "sp": t.startpoint, "ep": t.endpoint,
               "n": [[pin, spos(site)] for pin, site in t.nodes], "r": t.report}
              for t in design.paths]

    names = hier.short_labels(place.groups)
    unit = max(lay.width, lay.height) / 900.0
    labels = [(gi, x, y) for gi, x, y in label_positions(lay, place, names, unit, 9.0, 0.45)]
    light, dark = group_colors("light", place.groups), group_colors("dark", place.groups)
    meta = {
        "top": design.top, "part": dev.part, "family": dev.info.get("family", ""),
        "tool": design.tool(), "date": design.info.get("date", ""),
        "source": os.path.basename(design.source or ""), "note": dev.note,
        "wns": design.slack("wns"), "whs": design.slack("whs"),
        "width": lay.width, "height": lay.height, "q": q, "nsites": int(len(drawn)),
        "arrays": pk.index,
        "prefixes": list(prefixes), "types": list(types), "crs": list(crs),
        "odd": odd, "pins": {int(pos[i]): dev.pins[s.name] for i, s in enumerate(dev.sites)
                             if s.name in dev.pins and pos[i] >= 0},
        "paths": list(paths), "refs": list(refs), "bels": list(bels), "leaves": leaves,
        "groups": [{"label": g.label, "short": n, "cells": g.cells, "light": a, "dark": b,
                    "ink_light": text_on(a), "ink_dark": text_on(b)}
                   for g, n, a, b in zip(place.groups, names, light, dark)],
        "util": [[lab, used, total] for lab, used, total in place.utilization],
        "clock_regions": [[cr.name, _q(r, q)] for cr, r in lay.cr_rect],
        "slrs": [[n, _q(r, q)] for n, r in lay.slr_rect] if len(lay.slr_rect) > 1 else [],
        "blocks": [[block_label(dev.tile_type[t]), _q(r, q)] for t, r in lay.block_rect.items()],
        "site_labels": [[n, _q(r, q)] for n, r in site_labels(dev, lay, set(names))],
        "pblocks": [[n, [_q(r, q) for r in rects]]
                    for n, rects in pblock_rects(dev, lay, design.pblocks)],
        "labels": [[gi, x, y] for gi, x, y in labels],
        "heat": heat,
        "tpaths": tpaths,
        "themes": {k: {x: v[x] for x in ("surface", "text", "text2", "muted", "unused", "block",
                                         "context", "cr", "slr", "seq")}
                   for k, v in THEMES.items()},
    }
    page = (TEMPLATE
            .replace("__TITLE__", _esc(f"{design.top} · {dev.part}"))
            .replace("__META__", _b64gz(json.dumps(meta, separators=(",", ":")).encode()))
            .replace("__BIN__", _b64gz(pk.blob())))
    with open(path, "w") as f:
        f.write(page)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --bg: #fcfcfb; --panel: #f4f3f0; --text: #0b0b0b; --text2: #52514e; --muted: #8a8984;
  --border: #dcdad3; --accent: #2a78d6; --input: #ffffff; --tip: #ffffffee; --fail: #cf3534;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #1a1a19; --panel: #222221; --text: #f0efec; --text2: #c3c2b7; --muted: #8f8e88;
    --border: #3a3a37; --accent: #3987e5; --input: #2a2a28; --tip: #262624ee; --fail: #f07575;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #1a1a19; --panel: #222221; --text: #f0efec; --text2: #c3c2b7; --muted: #8f8e88;
  --border: #3a3a37; --accent: #3987e5; --input: #2a2a28; --tip: #262624ee; --fail: #f07575;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
  font: 13px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
#app { display: flex; height: 100%; }
#view { flex: 1; position: relative; min-width: 0; overflow: hidden; }
canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
canvas.drag { cursor: grabbing; }
#panel { width: 360px; flex: none; background: var(--panel); border-left: 1px solid var(--border);
  overflow-y: auto; padding: 16px; }
h1 { font-size: 18px; margin: 0 0 2px; word-break: break-all; }
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--text2);
  margin: 18px 0 6px; }
.sub { color: var(--text2); }
.mut { color: var(--muted); font-size: 12px; }
.fail { font-weight: 600; }
input[type=search] { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--input); color: var(--text); font: inherit; }
.row { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
.row .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.num { font-variant-numeric: tabular-nums; color: var(--text2); white-space: nowrap; }
.sw { width: 12px; height: 12px; border-radius: 3px; flex: none; }
.legend .row { cursor: pointer; border-radius: 4px; padding: 2px 4px; margin: 0 -4px; }
.legend .row:hover { background: var(--border); }
.legend .row.off { opacity: .4; }
.legend .row.off .sw { background: transparent !important; outline: 1px solid var(--muted); }
table { width: 100%; border-collapse: collapse; }
td { padding: 1px 0; }
td.r { text-align: right; font-variant-numeric: tabular-nums; }
label { display: flex; align-items: center; gap: 6px; padding: 1px 0; }
.btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
button { font: inherit; padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--input); color: var(--text); cursor: pointer; }
button:hover { border-color: var(--accent); }
#tip { position: absolute; pointer-events: none; background: var(--tip); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 8px; font-size: 12px; max-width: 420px; display: none;
  box-shadow: 0 2px 8px #0002; }
#tip b { font-size: 13px; }
.cells { max-height: 320px; overflow-y: auto; }
.cell { padding: 3px 0; border-bottom: 1px solid var(--border); word-break: break-all; cursor: pointer; }
.cell:hover { color: var(--accent); }
#hint { position: absolute; left: 12px; bottom: 10px; color: var(--muted); font-size: 12px;
  pointer-events: none; }
.seg { display: flex; margin-bottom: 6px; }
.seg button { flex: 1; border-radius: 0; }
.seg button:first-child { border-radius: 6px 0 0 6px; }
.seg button:last-child { border-radius: 0 6px 6px 0; margin-left: -1px; }
.seg button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.plist { max-height: 230px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px;
  background: var(--input); }
.prow { display: flex; align-items: baseline; gap: 8px; padding: 3px 8px; cursor: pointer;
  border-bottom: 1px solid var(--border); }
.prow:last-child { border-bottom: 0; }
.prow:hover { background: var(--panel); }
.prow.on { background: var(--accent); color: #fff; }
.prow.on .num, .prow.on .sl { color: inherit; }
.pid { color: var(--muted); font-size: 11px; width: 2.6em; flex: none; }
.prow.on .pid { color: inherit; opacity: .8; }
.sl { font-variant-numeric: tabular-nums; width: 4.6em; text-align: right; flex: none; }
.neg { color: var(--fail); font-weight: 600; }
.ep { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 1px 12px; margin: 6px 0; }
.kv > :nth-child(odd) { color: var(--text2); }
.hop { display: flex; align-items: baseline; gap: 6px; padding: 3px 0; border-bottom: 1px solid var(--border);
  cursor: pointer; }
.hop:hover { color: var(--accent); }
.hop .sw { align-self: flex-start; margin-top: 3px; }
.hop .k { color: var(--muted); font-size: 11px; width: 1.6em; text-align: right; flex: none; }
.hop .grow { flex: 1; min-width: 0; word-break: break-all; }
code.tcl { display: block; margin-top: 6px; padding: 6px 8px; font: 11px/1.4 ui-monospace, SFMono-Regular,
  Menlo, monospace; word-break: break-all; background: var(--input); border: 1px solid var(--border);
  border-radius: 6px; user-select: all; }
#timing span[data-j] { cursor: pointer; border-bottom: 1px dotted var(--muted); }
#drawer { position: absolute; left: 0; right: 0; bottom: 0; height: 40%; display: flex; flex-direction: column;
  background: var(--panel); border-top: 1px solid var(--border); }
#drawer[hidden] { display: none; }
#drawer .dh { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-bottom: 1px solid var(--border); }
#drawer .dh b { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#rep { flex: 1; margin: 0; padding: 8px 12px; overflow: auto; font: 11.5px/1.4 ui-monospace, SFMono-Regular,
  Menlo, monospace; white-space: pre; color: var(--text); }
#rep a { color: var(--accent); cursor: pointer; text-decoration: none; border-bottom: 1px dotted var(--accent); }
#rep a:hover { border-bottom-style: solid; }
#loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--muted); }
@media (max-width: 760px) {
  #app { flex-direction: column; }
  #view { height: 65vh; flex: none; }
  #panel { width: auto; border-left: 0; border-top: 1px solid var(--border); flex: 1; }
}
</style>
</head>
<body>
<div id="app">
  <div id="view">
    <canvas id="cv"></canvas>
    <div id="tip"></div>
    <div id="hint">scroll to zoom · drag to pan · click a site · F to fit</div>
    <div id="drawer" hidden>
      <div class="dh"><b id="dtitle"></b><span class="mut">site names are links</span>
        <button id="dclose" title="Close (Esc)">Close</button></div>
      <pre id="rep"></pre>
    </div>
    <div id="loading">loading…</div>
  </div>
  <div id="panel">
    <h1 id="top"></h1>
    <div class="sub" id="part"></div>
    <div class="mut" id="meta"></div>
    <div id="timing" style="margin-top:6px"></div>
    <div id="tpaths" hidden>
      <h2>Timing paths</h2>
      <div class="seg" id="tcheck"></div>
      <div class="plist" id="plist"></div>
      <div class="mut">Click a path to draw it on the device · N / P for the next one.</div>
      <div id="pdet"></div>
    </div>
    <h2>Search cells</h2>
    <input type="search" id="q" placeholder="name or path, * and ? as wildcards">
    <div class="mut" id="qres"></div>
    <div id="sel"></div>
    <h2>Hierarchy</h2>
    <div class="legend" id="legend"></div>
    <div class="mut">Click a group to hide it. A site takes the color of most of its cells.</div>
    <h2>Layers</h2>
    <div id="layers"></div>
    <div class="btns">
      <button id="fit">Fit</button>
      <button id="theme">Dark</button>
      <button id="save">Save view as PNG</button>
    </div>
    <h2>Utilization</h2>
    <table id="util"></table>
  </div>
</div>
<script type="text/plain" id="meta-data">__META__</script>
<script type="text/plain" id="bin-data">__BIN__</script>
<script>
(async function () {
const $ = id => document.getElementById(id);

async function inflate(id) {
  const bin = atob($(id).textContent.trim());
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const s = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(s).arrayBuffer());
}

const M = JSON.parse(new TextDecoder().decode(await inflate("meta-data")));
const buf = (await inflate("bin-data")).buffer;
const KIND = { u1: Uint8Array, i1: Int8Array, u2: Uint16Array, i4: Int32Array, f4: Float32Array };
const A = {};
for (const [k, [t, off, n]] of Object.entries(M.arrays)) A[k] = new KIND[t](buf, off, n);
$("loading").remove();

const N = M.nsites, Q = M.q;
const R = new Float32Array(4 * N);
for (let i = 0; i < 4 * N; i++) R[i] = A.rect[i] / Q;
const NH = M.heat.n || 0;
const HR = new Float32Array(4 * NH);
for (let i = 0; i < 4 * NH; i++) HR[i] = A.heat_rect[i] / Q;

// ---- state
let theme = "light";
try { theme = localStorage.getItem("implview-theme") || ""; } catch (e) { theme = ""; }
if (!theme) theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
const hidden = new Set();
const layers = { unused: true, routing: false, cr: true, pblocks: true, labels: true, allpaths: false };
let match = null, sel = -1, hover = -1;
let view = { s: 1, tx: 0, ty: 0 };
let classes = [];
let dirty = true;

// ---- timing paths: the pins in order, grouped by cell (hops), and the
// distinct sites they go through (what is drawn)
const P = M.tpaths || [];
{
  const nk = { s: 0, h: 0 };
  for (const p of P) {
    const c = p.k === "hold" ? "h" : "s";
    p.id = c + (++nk[c]);
    p.hops = [];
    for (const [name, site] of p.n) {
      const cut = name.lastIndexOf("/"), port = cut < 0;
      const cell = port ? name : name.slice(0, cut), pin = port ? "" : name.slice(cut + 1);
      const last = p.hops[p.hops.length - 1];
      if (last && !port && !last.port && last.cell === cell) last.pins.push(pin);
      else p.hops.push({ cell, pins: pin ? [pin] : [], site, port });
    }
    p.sites = [];
    for (const h of p.hops) if (h.site >= 0 && p.sites[p.sites.length - 1] !== h.site) p.sites.push(h.site);
    p.fails = p.sl !== null && p.sl < 0;
  }
}
const sitePaths = new Map();
P.forEach((p, j) => {
  for (const i of new Set(p.sites)) { if (!sitePaths.has(i)) sitePaths.set(i, []); sitePaths.get(i).push(j); }
});
let pcheck = P.length && !P.some(p => p.k === "setup") ? "hold" : "setup";
let psel = -1, onPath = null;

const cv = $("cv"), ctx = cv.getContext("2d");
let cw = 0, ch = 0, dpr = 1;

function T() { return M.themes[theme]; }
function gcol(g) { return M.groups[g][theme]; }

function siteName(i) {
  if (M.odd[i] !== undefined) return M.odd[i];
  return `${M.prefixes[A.prefix[i]]}_X${A.sx[i]}Y${A.sy[i]}`;
}
function cellName(k) {
  const p = M.paths[A.cpath[k]];
  return p ? `${p}/${M.leaves[k]}` : M.leaves[k];
}

// ---- color classes, rebuilt when the state changes
function rebuild() {
  const t = T();
  const by = new Map();
  const add = (key, color, i, z) => {
    if (!by.has(key)) by.set(key, { color, z, idx: [] });
    by.get(key).idx.push(i);
  };
  for (let i = 0; i < N; i++) {
    const g = A.group[i], used = g >= 0 && !hidden.has(g), ov = A.overlay[i];
    if (ov && !used) continue;
    let color, key;
    if (!used) { if (!layers.unused) continue; color = t.unused; key = "u"; }
    else if (layers.routing) { color = t.context; key = "c" + ov; }
    else if (onPath ? !onPath[i] : (match && !match[i])) { color = t.context; key = "c" + ov; }
    else { color = gcol(g); key = g + ":" + ov; }
    add(key, color, i, (used ? 1 : 0) + ov * 2);
  }
  classes = [...by.values()].sort((a, b) => a.z - b.z).map(c => ({ color: c.color, idx: Int32Array.from(c.idx) }));
  dirty = true;
}

// ---- heat colors
function heatColors() {
  const seq = T().seq.slice(1), top = M.heat.top || 1, out = new Array(NH);
  const rgb = seq.map(h => [1, 3, 5].map(k => parseInt(h.substr(k, 2), 16)));
  for (let i = 0; i < NH; i++) {
    const f = Math.min(A.heat_val[i] / top, 1) * (rgb.length - 1);
    const a = Math.floor(f), b = Math.min(a + 1, rgb.length - 1), w = f - a;
    const c = rgb[a].map((v, k) => Math.round(v + (rgb[b][k] - v) * w));
    out[i] = `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  return out;
}
let heatCol = [];

// ---- drawing
function rectPath(r, k, x0, y0, x1, y1) {
  const a = r[k], b = r[k + 1], c = r[k + 2], d = r[k + 3];
  if (c < x0 || a > x1 || d < y0 || b > y1) return;
  ctx.rect(a * view.s + view.tx, b * view.s + view.ty, (c - a) * view.s, (d - b) * view.s);
}
function sx(u) { return u * view.s + view.tx; }
function sy(v) { return v * view.s + view.ty; }
function qr(r) { return r.map(v => v / Q); }

function text(str, x, y, size, color, align, weight) {
  ctx.font = `${weight || 400} ${size}px system-ui, sans-serif`;
  ctx.textAlign = align || "left";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3; ctx.strokeStyle = T().surface; ctx.lineJoin = "round";
  ctx.strokeText(str, x, y);
  ctx.fillStyle = color; ctx.fillText(str, x, y);
}

// group name as a rounded badge in the group color, like the pictures
function pill(str, x, y, g) {
  ctx.font = "600 12px Ubuntu, system-ui, sans-serif";
  const w = ctx.measureText(str).width + 14, h = 20;
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.3)"; ctx.shadowBlur = 3; ctx.shadowOffsetX = 1; ctx.shadowOffsetY = 1;
  ctx.fillStyle = g[theme]; ctx.strokeStyle = T().surface; ctx.lineWidth = 1.5;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x - w / 2, y - h / 2, w, h, h / 2); else ctx.rect(x - w / 2, y - h / 2, w, h);
  ctx.fill(); ctx.shadowColor = "transparent"; ctx.stroke();
  ctx.restore();
  ctx.fillStyle = g["ink_" + theme]; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(str, x, y + 0.5);
}

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function center(i) { const k = 4 * i; return [sx((R[k] + R[k + 2]) / 2), sy((R[k + 1] + R[k + 3]) / 2)]; }
function polyline(pts) {
  ctx.beginPath();
  pts.forEach(([x, y], k) => k ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  ctx.stroke();
}
function drawPaths() {
  const t = T(), fail = cssVar("--fail"), acc = cssVar("--accent");
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  if (layers.allpaths) {
    ctx.globalAlpha = psel >= 0 ? 0.3 : 0.8; ctx.lineWidth = 1.5;
    for (const p of P) if (p.k === pcheck && p !== P[psel]) {
      ctx.strokeStyle = p.fails ? fail : t.text2;
      polyline(p.sites.map(center));
    }
    ctx.globalAlpha = 1;
  }
  if (psel < 0) return;
  const p = P[psel], pts = p.sites.map(center), col = p.fails ? fail : acc;
  ctx.strokeStyle = col; ctx.lineWidth = 1.5;
  for (const i of p.sites) {
    const k = 4 * i;
    ctx.strokeRect(sx(R[k]) - 1.5, sy(R[k + 1]) - 1.5, (R[k + 2] - R[k]) * view.s + 3, (R[k + 3] - R[k + 1]) * view.s + 3);
  }
  ctx.strokeStyle = t.surface; ctx.lineWidth = 5; polyline(pts);
  ctx.strokeStyle = col; ctx.lineWidth = 2.25; polyline(pts);
  // direction: an arrowhead in the middle of each segment long enough
  ctx.fillStyle = col;
  for (let k = 1; k < pts.length; k++) {
    const [ax, ay] = pts[k - 1], [bx, by] = pts[k], dx = bx - ax, dy = by - ay, d = Math.hypot(dx, dy);
    if (d < 36) continue;
    const ux = dx / d, uy = dy / d, mx = ax + dx / 2 + ux * 4, my = ay + dy / 2 + uy * 4;
    ctx.beginPath(); ctx.moveTo(mx, my);
    ctx.lineTo(mx - ux * 9 - uy * 5, my - uy * 9 + ux * 5);
    ctx.lineTo(mx - ux * 9 + uy * 5, my - uy * 9 - ux * 5);
    ctx.closePath(); ctx.fill();
  }
  // a ring on the startpoint, a dot on the endpoint and the sites between
  const dot = ([x, y], r, fill) => {
    ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.lineWidth = 3; ctx.strokeStyle = t.surface; ctx.stroke();
    ctx.fillStyle = fill ? col : t.surface; ctx.fill();
    if (!fill) { ctx.lineWidth = 2; ctx.strokeStyle = col; ctx.stroke(); }
  };
  for (let k = 1; k < pts.length - 1; k++) dot(pts[k], 2.5, true);
  if (pts.length) { dot(pts[0], 5.5, false); if (pts.length > 1) dot(pts[pts.length - 1], 5.5, true); }
}

function draw() {
  dirty = false;
  const t = T();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = t.surface; ctx.fillRect(0, 0, cw, ch);
  const x0 = -view.tx / view.s, y0 = -view.ty / view.s;
  const x1 = (cw - view.tx) / view.s, y1 = (ch - view.ty) / view.s;

  for (const [name, r0] of M.blocks) {
    const r = qr(r0);
    ctx.fillStyle = t.block;
    ctx.fillRect(sx(r[0]), sy(r[1]), (r[2] - r[0]) * view.s, (r[3] - r[1]) * view.s);
    text(name, sx((r[0] + r[2]) / 2), sy((r[1] + r[3]) / 2), 14, t.muted, "center");
  }
  for (const c of classes) {
    ctx.fillStyle = c.color; ctx.beginPath();
    for (const i of c.idx) rectPath(R, 4 * i, x0, y0, x1, y1);
    ctx.fill();
  }
  for (const [name, r0] of M.site_labels) {
    const r = qr(r0);
    text(name, sx((r[0] + r[2]) / 2), sy((r[1] + r[3]) / 2), 14, t.muted, "center");
  }
  if (layers.routing) {
    for (let i = 0; i < NH; i++) {
      ctx.fillStyle = heatCol[i]; ctx.beginPath(); rectPath(HR, 4 * i, x0, y0, x1, y1); ctx.fill();
    }
  }
  if (match && !layers.routing) {
    ctx.strokeStyle = t.text; ctx.lineWidth = 1; ctx.beginPath();
    if (view.s > 3) for (let i = 0; i < N; i++) if (match[i]) rectPath(R, 4 * i, x0, y0, x1, y1);
    ctx.stroke();
  }
  if (layers.cr) {
    ctx.setLineDash([4, 3]); ctx.strokeStyle = t.cr; ctx.lineWidth = 1;
    for (const [name, r0] of M.clock_regions) {
      const r = qr(r0);
      ctx.strokeRect(sx(r[0]), sy(r[1]), (r[2] - r[0]) * view.s, (r[3] - r[1]) * view.s);
    }
    ctx.setLineDash([]);
    for (const [name, r0] of M.clock_regions) {
      const r = qr(r0);
      if ((r[2] - r[0]) * view.s > 50) text(name, sx(r[0]) + 4, sy(r[1]) + 9, 11, t.muted);
    }
  }
  for (const [name, r0] of M.slrs) {
    const r = qr(r0);
    ctx.strokeStyle = t.slr; ctx.lineWidth = 2;
    ctx.strokeRect(sx(r[0]), sy(r[1]), (r[2] - r[0]) * view.s, (r[3] - r[1]) * view.s);
    text(name, sx(r[0]) + 4, sy(r[3]) - 10, 12, t.text2, "left", 600);
  }
  if (layers.pblocks) {
    for (const [name, rects] of M.pblocks) {
      ctx.strokeStyle = t.text; ctx.lineWidth = 1.5;
      for (const r0 of rects) {
        const r = qr(r0);
        ctx.strokeRect(sx(r[0]), sy(r[1]), (r[2] - r[0]) * view.s, (r[3] - r[1]) * view.s);
      }
      if (rects.length) { const r = qr(rects[0]); text(name, sx(r[0]) + 4, sy(r[3]) - 9, 12, t.text, "left", 600); }
    }
  }
  if (P.length) drawPaths();
  if (layers.labels && !layers.routing && !match && psel < 0) {
    for (const [g, x, y] of M.labels) pill(M.groups[g].short, sx(x), sy(y), M.groups[g]);
  }
  for (const [i, w] of [[hover, 1.5], [sel, 2.5]]) {
    if (i < 0) continue;
    const k = 4 * i;
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
    ctx.lineWidth = w;
    const pad = 2;
    ctx.strokeRect(sx(R[k]) - pad, sy(R[k + 1]) - pad,
                   (R[k + 2] - R[k]) * view.s + 2 * pad, (R[k + 3] - R[k + 1]) * view.s + 2 * pad);
  }
}

function frame() {
  try { if (dirty) draw(); } catch (e) { $("hint").textContent = "implview: " + e; dirty = false; }
  requestAnimationFrame(frame);
}

function resize() {
  const r = cv.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  const first = !cw;
  cw = r.width; ch = r.height;
  cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr);
  if (first) fit();
  dirty = true;
}
function fit() {
  const s = Math.min(cw / M.width, ch / M.height) * 0.96;
  view = { s, tx: (cw - M.width * s) / 2, ty: (ch - M.height * s) / 2 };
  dirty = true;
}

// ---- hit test: sites bucketed on a 4x4 unit grid
const G = 4, gw = Math.ceil(M.width / G) + 1, gh = Math.ceil(M.height / G) + 1;
const bstart = new Int32Array(gw * gh + 1);
{
  const span = i => [Math.floor(R[4 * i] / G), Math.floor(R[4 * i + 1] / G),
                     Math.floor(R[4 * i + 2] / G), Math.floor(R[4 * i + 3] / G)];
  for (let i = 0; i < N; i++) {
    const [a, b, c, d] = span(i);
    for (let y = b; y <= d; y++) for (let x = a; x <= c; x++) bstart[y * gw + x + 1]++;
  }
  for (let k = 1; k <= gw * gh; k++) bstart[k] += bstart[k - 1];
  var bidx = new Int32Array(bstart[gw * gh]);
  const fill = bstart.slice(0, gw * gh);
  for (let i = 0; i < N; i++) {
    const [a, b, c, d] = span(i);
    for (let y = b; y <= d; y++) for (let x = a; x <= c; x++) bidx[fill[y * gw + x]++] = i;
  }
}
function pick(px, py) {
  const u = (px - view.tx) / view.s, v = (py - view.ty) / view.s;
  const bx = Math.floor(u / G), by = Math.floor(v / G);
  if (bx < 0 || by < 0 || bx >= gw || by >= gh) return -1;
  let best = -1;
  for (let k = bstart[by * gw + bx]; k < bstart[by * gw + bx + 1]; k++) {
    const i = bidx[k], j = 4 * i;
    if (u < R[j] || u > R[j + 2] || v < R[j + 1] || v > R[j + 3]) continue;
    if (A.overlay[i]) { if (A.group[i] >= 0) return i; continue; }
    best = i;
  }
  return best;
}

// ---- tooltip and selection
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function siteHead(i) {
  const pins = M.pins[i] ? M.pins[i].split(" ") : [];
  const pin = pins.length ? ` · pin${pins.length > 1 ? "s" : ""} ${pins.slice(0, 4).join(" ")}` +
    (pins.length > 4 ? ` +${pins.length - 4}` : "") : "";
  const n = A.cstart[i + 1] - A.cstart[i];
  return `<b>${esc(siteName(i))}</b><br><span class="sub">${esc([M.types[A.type[i]], M.crs[A.cr[i]]].filter(Boolean).join(" · "))}${pin}</span>` +
    `<br><span class="mut">${n ? n + " cell" + (n > 1 ? "s" : "") : "unused"}</span>`;
}
function cellRow(k, full) {
  const g = A.cgroup[k];
  const name = full ? cellName(k) : M.leaves[k];
  return `<div class="row"><span class="sw" style="background:${gcol(g)}"></span>` +
    `<span class="grow">${esc(name)}</span><span class="num">${esc(M.refs[A.cref[k]])} · ${esc(M.bels[A.cbel[k]])}</span></div>`;
}
function showTip(i, px, py) {
  const tip = $("tip");
  if (i < 0) { tip.style.display = "none"; return; }
  const a = A.cstart[i], b = A.cstart[i + 1];
  let h = siteHead(i);
  for (let k = a; k < Math.min(b, a + 8); k++) h += cellRow(k, false);
  if (b - a > 8) h += `<div class="mut">… ${b - a - 8} more</div>`;
  if (b > a) h += `<div class="mut" style="margin-top:4px">${esc(M.paths[A.cpath[a]] || "(top)")}</div>`;
  if (sitePaths.has(i)) h += `<div class="mut">${pathsNote(i)} · click to list</div>`;
  tip.innerHTML = h;
  tip.style.display = "block";
  const vw = $("view").clientWidth, vh = $("view").clientHeight;
  const w = tip.offsetWidth, ht = tip.offsetHeight;
  tip.style.left = Math.min(px + 14, vw - w - 6) + "px";
  tip.style.top = Math.min(py + 14, vh - ht - 6) + "px";
}
function select(i) {
  sel = i;
  const box = $("sel");
  if (i < 0) { box.innerHTML = ""; dirty = true; return; }
  const a = A.cstart[i], b = A.cstart[i + 1];
  let h = `<h2>Selected site</h2>${siteHead(i)}<div class="cells">`;
  for (let k = a; k < b; k++) h += `<div class="cell" data-k="${k}">${cellRow(k, true)}</div>`;
  h += "</div>";
  if (b > a) h += `<div class="mut">Click a cell to show its whole module.</div>`;
  if (sitePaths.has(i)) h += `<h2>Timing paths through this site</h2><div class="plist">${sitePaths.get(i).map(prow).join("")}</div>`;
  box.innerHTML = h;
  box.querySelectorAll(".prow").forEach(el => el.onclick = () => selectPath(+el.dataset.j));
  box.querySelectorAll(".cell").forEach(el => el.onclick = () => {
    const p = M.paths[A.cpath[+el.dataset.k]];
    $("q").value = p ? p + "/*" : "*"; search();
  });
  dirty = true;
}

// ---- timing paths panel
function slackTxt(v) { return v === null || v === undefined ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(3); }
function slackNs(v) { return v === null || v === undefined ? "–" : slackTxt(v) + " ns"; }
function ns(v) { return v === null || v === undefined ? "–" : v.toFixed(3) + " ns"; }
function shortName(n) { const a = n.split("/"); return a.length > 3 ? "…/" + a.slice(-3).join("/") : n; }
function pathsNote(i) {
  const parts = [];
  for (const k of ["setup", "hold"]) {
    const js = sitePaths.get(i).filter(j => P[j].k === k);
    if (!js.length) continue;
    const w = Math.min(...js.map(j => P[j].sl ?? Infinity));
    parts.push(`${js.length} ${k}${isFinite(w) ? ` (worst ${slackTxt(w)})` : ""}`);
  }
  return "on timing paths: " + parts.join(", ");
}
function prow(j) {
  const p = P[j];
  return `<div class="prow${j === psel ? " on" : ""}" data-j="${j}" title="${esc(p.sp)} → ${esc(p.ep)}">` +
    `<span class="pid">${p.id}</span><span class="sl${p.fails ? " neg" : ""}">${slackTxt(p.sl)}</span>` +
    `<span class="ep">${esc(shortName(p.ep))}</span><span class="num">${p.lv ?? "–"} lv</span></div>`;
}
function cellAt(s, name) {
  for (let k = A.cstart[s]; k < A.cstart[s + 1]; k++) if (cellName(k) === name) return k;
  return -1;
}
function pathPanel() {
  if (!P.length) return;
  $("tpaths").hidden = false;
  const n = k => P.filter(p => p.k === k).length;
  $("tcheck").innerHTML = ["setup", "hold"].map(k =>
    `<button data-k="${k}" class="${k === pcheck ? "on" : ""}" ${n(k) ? "" : "disabled"}>${k[0].toUpperCase() + k.slice(1)} · ${n(k)}</button>`).join("");
  $("tcheck").querySelectorAll("button").forEach(el => el.onclick = () => { pcheck = el.dataset.k; pathPanel(); dirty = true; });
  $("plist").innerHTML = P.map((p, j) => p.k === pcheck ? prow(j) : "").join("");
  $("plist").querySelectorAll(".prow").forEach(el => el.onclick = () => selectPath(+el.dataset.j));
  pathDetail();
}
function pathDetail() {
  const box = $("pdet");
  if (psel < 0) { box.innerHTML = ""; return; }
  const p = P[psel];
  const crs = [];
  for (const i of p.sites) { const c = M.crs[A.cr[i]]; if (c && crs[crs.length - 1] !== c) crs.push(c); }
  const dp = p.dp > 0 && p.lg !== null && p.nt !== null
    ? `${ns(p.dp)} <span class="mut">logic ${Math.round(100 * p.lg / p.dp)}% · net ${Math.round(100 * p.nt / p.dp)}%</span>` : ns(p.dp);
  let h = `<h2>Path ${p.id} · ${p.k}</h2><div class="kv">` +
    `<span>Slack</span><span class="${p.fails ? "neg" : ""}">${slackNs(p.sl)}${p.fails ? " (fails)" : ""}</span>` +
    `<span>Clocks</span><span>${esc(p.sc || "–")} → ${esc(p.ec || "–")}</span>` +
    `<span>Requirement</span><span>${ns(p.rq)}</span>` +
    `<span>Data path</span><span>${dp}</span>` +
    `<span>Logic levels</span><span>${p.lv ?? "–"}</span>` +
    `<span>Clock skew</span><span>${ns(p.sk)}</span>` +
    (crs.length ? `<span>Clock regions</span><span>${crs.map(esc).join(" → ")}</span>` : "") +
    `</div><div class="cells">`;
  p.hops.forEach((hp, k) => {
    const c = hp.site >= 0 && !hp.port ? cellAt(hp.site, hp.cell) : -1;
    const sw = c >= 0 ? `<span class="sw" style="background:${gcol(A.cgroup[c])}"></span>`
                      : `<span class="sw" style="outline:1px solid var(--muted)"></span>`;
    const what = hp.port ? "port" : c >= 0 ? M.refs[A.cref[c]] : "";
    const where = [what, hp.site >= 0 ? siteName(hp.site) : "not placed"].filter(Boolean).join(" · ");
    h += `<div class="hop" data-s="${hp.site}" title="${esc(hp.cell)}">${sw}<span class="k">${k + 1}</span>` +
      `<span class="grow">${esc(shortName(hp.cell))}${hp.pins.length ? ` <span class="mut">${esc(hp.pins.join(" → "))}</span>` : ""}` +
      `<br><span class="mut">${esc(where)}</span></span></div>`;
  });
  h += `</div><div class="btns">${p.r ? `<button id="prep">Timing report</button>` : ""}` +
    `<button id="ptcl">Copy Tcl</button><button id="pclr">Clear</button></div>` +
    `<code class="tcl" id="tcl">${esc(tclFor(p))}</code>` +
    `<div class="mut">In Vivado, opens the same path in a timing window.</div>`;
  box.innerHTML = h;
  box.querySelectorAll(".hop").forEach(el => el.onclick = () => {
    const i = +el.dataset.s;
    if (i >= 0) { select(i); centerOn(i); }
  });
  if ($("prep")) $("prep").onclick = () => openReport(psel);
  $("ptcl").onclick = () => copyTcl(p);
  $("pclr").onclick = () => selectPath(-1);
}
function selectPath(j, zoom = true) {
  psel = j;
  onPath = null;
  if (j >= 0) {
    const p = P[j];
    onPath = new Uint8Array(N);
    for (const i of p.sites) onPath[i] = 1;
    pcheck = p.k;
  }
  pathPanel(); rebuild();
  if (j >= 0 && zoom) fitPath(P[j]);
  const row = $("plist").querySelector(".prow.on");
  if (row) row.scrollIntoView({ block: "nearest" });
  if (!$("drawer").hidden) { if (j >= 0 && P[j].r) openReport(j); else closeReport(); }
  setHash("path", j >= 0 ? P[j].id : null);
}
function visH() { return ch - ($("drawer").hidden ? 0 : $("drawer").offsetHeight); }
function fitPath(p) {
  if (!p.sites.length) return;
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const i of p.sites) {
    const k = 4 * i;
    b[0] = Math.min(b[0], R[k]); b[1] = Math.min(b[1], R[k + 1]);
    b[2] = Math.max(b[2], R[k + 2]); b[3] = Math.max(b[3], R[k + 3]);
  }
  const vh = visH(), fitS = Math.min(cw / M.width, ch / M.height);
  const pad = Math.max(b[2] - b[0], b[3] - b[1]) * 0.2 + 4;
  const s = Math.max(fitS * 0.96, Math.min(cw / (b[2] - b[0] + 2 * pad), vh / (b[3] - b[1] + 2 * pad), fitS * 16));
  view = { s, tx: cw / 2 - (b[0] + b[2]) / 2 * s, ty: vh / 2 - (b[1] + b[3]) / 2 * s };
  dirty = true;
}
function centerOn(i) {
  const k = 4 * i;
  view.tx = cw / 2 - (R[k] + R[k + 2]) / 2 * view.s;
  view.ty = visH() / 2 - (R[k + 1] + R[k + 3]) / 2 * view.s;
  dirty = true;
}
let siteByName = null;
function openReport(j) {
  const p = P[j];
  if (!siteByName) { siteByName = new Map(); for (let i = 0; i < N; i++) siteByName.set(siteName(i), i); }
  $("dtitle").textContent = `Path ${p.id} · ${p.k} · slack ${slackNs(p.sl)}`;
  $("rep").innerHTML = esc(p.r).replace(/\b[A-Z][A-Z0-9_]*_X\d+Y\d+\b/g,
    m => siteByName.has(m) ? `<a data-s="${siteByName.get(m)}">${m}</a>` : m);
  $("rep").scrollTop = 0;
  $("rep").querySelectorAll("a").forEach(a => a.onclick = () => { const i = +a.dataset.s; select(i); centerOn(i); });
  const was = $("drawer").hidden;
  $("drawer").hidden = false; $("hint").hidden = true;
  if (was && j === psel) fitPath(p);
}
function closeReport() { $("drawer").hidden = true; $("hint").hidden = false; }
$("dclose").onclick = closeReport;
function tclFor(p) {
  const obj = n => n.includes("/") ? `[get_pins {${n}}]` : `[get_ports {${n}}]`;
  return `report_timing -from ${obj(p.sp)} -to ${obj(p.ep)} -delay_type ${p.k === "hold" ? "min" : "max"} -name implview_${p.id}`;
}
function copyTcl(p) {
  const b = $("ptcl");
  const done = msg => { b.textContent = msg; setTimeout(() => { b.textContent = "Copy Tcl"; }, 1500); };
  const pick = () => {
    const r = document.createRange(); r.selectNodeContents($("tcl"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); done("Selected, Ctrl+C");
  };
  try { navigator.clipboard.writeText(tclFor(p)).then(() => done("Copied"), pick); } catch (e) { pick(); }
}
function setHash(k, v) {
  try {
    const h = new URLSearchParams(location.hash.slice(1));
    if (v === null) h.delete(k); else h.set(k, v);
    const str = h.toString();
    history.replaceState(null, "", str ? "#" + str : location.pathname + location.search);
  } catch (e) {}
}

// ---- search
let lower = null;
function search() {
  const q = $("q").value.trim();
  if (!q) { match = null; $("qres").textContent = ""; rebuild(); return; }
  if (!lower) { lower = new Array(M.leaves.length); for (let k = 0; k < lower.length; k++) lower[k] = cellName(k).toLowerCase(); }
  const ql = q.toLowerCase();
  let test;
  if (/[*?]/.test(ql)) {
    const re = new RegExp("^" + ql.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
    test = s => re.test(s);
  } else test = s => s.includes(ql);
  match = new Uint8Array(N);
  let nc = 0, ns = 0;
  for (let i = 0; i < N; i++) {
    for (let k = A.cstart[i]; k < A.cstart[i + 1]; k++) if (test(lower[k])) { nc++; match[i] = 1; }
    ns += match[i];
  }
  $("qres").textContent = `${nc.toLocaleString()} cells in ${ns.toLocaleString()} sites`;
  rebuild();
}
let qt = 0;
$("q").addEventListener("input", () => { clearTimeout(qt); qt = setTimeout(search, 180); });

// ---- panel
function fmt(v) { return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 1 }); }
function panel() {
  $("top").textContent = M.top || "design";
  $("part").textContent = M.part + (M.family ? `  (${M.family})` : "");
  $("meta").textContent = [M.tool, M.date, M.source, M.note].filter(Boolean).join(" · ");
  const tm = [];
  for (const [k, v, c] of [["WNS", M.wns, "setup"], ["WHS", M.whs, "hold"]]) if (v !== null) {
    const j = P.findIndex(p => p.k === c);
    tm.push(`<span class="${v < 0 ? "fail" : ""}"${j >= 0 ? ` data-j="${j}" title="Show the worst ${c} path"` : ""}>` +
            `${k} ${v >= 0 ? "+" : ""}${v.toFixed(3)} ns${v < 0 ? " (fails)" : ""}</span>`);
  }
  $("timing").innerHTML = tm.join(" &nbsp; ");
  $("timing").querySelectorAll("[data-j]").forEach(el => el.onclick = () => selectPath(+el.dataset.j));
  pathPanel();
  const total = M.groups.reduce((s, g) => s + g.cells, 0) || 1;
  $("legend").innerHTML = M.groups.map((g, k) =>
    `<div class="row${hidden.has(k) ? " off" : ""}" data-g="${k}" title="${esc(g.label)}"><span class="sw" style="background:${g[theme]}"></span>` +
    `<span class="grow">${esc(g.label)}</span><span class="num">${g.cells.toLocaleString()} · ${(100 * g.cells / total).toFixed(1)}%</span></div>`).join("") +
    `<div class="row"><span class="sw" style="background:${T().unused}"></span><span class="grow sub">unused site</span></div>`;
  $("legend").querySelectorAll("[data-g]").forEach(el => el.onclick = () => {
    const g = +el.dataset.g; hidden.has(g) ? hidden.delete(g) : hidden.add(g); panel(); rebuild();
  });
  $("util").innerHTML = M.util.filter(u => u[2]).map(([l, u, t]) =>
    `<tr><td class="sub">${l}</td><td class="r">${fmt(u)} / ${t.toLocaleString()}</td><td class="r">${(100 * u / t).toFixed(1)}%</td></tr>`).join("");
  const lay = [["unused", "Unused sites"], ["routing", "Routing usage" + (NH ? "" : " (not exported)")],
               ["cr", "Clock regions"], ["pblocks", "Pblocks"], ["labels", "Group labels"],
               ["allpaths", "All timing paths, as lines"]];
  $("layers").innerHTML = lay.filter(([k]) => (k !== "pblocks" || M.pblocks.length) && (k !== "allpaths" || P.length)).map(([k, l]) =>
    `<label><input type="checkbox" data-l="${k}" ${layers[k] ? "checked" : ""} ${k === "routing" && !NH ? "disabled" : ""}> ${l}</label>`).join("");
  $("layers").querySelectorAll("input").forEach(el => el.onchange = () => { layers[el.dataset.l] = el.checked; rebuild(); });
  $("theme").textContent = theme === "dark" ? "Light" : "Dark";
}
function setTheme(t) {
  theme = t;
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("implview-theme", t); } catch (e) {}
  heatCol = heatColors(); panel(); rebuild();
}
$("theme").onclick = () => setTheme(theme === "dark" ? "light" : "dark");
$("fit").onclick = fit;
$("save").onclick = () => cv.toBlob(b => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(b); a.download = `${M.top || "design"}_view.png`; a.click();
});

// ---- mouse
let drag = null;
cv.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false }; cv.setPointerCapture(e.pointerId); });
cv.addEventListener("pointermove", e => {
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  if (drag) {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; cv.classList.add("drag"); }
    if (drag.moved) { view.tx = drag.tx + dx; view.ty = drag.ty + dy; dirty = true; showTip(-1); return; }
  }
  const i = pick(px, py);
  if (i !== hover) { hover = i; dirty = true; }
  showTip(i, px, py);
});
cv.addEventListener("pointerup", e => {
  const r = cv.getBoundingClientRect();
  if (drag && !drag.moved) select(pick(e.clientX - r.left, e.clientY - r.top));
  drag = null; cv.classList.remove("drag");
});
cv.addEventListener("pointerleave", () => { hover = -1; showTip(-1); dirty = true; });
cv.addEventListener("wheel", e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const f = Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0015));
  const fitS = Math.min(cw / M.width, ch / M.height);
  const s = Math.min(Math.max(view.s * f, fitS * 0.5), 400);
  view.tx = px - (px - view.tx) * s / view.s; view.ty = py - (py - view.ty) * s / view.s; view.s = s;
  dirty = true;
}, { passive: false });
addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" && e.target.type !== "checkbox") { if (e.key === "Escape") { e.target.value = ""; search(); } return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;     // Ctrl+F, Ctrl+P: the browser's
  const key = e.key.toLowerCase();
  if (key === "f") fit();
  if ((key === "n" || key === "p") && P.length) {
    const js = P.map((p, j) => p.k === pcheck ? j : -1).filter(j => j >= 0);
    const at = js.indexOf(psel);
    selectPath(at < 0 ? js[0] : js[Math.min(Math.max(at + (key === "n" ? 1 : -1), 0), js.length - 1)]);
  }
  if (e.key === "Escape") {
    if (!$("drawer").hidden) { closeReport(); return; }
    select(-1); selectPath(-1, false); $("q").value = ""; search();
  }
});
new ResizeObserver(resize).observe(cv);

// #q=<search>&theme=dark&layer=routing&site=<name>&path=s3: start from that view
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.get("theme")) theme = hash.get("theme");
for (const l of (hash.get("layer") || "").split(",")) if (l in layers) layers[l] = !(l === "routing" && !NH);
setTheme(theme);
if (hash.get("q")) { $("q").value = hash.get("q"); search(); }
if (hash.get("site")) {
  const want = hash.get("site");
  for (let i = 0; i < N; i++) if (siteName(i) === want) { select(i); break; }
}
resize();
if (P.length) $("hint").textContent += " · N/P timing paths";
if (hash.get("path")) {
  const j = P.findIndex(p => p.id === hash.get("path"));
  if (j >= 0) selectPath(j);
}
requestAnimationFrame(frame);
})().catch(e => {
  const el = document.getElementById("loading") || document.getElementById("hint");
  el.textContent = "implview: " + e;
  console.error(e);
});
</script>
</body>
</html>
"""
