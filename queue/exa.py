"""The ExaModels benchmarks (https://benchmark.madsuite.org/examodels/): the pages of the jobs of
ExaModelsBenchmark.jl, rendered on request from their result files in the uploads root
(`exa-x<id>-<target>/<machine>/<set>_<config>.csv`). Standard library only.

A run's times are compared with a reference: the newest other run of ExaModels.jl's main on the
same machine, over the instances both timed (and only those)."""

import csv
import html
import math
import os
import re

REPO = "madsuite-org/ExaModels.jl"
HARNESS = "https://github.com/madsuite-org/ExaModelsBenchmark.jl"
DEFAULT_BRANCH = "main"
SETS = ("luksanvlcek", "cops", "acopf", "mittelmann", "cutest")
DEFAULT_SETS = ",".join(SETS)
KNOWN_SETS = set(SETS) | {"smoke"}
SET_NAMES = {"luksanvlcek": "Lukšan–Vlček", "cops": "COPS", "acopf": "AC OPF (pglib-opf)", "mittelmann": "Mittelmann", "cutest": "CUTEst", "smoke": "smoke"}
COLUMNS = ("t_create", "t_obj", "t_cons", "t_grad", "t_jac", "t_hess", "t_hprod", "t_jprod", "t_jtprod")
SHORT = {c: c[2:] for c in COLUMNS}
TIMED = ("1", "2")
STATUS = {"1": "timed", "2": "partial", "-100": "killed", "-101": "error"}
NAME = re.compile(r"[A-Za-z0-9_.+-]{1,100}")
FOLDER = re.compile(r"exa-x\d+-[a-z0-9-]{0,40}")
e = html.escape


def is_folder(name):
    return bool(FOLDER.fullmatch(name))


def read_rows(path):
    try:
        with open(path, newline="") as f:
            return [r for r in csv.DictReader(f) if r.get("case")]
    except OSError:
        return []


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


def results(root):
    """`machine => {(set, config): rows}` of a run's folder."""
    out = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for m in names:
        d = os.path.join(root, m)
        if not (NAME.fullmatch(m) and os.path.isdir(d)) or m.startswith("."):
            continue
        files = {}
        for f in sorted(os.listdir(d)):
            mm = re.fullmatch(r"([a-z]+)_([A-Za-z0-9_.+-]+)\.csv", f)
            if mm:
                files[(mm[1], mm[2])] = read_rows(os.path.join(d, f))
        if files:
            out[m] = files
    return out


def meta(root, machine, set_, config):
    """The metadata beside a result file, as flat `key = value` pairs (the TOML run.jl writes)."""
    out = {}
    try:
        with open(os.path.join(root, machine, f"{set_}_{config}.csv.meta.toml")) as f:
            for line in f:
                m = re.fullmatch(r'\s*([A-Za-z0-9_]+)\s*=\s*"?(.*?)"?\s*', line.rstrip("\n"))
                if m:
                    out[m[1]] = m[2]
    except OSError:
        pass
    return out


def status_line(root, machine):
    """The first line of a machine's summary.md: how far it has got."""
    try:
        with open(os.path.join(root, machine, "summary.md")) as f:
            for line in f:
                if line.startswith("status"):
                    return line.strip()
    except OSError:
        pass
    return ""


def gmean(xs):
    xs = [x for x in xs if math.isfinite(x) and x > 0]
    return math.exp(sum(map(math.log, xs)) / len(xs)) if xs else math.nan


def fmt_time(t):
    if not math.isfinite(t):
        return "–"
    if t == 0:
        return "0"
    for unit, scale in (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)):
        if t >= scale:
            return f"{t / scale:.3g} {unit}"
    return f"{t / 1e-9:.3g} ns"


def fmt_ratio(r):
    if not math.isfinite(r):
        return "–"
    cls = "text-success" if r < 0.95 else "text-danger" if r > 1.05 else ""
    return f'<span class="{cls}">{r:.2f}×</span>'


def timed(r):
    return r.get("status") in TIMED


def ratios(rows, ref_rows, col):
    """This run's time over the reference's, instance by instance, over those timed in both."""
    ref = {r["case"]: r for r in ref_rows if timed(r)}
    out = {}
    for r in rows:
        o = ref.get(r["case"])
        if o is None or not timed(r):
            continue
        a, b = num(r.get(col)), num(o.get(col))
        if math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0:
            out[r["case"]] = a / b
    return out


def size(r):
    return num(r.get("nnzj")) + num(r.get("nnzh"))


def job_title(j, display_name):
    return f"Job {j['id']}: {display_name(j)}"


def commit_link(j):
    sha = j.get("sha") or ""
    if not sha:
        return "–"
    if j.get("local"):
        return f"<code>{e(sha[:8])}</code> (local)"
    return f'<a href="https://github.com/{REPO}/commit/{e(sha)}"><code>{e(sha[:8])}</code></a>'


def target_link(j):
    if j.get("kind") == "pr" and j.get("pr"):
        return f'<a href="https://github.com/{REPO}/pull/{int(j["pr"])}">#{int(j["pr"])}</a>'
    return ""


def is_main(j):
    """A run of ExaModels.jl's main as it was: what a reference is made of."""
    return (j.get("project") == "examodels" and j.get("kind") == "commit" and str(j.get("title", "")).startswith("main ")
            and not j.get("local") and not j.get("fork"))


def reference(j, jobs, root_of):
    """The reference of run `j` for each of its machines: `machine => job`, the newest other main run
    with results on that machine."""
    out = {}
    mine = results(root_of(j))
    for o in sorted((o for o in jobs if is_main(o) and o["id"] != j["id"]), key=lambda o: -o["id"]):
        theirs = results(root_of(o))
        for m in mine:
            if m not in out and m in theirs:
                out[m] = o
    return out


def breadcrumb(base, j=None, extra=""):
    trail = f'<a href="{e(base)}/">ExaModels</a>'
    if j is not None:
        trail += f' / <a href="./">Job {j["id"]}</a>' if extra else f" / Job {j['id']}"
    return f'<p class="m mb-1">MadSuite benchmarks / {trail}{(" / " + extra) if extra else ""}</p>'


def index(jobs, base, display_name, folder, when, state_badge):
    """The dashboard: every ExaModels run, newest first."""
    rows = []
    for j in sorted(jobs, key=lambda j: -j["id"]):
        badge = f'<span class="badge text-bg-{state_badge.get(j["state"], "secondary")}">{e(j["state"])}</span>'
        rows.append(f'<tr><td><a href="{e(base)}/{e(folder(j))}/">{j["id"]}</a></td><td>{e(display_name(j))}</td>'
                    f'<td>{commit_link(j)} {target_link(j)}</td><td>{e(j.get("devices", ""))}</td><td>{badge}</td>'
                    f'<td>{when(j.get("started") or j.get("queued"))}</td><td class="m">{e(j.get("purpose", ""))}</td></tr>')
    table = ('<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr><th>Job</th><th>Run</th><th>Commit</th>'
             '<th>Devices</th><th>State</th><th>Started</th><th>Note</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
             if rows else '<p class="m">No runs yet.</p>')
    return (f'<h1 class="h3">ExaModels benchmarks</h1>'
            f'<p class="m">Model construction and callback times of <a href="https://github.com/{REPO}">ExaModels.jl</a>, on the host and on '
            f'GPUs, over Lukšan–Vlček, COPS, AC OPF, Mittelmann and CUTEst. Each run is compared with the newest run of main on the same '
            f'machine. Runs are made with <a href="{HARNESS}#readme">ExaModelsBenchmark.jl</a>.</p>' + table)


def summary_table(files, ref_files, prefix=""):
    """Per set: instances timed and the geometric mean of each time; with a reference, the geometric
    mean of the ratios over the instances both timed."""
    head = "".join(f"<th>{SHORT[c]}</th>" for c in COLUMNS)
    out = []
    for (set_, config), rows in sorted(files.items(), key=lambda kv: (SETS.index(kv[0][0]) if kv[0][0] in SETS else 99, kv[0])):
        label = f'<a href="{e(prefix)}{e(set_)}-{e(config)}.html">{e(SET_NAMES.get(set_, set_))}</a>' + ("" if config == "default" else f" ({e(config)})")
        n = sum(timed(r) for r in rows)
        cells = "".join(f"<td>{fmt_time(gmean([num(r.get(c)) for r in rows if timed(r)]))}</td>" for c in COLUMNS)
        out.append(f"<tr><td>{label}</td><td>{n} / {len(rows)}</td>{cells}</tr>")
        ref = ref_files.get((set_, config)) if ref_files else None
        if ref is not None:
            common = len(ratios(rows, ref, "t_jac") or ratios(rows, ref, "t_obj"))
            cells = "".join(f"<td>{fmt_ratio(gmean(list(ratios(rows, ref, c).values())))}</td>" for c in COLUMNS)
            out.append(f'<tr class="m"><td class="ps-3">vs reference</td><td>{common} common</td>{cells}</tr>')
    return ('<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Set</th><th>Timed</th>' + head +
            "</tr></thead><tbody>" + "".join(out) + "</tbody></table></div>")


def machine_line(root, machine, files):
    set_, config = next(iter(files))
    m = meta(root, machine, set_, config)
    parts = [m.get("host", ""), m.get("cpu_model", "")]
    if m.get("gpu_name"):
        parts.append(f'{m["gpu_name"]}, CUDA {m.get("cuda_runtime", "")}')
    return ", ".join(p for p in parts if p)


def run_page(j, root, refs, root_of, base, display_name, folder, extra=""):
    """A run: per machine, its summary by set, with the reference's ratios."""
    res = results(root)
    head = (breadcrumb(base, j) + f'<h1 class="h4">{e(display_name(j))}</h1>'
            f'<p class="m">{commit_link(j)} {target_link(j)} · {e(j.get("devices", ""))} · {e(j["state"])}'
            + (f' · {e(j["purpose"])}' if j.get("purpose") else "")
            + (' · <b>local code</b>' if j.get("local") else "") + (f' · <b>from the fork {e(j["fork"])}</b>' if j.get("fork") else "")
            + f' · <a href="workers.html">workers</a> · <a href="data/">files</a></p>')
    if not res:
        return head + extra + '<p class="m">No results yet: the nodes are setting up, or none has started.</p>'
    body = head + extra
    for machine, files in res.items():
        ref = refs.get(machine)
        ref_files = results(root_of(ref)).get(machine) if ref else None
        line = status_line(root, machine)
        body += (f'<h2 class="h5 mt-4">{e(machine)}</h2><p class="m mb-1">{e(machine_line(root, machine, files))}'
                 + (f" · {e(line)}" if line and j["state"] == "running" else "") + "</p>")
        body += (f'<p class="m mb-1">Reference: <a href="{e(base)}/{e(folder(ref))}/">Job {ref["id"]}</a> ({commit_link(ref)}); a ratio '
                 'below 1 is faster than the reference.</p>' if ref else '<p class="m mb-1">No reference: no other run of main on this machine yet.</p>')
        body += summary_table(files, ref_files, f"{machine}/")
    return body


def set_page(j, root, machine, set_, config, ref, root_of, base, display_name, folder, view):
    """One set of one machine: a row per instance, by size; with a reference, its times or the ratios."""
    rows = results(root).get(machine, {}).get((set_, config))
    if rows is None:
        return None
    ref_rows = results(root_of(ref)).get(machine, {}).get((set_, config)) if ref else None
    show_ratio = view == "ratio" and ref_rows is not None
    by_col = {c: ratios(rows, ref_rows, c) for c in COLUMNS} if ref_rows is not None else {}
    head = "".join(f"<th>{SHORT[c]}</th>" for c in COLUMNS)
    out = []
    for r in sorted(rows, key=size):
        st = STATUS.get(r.get("status", ""), r.get("status", ""))
        if show_ratio:
            cells = "".join(f"<td>{fmt_ratio(by_col[c].get(r['case'], math.nan))}</td>" for c in COLUMNS)
        else:
            cells = "".join(f"<td>{fmt_time(num(r.get(c)))}</td>" for c in COLUMNS)
        dims = "".join(f"<td>{e(r.get(k, ''))}</td>" for k in ("nvar", "ncon", "nnzj", "nnzh"))
        out.append(f"<tr><td>{e(r['case'])}</td><td>{e(st)}</td>{dims}{cells}</tr>")
    switch = ""
    if ref_rows is not None:
        switch = (f'<p class="m">Reference: <a href="{e(base)}/{e(folder(ref))}/">Job {ref["id"]}</a> ({commit_link(ref)}). Show '
                  + ('<a href="?">times</a> · <b>ratios to the reference</b>' if show_ratio else '<b>times</b> · <a href="?view=ratio">ratios to the reference</a>')
                  + "</p>")
    name = SET_NAMES.get(set_, set_) + ("" if config == "default" else f" ({config})")
    return (breadcrumb(base, j, e(f"{machine} / {name}")).replace('href="./"', 'href="../"') + f'<h1 class="h4">{e(name)} on {e(machine)}</h1>' + switch +
            '<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Instance</th><th>Status</th><th>nvar</th><th>ncon</th>'
            '<th>nnzj</th><th>nnzh</th>' + head + "</tr></thead><tbody>" + "".join(out) + "</tbody></table></div>")
