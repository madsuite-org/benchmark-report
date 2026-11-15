"""The ExaModels benchmarks (https://benchmark.madsuite.org/examodels/): the pages of the runs of
ExaModelsBenchmark.jl's jobs and of reports made of them, rendered on request from the result files in
the uploads root (`exa-x<id>-<target>/<machine>/<set>_<config>.csv`). Standard library only.

The pages are MadNLP's (src/site.jl): its banner, tabs, tables and script. A run's page shows the run
alone; a report sets its runs side by side, a column group each, over the instances every column has
timed (Sungho)."""

import csv
import html
import math
import os
import re
import time

REPO = "madsuite-org/ExaModels.jl"
HARNESS = "https://github.com/madsuite-org/ExaModelsBenchmark.jl"
DEFAULT_BRANCH = "main"
SETS = ("luksanvlcek", "cops", "acopf", "mittelmann", "cutest")
DEFAULT_SETS = ",".join(SETS)
KNOWN_SETS = set(SETS) | {"smoke"}
SET_NAMES = {"luksanvlcek": "Lukšan–Vlček", "cops": "COPS", "acopf": "AC OPF", "mittelmann": "Mittelmann", "cutest": "CUTEst", "smoke": "smoke"}
COLUMNS = ("t_create", "t_obj", "t_cons", "t_grad", "t_jac", "t_hess", "t_hprod", "t_jprod", "t_jtprod")
SUMMARY = ("t_create", "t_obj", "t_jac", "t_hess")      # the times of a summary's column group, after "timed"
SHORT = {c: c[2:] for c in COLUMNS}
TIMED = ("1", "2")
STATUS = {"1": ("ok", "timed"), "2": ("p", "timed, but a callback threw"), "-100": ("k", "killed: no progress, or out of memory"),
          "-101": ("e", "error: the model could not be built, or the timing threw")}
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
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def results(root):
    """`machine => {set: {case: row}}` of a run's folder (its configuration `default`)."""
    out = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for m in names:
        d = os.path.join(root, m)
        if not NAME.fullmatch(m) or m.startswith(".") or not os.path.isdir(d):
            continue
        sets = {}
        for f in sorted(os.listdir(d)):
            mm = re.fullmatch(r"([a-z]+)_default\.csv", f)
            if mm:
                sets[mm[1]] = {r["case"]: r for r in read_rows(os.path.join(d, f))}
        if sets:
            out[m] = sets
    return out


def updated(roots):
    """When the results of these folders last changed, `YYYY-mm-ddTHH:MM:SSZ`, or now if none."""
    latest = 0.0
    for root in roots:
        for dirpath, _, files in os.walk(root):
            latest = max([latest] + [os.stat(os.path.join(dirpath, f)).st_mtime for f in files if f.endswith(".csv")])
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(latest or time.time()))


def meta(root, machine):
    """The metadata beside one of a machine's result files, as flat `key = value` pairs."""
    out = {}
    try:
        name = next(f for f in sorted(os.listdir(os.path.join(root, machine))) if f.endswith(".csv.meta.toml"))
        with open(os.path.join(root, machine, name)) as f:
            for line in f:
                m = re.fullmatch(r'\s*([A-Za-z0-9_]+)\s*=\s*"?(.*?)"?\s*', line.rstrip("\n"))
                if m:
                    out[m[1]] = m[2]
    except (OSError, StopIteration):
        pass
    return out


def gmean(xs):
    xs = [x for x in xs if math.isfinite(x) and x > 0]
    return math.exp(sum(map(math.log, xs)) / len(xs)) if xs else math.nan


def us(t):
    """Seconds in microseconds, to three digits: what the tables show, and sort by."""
    if not math.isfinite(t):
        return "–"
    v = 1e6 * t
    return "0" if v == 0 else f"{v:.0f}" if v >= 100 else f"{v:.3g}"


def ratio(a, b):
    return a / b if math.isfinite(a) and math.isfinite(b) and b > 0 else math.nan


def timed(r):
    return r is not None and r.get("status") in TIMED


def size(r):
    return num(r.get("nnzj")) + num(r.get("nnzh"))


def commit_link(j):
    sha = j.get("sha") or ""
    if not sha:
        return ""
    return f'<code class="m">{e(sha[:8])}</code>' if j.get("local") else f'<a class="m" href="https://github.com/{REPO}/commit/{e(sha)}">{e(sha[:8])}</a>'


def machine_line(root, machine):
    m = meta(root, machine)
    parts = [m.get("host", ""), m.get("cpu_model", ""), (f'{m["gpu_name"]}, CUDA {m.get("cuda_runtime", "")}' if m.get("gpu_name") else "")]
    return ", ".join(p for p in parts if p)


class Columns:
    """The columns of a page, `(label, job, root)` each: a run's own, or a report's runs, the first being
    what the others' ratios are to."""

    def __init__(self, cols):
        self.cols = cols
        self.res = [results(root) for _, _, root in cols]
        self.machines = []
        for r in self.res:
            self.machines += [m for m in r if m not in self.machines]
        self.sets = sorted({s for r in self.res for m in r.values() for s in m}, key=lambda s: (SETS.index(s) if s in SETS else 99, s))

    def rows(self, machine, set_):
        """Per column, `{case: row}` on this machine, or None where the column has none of the set."""
        return [r.get(machine, {}).get(set_) for r in self.res]

    def common(self, machine, set_):
        """The instances every column with the set has timed (strict intersection, Sungho)."""
        have = [rows for rows in self.rows(machine, set_) if rows is not None]
        if not have:
            return []
        cases = set.intersection(*({c for c, r in rows.items() if timed(r)} for rows in have))
        return sorted(cases, key=lambda c: size(have[0][c]))


def means_cells(rows, common, first, cols, i):
    out = []
    means = {c: gmean([num(rows[k].get(c)) for k in common]) for c in cols}
    for c in cols:
        r = ratio(means[c], first[c]) if i and first else math.nan
        out.append(f"<td>{us(means[c])}" + (f' <span class="m" title="to the first column">{r:.2f}×</span>' if math.isfinite(r) else "") + "</td>")
    return means, "".join(out)


def summary_page(cols):
    """The summary, per machine: a row per set, a column group per run, the geometric means over the
    instances every column timed; a later column's also as a ratio to the first's."""
    if not cols.machines:
        return '<p class="m">No results yet: the nodes are setting up, or none has started.</p>'
    out = []
    for machine in cols.machines:
        out.append(f'<h2>{e(machine.replace("_", " "))}</h2>')
        lines = sorted({machine_line(root, machine) for _, _, root in cols.cols} - {""})
        if lines:
            out.append('<p class="m">' + "<br>".join(e(x) for x in lines) + "</p>")
        out.append('<div class="wide"><table class="table table-sm summary"><thead><tr><th rowspan="2">set</th>'
                   '<th rowspan="2" title="instances every column has timed, out of all">instances</th>')
        out.extend(f'<th colspan="{1 + len(SUMMARY)}" class="g">{e(label)}</th>' for label, _, _ in cols.cols)
        out.append("</tr><tr>" + ('<th class="g">timed</th>' + "".join(f"<th>{SHORT[c]} (µs)</th>" for c in SUMMARY)) * len(cols.cols) + "</tr></thead><tbody>")
        for set_ in cols.sets:
            per = cols.rows(machine, set_)
            if all(p is None for p in per):
                continue
            common = cols.common(machine, set_)
            total = len(set().union(*(set(p) for p in per if p is not None)))
            out.append(f'<tr><td><a href="{e(set_)}.html">{e(SET_NAMES.get(set_, set_))}</a></td><td>{len(common)}/{total}</td>')
            first = None
            for i, rows in enumerate(per):
                if rows is None:
                    out.append('<td class="g"></td>' + "<td></td>" * len(SUMMARY))
                    continue
                means, cells = means_cells(rows, common, first, SUMMARY, i)
                first = first or means
                out.append(f'<td class="g">{sum(timed(r) for r in rows.values())}/{len(rows)}</td>{cells}')
            out.append("</tr>")
        out.append("</tbody></table></div>")
    out.append('<p class="m">' + ("Geometric means over the instances every column has timed, in microseconds; the ratio is to the first "
                                  "column (below 1 is faster)." if len(cols.cols) > 1 else
                                  "Geometric means over the instances timed, in microseconds. A report compares runs.") + "</p>")
    return "".join(out)


def set_page(cols, set_):
    """A set: per machine, the geometric means of every time per column, then every instance."""
    out = []
    for machine in cols.machines:
        per = cols.rows(machine, set_)
        if all(p is None for p in per):
            continue
        common = cols.common(machine, set_)
        out.append(f'<h2>{e(machine.replace("_", " "))}</h2><div class="wide"><table class="table table-sm"><thead><tr><th>run</th><th>timed</th>'
                   + "".join(f"<th>{SHORT[c]} (µs)</th>" for c in COLUMNS) + "</tr></thead><tbody>")
        first = None
        for i, ((label, _, _), rows) in enumerate(zip(cols.cols, per)):
            if rows is None:
                continue
            means, cells = means_cells(rows, common, first, COLUMNS, i)
            first = first or means
            out.append(f"<tr><td>{e(label)}</td><td>{sum(timed(r) for r in rows.values())}/{len(rows)}</td>{cells}</tr>")
        out.append(f'</tbody></table></div><p class="m">Geometric means over the {len(common)} instances every column has timed.</p>')
        out.append(instance_table(cols, per))
    if not out:
        return '<p class="m">No results for this set yet.</p>'
    return ('<p class="m">nv, nc: variables, constraints. st: ok timed, p a callback threw, k killed, e error. Times are of one call, '
            'in microseconds; create is one construction of the model.</p>'
            '<input id="filter" placeholder="Filter by name"><label class="only"><input id="failed" type="checkbox">Failures only</label>'
            + "".join(out))


def instance_table(cols, per):
    have = [(label, rows) for (label, _, _), rows in zip(cols.cols, per) if rows is not None]
    out = ['<div class="scroll"><table class="table table-sm table-hover cases"><thead><tr><th class="s" rowspan="2">instance</th>'
           '<th class="s" rowspan="2">nv</th><th class="s" rowspan="2">nc</th>']
    out.extend(f'<th colspan="{1 + len(COLUMNS)}" class="l">{e(label)}</th>' for label, _ in have)
    out.append("</tr><tr>" + ('<th class="s l">st</th>' + "".join(f'<th class="s">{SHORT[c]}</th>' for c in COLUMNS)) * len(have) + "</tr></thead><tbody>")
    cases = set().union(*(set(rows) for _, rows in have))
    any_row = lambda c: next(rows[c] for _, rows in have if c in rows)
    for case in sorted(cases, key=lambda c: (size(any_row(c)), c)):
        r0 = any_row(case)
        out.append(f'<tr><td>{e(case)}</td><td>{e(r0.get("nvar", ""))}</td><td>{e(r0.get("ncon", ""))}</td>')
        for _, rows in have:
            r = rows.get(case)
            if r is None:
                out.append('<td class="l">–</td>' + "<td></td>" * len(COLUMNS))
                continue
            letter, name = STATUS.get(r.get("status", ""), (r.get("status", ""), ""))
            out.append(f'<td class="l{"" if letter == "ok" else " fail"}" title="{e(name)}">{e(letter)}</td>' + "".join(f"<td>{us(num(r.get(c)))}</td>" for c in COLUMNS))
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def tabs(cols, workers=False, raw=False):
    items = ['<a href="index.html">Summary</a>'] + [f'<a href="{e(s)}.html">{e(SET_NAMES.get(s, s))}</a>' for s in cols.sets]
    if workers:
        items.append('<a href="workers.html">Workers</a>')
    return '<nav class="tabs">' + "".join(items) + ('<a class="raw" href="data/">Raw data</a>' if raw else "") + "</nav>"
