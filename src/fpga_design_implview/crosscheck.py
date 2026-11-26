"""Cross-check a --detail export against the bitstream (7-series).

The bitstream Vivado writes from the same checkpoint is decoded with Project
X-Ray's bit2fasm into FASM features, an independent view of what is
configured. The export is then compared feature by feature:

  sites     used SLICEs, and LUT / FF BELs, export vs FASM
  lut_init  physical LUT contents (logical INIT through the pin map) vs the
            FASM <L>LUT.INIT bits
  ff        FF init/reset (ZINI, ZRST) per FF; CE, SR, sync/async, latch and
            clock inversion per slice
  pips      the pips of every net (signal from the export, VCC/GND from
            Vivado) vs the FASM routing features; only pips that have
            configuration bits in the database count

UltraScale(+) and Versal have no public bitstream database here: for them
only Vivado-internal checks run (route status, ROUTE vs get_nodes).
"""

import glob
import json
import os
import re
import shlex
import subprocess
from collections import defaultdict

from .lut import lut_inputs, parse_init, parse_pinmap, physical_init


class CrosscheckError(Exception):
    """A missing tool or database, or a failed bit2fasm run (reported without a traceback)."""


# ---- database and bit2fasm

def find_db(arch, part, root=None):
    """(family directory, part name in the database) for a Vivado part."""
    m = re.match(r"(xc7[a-z]\d+t?)(i?)([a-z]+\d+)(-\S+)?$", part)
    cands = [part]
    if m:
        dev, _, pkg, spd = m.groups()
        spd = spd or "-1"
        for s in (spd, re.sub(r"[A-Za-z]+$", "", spd), "-1"):
            cands.append(f"{dev}{pkg}{s}")
    r = root or os.environ.get("IMPLVIEW_PRJXRAY_DB")
    if r:
        fam = os.path.join(os.path.expanduser(r), arch)
        for c in dict.fromkeys(cands):
            if os.path.isdir(os.path.join(fam, c)):
                return fam, c
    return None, None


def bit2fasm(bit, fam, dbpart, work):
    """FASM text of a bitstream. $IMPLVIEW_BIT2FASM is a command taking
    --db-root/--part/<bit> (e.g. `python3 .../bit2fasm.py --bitread bitread`)."""
    if not os.environ.get("IMPLVIEW_BIT2FASM"):
        raise CrosscheckError("bit2fasm not found: set IMPLVIEW_BIT2FASM")
    cmd = shlex.split(os.environ["IMPLVIEW_BIT2FASM"])
    p = subprocess.run(cmd + ["--db-root", fam, "--part", dbpart, bit], capture_output=True,
                       text=True)
    if p.returncode != 0:
        raise CrosscheckError(f"bit2fasm failed: {p.stderr[-2000:]}")
    return p.stdout


# ---- FASM

_FASM = re.compile(r"^([^\s\[=]+)(?:\[(\d+)(?::(\d+))?\])?(?:\s*=\s*(\d+)'b([01_]+))?\s*$")


def parse_fasm(text):
    """{feature: value}: 1 for a plain feature, the integer of a [hi:lo] range."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _FASM.match(line)
        if not m:
            continue
        name, hi, lo, _, bits = m.groups()
        if hi is None:
            out[name] = 1
            continue
        lo = int(lo) if lo is not None else int(hi)
        v = int(bits.replace("_", ""), 2) if bits is not None else 1
        out[name] = out.get(name, 0) | (v << lo)
    return out


def segbit_features(fam, tile_type, cache={}):
    """Feature names (without the tile type) that have bits in the database."""
    key = (fam, tile_type)
    if key not in cache:
        names = set()
        for path in glob.glob(os.path.join(fam, f"segbits_{tile_type.lower()}.db")) + \
                glob.glob(os.path.join(fam, f"segbits_{tile_type.lower()}.*.db")):
            if "origin_info" in path:
                continue
            with open(path) as f:
                for line in f:
                    n = line.split(" ", 1)[0]
                    n = n.split("[", 1)[0]
                    names.add(n.split(".", 1)[1] if "." in n else n)
        cache[key] = names
    return cache[key]


# ---- comparisons

def _pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def _slice_keys(dev):
    """site name -> FASM prefix TILE.SLICEL_X1 (X0/X1: the site order in the tile)."""
    by_tile = defaultdict(list)
    for s in dev.sites:
        if s.type in ("SLICEL", "SLICEM"):
            by_tile[s.tile].append(s)
    out = {}
    for t, ss in by_tile.items():
        for k, s in enumerate(sorted(ss, key=lambda s: s.x)):
            out[s.name] = f"{dev.tile_name[t]}.{s.type}_X{k}"
    return out


def compare(dev, des, fasm, fam, const_pips=()):
    """The report dict: per category, counts, match % and mismatch samples."""
    sk = _slice_keys(dev)
    pin_nets = des.pin_nets()
    rep = {}

    # used slices and BELs
    v_slices = {sk[c.site] for c in des.cells if c.site in sk}
    # an unused slice can still decode its defaults (NOCLKINV, PRECYINIT.C0,
    # the SLICEM DI1MUX settings)
    f_slices = {".".join(k.split(".")[:2]) for k in fasm if k.split(".")[1:2] and
                k.split(".")[1].startswith("SLICE") and
                not k.endswith((".NOCLKINV", ".PRECYINIT.C0")) and ".DI1MUX." not in k}
    v_lut, v_ff = set(), set()
    luts = defaultdict(dict)        # slice key + letter -> {6: cell idx, 5: cell idx}
    ffs = []
    for i, c in enumerate(des.cells):
        if c.site not in sk:
            continue
        p = sk[c.site]
        if re.fullmatch(r"[A-D][56]LUT", c.bel):
            luts[(p, c.bel[0])][int(c.bel[1])] = i
            if lut_inputs(c.ref):
                v_lut.add(f"{p}.{c.bel[0]}LUT")
        elif re.fullmatch(r"[A-D]5?FF", c.bel):
            ffs.append((i, p, c.bel))
            v_ff.add(f"{p}.{c.bel}")
    f_lut = {k.rsplit(".", 1)[0] for k, v in fasm.items() if k.endswith("LUT.INIT") and v}
    f_ff = {re.sub(r"MUX\.[^.]+$", "", k) for k in fasm if re.search(r"\.[A-D]5?FFMUX\.[^.]+$", k)}
    rep["sites"] = {
        "slices_vivado": len(v_slices), "slices_fasm": len(f_slices),
        "slices_match_pct": _pct(len(v_slices & f_slices), len(v_slices | f_slices)),
        "slices_only_vivado": sorted(v_slices - f_slices)[:20],
        "slices_only_fasm": sorted(f_slices - v_slices)[:20],
        "lut_bels_vivado": len(v_lut), "lut_bels_fasm_nonzero": len(f_lut),
        "lut_bels_found_pct": _pct(len(v_lut & f_lut), len(v_lut)),
        "lut_bels_only_fasm": len(f_lut - v_lut),
        "lut_bels_only_fasm_note": "route-thru / constant LUTs and SRL/LUTRAM have INIT bits "
                                   "but no LUT1-6 cell",
        "ff_bels_vivado": len(v_ff), "ff_bels_fasm": len(f_ff),
        "ff_bels_match_pct": _pct(len(v_ff & f_ff), len(v_ff | f_ff)),
        "ff_only_vivado": sorted(v_ff - f_ff)[:20], "ff_only_fasm": sorted(f_ff - v_ff)[:20],
    }

    # LUT contents, physical
    ok, bad, skipped, samples = 0, 0, 0, []
    for (p, letter), m in sorted(luts.items()):
        f = fasm.get(f"{p}.{letter}LUT.INIT", 0)
        for size, i in m.items():
            c = des.cells[i]
            n = lut_inputs(c.ref)
            props = des.detail.get(i, {})
            if not n or "INIT" not in props:
                skipped += 1
                continue
            init, _ = parse_init(props["INIT"])
            pm = parse_pinmap(props.get("PINMAP", ""))
            phys = physical_init(init, n, pm)
            if size == 5:
                mask = (1 << 32) - 1                    # A6 = 0 half
            elif 5 in m or 6 not in pm.values():
                mask = ((1 << 32) - 1) << 32            # A6 = 1 half (A6 tied high)
            else:
                mask = (1 << 64) - 1
            if (phys ^ f) & mask == 0:
                ok += 1
            else:
                bad += 1
                if len(samples) < 20:
                    samples.append({"cell": c.name, "bel": f"{p}.{letter}{size}LUT",
                                    "init": props["INIT"], "pins": props.get("PINMAP", ""),
                                    "expect": f"{phys & mask:016x}", "fasm": f"{f & mask:016x}"})
    rep["lut_init"] = {"compared": ok + bad, "match": ok, "match_pct": _pct(ok, ok + bad),
                       "skipped_no_init": skipped, "mismatches": samples}

    # flip-flops
    ff_ok = ff_bad = 0
    ff_bad_list = []
    sl = defaultdict(lambda: {"ce": False, "sr": False, "sync": False, "latch": False, "inv": False})
    for i, p, bel in ffs:
        c = des.cells[i]
        props = des.detail.get(i, {})
        try:
            init = parse_init(props.get("INIT", "1'b0"))[0]
        except ValueError:
            init = 0
        zini = init == 0
        zrst = c.ref in ("FDRE", "FDCE", "LDCE")
        got_zini = f"{p}.{bel}.ZINI" in fasm
        got_zrst = f"{p}.{bel}.ZRST" in fasm
        if (zini, zrst) == (got_zini, got_zrst):
            ff_ok += 1
        else:
            ff_bad += 1
            if len(ff_bad_list) < 20:
                ff_bad_list.append({"cell": c.name, "ref": c.ref, "bel": f"{p}.{bel}",
                                    "expect": {"ZINI": zini, "ZRST": zrst},
                                    "fasm": {"ZINI": got_zini, "ZRST": got_zrst}})
        s = sl[p]
        s["ce"] |= (i, "CE") in pin_nets or (i, "GE") in pin_nets
        s["sr"] |= any((i, q) in pin_nets for q in ("R", "S", "CLR", "PRE"))
        s["sync"] |= c.ref in ("FDRE", "FDSE")
        s["latch"] |= c.ref.startswith("LD")
        inv = props.get("IS_C_INVERTED", props.get("IS_G_INVERTED", "1'b0"))
        s["inv"] |= inv.endswith("1")
    ctrl = {}
    for key, feat in (("ce", "CEUSEDMUX"), ("sr", "SRUSEDMUX"), ("sync", "FFSYNC"),
                      ("latch", "LATCH"), ("inv", "CLKINV")):
        good, wrong = 0, []
        for p, s in sl.items():
            if s[key] == (f"{p}.{feat}" in fasm):
                good += 1
            elif len(wrong) < 10:
                wrong.append(p)
        ctrl[feat] = {"slices": len(sl), "match_pct": _pct(good, len(sl)), "mismatches": wrong}
    rep["ff"] = {"ffs": ff_ok + ff_bad, "init_reset_match_pct": _pct(ff_ok, ff_ok + ff_bad),
                 "init_reset_mismatches": ff_bad_list, "slice_controls": ctrl}

    # pips
    tix = dev.tile_index()
    v_pips, nocfg, bidir = set(), 0, []
    sources = [(None, x) for n in des.nets for x in n.pips] + [(t, x) for t, x in const_pips]
    for _, pip in sources:
        m = re.match(r"^([^/]+)/([^.]+)\.(.+?)(<<->>|->>|->)(.+)$", pip)
        if not m:
            continue
        tile, ttype, a, arrow, b = m.groups()
        feats = segbit_features(fam, ttype)
        fwd, rev = f"{b}.{a}" in feats, f"{a}.{b}" in feats
        if arrow == "<<->>" and fwd and rev:
            # a bidirectional pip does not say which way it is used
            bidir.append((f"{tile}.{b}.{a}", f"{tile}.{a}.{b}"))
        elif fwd:
            v_pips.add(f"{tile}.{b}.{a}")
        elif arrow == "<<->>" and rev:
            v_pips.add(f"{tile}.{a}.{b}")
        else:
            nocfg += 1
    f_pips = set()
    for k in fasm:
        parts = k.split(".")
        # ENABLE_BUFFER (HCLK buffer enables) and VREF are tile settings, not pips
        if len(parts) != 3 or parts[1] in ("ENABLE_BUFFER", "VREF") or parts[1].startswith(("SLICE", "IOB", "RAMB", "DSP", "MMCM", "PLL",
                                                    "BUFG", "BUFH", "BUFR", "IDELAY", "OLOGIC", "ILOGIC")):
            continue
        t = tix.get(parts[0])
        if t is None:
            continue
        if f"{parts[1]}.{parts[2]}" in segbit_features(fam, dev.tile_type[t]):
            f_pips.add(k)
    for x, y in bidir:
        v_pips.add(y if y in f_pips and x not in f_pips else x)
    both = v_pips & f_pips
    # unused BUFGCTRL input muxes decode as set (default bits): not a route
    # and the BRAM upper address muxes of a used BRAM tile: default bits, not routes
    dflt = {k for k in f_pips - v_pips if re.search(
        r"\.CLK_BUFG_BUFGCTRL\d+_I[01]\.|\.BRAM_ADDR(ARDADDR|BWRADDR)U\d+\.", k)}
    rep["pips"] = {
        "vivado_pips_with_bits": len(v_pips), "vivado_pips_without_bits": nocfg,
        "fasm_pips": len(f_pips), "match": len(both),
        "vivado_found_in_fasm_pct": _pct(len(both), len(v_pips)),
        "fasm_explained_by_vivado_pct": _pct(len(both), len(f_pips)),
        "only_vivado": sorted(v_pips - f_pips)[:30], "only_fasm": sorted(f_pips - v_pips)[:30],
        "only_vivado_count": len(v_pips - f_pips), "only_fasm_count": len(f_pips - v_pips),
        "fasm_default_bufgctrl_inputs": len(dflt),
        "fasm_explained_excl_defaults_pct": _pct(len(both), len(f_pips) - len(dflt)),
        "only_fasm_excl_defaults": sorted(f_pips - v_pips - dflt)[:30],
    }
    return rep


def markdown(rep, title):
    s, f, l, p = rep.get("sites", {}), rep.get("ff", {}), rep.get("lut_init", {}), rep.get("pips", {})
    lines = [f"# Crosscheck: {title}", ""]
    for k, v in rep.get("info", {}).items():
        lines.append(f"- {k}: {v}")
    if s:
        lines += ["", "| Category | Compared | Match |", "|---|---:|---:|",
                  f"| Used SLICEs (export vs FASM) | {s['slices_vivado']} / {s['slices_fasm']} | {s['slices_match_pct']}% |",
                  f"| LUT BELs found in FASM | {s['lut_bels_vivado']} | {s['lut_bels_found_pct']}% |",
                  f"| FF BELs (export vs FASM FFMUX) | {s['ff_bels_vivado']} / {s['ff_bels_fasm']} | {s['ff_bels_match_pct']}% |",
                  f"| LUT physical INIT | {l['compared']} | {l['match_pct']}% |",
                  f"| FF ZINI/ZRST | {f['ffs']} | {f['init_reset_match_pct']}% |"]
        for feat, c in f["slice_controls"].items():
            lines.append(f"| Slice {feat} | {c['slices']} | {c['match_pct']}% |")
        lines += [f"| PIPs: Vivado (with bits) found in FASM | {p['vivado_pips_with_bits']} | {p['vivado_found_in_fasm_pct']}% |",
                  f"| PIPs: FASM explained by Vivado | {p['fasm_pips']} | {p['fasm_explained_by_vivado_pct']}% |",
                  f"| PIPs: FASM explained, without {p['fasm_default_bufgctrl_inputs']} default BUFGCTRL input / BRAM ADDR*U muxes | {p['fasm_pips'] - p['fasm_default_bufgctrl_inputs']} | {p['fasm_explained_excl_defaults_pct']}% |",
                  "", f"LUT BELs with INIT bits but no LUT1-6 cell: {s['lut_bels_only_fasm']} "
                  f"({s['lut_bels_only_fasm_note']}). Vivado pips without configuration bits "
                  f"(site pips, pseudo pips): {p['vivado_pips_without_bits']}."]
    for k in ("vivado", "open_flow"):
        if k in rep:
            lines += ["", f"## {k}", "", "```", json.dumps(rep[k], indent=1)[:4000], "```"]
    lines += ["", "Mismatch samples are in the JSON report."]
    return "\n".join(lines) + "\n"
