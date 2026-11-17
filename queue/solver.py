"""The pages of a solver benchmark run by the app (MadIPM: https://benchmark.madsuite.org/madipm/), rendered
on request from the result files in the uploads root (`<prefix>-x<id>-<target>/<machine>/<set>_<config>.csv`,
the rows of MadNLPBenchmark.jl's format). Standard library only.

The pages are MadNLP's (src/site.jl): a summary of a row per set and a column group per solver with the
instances solved and the shifted geometric mean of the solve time, a failure counting as the wall
limit, over the instances every column has a result for (Sungho); and a page per set with every
instance. A run's page shows its configurations side by side, the reference solver among them; a
report's, the configurations of its runs."""

import csv
import html
import math
import os
import re
import time

WALL = 600.0          # the benchmark's limit on a solve, in seconds (Sungho, 2026-11-15)
SHIFT = 10.0
SOLVED = ("1", "2")
NAME = re.compile(r"[A-Za-z0-9_.+-]{1,100}")
e = html.escape


def read_rows(path):
    try:
        with open(path, newline="") as f:
            return {r["case"]: r for r in csv.DictReader(f) if r.get("case")}
    except OSError:
        return {}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def results(root):
    """`machine => {(set, config): {case: row}}` of a run's folder."""
    out = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for m in names:
        d = os.path.join(root, m)
        if not NAME.fullmatch(m) or m.startswith(".") or not os.path.isdir(d):
            continue
        files = {}
        for f in sorted(os.listdir(d)):
            mm = re.fullmatch(r"([a-z]+)_([A-Za-z0-9_.+-]+)\.csv", f)
            if mm:
                files[(mm[1], mm[2])] = read_rows(os.path.join(d, f))
        if files:
            out[m] = files
    return out


def updated(roots):
    latest = 0.0
    for root in roots:
        for dirpath, _, files in os.walk(root):
            latest = max([latest] + [os.stat(os.path.join(dirpath, f)).st_mtime for f in files if f.endswith(".csv")])
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(latest or time.time()))


def meta(root, machine):
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


def machine_line(root, machine):
    m = meta(root, machine)
    parts = [m.get("host", ""), m.get("cpu_model", ""), (f'{m["gpu_name"]}, CUDA {m.get("cuda_runtime", "")}' if m.get("gpu_name") else "")]
    return ", ".join(p for p in parts if p)


def solved(r):
    return r is not None and r.get("status") in SOLVED


def score(r):
    t = num(r.get("t_total"))
    return min(t, WALL) if solved(r) and math.isfinite(t) else WALL


def sgm(times):
    return math.exp(sum(math.log(t + SHIFT) for t in times) / len(times)) - SHIFT if times else math.nan


def letter(r):
    s = r.get("status", "")
    return ("ok" if s == "1" else "a" if s == "2" else "t" if s in ("-100", "7") else "i" if s == "6" else "e" if s == "-101" else "f")


def name_of(config, labels):
    return labels.get(config, config)


class Columns:
    """The columns of a page: `(label, root, config)` each, a run's configuration; the first is what the
    others' ratios are to."""

    def __init__(self, cols, sets_order=()):
        self.cols = cols
        self.res = {root: results(root) for _, root, _ in cols}
        self.machines = []
        for r in self.res.values():
            self.machines += [m for m in r if m not in self.machines]
        found = {s for r in self.res.values() for files in r.values() for (s, _) in files}
        self.sets = sorted(found, key=lambda s: (sets_order.index(s) if s in sets_order else 99, s))

    def rows(self, machine, set_):
        return [self.res[root].get(machine, {}).get((set_, config)) for _, root, config in self.cols]

    def common(self, machine, set_):
        """The instances every column with results for the set has a row for (strict intersection)."""
        have = [rows for rows in self.rows(machine, set_) if rows is not None]
        return sorted(set.intersection(*(set(r) for r in have))) if have else []


def summary_page(cols):
    if not cols.machines:
        return '<p class="m">No results yet: the nodes are setting up, or none has started.</p>'
    out = []
    for machine in cols.machines:
        if all(all(p is None for p in cols.rows(machine, s)) for s in cols.sets):
            continue
        out.append(f'<h2>{e(machine.replace("_", " "))}</h2>')
        lines = sorted({machine_line(root, machine) for _, root, _ in cols.cols} - {""})
        if lines:
            out.append('<p class="m">' + "<br>".join(e(x) for x in lines) + "</p>")
        here = [i for i, c in enumerate(cols.cols) if any(cols.rows(machine, s)[i] is not None for s in cols.sets)]
        out.append('<div class="wide"><table class="table table-sm summary"><thead><tr><th rowspan="2">set</th>'
                   '<th rowspan="2" title="instances every column has a result for, out of all">instances</th>')
        out.extend(f'<th colspan="3" class="g">{e(cols.cols[i][0])}</th>' for i in here)
        out.append("</tr><tr>" + '<th class="g">solved</th><th>SGM (s)</th><th>×</th>' * len(here) + "</tr></thead><tbody>")
        for set_ in cols.sets:
            per = cols.rows(machine, set_)
            if all(per[i] is None for i in here):
                continue
            common = cols.common(machine, set_)
            total = len(set().union(*(set(p) for p in per if p is not None)))
            out.append(f'<tr><td><a href="{e(set_)}.html">{e(set_)}</a></td><td>{len(common)}/{total}</td>')
            first = None
            for i in here:
                rows = per[i]
                if rows is None:
                    out.append('<td class="g"></td><td></td><td></td>')
                    continue
                g = sgm([score(rows[k]) for k in common])
                first = g if first is None else first
                ratio = g / first if first and math.isfinite(g) else math.nan
                out.append(f'<td class="g">{sum(solved(rows[k]) for k in common)}</td><td>{g:.2f}</td><td>{ratio:.2f}</td>' if common else
                           '<td class="g">0</td><td>–</td><td>–</td>')
            out.append("</tr>")
        out.append("</tbody></table></div>")
    out.append(f'<p class="m">Over the instances every column has a result for: solved, and the shifted geometric mean of the solve '
               f'time (shift {SHIFT:g} s), a failure counting as the {WALL:g} s limit; × is the mean over the first column\'s.</p>')
    return "".join(out)


def set_page(cols, set_):
    out = []
    for machine in cols.machines:
        per = cols.rows(machine, set_)
        have = [(i, rows) for i, rows in enumerate(per) if rows is not None]
        if not have:
            continue
        out.append(f'<h2>{e(machine.replace("_", " "))}</h2>')
        out.append('<div class="scroll"><table class="table table-sm table-hover cases"><thead><tr><th class="s" rowspan="2">instance</th>'
                   '<th class="s" rowspan="2">nv</th><th class="s" rowspan="2">nc</th>')
        out.extend(f'<th colspan="5" class="l">{e(cols.cols[i][0])}</th>' for i, _ in have)
        out.append("</tr><tr>" + ('<th class="s l">st</th><th class="s">it</th><th class="s">t (s)</th><th class="s">obj</th><th class="s">inf</th>' * len(have))
                   + '</tr></thead><tbody></tbody></table><template class="caserows">')
        cases = sorted(set().union(*(set(rows) for _, rows in have)))
        for case in cases:
            r0 = next(rows[case] for _, rows in have if case in rows)
            out.append(f'<tr><td>{e(case)}</td><td>{e(r0.get("nvar", ""))}</td><td>{e(r0.get("ncon", ""))}</td>')
            for _, rows in have:
                r = rows.get(case)
                if r is None:
                    out.append('<td class="l">–</td>' + "<td></td>" * 4)
                    continue
                lt, t = letter(r), num(r.get("t_total"))
                out.append(f'<td class="l{"" if lt in ("ok", "a") else " fail"}" title="status {e(r.get("status", ""))}">{lt}</td>'
                           f'<td>{e(r.get("iters", ""))}</td><td>{t:.3f}</td><td>{num(r.get("obj")):.6g}</td><td>{num(r.get("primal_feas")):.1e}</td>')
            out.append("</tr>")
        out.append("</template></div>")
    if not out:
        return '<p class="m">No results for this set yet.</p>'
    return ('<p class="m">nv, nc: variables, constraints. st: ok solved, a solved to the acceptable level, t time limit, i iteration limit, '
            'e not run (the model was not built or the worker failed), f other failure. it: iterations. t: the solve time. inf: infeasibility.</p>'
            '<input id="filter" placeholder="Filter by name"><label class="only"><input id="failed" type="checkbox">Failures only</label>' + "".join(out))


def tabs(cols, workers=False, raw=False):
    items = ['<a href="index.html">Summary</a>'] + [f'<a href="{e(s)}.html">{e(s)}</a>' for s in cols.sets]
    if workers:
        items.append('<a href="workers.html">Workers</a>')
    return '<nav class="tabs">' + "".join(items) + ('<a class="raw" href="data/">Raw data</a>' if raw else "") + "</nav>"
