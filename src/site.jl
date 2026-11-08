# The benchmark report: a static site with a front page of the machines, the summary table and
# Dolan-Moré performance profiles, a page per set with every instance, and the result files
# and solver logs under `data/`.

# A device has one colour on every page, and this run takes the colour of the device it ran on:
# CPU blue, CUDA green. Its base is orange, Ipopt, the reference, gray, and further configurations
# the next hues in order. Red marks failures only.
# The values, for the light and the dark theme, are in `assets/style.css`.
const COLORS = ["var(--s$i)" for i in 1:7]
const REFERENCE_COLOR = "var(--ref)"
device_color(device) = device == "cuda" ? COLORS[3] : device == "amdgpu" ? COLORS[5] : COLORS[1]

const SET_TITLES = Dict("acopf" => "ACOPF", "cops" => "COPS", "cutest" => "CUTEst", "mittelmann" => "Mittelmann")


escape_html(s) = replace(string(s), "&" => "&amp;", "<" => "&lt;", ">" => "&gt;", "\"" => "&quot;", "'" => "&#39;")

function status_name(code)
    code == KILLED_STATUS && return "killed at the limit"
    code == ERROR_STATUS && return "error"
    code == SOLVE_SUCCEEDED && return "solved"
    code == SOLVED_TO_ACCEPTABLE_LEVEL && return "solved to acceptable level"
    return get(STATUS_NAMES, code, "status $code")
end

# The letter of an outcome in the instance tables, as in Hans Mittelmann's tables.
function status_letter(code)
    code == SOLVE_SUCCEEDED && return "ok"
    code == SOLVED_TO_ACCEPTABLE_LEVEL && return "a"
    (code == KILLED_STATUS || code == MAXIMUM_WALLTIME_EXCEEDED) && return "t"
    code == MAXIMUM_ITERATIONS_EXCEEDED && return "i"
    return "f"
end

# The columns of a report: one per solver and configuration of a side.
struct Side
    label::String
    color::String
    key::Tuple{String, String}   # device, configuration
    base::Bool
end

# Per device the columns are Ipopt, the base and the current results.
# A run added for comparison sits in the base results under the configuration "cmp:<label>:<config>";
# its label ends in † when it ran on another machine, whose times are not comparable.
untimed(label) = endswith(label, "†")

function report_sides(cand, base)
    suffix(key) = key[2] == "default" ? "" : " " * key[2]
    cmp(key) = match(r"^cmp:(.*):([^:]*)$", key[2])
    label(key, name) = key[2] == "ipopt" ? "Ipopt" : cmp(key) !== nothing ? cmp(key)[1] : name * suffix(key)
    keys_of(rs) = rs === nothing ? [] : sort!(unique!([(k[1], k[3]) for k in keys(rs.rows)]); by = k -> (k[1] != "cpu", k))
    sides = [(label(k, "master"), k, true) for k in keys_of(base) if k[2] != "ipopt"]
    append!(sides, [(label(k, "current"), k, false) for k in keys_of(cand)])
    sort!(sides; by = s -> s[2][2] != "ipopt")
    others = sort!(unique!([l for (l, _, _) in sides if l ∉ ("Ipopt", "current", "master")]))
    color(l, device) = l == "Ipopt" ? REFERENCE_COLOR : l == "current" ? device_color(device) : l == "master" ? COLORS[2] : COLORS[mod1(3 + findfirst(==(l), others), length(COLORS))]
    return [Side(l, color(l, k[1]), k, b) for (l, k, b) in sides]
end

side_rows(side, cand, base, set) = get((side.base ? base : cand).rows, (side.key[1], set, side.key[2]), Dict{String, Any}())

# Every solver on a device's pages is held to that device's limit on a solve, also in runs from
# before it (run with a longer one): a longer solve counts as stopped at the limit. The result
# files are left as they were. The limits are the benchmark's, 1800 s on the host and 600 s on a
# GPU (Sungho, 2026-11-08); a run that states a lower one (`wall_limit_s`) is held to that.
const CPU_WALL = 1800.0
const GPU_WALL = 600.0
function device_walls(root, wall)
    stated = Dict{String, Float64}()
    for (dir, _, files) in walkdir(root), file in files
        endswith(file, ".meta.toml") || continue
        meta = TOML.parsefile(joinpath(dir, file))
        limit, device = get(meta, "wall_limit_s", nothing), get(meta, "device", nothing)
        (limit isa Real && limit > 0 && device in DEVICES) && (stated[device] = min(get(stated, device, Inf), limit))
    end
    return Dict(d => min(get(stated, d, Inf), wall, d == "cpu" ? CPU_WALL : GPU_WALL) for d in DEVICES)
end
device_wall(device, walls) = walls[device]
capped(rows, wall) = Dict{String, Any}(c => r.t_total > wall ? merge(r, (; status = MAXIMUM_WALLTIME_EXCEEDED, t_total = wall)) : r for (c, r) in rows)
page_rows(side, cand, base, set, device, wall) = capped(side_rows(side, cand, base, set), device_wall(device, wall))

# What a performance profile compares, each on its own.
const PROFILE_METRICS = ["Wall time" => (r -> max(r.t_total, 1.0e-3)), "Iterations" => (r -> max(r.iters, 1))]

# Per side the ratios to the best side on the instances that every side has a result for; a
# failure has no ratio.
function profile(rows, value)
    cases = isempty(rows) ? Set{String}() : reduce(intersect, [Set(keys(r)) for r in rows])
    ratios = [Float64[] for _ in rows]
    for case in cases
        times = [solved(r[case]) ? value(r[case]) : Inf for r in rows]
        best = minimum(times)
        isfinite(best) || continue
        for (i, t) in enumerate(times)
            isfinite(t) && push!(ratios[i], t / best)
        end
    end
    return length(cases), sort!.(ratios)
end

function profile_svg(io, title, sides, n, ratios)
    # small enough a canvas that its 14-unit text stays readable on a phone
    w, h, left, right, top, bottom = 460, 300, 42, 26, 10, 44
    xmax = clamp(ceil(log2(maximum(Iterators.flatten(ratios); init = 2.0))), 1, 12)
    X(t) = left + (w - left - right) * min(log2(t), xmax) / xmax
    Y(f) = top + (h - top - bottom) * (1 - f)
    json = join(["[\"$(s.label)\",\"$(s.color)\",[$(join(round.(r; digits = 3), ","))]]" for (s, r) in zip(sides, ratios)], ",")
    print(io, "<div class=\"tip\"></div><svg viewBox=\"0 0 $w $h\" role=\"img\" aria-label=\"$(escape_html(title))\" ")
    print(io, "data-c='{\"w\":$w,\"l\":$left,\"r\":$(w - right),\"x\":$xmax,\"n\":$n,\"s\":[$json]}'>")
    for f in 0:0.25:1
        print(io, "<line x1=\"$left\" x2=\"$(w - right)\" y1=\"$(Y(f))\" y2=\"$(Y(f))\" class=\"grid\"/>")
        print(io, "<text x=\"$(left - 6)\" y=\"$(Y(f) + 5)\" text-anchor=\"end\">$(round(Int, 100f))%</text>")
    end
    step = ceil(Int, xmax / 5)
    for k in 0:step:Int(xmax)
        print(io, "<text x=\"$(X(2.0^k))\" y=\"$(h - bottom + 18)\" text-anchor=\"middle\">$(2^k)×</text>")
    end
    print(io, "<text x=\"$((left + w - right) / 2)\" y=\"$(h - 5)\" text-anchor=\"middle\">within this factor of the best</text>")
    print(io, "<line class=\"x\" y1=\"$top\" y2=\"$(h - bottom)\" style=\"display:none\"/>")
    for (s, r) in zip(sides, ratios)
        d = IOBuffer()
        print(d, "M$(X(1.0)) $(Y(0.0))")
        for (i, t) in enumerate(r)
            print(d, "L$(X(t)) $(Y((i - 1) / n))L$(X(t)) $(Y(i / n))")
        end
        print(d, "L$(w - right) $(Y(length(r) / n))")
        print(io, "<path d=\"$(String(take!(d)))\" fill=\"none\" style=\"stroke:$(s.color)\" stroke-width=\"2\" stroke-linejoin=\"round\" stroke-linecap=\"round\"", s.label == "Ipopt" ? " stroke-dasharray=\"6 4\"" : "", "/>")
    end
    println(io, "</svg>")
    return
end

# The solvers shown for a device: its own, with Ipopt as the reference of every device. MadNLP on
# the host and a solver on a device are never compared.
on_device(sides, device) = [s for s in sides if s.key[1] == device || s.key[2] == "ipopt"]

# What a column shows as its name: its label with what it ran, `master 31576532`, `current
# 1b00be2e`, `Ipopt 3.14.19`, a commit linked; set for each report by `set_titles!`.
const TITLES = Dict{String, String}()
title(label) = get(TITLES, label, escape_html(label))
function set_titles!(sides, cand, base, code)
    empty!(TITLES)
    for s in sides
        rev = get((s.base ? base : cand).revs, s.key, "")
        short = something(match(r"([0-9a-f]{8})[0-9a-f]*$", rev), match(r"^(.*)$", rev))[1]
        name = haskey(COLUMN_REPORTS, s.label) ? "<a href=\"../$(escape_html(COLUMN_REPORTS[s.label]))/\">$(escape_html(s.label))</a>" : escape_html(s.label)
        isempty(short) && (TITLES[s.label] = name; continue)
        link = occursin(r"^[0-9a-f]{8}$", short) && code !== nothing ? "<a href=\"$code/commit/$short\">$short</a>" : escape_html(short)
        # one name across devices: a label whose devices ran different things lists them
        TITLES[s.label] = haskey(TITLES, s.label) && !occursin(link, TITLES[s.label]) ? TITLES[s.label] * ", " * link : name * " " * link
    end
    return
end
key(s) = "<span class=\"key\"><i style=\"background:$(s.color)\"></i>$(title(s.label))</span>"

function device_profile(io, set, device, sides, cand, base, wall)
    rows = [page_rows(s, cand, base, set, device, wall) for s in sides]
    kept = [i for i in eachindex(sides) if !isempty(rows[i])]
    isempty(kept) && return
    if length(kept) < 2
        println(io, "<p class=\"m\">No profile: there is one solver on this device, and no archived results to compare with.</p>")
        return
    end
    n = length(reduce(intersect, [Set(keys(r)) for r in rows[kept]]))
    println(io, "<p class=\"m\">Over the $n instances that all of them have a result for. A curve gives the share of them solved within a factor of the best.</p>")
    println(io, "<p>", join([key(s) for s in sides[kept]]), "</p>")
    n > 0 || return
    println(io, "<div class=\"profiles\">")
    for (name, value) in PROFILE_METRICS
        # a run on another machine is shown in the times too, marked (Sungho)
        use = kept
        length(use) < 2 && continue
        _, ratios = profile(rows[use], value)
        println(io, "<div class=\"prof\"><p><b>", name, "</b></p>")
        profile_svg(io, "$name profile of $set on $device", sides[use], n, ratios)
        println(io, "</div>")
    end
    any(i -> untimed(sides[i].label), kept) && println(io, "<p class=\"m\">† ran on another machine: its times are of different hardware.</p>")
    println(io, "</div>")
    return
end

# The banner of every page: the site, where this page is in it, and a line about what is shown.
function banner(home, trail, line = "", menus = "")
    return "<header class=\"site\"><div class=\"container\"><a class=\"brand\" href=\"/\">MadSuite benchmarks</a><span class=\"trail\"><a href=\"$home\">MadNLP</a></span>$trail<nav class=\"app\"><details class=\"menu\"><summary aria-label=\"Menu\">☰</summary><div><a href=\"/queue/\">Sign in</a><button id=\"theme\" type=\"button\">Light or dark</button></div></details></nav></div></header>" *
        "<div class=\"bar\"><div class=\"container\"><div class=\"line\">$line</div>$menus</div></div>"
end
state_badge(state) = "<span class=\"state $(state == "running" ? "on" : "off")\">$(escape_html(state))</span>"

# A run that says it is running but has not written for an hour has stopped without saying so (its
# job was killed): the list says stalled rather than running.
const STALLED_AFTER = 3600.0
shown_state(r) = r.state == "running" && hasproperty(r, :mtime) && time() - r.mtime > STALLED_AFTER ? "stalled" : r.state

# Every page has the same shell, and shares `style.css` and `report.js` at `assets`.
function page(path, title, content, bar; refresh = false, assets = ".")
    tmp = path * ".tmp"
    write(
        tmp, "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"robots\" content=\"noindex, nofollow\">",
        refresh ? "<meta name=\"live\" content=\"1\">" : "",
        "<title>", escape_html(title), "</title><script src=\"", assets, "/theme.js\"></script><link rel=\"stylesheet\" href=\"", assets, "/bootstrap.min.css\"><link rel=\"stylesheet\" href=\"", assets, "/style.css\"><body><div id=\"live\">",
        bar, "<main class=\"container\">", content, "</main></div><script src=\"", assets, "/report.js\"></script></body></html>\n",
    )
    chmod(tmp, 0o644)
    mv(tmp, path; force = true)
    return
end

# The head of a table of Hans Mittelmann's benchmarks, per set: the scaled shifted geometric mean,
# with the reference solver at 1, and the instances solved, over the instances all solvers have run.
# The problems of a set: all those that any solver on any device has reported. The Ipopt
# reference holds the whole set, so this is short of it only where there is no reference.
function set_size(cand, base, set)
    cases = Set{String}()
    for rs in (cand, base)
        rs === nothing && continue
        foreach(k -> k[2] == set && union!(cases, keys(rs.rows[k])), keys(rs.rows))
    end
    return length(cases)
end

function solver_table(io, sides, cand, base, code)
    info(s, field) = get(getfield(s.base ? base : cand, field), s.key, "–")
    revision(s) = code === nothing ? escape_html(info(s, :revs)) : replace(escape_html(info(s, :revs)), r"\b[0-9a-f]{8,40}\b" => c -> "<a href=\"$code/commit/$c\">$c</a>")
    println(io, "<div class=\"wide\"><table class=\"table table-sm\"><thead><tr><th>solver</th><th class=\"l\">device</th><th class=\"l\">revision</th><th class=\"l\">benchmark</th></tr></thead><tbody>")
    for s in sides
        println(io, "<tr><td>", key(s), "</td><td class=\"l\">", device_tag(s.key[2] == "ipopt" ? "cpu" : s.key[1]), "</td><td class=\"l\">", revision(s), "</td><td class=\"l\">", escape_html(info(s, :harness)), "</td></tr>")
    end
    println(io, "</tbody></table></div>")
    return
end

# One bar per device: how much of its sets has been run.
function progress(io, devices, sets, sides, cand, base, wall)
    counts = Tuple{Int, Int}[]
    println(io, "<div class=\"tiles\">")
    for device in devices
        candidates = [s for s in sides if s.key[1] == device && s.key[2] != "ipopt"]
        # a run of Ipopt alone: its own Ipopt
        isempty(candidates) && (candidates = [s for s in sides if s.key[2] == "ipopt" && !s.base])
        isempty(candidates) && continue
        mine = something(findfirst(s -> !s.base, candidates), 1) |> i -> candidates[i]
        done = solved_count = total = 0
        for set in sets
            rows = page_rows(mine, cand, base, set, device, wall)
            isempty(rows) && continue
            done += length(rows)
            solved_count += count(solved, values(rows))
            total += set_size(cand, base, set)
        end
        push!(counts, (done, total))
        share = total == 0 ? 0 : round(Int, 100 * done / total)
        println(io, "<div class=\"tile\"><div class=\"m\">", device_tag(device), " ", escape_html(mine.label), "</div><div class=\"big\">", done, "<small>/", total, "</small></div>")
        println(io, "<div class=\"meter\"><i style=\"width:", share, "%;background:", device_color(device), "\"></i></div><div class=\"m\">", solved_count, " solved</div></div>")
    end
    println(io, "</div>")
    return counts
end

function summary_table(io, blocks, sides, cand, base, wall)
    # a group of columns per solver: Ipopt, the base, current
    labels = unique!([s.label for (set, device) in blocks for s in on_device(sides, device) if !isempty(side_rows(s, cand, base, set))])
    rank(l) = l == "Ipopt" ? 1 : l == "current" ? 3 : any(s -> s.base && s.label == l, sides) ? 2 : 4
    sort!(labels; by = rank)
    println(io, "<div class=\"wide\"><table class=\"table table-sm summary\"><thead><tr><th rowspan=\"2\">set</th><th rowspan=\"2\" class=\"l\">device</th><th rowspan=\"2\" title=\"compared out of the whole set\">problems</th>")
    foreach(l -> print(io, "<th colspan=\"3\" class=\"g\">", title(l), "</th>"), labels)
    print(io, "</tr><tr>")
    foreach(_ -> print(io, "<th class=\"g\">solved</th><th>SGM (s)</th><th>SSGM</th>"), labels)
    println(io, "</tr></thead><tbody>")
    for (set, device) in blocks
        here = Dict(s.label => page_rows(s, cand, base, set, device, wall) for s in on_device(sides, device))
        have = [l for l in labels if !isempty(get(here, l, Dict()))]
        isempty(have) && continue
        cases = collect(reduce(intersect, [Set(keys(here[l])) for l in have]))
        isempty(cases) && continue
        means = Dict(l => sgm([score_time(here[l][c], device_wall(device, wall)) for c in cases]) + 10 for l in have)
        # Ipopt is the reference where it has run, else the best solver
        scale = haskey(means, "Ipopt") ? means["Ipopt"] : minimum(values(means))
        print(io, "<tr><td><a href=\"$(page_name(device, set))\">$(get(SET_TITLES, set, set))</a></td><td class=\"l\">$(device_tag(device))</td><td>$(all(==("Ipopt"), have) ? 0 : length(cases))/$(set_size(cand, base, set))</td>")
        for l in labels
            if l in have
                # a column of other hardware (†) is computed all the same; its name says so
                @printf(io, "<td class=\"g\">%d</td><td>%.2f</td><td>%.3g</td>", count(c -> solved(here[l][c]), cases), means[l] - 10, means[l] / scale)
            else
                print(io, "<td class=\"g\"></td><td></td><td></td>")
            end
        end
        println(io, "</tr>")
    end
    println(io, "</tbody></table></div>")
    return
end

# How each solver ended on a set, and how it fared by problem size.
function outcome_table(io, sides, rows, total)
    names = ["ok" => "solved", "a" => "acceptable", "t" => "time limit", "i" => "iteration limit", "f" => "other failure"]
    println(io, "<div class=\"wide\"><table class=\"table table-sm\"><thead><tr><th>solver</th>")
    foreach(n -> print(io, "<th>", n[2], "</th>"), names)
    println(io, "<th>not run yet</th></tr></thead><tbody>")
    for (s, r) in zip(sides, rows)
        print(io, "<tr><td>", key(s), "</td>")
        foreach(n -> print(io, "<td>", count(x -> status_letter(x.status) == n[1], values(r)), "</td>"), names)
        println(io, "<td>", total - length(r), "</td></tr>")
    end
    println(io, "</tbody></table></div>")
    return
end

function size_table(io, sides, rows)
    println(io, "<div class=\"wide\"><table class=\"table table-sm\"><thead><tr><th>solver</th><th title=\"Jacobian and Hessian nonzeros below 2^18\">small</th><th title=\"from 2^18 to 2^20 nonzeros\">medium</th><th title=\"2^20 nonzeros and above\">large</th></tr></thead><tbody>")
    for (s, r) in zip(sides, rows)
        print(io, "<tr><td>", key(s), "</td>")
        for class in (:small, :medium, :large)
            mine = [x for x in values(r) if size_class(x) == class]
            print(io, "<td>", count(solved, mine), "/", length(mine), "</td>")
        end
        println(io, "</tr>")
    end
    println(io, "</tbody></table></div>")
    return
end

# Where the time goes, over the instances with a breakdown. Ipopt reports none.
function breakdown(io, sides, rows)
    println(io, "<div class=\"wide\"><table class=\"table table-sm\"><thead><tr><th>solver</th><th>total (s)</th><th>linear solver</th><th>evaluations</th><th>initialization</th><th>other</th></tr></thead><tbody>")
    for (s, r) in zip(sides, rows)
        known = [x for x in values(r) if isfinite(x.t_linsolve) && x.t_total > 0]
        isempty(known) && continue
        total = sum(x -> x.t_total, known)
        share(f) = 100 * sum(f, known) / total
        lin, eval, init = share(x -> x.t_linsolve), share(x -> x.t_eval), share(x -> x.t_init)
        @printf(io, "<tr><td>%s</td><td>%.1f</td><td>%.1f%%</td><td>%.1f%%</td><td>%.1f%%</td><td>%.1f%%</td></tr>\n", key(s), total, lin, eval, init, 100 - lin - eval - init)
    end
    println(io, "</tbody></table></div>")
    return
end

function instance_table(io, set, sides, rows, logs)
    println(io, "<div class=\"scroll\"><table class=\"table table-sm table-hover cases\"><thead><tr><th class=\"s\" rowspan=\"2\">instance</th><th class=\"s\" rowspan=\"2\">nv</th><th class=\"s\" rowspan=\"2\">nc</th>")
    foreach(s -> print(io, "<th colspan=\"12\" class=\"l\">", title(s.label), "</th>"), sides)
    print(io, "</tr><tr>")
    foreach(_ -> print(io, "<th class=\"s l\">st</th><th class=\"s\">it</th><th class=\"s\">t (s)</th><th class=\"s\">obj</th><th class=\"s\">inf</th><th class=\"s\">nfac</th><th class=\"s\">nsol</th><th class=\"s\">nf</th><th class=\"s\">ng</th><th class=\"s\">nc</th><th class=\"s\">nj</th><th class=\"s\">nh</th>"), sides)
    println(io, "</tr></thead><tbody>")
    for case in sort!(collect(reduce(union, [Set(keys(r)) for r in rows]; init = Set{String}())))
        any_row = first(r[case] for r in rows if haskey(r, case))
        print(io, "<tr><td>", escape_html(case), "</td><td>", any_row.nvar, "</td><td>", any_row.ncon, "</td>")
        for (s, r) in zip(sides, rows)
            if !haskey(r, case)
                print(io, "<td class=\"l\">–</td>", "<td></td>"^11)
                continue
            end
            row = r[case]
            link = s.base ? nothing : get(logs, (s.key[1], set, case, s.key[2]), nothing)
            print(io, "<td class=\"l", solved(row) ? "" : " fail", "\" title=\"", status_name(row.status), "\">", link === nothing ? status_letter(row.status) : "<a href=\"$link\">$(status_letter(row.status))</a>", "</td><td>", row.iters, "</td>")
            # where the instance ran, when a run's nodes differ
            hasproperty(row, :machine) ? @printf(io, "<td title=\"on %s: not the machine of the other results\">%.3f<sup>m</sup></td>", escape_html(row.machine), row.t_total) : @printf(io, "<td>%.3f</td>", row.t_total)
            @printf(io, "<td>%.6g</td><td>%.1e</td>", row.obj, row.primal_feas)
            foreach(n -> print(io, "<td>", n, "</td>"), (row.n_fact, row.n_backsolve, row.n_obj, row.n_grad, row.n_con, row.n_jac, row.n_hess))
        end
        println(io, "</tr>")
    end
    println(io, "</tbody></table></div>")
    return
end

# What every worker is on.
function workers_page(site, root, bar, assets)
    io = IOBuffer()
    println(io, "<h1>Worker status</h1>")
    for dir in sort!(readdir(root; join = true))
        file = joinpath(dir, "summary.md")
        isfile(file) || continue
        m = match(r"^```\n(.*?)\n```"s, read(file, String))
        m === nothing && continue
        println(io, "<h2>", escape_html(basename(dir)), "</h2>")
        # a job run on several machines has the status of each, under `node <name>:` lines
        workers, node, nodes = String[], "", false
        for line in split(m[1], '\n')
            n = match(r"^node (.+):$", line)
            n === nothing || (node = n[1]; nodes = true; continue)
            w = match(r"worker (\d+): (\S+) (\S*) for (\d+) s, ([\d.]+) GB", line)
            if w === nothing
                println(io, "<p class=\"m\">", isempty(node) ? "" : escape_html(node) * ": ", escape_html(strip(line)), "</p>")
            else
                push!(workers, "<tr>" * (nodes ? "<td class=\"l\">$(escape_html(node))</td>" : "") * "<td>$(w[1])</td><td class=\"l\">$(escape_html(w[2]))</td><td class=\"l\">$(escape_html(w[3]))</td><td>$(w[4])</td><td>$(w[5])</td></tr>")
            end
        end
        isempty(workers) || println(io, "<div class=\"wide\"><table class=\"table table-sm\"><thead><tr>", nodes ? "<th class=\"l\">node</th>" : "", "<th>worker</th><th class=\"l\">state</th><th class=\"l\">instance</th><th>for (s)</th><th>memory (GB)</th></tr></thead><tbody>", join(workers), "</tbody></table></div>")
    end
    page(joinpath(site, "workers.html"), "Workers", String(take!(io)), bar; refresh = true, assets)
    return
end

page_name(device, set) = "$device-$set.html"
device_tag(device) = "<span class=\"tag $device\">$(uppercase(device))</span>"

# The page of a set on a device.
# Links to the published result files of a set, from the pages next to `data/`.
function result_links(site, set)
    links = String[]
    data = joinpath(site, "data")
    isdir(data) || return links
    for machine in filter(d -> isdir(joinpath(data, d)), readdir(data)), file in readdir(joinpath(data, machine))
        (startswith(file, set * "_") && endswith(file, ".csv")) || continue
        path = escape_html("data/$machine/$file")
        push!(links, "<a href=\"$path\">$(escape_html(replace(machine, "_" => " ")))</a>")
    end
    return links
end

function set_page(site, set, device, sides, cand, base, wall, logs, bar; refresh, assets)
    sides = on_device(sides, device)
    rows = [page_rows(s, cand, base, set, device, wall) for s in sides]
    keep = [i for i in eachindex(sides) if !isempty(rows[i])]
    sides, rows = sides[keep], rows[keep]
    io = IOBuffer()
    println(io, "<h1>", get(SET_TITLES, set, set), " ", device_tag(device), "</h1>")
    summary_table(io, [(set, device)], sides, cand, base, wall)
    println(io, "<h2>Outcomes</h2>")
    outcome_table(io, sides, rows, set_size(cand, base, set))
    println(io, "<h2>Solved by problem size</h2>")
    size_table(io, sides, rows)
    println(io, "<h2>Performance profiles</h2>")
    device_profile(io, set, device, sides, cand, base, wall)
    println(io, "<h2>Time breakdown</h2>")
    breakdown(io, sides, rows)
    files = result_links(site, set)
    isempty(files) || println(io, "<p class=\"m\">Result files: ", join(files, ", "), "</p>")
    println(io, "<h2>Instances</h2><p class=\"m\">nv, nc: variables, constraints. st: ok solved, a solved to the acceptable level, t time limit, i iteration limit, f other failure; click it for the solver log. it: iterations. inf: infeasibility. nfac, nsol: factorizations, linear solves. nf, ng, nc, nj, nh: evaluations of the objective, gradient, constraints, Jacobian, Hessian.</p><input id=\"filter\" placeholder=\"Filter by name\"><label class=\"only\"><input id=\"failed\" type=\"checkbox\">Failures only</label>")
    instance_table(io, set, sides, rows, logs)
    here = "href=\"$(page_name(device, set))\""
    page(joinpath(site, page_name(device, set)), "$set on $device: MadNLP benchmarks", String(take!(io)), replace(bar, here => here * " class=\"active\" aria-current=\"page\""); refresh, assets)
    return
end

const MAX_FILE_SIZE = 64 * 2^20

# Bring `data/` up to date with the result directories, and index the solver logs.
function sync_data(site, root)
    logs = Dict{Tuple{String, String, String, String}, String}()
    # a job run on several machines shows all of their results, so links all of their logs
    chosen = startswith(basename(normpath(root)), "run-x") ? Dict{String, String}() : current_machines(root)
    for dir in sort!(readdir(root))
        source = joinpath(root, dir)
        isdir(source) || continue
        device = nothing
        for (path, _, files) in walkdir(source), file in files
            from = joinpath(path, file)
            # the files of a run are public: only the kinds a run writes, and no links out of it
            (islink(from) || !occursin(r"\.(csv|toml|txt|md)$", file) || filesize(from) > MAX_FILE_SIZE) && continue
            relative = relpath(from, root)
            to = joinpath(site, "data", relative)
            if !isfile(to) || mtime(to) < mtime(from)
                mkpath(dirname(to))
                # a solver log carries terminal colours
                endswith(file, ".txt") ? write(to, replace(read(from, String), r"\e\[[0-9;]*m" => "")) : cp(from, to; force = true)
                chmod(to, 0o644)
            end
            endswith(file, ".meta.toml") && device === nothing && (device = read_meta(from[1:(end - 10)])["device"])
        end
        logdir = joinpath(source, "logs")
        # the logs of the machine whose results are shown
        (device === nothing || !isdir(logdir) || get(chosen, device, normpath(source)) != normpath(source)) && continue
        for set in readdir(logdir), file in readdir(joinpath(logdir, set))
            m = match(r"^(.*)__(.*)\.txt$", file)
            m === nothing || !safe_name(dir) || !safe_name(set) || !safe_name(file) || (logs[(device, set, m[1], m[2])] = "data/$dir/logs/$set/$file")
        end
    end
    return logs
end

"""
    write_report(site, root; base = nothing, wall = 7200.0, title, link = nothing, reference = nothing, finished = false, assets = ".")

Write the report of the result directories under `root` to the directory `site`. `base` is a
`ResultSet` to compare with, `reference` the Ipopt reference and `link` a page to link at the
top, such as the pull request.
While `finished = false` the pages update themselves in place and the front page shows what
every worker is on. `assets` is where the stylesheet and script are, relative to `site`; with
the default they are written there.
"""
# The web server lists no directories, so `data/` gets a page per directory.
function write_listings(site, assets)
    data = joinpath(site, "data")
    isdir(data) || return
    for (dir, folders, files) in walkdir(data)
        where = relpath(dir, data)
        depth = where == "." ? 1 : 1 + length(splitpath(where))
        up = "../"^depth
        io = IOBuffer()
        println(io, "<h1>", where == "." ? "Raw data" : escape_html(where), "</h1><ul class=\"files\">")
        where == "." || println(io, "<li><a href=\"../\">..</a></li>")
        foreach(f -> println(io, "<li><a href=\"", escape_html(escape_url(f)), "/\">", escape_html(f), "/</a></li>"), sort(folders))
        for file in sort(files)
            file == "index.html" && continue
            println(io, "<li><a href=\"", escape_html(escape_url(file)), "\">", escape_html(file), "</a> <span class=\"m\">", Base.format_bytes(filesize(joinpath(dir, file))), "</span></li>")
        end
        println(io, "</ul>")
        rel = assets == "." ? rstrip(up, '/') : up * assets
        home = assets == "." ? up * "index.html" : rel * "/index.html"
        trail = "<span class=\"trail\"><a href=\"$(up)index.html\">Report</a></span><span class=\"trail\">Raw data</span>"
        page(joinpath(dir, "index.html"), "Raw data: MadNLP benchmarks", String(take!(io)), banner(home, trail); assets = rel)
    end
    return
end
escape_url(s) = replace(s, r"[^A-Za-z0-9_.~+-]" => c -> join(["%" * uppercase(string(b; base = 16, pad = 2)) for b in codeunits(c)]))

function write_report(site, root; base = nothing, wall = 7200.0, title = "MadNLP benchmark", link = nothing, reference = nothing, finished::Bool = false, assets = ".", live = !finished)
    mkpath(site)
    chmod(site, 0o755)
    assets == "." && write_assets(site)
    logs = sync_data(site, root)
    write_listings(site, assets)
    repository = link === nothing ? nothing : match(r"^(https://github\.com/[^/]+/[^/]+)/", link)
    if reference !== nothing && repository !== nothing
        for (key, path) in reference.logs
            logs[key] = (isempty(ARCHIVE_URL[]) ? repository[1] : ARCHIVE_URL[]) * "/blob/$ARCHIVE_BRANCH/" * path
        end
    end
    cand = from_directory(root, "this run")
    # a run of Ipopt itself (config `ipopt`) is shown with its own results, not the reference's
    own_ipopt = any(k -> k[3] == "ipopt", keys(cand.rows))
    # until the run has results of its own there is nothing to compare: no reference, no base
    if isempty(cand.rows)
        reference, base = nothing, nothing
    end
    if reference !== nothing
        mergewith!((mine, _) -> mine, cand.rows, reference.rows)
        mergewith!((mine, _) -> mine, cand.revs, reference.revs)
        mergewith!((mine, _) -> mine, cand.harness, reference.harness)
    end
    sides = report_sides(cand, base)
    set_titles!(sides, cand, base, repository === nothing ? nothing : repository[1])
    walls = device_walls(root, wall)
    sets = sort!(unique!([k[2] for k in keys(cand.rows)]))
    stamp = Dates.format(now(UTC), "yyyy-mm-dd HH:MM:SS")
    # the bar pinned to the top of every page
    devices = unique!(vcat([s.key[1] for s in sides if s.key[2] != "ipopt"], own_ipopt ? ["cpu"] : String[]))
    menu(device) = "<nav class=\"$device\">" * device_tag(device) * join(["<a href=\"$(page_name(device, set))\">$(get(SET_TITLES, set, set))</a>" for set in sets]) * "</nav>"
    tools = ["<a href=\"index.html\">Summary</a>"]
    finished || push!(tools, "<a href=\"workers.html\">Workers</a>")
    push!(tools, "<a href=\"data/\">Raw data</a>")
    trail = "<span class=\"trail\">" * (link === nothing ? escape_html(title) : "<a href=\"" * escape_html(link) * "\">" * escape_html(title) * "</a>") * "</span>"
    line = state_badge(finished ? "finished" : "running") * " <span class=\"m\">updated <span class=\"ago\" data-t=\"$(replace(stamp, " " => "T"))Z\">$stamp UTC</span></span>"
    bar = banner(assets == "." ? "index.html" : "$assets/index.html", trail, line, "<nav class=\"tools\">" * join(tools) * "</nav>" * join(menu.(devices)))
    for device in devices, set in sets
        set_page(site, set, device, sides, cand, base, walls, logs, bar; refresh = live, assets)
    end
    finished || workers_page(site, root, bar, assets)
    io = IOBuffer()
    println(io, "<h1>Summary</h1>")
    counts = progress(io, devices, sets, sides, cand, base, walls)
    summary_table(io, [(set, device) for device in devices for set in sets], sides, cand, base, walls)
    println(io, "<h2>Solvers</h2>")
    solver_table(io, sides, cand, base, repository === nothing ? nothing : repository[1])
    for device in devices
        machines = get(cand.machines, device, String[])
        isempty(machines) || println(io, "<p class=\"m\">", uppercase(device), ": ", escape_html(join(machines, "; ")), "</p>")
    end
    versions = filter(!=("–"), unique!(collect(values(cand.harness))))
    ipopt_versions = unique!([v for (k, v) in cand.harness if k[2] == "ipopt"])
    own_versions = unique!([v for (k, v) in cand.harness if k[2] != "ipopt"])
    if !isempty(ipopt_versions) && !isempty(own_versions) && ipopt_versions != own_versions
        println(io, "<p class=\"m fail\">The Ipopt reference is from benchmark ", escape_html(join(ipopt_versions, ", ")),
                ", as benchmark ", escape_html(join(own_versions, ", ")), " has none yet: compare with care.</p>")
    end
    println(io, "<p class=\"m\">Benchmark ", escape_html(join(versions, ", ")), ": the version fixes the instances, the options, Ipopt and HSL.</p>")
    # the runs the base names link to their reports
    base === nothing || isempty(base.label) || println(io, "<p class=\"m\">Base: ", replace(escape_html(base.label), r"\b(run-[a-z0-9-]+|pr-\d+(?:-[0-9a-f]{8})?|master-[0-9a-f]{8})\b" => m -> "<a href=\"../$m/\">$m</a>"), "</p>")
    println(io, "<p class=\"m\">SGM: shifted geometric mean of the solve time, with a shift of 10 s and a failure counting as the time limit of a solve (CPU ", round(Int, walls["cpu"]), " s, GPU ", round(Int, walls["cuda"]), " s, for every solver on those pages), over the problems that all solvers shown have run. SSGM: the SGM scaled so that Ipopt, or the best solver where Ipopt has not run, is 1.</p>")
    page(joinpath(site, "index.html"), title, String(take!(io)), bar; refresh = live, assets)
    # for a check outside this machine that the report is alive
    write_atomic(joinpath(site, "status.json"), "{\"state\":\"$(finished ? "finished" : "running")\",\"updated\":\"$stamp\"}\n")
    # how much of the run is done, over every device: instances with a result, of all
    return (; done = sum(first, counts; init = 0), total = sum(last, counts; init = 0))
end

# The theme of every page: Bootstrap, this site's stylesheet and its script, all served locally.
const ASSETS = joinpath(dirname(@__DIR__), "assets")
function write_assets(site)
    for name in readdir(ASSETS)
        write_atomic(joinpath(site, name), read(joinpath(ASSETS, name)))
    end
    return
end

"""
    write_index(site, runs; repository)

Write the list of benchmarks to `site/index.html`: the running pull requests, the finished ones
and the archived results. `runs` holds `(; name, pr, title, commit, state, updated)` per run; `pr` is empty for a manual run.
"""
# " at <sha8>" for the folder of one commit of a pull request
at_commit(name) = (m = match(r"^pr-\d+-([0-9a-f]{8})$", name); m === nothing ? "" : " at " * m[1])

function write_index(site, runs; repository)
    write_assets(site)
    code = "https://github.com/" * repository
    io = IOBuffer()
    # One list of every run, however it was started (Sungho): pull requests, master, runs on the
    # queue's machines or on clusters, the archived results and the Ipopt references. Running first,
    # then the newest.
    row(name_html, github, commit, state, updated, files) = "<tr><td>$name_html</td><td class=\"l\">$github</td><td class=\"l\">$commit</td><td class=\"l\">$state</td><td>$updated</td><td class=\"l\">$files</td></tr>"
    commit_link(c) = occursin(r"^[0-9a-f]{40}$", c) ? "<a href=\"$code/commit/$c\">$(first(c, 8))</a>" : escape_html(first(c, 8))
    live_row(r) = row(
        "<a href=\"$(escape_html(r.name))/\">" * (isempty(r.pr) ? escape_html(replace(r.title, r"^Manual run: " => "")) : "#" * escape_html(r.pr) * at_commit(r.name)) * "</a>",
        isempty(r.pr) ? (occursin(r"^[0-9a-f]{40}$", r.commit) ? "<a href=\"$code/commit/$(r.commit)\">commit</a>" : "–") : "<a href=\"$code/pull/$(escape_html(r.pr))\">pull request</a>",
        commit_link(r.commit),
        state_badge(shown_state(r)) * (shown_state(r) == "running" && get(r, :total, 0) > 0 ? " <span class=\"m\">$(r.done)/$(r.total)</span>" : ""),
        escape_html(replace(first(r.updated, 16), "T" => " ")),
        "<a href=\"$(escape_html(r.name))/data/\">files</a>",
    )
    running = [r for r in runs if shown_state(r) == "running"]
    finished = [r for r in runs if shown_state(r) != "running"]
    rows = [live_row(r) for r in vcat(sort(running; by = r -> r.updated, rev = true), sort(finished; by = r -> r.updated, rev = true))]
    paths = split(archive_git("ls-tree", "-r", "--name-only", "origin/$ARCHIVE_BRANCH"), '\n'; keepempty = false)
    archived = [match(r"^([^/]+)/([0-9a-f]{40})/summary\.md$", p) for p in paths]
    filter!(!isnothing, archived)
    commits = unique!([String(m[2]) for m in archived])
    for commit in commits
        machines = [replace(m[1], "_" => " ") for m in archived if m[2] == commit]
        push!(rows, row("<a href=\"archive/$commit/\">master $(first(commit, 8))</a> <span class=\"m\">$(escape_html(join(machines, ", ")))</span>",
            "<a href=\"$code/commit/$commit\">commit</a>", commit_link(commit), state_badge("archived"), "", "<a href=\"archive/$commit/data/\">files</a>"))
    end
    versions = unique!([m[1] for m in filter(!isnothing, [match(r"^ipopt/([^/]+)/", p) for p in paths])])
    tree = isempty(ARCHIVE_URL[]) ? code : escape_html(ARCHIVE_URL[])
    for v in versions
        push!(rows, row("<a href=\"$tree/tree/$ARCHIVE_BRANCH/ipopt/$(escape_html(v))\">Ipopt reference, benchmark $(escape_html(v))</a>", "–", "–", state_badge("reference"), "", "<a href=\"$tree/tree/$ARCHIVE_BRANCH/ipopt/$(escape_html(v))\">files</a>"))
    end
    println(io, "<h2>Runs</h2><div class=\"wide\"><table class=\"table table-sm\"><thead><tr><th>run</th><th class=\"l\">GitHub</th><th class=\"l\">commit</th><th class=\"l\">state</th><th>updated (UTC)</th><th class=\"l\">raw data</th></tr></thead><tbody>")
    foreach(r -> println(io, r), rows)
    println(io, "</tbody></table></div>")
    line = "<span class=\"m\">$(length(running)) running, $(length(finished)) finished, $(length(commits)) archived</span>"
    page(joinpath(site, "index.html"), "MadNLP benchmarks", String(take!(io)), banner("index.html", "", line); refresh = true)
    return
end
