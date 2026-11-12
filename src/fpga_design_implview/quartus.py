"""A compiled Quartus project, read from the files the compilation leaves.

Quartus has no command that lists the resources of a device, and the placement
is already in text files, so nothing is run:

- the post-fit netlist of the EDA Netlist Writer (<revision>.vo or .vho, under
  simulation/) names the location of every atom:

      // Location: LABCELL_X12_Y7_N0
      cyclonev_lcell_comb \\u_core|u_fft|Add0~1 ( ...

- the Fitter report (<revision>.fit.rpt): part, family, utilization, the pin
  and I/O bank of each pad;
- the Timing Analyzer summary (<revision>.sta.summary): the worst slacks.

`implview.altera` turns the atoms into the device and design files.
"""

import glob
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

NETLISTS = (".vo", ".vho")
LOC_RE = re.compile(r"(\w+?)_X(\d+)_Y(\d+)(?:_N(\d+))?$")
VERSION_RE = re.compile(r"(\d+(?:\.\d+)+) Build \d+ .*\b(\w+) (?:Edition|Version)\b")
USAGE_RE = re.compile(r"([\d,]+)(?:\s*/\s*([\d,]+))?")

# Rows of the Fitter Resource Usage Summary shown as the utilization, with
# the label to show (\1: the block name of the row, M9K or M10K).
UTILIZATION = (
    (r"Logic utilization", "ALM"),
    (r"Total logic elements", "LE"),
    (r"Total registers", "Register"),
    (r"Total LABs", "LAB"),
    (r"(M\d+K|MLAB)s?( blocks)?$", r"\1"),
    (r"Total block memory bits", "Memory bit"),
    (r"Total DSP Blocks", "DSP"),
    (r"Embedded Multiplier 9-bit elements", "Multiplier 9-bit"),
    (r"I/O pins", "I/O pin"),
    (r"(Fractional )?PLLs$", "PLL"),
)


class QuartusError(RuntimeError):
    pass


@dataclass
class Project:
    dir: str
    revision: str
    netlist: str = ""       # post-fit netlist, "" if the project has none
    fit: str = ""           # Fitter report
    sta: str = ""           # Timing Analyzer summary, else its report


@dataclass
class Fit:
    """What the files say about a compiled revision."""
    info: dict = field(default_factory=dict)        # part, family, top, quartus, date
    atoms: list = field(default_factory=list)       # (kind, x, y, n, atom type, name)
    pads: dict = field(default_factory=dict)        # (x, y, z) -> (pin, bank, port)
    utilization: list = field(default_factory=list)  # (label, used, total)
    warnings: list = field(default_factory=list)


def _revisions(d, qpf=None):
    """Revisions of the project in a directory: the ones its .qpf lists, else
    one per settings file."""
    revs = []
    for path in [qpf] if qpf else sorted(glob.glob(os.path.join(d, "*.qpf"))):
        with open(path, errors="replace") as f:
            revs += re.findall(r'^\s*PROJECT_REVISION\s*=\s*"?([^"\n]+?)"?\s*$', f.read(), re.M)
    if not revs:
        revs = [os.path.basename(p)[:-4] for p in sorted(glob.glob(os.path.join(d, "*.qsf")))]
    return list(dict.fromkeys(revs))


def _setting(qsf, name):
    try:
        with open(qsf, errors="replace") as f:
            m = re.findall(rf'^\s*set_global_assignment\s+-name\s+{name}\s+(?:"([^"\n]*)"|(\S+))',
                           f.read(), re.M)
    except OSError:
        return ""
    return (m[-1][0] or m[-1][1]).strip() if m else ""


def _newest(paths, key=os.path.getmtime):
    paths = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
    return max(paths, key=key) if paths else ""


def _written(path):
    """When a netlist was written, from its header (the file times of a
    checkout say nothing), then Verilog before VHDL."""
    info, ports = {}, {}
    lead = "-- " if path.lower().endswith(".vho") else "// "
    with open(path, errors="replace") as f:
        for _, line in zip(range(40), f):
            _header(line, lead, info, ports)
    return info.get("date", ""), path.lower().endswith(".vo")


def _project(d, rev):
    qsf = os.path.join(d, rev + ".qsf")
    outs = [d, os.path.join(d, "output_files")]
    out = _setting(qsf, "PROJECT_OUTPUT_DIRECTORY")
    if out:
        outs.insert(0, os.path.join(d, out))
    p = Project(d, rev)
    p.fit = _newest(os.path.join(o, rev + ".fit.rpt") for o in outs)
    p.sta = (_newest(os.path.join(o, rev + ".sta.summary") for o in outs)
             or _newest(os.path.join(o, rev + ".sta.rpt") for o in outs))
    # <revision>.vo, in the directory of the simulator it was written for; the
    # <revision>_<corner>.vo next to it are the same netlist
    sims = glob.glob(os.path.join(glob.escape(d), "simulation", "**", ""), recursive=True)
    p.netlist = _newest((os.path.join(o, rev + e) for o in sims + outs for e in NETLISTS), _written)
    return p


def find_project(path):
    """The compiled revision behind a path: a project directory, its .qpf or
    .qsf, or the post-fit netlist itself. None if the path is not a Quartus
    one. With several revisions, the one compiled last."""
    low = path.lower()
    if os.path.isdir(path):
        d, revs = path, _revisions(path)
    elif low.endswith(NETLISTS):
        return _from_netlist(path)
    elif low.endswith(".qpf"):
        d = os.path.dirname(path) or "."
        revs = _revisions(d, path) or [os.path.basename(path)[:-4]]
    elif low.endswith(".qsf"):
        d, revs = os.path.dirname(path) or ".", [os.path.basename(path)[:-4]]
    else:
        return None
    found = [_project(d, r) for r in revs]
    done = [p for p in found if p.netlist]
    if done:
        return max(done, key=lambda p: _written(p.netlist))
    return found[0] if found else None


def _from_netlist(path):
    """The reports of a netlist: next to it or in the project above it."""
    rev = os.path.splitext(os.path.basename(path))[0]
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        p = _project(d, rev)
        if p.fit or os.path.exists(os.path.join(d, rev + ".qsf")):
            p.netlist = path
            return p
        d = os.path.dirname(d)
    return Project(os.path.dirname(path) or ".", rev, netlist=path)


def _date(text, fmt):
    try:
        return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def _version(text):
    m = VERSION_RE.search(text)
    return f"{m.group(1)} {m.group(2)}" if m else ""


def _instance(line, vhdl):
    """(atom type, name) of the instance a Location comment is about."""
    line = line.strip()
    if vhdl:                            # \name\ : type
        name, _, ref = line.rpartition(" : ")
        name = name.strip()
        if len(name) > 1 and name[0] == name[-1] == "\\":
            name = name[1:-1].replace("\\\\", "\\")
        return ref.strip(), name
    ref, _, rest = line.partition(" ")  # type \name (
    rest = rest.strip()
    if rest.startswith("\\"):
        return ref, rest[1:].split(None, 1)[0]
    return ref, rest.split("(", 1)[0].strip()


def read_netlist(path):
    """(info, atoms, ports) of a post-fit netlist. An atom is (location kind,
    x, y, n, atom type, name), x -1 for a location without coordinates (PLL_1);
    ports is {port: package pin}."""
    vhdl = path.lower().endswith(".vho")
    lead = "-- " if vhdl else "// "
    mark = lead + "Location: "
    skip = len(mark)
    info, atoms, ports = {}, [], {}
    loc = None
    with open(path, errors="replace") as f:
        for line in f:
            if line.startswith(mark):
                loc = line[skip:].strip()
            elif loc is not None:
                if not line.strip() or line.startswith(lead):
                    continue
                ref, name = _instance(line, vhdl)   # the instance follows its location
                m = LOC_RE.match(loc)
                if m:
                    atoms.append((m.group(1), int(m.group(2)), int(m.group(3)),
                                  int(m.group(4) or 0), ref, name))
                else:
                    atoms.append((loc, -1, -1, 0, ref, name))
                loc = None
            elif not atoms:
                _header(line, lead, info, ports)
    return info, atoms, ports


def _header(line, lead, info, ports):
    if line.startswith(lead):
        text = line[len(lead):].strip()
        if text.startswith("VERSION"):
            info["quartus"] = _version(text)
        elif text.startswith("DATE"):
            info["date"] = _date(text[4:].strip(' "'), "%m/%d/%Y %H:%M:%S")
        elif text.startswith("Device:"):
            w = text.split()
            info["part"] = w[2] if len(w) > 2 else ""
        elif "=>  Location: PIN_" in text:
            port, _, rest = text.partition("=>")
            ports[port.strip()] = rest.split("PIN_", 1)[1].split(",")[0].strip()
    elif "top" not in info:
        m = re.match(r"(?:module|ENTITY)\s+(\S+?)\s*(?:\(|;|IS\b|$)", line)
        if m:
            info["top"] = m.group(1)


def _panels(path, wanted):
    """Panels of a report by title: the rows of each, a list of cells. The
    first cell keeps its indentation (sub-rows are indented)."""
    out = {}
    rows = None
    border = False
    with open(path, errors="replace") as f:
        for line in f:
            if line.startswith("+-"):
                border = True
                continue
            if line.startswith(";"):
                cells = line.rstrip().rstrip(";")[1:].split(";")
                if len(cells) == 1 and border:
                    title = cells[0].strip()
                    rows = out.setdefault(title, []) if wanted(title) else None
                elif rows is not None:
                    rows.append([cells[0][1:].rstrip()] + [c.strip() for c in cells[1:]])
            elif not line.strip():
                rows = None
            border = False
    return out


def _int(text):
    return int(text.replace(",", ""))


def read_fit_report(path):
    """(info, utilization, pads) of a Fitter report."""
    titles = {"Fitter Summary", "Fitter Resource Usage Summary",
              "Input Pins", "Output Pins", "Bidir Pins"}
    panels = _panels(path, titles.__contains__)
    info = {}
    for row in panels.get("Fitter Summary", []):
        if len(row) < 2:
            continue
        key, value = row[0].strip(), row[1]
        if key == "Device":
            info["part"] = value
        elif key == "Family":
            info["family"] = value
        elif key == "Top-level Entity Name":
            info["top"] = value
        elif key == "Revision Name":
            info["revision"] = value
        elif key.startswith("Quartus") and key.endswith("Version"):
            info["quartus"] = _version(value) or value
        elif key == "Fitter Status":
            info["date"] = _date(value.partition(" - ")[2], "%a %b %d %H:%M:%S %Y")

    util = []
    for row in panels.get("Fitter Resource Usage Summary", []):
        if len(row) < 2 or row[0][:1].isspace():
            continue
        label = row[0].rstrip("*")
        m = USAGE_RE.match(row[1])
        for pattern, name in UTILIZATION:
            hit = re.match(pattern, label)
            if hit and m:
                util.append((hit.expand(name), _int(m.group(1)), _int(m.group(2) or "0")))
                break

    pads = {}
    for title in ("Input Pins", "Output Pins", "Bidir Pins"):
        rows = panels.get(title, [])
        if not rows:
            continue
        col = {name.strip(): i for i, name in enumerate(rows[0])}
        try:
            name, pin, bank = col["Name"], col["Pin #"], col["I/O Bank"]
            xyz = [col[f"{a} coordinate"] for a in "XYZ"]
        except KeyError:
            continue
        for row in rows[1:]:
            try:
                key = tuple(int(row[i]) for i in xyz)
            except (ValueError, IndexError):
                continue
            pads[key] = (row[pin], row[bank], row[name].strip())
    return info, util, pads


def read_slacks(path):
    """Worst setup and hold slack of a Timing Analyzer summary (.sta.summary)
    or report (.sta.rpt), over all clocks and corners; None where it has none."""
    worst = {}

    def take(check, value):
        try:
            v = float(value)
        except ValueError:
            return
        worst[check] = min(worst.get(check, v), v)

    if path.endswith(".summary"):
        check = None
        with open(path, errors="replace") as f:
            for line in f:
                key, _, value = line.partition(":")
                if key.strip() == "Type":
                    m = re.search(r"\b(Setup|Hold) '", value)
                    check = m.group(1).lower() if m else None
                elif key.strip() == "Slack" and check:
                    take(check, value.strip())
    else:
        rx = re.compile(r"Model (Setup|Hold) Summary$")
        for title, rows in _panels(path, rx.search).items():
            for row in rows[1:]:
                if len(row) > 1:
                    take(rx.search(title).group(1).lower(), row[1])
    return worst.get("setup"), worst.get("hold")


def read(project):
    """Everything implview shows of a compiled revision."""
    if not project.netlist:
        raise QuartusError(
            f"no post-fit netlist ({project.revision}.vo or .vho) under {project.dir}: the "
            f"placement is read from it. Write it in the project directory with\n"
            f"  quartus_eda <project> -c {project.revision} --simulation=on --tool=modelsim "
            f"--format=verilog\n"
            f"or set a simulation tool in the EDA Tool Settings and compile again")
    fit = Fit()
    head, fit.atoms, ports = read_netlist(project.netlist)
    if not fit.atoms:
        raise QuartusError(f"{project.netlist}: no locations in it, not a post-fit netlist")
    qsf = os.path.join(project.dir, project.revision + ".qsf")
    fit.info = {"part": _setting(qsf, "DEVICE"), "family": _setting(qsf, "FAMILY"),
                "top": _setting(qsf, "TOP_LEVEL_ENTITY")}
    fit.info.update({k: v for k, v in head.items() if v})
    if project.fit:
        rep, fit.utilization, fit.pads = read_fit_report(project.fit)
        if rep.get("date") and head.get("date") and head["date"] < rep["date"]:
            fit.warnings.append(f"{project.netlist} is older than the Fitter report "
                                f"({head['date']}, fitted {rep['date']}): write it again")
        fit.info.update({k: v for k, v in rep.items() if v})
    else:
        fit.warnings.append(f"no Fitter report ({project.revision}.fit.rpt): no utilization")
    # for the pads the report does not list (JTAG pins), or without the report:
    # the pin of each port, on the pad of its buffers
    fit.pads = {**_pads_by_name(fit.atoms, ports), **fit.pads}
    if project.sta:
        wns, whs = read_slacks(project.sta)
        fit.info["wns"] = "" if wns is None else f"{wns:.3f}"
        fit.info["whs"] = "" if whs is None else f"{whs:.3f}"
    return fit


# The location of a pad buffer is the pad's Z coordinate plus this.
BUFFER_N = {"IOIBUF": 1, "IOOBUF": 2}


def _pads_by_name(atoms, ports):
    pads = {}
    for kind, x, y, n, _ref, name in atoms:
        port, _, tail = name.rpartition("~")
        if kind in BUFFER_N and tail in ("input", "output") and port in ports:
            pads[(x, y, n - BUFFER_N[kind])] = (ports[port], "", port)
    return pads
