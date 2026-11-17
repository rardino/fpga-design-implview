"""implview command line."""

import argparse
import os
import re
import sys
import time

from . import __version__, cache, quartus, vivado
from .dcp import dcp_info, find_dcp
from .design import load_design
from .device import load_device


DEFAULT_PATHS = 50
DEFAULT_NETS = 20000


def _log(msg):
    sys.stderr.write(f"implview: {msg}\n")


def _atomic_run(outputs, args, a, version):
    """Run Vivado writing to temporary names, then move into place."""
    tmp = {p: p + f".tmp{os.getpid()}" for p in outputs}
    args = [tmp.get(x, x) for x in args]
    try:
        vivado.run(args, a.vivado, version)
        for p, t in tmp.items():
            os.replace(t, p)
    finally:
        for t in tmp.values():
            if os.path.exists(t):
                os.remove(t)


def _device_beside(design_path):
    """Device file of one design (a map inferred from its placement)."""
    return design_path.removesuffix(".ivd.gz") + ".ivdev.gz"


def export(a, src):
    """Design (and device on a cache miss) for a checkpoint, run directory,
    Quartus project or exported .ivd.gz. Returns (device, design, path of the
    design export)."""
    if src.endswith(".ivd.gz"):
        des = load_design(src)
        devp = _device_beside(src)
        if not os.path.exists(devp):
            devp = os.path.join(os.path.dirname(src), f"{des.part}.ivdev.gz")
        if not os.path.exists(devp):
            devp = cache.device_path(des.part)
        if not os.path.exists(devp):
            raise SystemExit(f"implview: no device file for {des.part}; run `fpga-design-implview device {des.part}`")
        des.source = src
        _check_placed(des)
        return load_device(devp), des, src

    project = quartus.find_project(src)
    if project is not None:
        return _export_quartus(a, project)

    dcp = find_dcp(src)
    info = dcp_info(dcp)
    part = info.get("part", "")
    routing = not getattr(a, "no_routing", False)
    npaths = max(getattr(a, "paths", DEFAULT_PATHS), 0)
    detail = getattr(a, "detail", False)
    nnets = getattr(a, "nets", DEFAULT_NETS)
    devp = cache.device_path(part)
    out = getattr(a, "output_design", None)
    dkey = [f"detail,nets={nnets}"] if detail else []
    desp = out or cache.design_path(dcp, routing, f"paths={npaths}", *dkey)
    full = cache.design_path(dcp, True, f"paths={npaths}")
    if not out and not detail:
        # an export with routing, or with detail, has it all
        for alt in [full] + [cache.design_path(dcp, True, f"paths={npaths}", f"detail,nets={nnets}")]:
            if not os.path.exists(desp) and os.path.exists(alt):
                desp = alt
    refresh = getattr(a, "refresh", False)
    if refresh or not os.path.exists(desp) or not os.path.exists(devp):
        args = ["design", os.path.abspath(dcp), desp]
        outputs = [desp]
        if refresh or not os.path.exists(devp):
            args += ["-device", devp]
            outputs.append(devp)
        if routing:
            args.append("-routing")
        args += ["-paths", str(npaths)]
        if detail:
            args += ["-detail", "-nets", str(nnets)]
        _log(f"{dcp}: {part}, top {info.get('top', '?')}, Vivado {info.get('vivado', '?')}: exporting")
        t0 = time.time()
        _atomic_run(outputs, args, a, info.get("vivado", ""))
        _log(f"export done in {time.time() - t0:.0f} s")
    des = load_design(desp)
    des.source = dcp
    if not routing:
        des.pips = {}
    _check_placed(des)
    return load_device(devp), des, desp


def _export_quartus(a, project):
    """Design and device of a compiled Quartus project, read from its files."""
    from . import altera
    if not project.netlist:
        quartus.read(project)                   # says what is missing
    out = getattr(a, "output_design", None)
    stamps = [os.stat(p).st_mtime_ns for p in (project.fit, project.sta) if p]
    desp = out or cache.design_path(project.netlist, "quartus", altera.MAP, *stamps)
    devp = _device_beside(desp)
    if getattr(a, "refresh", False) or not os.path.exists(desp) or not os.path.exists(devp):
        fit = quartus.read(project)
        info = fit.info
        _log(f"{project.netlist}: {info.get('part', '?')}, top {info.get('top', '?')}, "
             f"Quartus {info.get('quartus', '?')}")
        for w in fit.warnings:
            _log(f"warning: {w}")
        m = altera.write(fit, desp, devp)
        _log("device map inferred from the placement: " + m.summary(fit.utilization))
        if m.offgrid:
            kinds = list(dict.fromkeys(loc.rsplit("_", 1)[0] for loc in m.offgrid))
            _log(f"{len(m.offgrid)} cell{'s' * (len(m.offgrid) > 1)} on a location without "
                 f"coordinates ({', '.join(k + '_*' for k in kinds[:4])}): not drawn")
    des = load_design(desp)
    des.source = project.netlist
    return load_device(devp), des, desp


def _check_placed(des):
    """Warn when most cells have no site: a synthesized checkpoint (like the
    unsuffixed <top>.dcp Vivado copies into impl_1) draws almost nothing."""
    unplaced = des.unplaced()
    total = len(des.cells) + (unplaced or 0)
    if not unplaced or unplaced <= 0.01 * total:
        return
    if len(des.cells) >= total / 2:
        _log(f"warning: {unplaced:,} of {total:,} cells are not placed")
        return
    _log(f"warning: only {len(des.cells):,} of {total:,} cells have a site: "
         f"the checkpoint is not placed")
    if des.source.endswith(".dcp"):
        try:
            best = find_dcp(os.path.dirname(os.path.abspath(des.source)))
        except FileNotFoundError:
            return
        if os.path.abspath(best) != os.path.abspath(des.source):
            _log(f"the implemented checkpoint of this run is {best}")


def _render_options(a):
    """Highlight entries and render options: the command line wins over the
    [options] of the highlight file."""
    from .highlight import Entry, load_config
    entries, opts = [], {}
    if a.highlight:
        try:
            cfg = load_config(a.highlight)
        except (OSError, ValueError) as e:
            raise SystemExit(f"implview: {e}")
        entries, opts = cfg.entries, cfg.options
    entries += [Entry(g) for g in a.group or []]
    pick = lambda k, d: getattr(a, k) if getattr(a, k) is not None else opts.get(k, d)
    return entries, {"theme": pick("theme", "light"), "stats": pick("stats", False),
                     "labels": pick("labels", True), "panel": pick("panel", True),
                     "label_size": pick("label_size", 10.0),
                     "label_style": pick("label_style", "pill"),
                     "hide_other": opts.get("others") == "hide"}


def cmd_render(a):
    from .layout import build_layout
    from .placement import build_placement, group_cells
    entries, opt = _render_options(a)
    dev, des, _ = export(a, a.input)
    lay = build_layout(dev)
    groups, cg, unmatched = group_cells(des, min(a.max_groups, 8), entries, a.depth)
    for p in unmatched:
        _log(f"warning: highlight path {p!r} matches nothing")
    place = build_placement(dev, des, groups, cg, opt["hide_other"])
    if place.missing:
        _log(f"warning: {place.missing} cells are on sites the device file does not have")
    if a.save_highlight:
        from .highlight import write_config
        from .render import group_colors
        write_config(a.save_highlight, groups, group_colors("light", groups), des.top)
        _log(f"highlight file written to {a.save_highlight}")
    outs = a.output or [f"{des.top or 'design'}.png", f"{des.top or 'design'}.pdf"]
    if a.outdir:
        os.makedirs(a.outdir, exist_ok=True)
        outs = [o if os.path.isabs(o) else os.path.join(a.outdir, o) for o in outs]
    html = [o for o in outs if o.lower().endswith(".html")]
    pics = [o for o in outs if o not in html]
    written = []
    if pics:
        from .render import write_outputs
        written += write_outputs(pics, dev, lay, des, place,
                                 **{k: v for k, v in opt.items() if k != "hide_other"})
    if html:
        from .html import write_html
        for o in html:
            write_html(o, dev, lay, des, place)
            written.append(o)
    for w in written:
        print(w)


def cmd_export(a):
    a.output_design = os.path.abspath(a.output) if a.output else None
    dev, des, path = export(a, a.input)
    print(path)


def cmd_device(a):
    path = a.output or cache.device_path(a.part)
    if a.refresh or not os.path.exists(path):
        _atomic_run([path], ["device", a.part, path], a, "")
    print(path)


def cmd_info(a):
    project = quartus.find_project(a.input)
    if project is not None:
        fit = quartus.read(project)
        print(f"project     {project.dir} (revision {project.revision})")
        print(f"netlist     {project.netlist}")
        print(f"reports     {' '.join(p for p in (project.fit, project.sta) if p) or 'none'}")
        for k in ("part", "family", "top", "quartus"):
            print(f"{k:<11} {fit.info.get(k, '')}")
        return
    dcp = find_dcp(a.input)
    info = dcp_info(dcp)
    print(f"checkpoint  {dcp}")
    for k in ("part", "top", "product"):
        print(f"{k:<11} {info.get(k, '')}")
    devp = cache.device_path(info.get("part", ""))
    print(f"device      {devp} ({'cached' if os.path.exists(devp) else 'not cached'})")
    try:
        exe = vivado.command(a.vivado, info.get("vivado", ""))
        print(f"vivado      {' '.join(exe)}")
    except vivado.VivadoError as e:
        print(f"vivado      {e}")


def cmd_crosscheck(a):
    """--detail export vs the bitstream (prjxray bit2fasm) and Vivado checks."""
    import json
    import shutil
    from . import crosscheck as xc
    a.detail, a.nets, a.output_design = True, 10 ** 9, None
    a.no_routing = False
    dev, des, path = export(a, a.input)
    dcp = find_dcp(a.input)
    out = os.path.abspath(a.outdir)
    os.makedirs(out, exist_ok=True)
    info = dcp_info(dcp)
    args = ["crosscheck", os.path.abspath(dcp), out]
    if a.bit:
        args += ["-bit", os.path.abspath(a.bit)]
    t0 = time.time()
    _log(f"{dcp}: Vivado checks{'' if a.bit else ' and bitstream'}")
    vivado.run(args, a.vivado, info.get("vivado", ""))
    _log(f"done in {time.time() - t0:.0f} s")
    bit = os.path.abspath(a.bit) if a.bit else os.path.join(out, "design.bit")
    const = []
    with open(os.path.join(out, "const_pips.txt")) as f:
        for line in f:
            t, _, pip = line.rstrip("\n").partition("\t")
            if pip:
                const.append((t, pip))
    rv = open(os.path.join(out, "route_vs_nodes.txt")).read().splitlines()
    summ = [x for x in rv if x.startswith("SUMMARY")]
    nn, nbad = (int(x) for x in summ[0].split("\t")[1:3]) if summ else (0, 0)
    rs = open(os.path.join(out, "route_status.txt")).read()
    m = re.search(r"# of fully routed nets[ .:]*(\d+)", rs)
    rep = {"info": {"checkpoint": dcp, "part": des.part, "top": des.top,
                    "cells": len(des.cells), "nets": len(des.nets),
                    "export_pips": sum(len(n.pips) for n in des.nets), "const_pips": len(const)},
           "vivado": {"fully_routed_nets": int(m.group(1)) if m else None,
                      "route_status_errors": len(re.findall(r"nets with routing errors[ .:]*([1-9])", rs)),
                      "route_vs_nodes_nets": nn, "route_vs_nodes_disagree": nbad,
                      "route_vs_nodes_agree_pct": xc._pct(nn - nbad, nn)}}
    arch = dev.info.get("architecture", "")
    fam, dbpart = xc.find_db(arch, des.part, a.db_root)
    if fam:
        t0 = time.time()
        text = xc.bit2fasm(bit, fam, dbpart, out)
        with open(os.path.join(out, "design.fasm"), "w") as f:
            f.write(text)
        fasm = xc.parse_fasm(text)
        _log(f"bit2fasm ({dbpart}): {len(fasm):,} features in {time.time() - t0:.0f} s")
        rep["info"]["prjxray_db"] = f"{fam} ({dbpart})"
        rep.update(xc.compare(dev, des, fasm, fam, const))
    else:
        rep["info"]["bitstream_check"] = (f"no Project X-Ray database for {arch or '?'} {des.part}: "
                                          "only Vivado-internal checks")
    if a.nextpnr:
        from . import openflow
        from .design import load_design as _ld
        ftxt = open(a.nextpnr_fasm).read()
        op = os.path.join(out, "nextpnr.ivd.gz")
        openflow.write_design(a.nextpnr, ftxt, des.part, op, des.top)
        od = _ld(op)
        vu, ou = openflow.vivado_util(des), openflow.vivado_util(od)
        vint = sum(1 for n in des.nets for x in n.pips if re.match(r"INT_[LR]_X", x)
                   and "->>" in x)
        rep["open_flow"] = {
            "vivado": {"luts": vu["LUT"], "ffs": vu["FLOP_LATCH"], "carry4": vu["CARRY"],
                       "slices": openflow.slices_used(des.cells), "int_pips": vint},
            "nextpnr": {"luts": ou["LUT"], "ffs": ou["FLOP_LATCH"], "carry4": ou["CARRY"],
                        "slices": openflow.slices_used(od.cells),
                        "int_pips": sum(od.pips.values())},
            "note": "same RTL, independent synthesis (Yosys synth_xilinx) and place and route "
                    "(nextpnr-xilinx): a qualitative comparison, not a match"}
    with open(os.path.join(out, "crosscheck.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(os.path.join(out, "crosscheck.md"), "w") as f:
        f.write(xc.markdown(rep, f"{des.top} ({des.part})"))
    print(os.path.join(out, "crosscheck.md"))


def cmd_import_nextpnr(a):
    from . import openflow
    u = openflow.write_design(a.json, open(a.fasm).read(), a.part, a.output, a.top)
    _log(", ".join(f"{k} {v}" for k, v in sorted(u.items())))
    print(a.output)


def cmd_tcl(a):
    print(os.path.join(vivado.TCL_DIR, "implview.tcl"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fpga-design-implview",
        description="Pictures of implemented designs (Vivado, Quartus): the device "
                    "resources and what the design uses of them.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--vivado", help="command that runs Vivado (default: $IMPLVIEW_VIVADO, "
                                     "vivado on the PATH, or an install matching the checkpoint)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def source(p):
        p.add_argument("input", help="routed checkpoint, run directory (impl_1), project "
                                     "directory (Vivado or compiled Quartus), or an exported .ivd.gz")
        p.add_argument("--no-routing", action="store_true", help="skip the routing usage export")
        p.add_argument("--paths", type=int, default=DEFAULT_PATHS, metavar="N",
                       help=f"export the N worst setup and N worst hold paths (default "
                            f"{DEFAULT_PATHS}, 0 for none)")
        p.add_argument("--refresh", action="store_true", help="export again, ignore the cache")
        p.add_argument("--detail", action="store_true",
                       help="also export cell settings (LUT equations, FF controls, BRAM/DSP/IO/"
                            "clock properties), nets and their routes (slower, bigger)")
        p.add_argument("--nets", type=int, default=DEFAULT_NETS, metavar="N",
                       help=f"with --detail: draw the routes of at most N nets (default "
                            f"{DEFAULT_NETS}; clock nets and timing path nets first)")

    p = sub.add_parser("render", help="write PNG/PDF/SVG/HTML pictures of a design")
    source(p)
    p.add_argument("-o", "--output", action="append",
                   help="output file, by extension: .png .pdf .svg .html (repeatable; "
                        "default <top>.png and <top>.pdf)")
    p.add_argument("-O", "--outdir", help="directory for the outputs")
    p.add_argument("--theme", choices=("light", "dark"), help="default light")
    p.add_argument("-H", "--highlight", metavar="FILE",
                   help="highlight file (TOML): which hierarchy to color and how")
    p.add_argument("-g", "--group", action="append", metavar="PATH",
                   help="hierarchy path to color, palette colors, repeatable (* inside a "
                        "level, ** any depth)")
    p.add_argument("--depth", type=int, help="group by hierarchy cut at this depth")
    p.add_argument("--max-groups", type=int, default=8, help="automatic groups (max 8)")
    p.add_argument("--stats", action=argparse.BooleanOptionalAction,
                   help="utilization table and cells per group in the panel (default off)")
    p.add_argument("--labels", action=argparse.BooleanOptionalAction,
                   help="group and clock region names on the device (default on)")
    p.add_argument("--label-size", type=float, metavar="PT",
                   help="font size of the group names on the device (default 10)")
    p.add_argument("--label-style", choices=("pill", "text"),
                   help="group names as colored badges (default) or plain text")
    p.add_argument("--panel", action=argparse.BooleanOptionalAction,
                   help="design, part and legend on the right (default on)")
    p.add_argument("--save-highlight", metavar="FILE",
                   help="write the groups used as a highlight file, to edit")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("export", help="only export a design (and its device) from Vivado")
    source(p)
    p.add_argument("-o", "--output", help="design file (default: in the cache)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("device", help="export the resource map of a part into the cache")
    p.add_argument("part", help="e.g. xcvu37p-fsvh2892-2L-e")
    p.add_argument("-o", "--output", help="device file (default: in the cache)")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_device)

    p = sub.add_parser("info", help="part, top and cache status of a checkpoint")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("crosscheck", help="check a --detail export against the bitstream "
                                           "(7-series, Project X-Ray bit2fasm) and Vivado")
    source(p)
    p.add_argument("-O", "--outdir", default="crosscheck", help="report directory")
    p.add_argument("--bit", help="bitstream of the checkpoint (default: written by Vivado)")
    p.add_argument("--db-root", help="prjxray-db root (default: $IMPLVIEW_PRJXRAY_DB or known places)")
    p.add_argument("--nextpnr", metavar="JSON", help="nextpnr-xilinx --write JSON of the same "
                   "design: compare the open-flow implementation (needs --nextpnr-fasm)")
    p.add_argument("--nextpnr-fasm", metavar="FASM", help="nextpnr-xilinx --fasm output")
    p.set_defaults(func=cmd_crosscheck)

    p = sub.add_parser("import-nextpnr", help="an open-flow (nextpnr-xilinx) result as a "
                                              "design file, to render like a Vivado export")
    p.add_argument("json", help="nextpnr-xilinx --write JSON (placed and routed)")
    p.add_argument("--fasm", required=True, help="nextpnr-xilinx --fasm output")
    p.add_argument("--part", required=True, help="the part, e.g. xc7s25csga324-1 (its device "
                                                 "file comes from the cache or `device`)")
    p.add_argument("--top", default="")
    p.add_argument("-o", "--output", required=True, help="design file, .ivd.gz")
    p.set_defaults(func=cmd_import_nextpnr)

    p = sub.add_parser("tcl", help="path of implview.tcl, to export from a Vivado session")
    p.set_defaults(func=cmd_tcl)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except (vivado.VivadoError, quartus.QuartusError, FileNotFoundError, RuntimeError) as e:
        raise SystemExit(f"implview: {e}")


if __name__ == "__main__":
    main()
