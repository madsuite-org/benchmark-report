"""
    BenchmarkReport

The website of a benchmark: it reads result files and writes static pages. Result files are
treated as untrusted input, since the pages are public.
"""
module BenchmarkReport

using Dates, DelimitedFiles, FileWatching, Printf, TOML

export write_report, write_index, locate, ipopt_reference, benchmark_version, serve

# Status recorded for an instance whose worker was killed for overrunning the wall limit, and
# for one that could not be built or whose solve threw.
const KILLED_STATUS = -100
const ERROR_STATUS = -101

# The status codes of MadNLP that the report tells apart.
const SOLVE_SUCCEEDED = 1
const SOLVED_TO_ACCEPTABLE_LEVEL = 2
const MAXIMUM_ITERATIONS_EXCEEDED = 6
const MAXIMUM_WALLTIME_EXCEEDED = 7
const STATUS_NAMES = Dict(
    3 => "search direction too small", 4 => "diverging iterates", 5 => "infeasible problem detected",
    6 => "iteration limit", 7 => "time limit", -1 => "restoration failed", -2 => "invalid number detected",
    -3 => "error in step computation", -4 => "not enough degrees of freedom", -5 => "user stop", -6 => "internal error",
    -7 => "invalid objective", -8 => "invalid gradient", -9 => "invalid constraints", -10 => "invalid Jacobian",
    -11 => "invalid Hessian",
)

# ------------------------------------------------------------------------------------------
# Reading and scoring result files

"The rows of a result file, given by its path or its contents, as a vector of `NamedTuple`s."
read_results(path::AbstractString) = open(read_results, path)
function read_results(io::IO)
    data = readdlm(io, ',', Any)
    names = Tuple(Symbol.(data[1, :]))
    return [NamedTuple{names}(Tuple(data[i, :])) for i in axes(data, 1)[(begin + 1):end]]
end

"The metadata written beside the result file `path`, as a dictionary."
read_meta(path::AbstractString) = TOML.parsefile(path * ".meta.toml")

"The result files of a directory: `(set, config) => path`."
function result_files(dir::AbstractString)
    files = Dict{Tuple{String, String}, String}()
    for f in sort!(readdir(dir))
        m = match(r"^([a-z]+)_(.+)\.csv$", f)
        m === nothing || (files[(m[1], m[2])] = joinpath(dir, f))
    end
    return files
end

"""
    summarize(io, dir; wall = 7200.0)

Print, as Markdown, the machines of the runs in `dir` and, per set and configuration, the
instances solved and the shifted geometric mean of the solve time by size class.
"""
function summarize(io::IO, dir::AbstractString; wall = 7200.0)
    files = result_files(dir)
    metas = [read_meta(f) for f in values(files) if isfile(f * ".meta.toml")]
    solvers = unique!(sort!([solver_name(m["device"]) for m in metas]))
    println(io, "### ", join([solvers; basename(rstrip(dir, '/'))], ", ", " on "), "\n")
    machines = unique!([hardware_line(m) for m in metas])
    foreach(m -> println(io, "- ", m), machines)
    isempty(machines) || println(io)
    for set in sort!(unique(first.(keys(files))))
        println(io, "**", set, "** (solved, SGM in seconds; a failure counts as ", round(Int, wall), " s)\n")
        println(io, "| configuration | small | medium | large | total |")
        println(io, "|---|---|---|---|---|")
        for (s, config) in sort!(collect(keys(files)))
            s == set || continue
            rows = read_results(files[(s, config)])
            cells = map((:small, :medium, :large, :total)) do class
                sel = class == :total ? rows : [r for r in rows if size_class(r) == class]
                isempty(sel) ? "–" : @sprintf("%d / %d, %.2f", count(solved, sel), length(sel), sgm(score_time.(sel, wall)))
            end
            println(io, "| ", config, " | ", join(cells, " | "), " |")
        end
        println(io)
    end
    return
end

"The package that solves on `device`: MadNLP on the host, cuMadNLP on a CUDA device."
solver_name(device) = device == "cuda" ? "cuMadNLP" : "MadNLP"

"One line that names the machine of a run, from its metadata."
function hardware_line(meta)
    cores = haskey(meta, "cpu_cores") ? "$(meta["cpu_cores"]) cores, " : ""
    cpu = "$(meta["cpu_model"]) ($(cores)$(meta["cpu_threads"]) threads), $(meta["memory_gb"]) GB of memory"
    gpu = haskey(meta, "gpu_name") ? "; $(meta["gpu_name"]), $(meta["gpu_memory_gb"]) GB, driver $(meta["gpu_driver"]), CUDA runtime $(meta["cuda_runtime"])" : ""
    return "$(meta["host"]): $(cpu)$(gpu)"
end

"`SOLVE_SUCCEEDED` or `SOLVED_TO_ACCEPTABLE_LEVEL`."
solved(r) = r.status == SOLVE_SUCCEEDED || r.status == SOLVED_TO_ACCEPTABLE_LEVEL

"The time an instance is scored with: its solve time if solved, the wall limit otherwise."
score_time(r, wall) = solved(r) && r.t_total isa Real && isfinite(r.t_total) ? min(Float64(r.t_total), wall) : wall

"Shifted geometric mean of `times` with shift `shift`."
sgm(times; shift = 10.0) = isempty(times) ? NaN : exp(sum(t -> log(t + shift), times) / length(times)) - shift

"Size class of an instance by the nonzeros of its Jacobian and Hessian together."
size_class(r) = (n = r.nnzj + r.nnzh; n < 2^18 ? :small : n < 2^20 ? :medium : :large)

# ------------------------------------------------------------------------------------------
# Result sets

const ARCHIVE_BRANCH = "benchmark-results"
# The clone of the repository that holds the archive branch.
const REPOSITORY = Ref(".")
# The clone may belong to another user, and a server may keep it up to date by itself, with
# `FETCH[]` false.
const FETCH = Ref(true)
git_command(args...) = (root = REPOSITORY[]; `git -c safe.directory=$root -C $root $args`)
git(args...) = readchomp(git_command(args...))
# The archive of results may be another repository than the benchmarked one: its clone, and its
# address for links. Empty: the benchmarked repository.
const ARCHIVE = Ref("")
const ARCHIVE_URL = Ref("")
archive_command(args...) = (root = isempty(ARCHIVE[]) ? REPOSITORY[] : ARCHIVE[]; `git -c safe.directory=$root -C $root $args`)
archive_git(args...) = readchomp(archive_command(args...))

# The revision of the archive branch, up to date if the clone may fetch; `nothing` without one.
function archive_revision()
    FETCH[] || return "origin/$ARCHIVE_BRANCH"
    remote = get(ENV, "MADNLP_BENCHMARK_REMOTE", "origin")
    return success(archive_command("fetch", "--quiet", remote, ARCHIVE_BRANCH)) ? "FETCH_HEAD" : nothing
end
# a full commit hash as it is shown; any other revision as it is
short(rev) = occursin(r"^[0-9a-f]{40}$", rev) ? rev[1:8] : rev

# The results of one side: per (device, set, config) the rows by case, the machines per
# device, and what to call it.
struct ResultSet
    label::String
    rows::Dict{Tuple{String, String, String}, Dict{String, Any}}
    machines::Dict{String, Vector{String}}
    revs::Dict{Tuple{String, String}, String}      # (device, config) => what was run
    harness::Dict{Tuple{String, String}, String}   # (device, config) => version of this benchmark
    logs::Dict{Tuple{String, String, String, String}, String}   # (device, set, case, config) => address of the solver log
end

ResultSet(label) = ResultSet(label, Dict(), Dict(), Dict(), Dict(), Dict())

# A row as the report may use it: every column but the name of the instance is a number.
number(x) = x isa Real ? x : something(tryparse(Float64, string(x)), NaN)
clean_row(r) = NamedTuple{keys(r)}(Tuple(name === :case ? string(r[name]) : number(r[name]) for name in keys(r)))

# Files of the names below become pages and addresses of the report.
const DEVICES = ("cpu", "cuda", "amdgpu")
safe_name(s) = occursin(r"^[A-Za-z0-9_.+-]+$", s) && !startswith(s, ".")

function add!(rs::ResultSet, set, config, meta, rows; machine = "")
    device = string(get(meta, "device", ""))
    (device in DEVICES && safe_name(set) && safe_name(config)) || return rs
    # rows of several machines (a job whose nodes differ) are merged, each saying where it ran
    tagged(r) = isempty(machine) ? clean_row(r) : merge(clean_row(r), (; machine = replace(machine, "_" => " ")))
    merge!(get!(rs.rows, (device, set, config), Dict{String, Any}()), Dict(string(r.case) => tagged(r) for r in rows))
    abbreviate(commit) = replace(commit, r"^([0-9a-f]{8})[0-9a-f]{32}" => s"\1")
    commit = abbreviate(get(meta, "madnlp_commit", "unknown"))
    haskey(meta, "first_commit") && (commit = abbreviate(meta["first_commit"]) * " to " * commit)
    rs.harness[(device, config)] = get(meta, "benchmark_version", "–")
    rs.revs[(device, config)] = config == "ipopt" ? get(meta, "ipopt_version", "?") : commit
    line = hardware_line(meta)
    line in get!(rs.machines, device, String[]) || push!(rs.machines[device], line)
    return rs
end

function from_directory(dir, label)
    rs = ResultSet(label)
    # a job run on several nodes (run-x*) has the rows of all of them; elsewhere one machine per device
    together = startswith(basename(normpath(dir)), "run-x")
    chosen = together ? Dict{String, String}() : current_machines(dir)
    # the machines of each device: rows say where they ran only when there is more than one
    machines = Dict{String, Set{String}}()
    for (root, _, _) in walkdir(dir), path in values(result_files(root))
        isfile(path * ".meta.toml") && push!(get!(machines, string(get(read_meta(path), "device", "")), Set{String}()), normpath(root))
    end
    for (root, _, _) in walkdir(dir), ((set, config), path) in result_files(root)
        isfile(path * ".meta.toml") || continue
        meta = read_meta(path)
        device = string(get(meta, "device", ""))
        get(chosen, device, normpath(root)) == normpath(root) || continue
        mixed = together && length(get(machines, device, ())) > 1
        add!(rs, set, config, meta, read_results(path); machine = mixed ? basename(normpath(root)) : "")
    end
    return rs
end

"""
    current_machines(dir) -> Dict(device => folder)

The folder of results under `dir` to use for each device. A run can be taken up by another machine
(when the first one went away), and then a folder has results of one device from two machines: the
one written to last is the run's, and the other is not mixed in.
"""
function current_machines(dir)
    latest = Dict{String, Tuple{Float64, String}}()
    for (root, _, _) in walkdir(dir), path in values(result_files(root))
        isfile(path * ".meta.toml") || continue
        device = string(get(read_meta(path), "device", ""))
        t = mtime(path)
        (!haskey(latest, device) || t > latest[device][1] || (t == latest[device][1] && root == latest[device][2])) && (latest[device] = (t, root))
    end
    return Dict(d => normpath(r) for (d, (_, r)) in latest)
end

# The archived results of `rev`: for every device, those of the nearest commit on the
# first-parent history of `rev` that has results for that device. `nothing` if the archive
# has none.
function from_archive(rev; depth = 200)
    archive = archive_revision()
    archive === nothing && return nothing
    paths = split(archive_git("ls-tree", "-r", "--name-only", archive), '\n'; keepempty = false)
    show(path) = read(archive_command("show", "$archive:$path"), String)
    by_commit = Dict{String, Vector{Tuple{String, String, String}}}()   # commit => (path, set, config)
    for path in paths
        m = match(r"^[^/]+/([0-9a-f]{40})/([a-z]+)_(.+)\.csv$", path)
        m === nothing || push!(get!(by_commit, m[1], []), (String(path), m[2], m[3]))
    end
    rs = ResultSet("")
    used = Dict{String, String}()   # device => commit
    for sha in split(git("rev-list", "--first-parent", "-n", string(depth), rev), '\n')
        for (path, set, config) in get(by_commit, sha, [])
            meta = TOML.parse(show(path * ".meta.toml"))
            get!(used, meta["device"], sha) == sha || continue   # a nearer commit covers this device
            add!(rs, set, config, meta, read_results(IOBuffer(show(path))))
        end
    end
    isempty(used) && return nothing
    target = git("rev-parse", rev)
    # one entry per commit used; the devices are named only when they come from different commits
    commits = unique!(sort!(collect(values(used))))
    label = join(
        map(commits) do sha
            name = sha == target ? "`$(sha[1:8])`" : "`$(sha[1:8])`, the nearest archived ancestor of `$(short(rev))`"
            length(commits) == 1 ? name : name * " (" * join(sort!([d for (d, c) in used if c == sha]), ", ") * ")"
        end, "; ",
    )
    return ResultSet(label, rs.rows, rs.machines, rs.revs, rs.harness, rs.logs)
end

# The results that stand for master for the run in folder `name` of `live`: for each machine and
# set, the newest ones walking back along master, of a commit itself or of the pull request it
# merged. A merge commit names its pull request, `(#N)` at the end of its subject, and
# `pr-N/run.json` names the commit that was benchmarked, which may be an earlier head of that pull
# request. Timings are only comparable on the same machine. `nothing` if there are none.
function latest_merged(live, name; depth = 200)
    archive = archive_revision()
    (archive === nothing || !success(git_command("rev-parse", "--verify", "--quiet", "origin/master"))) && return nothing
    paths = split(archive_git("ls-tree", "-r", "--name-only", archive), '\n'; keepempty = false)
    show(path) = read(archive_command("show", "$archive:$path"), String)
    files = [m for m in (match(r"^([^/]+)/([0-9a-f]{40})/([a-z]+)_(.+)\.csv$", p) for p in paths) if m !== nothing]
    root = joinpath(live, name)
    machines = filter(d -> isdir(joinpath(root, d)), readdir(root))
    rs, done = ResultSet(""), Set{Tuple{String, String}}()
    used = Dict{String, Vector{String}}()     # device => where its rows came from
    best = Dict{String, Int}()                # device => position on master of its newest commit
    history = split(git("log", "--first-parent", "-n", string(depth), "--format=%H %s", "origin/master"), '\n'; keepempty = false)
    for (position, line) in enumerate(history)
        sha, subject = split(line, ' '; limit = 2)
        merged = match(r"\(#(\d+)\)$", subject)
        own(n) = n == "pr-$(merged[1])" || startswith(n, "pr-$(merged[1])-")
        merged !== nothing && own(name) && continue
        commits = [String(sha)]
        if merged !== nothing
            # the pull request's runs, one folder per commit (or one folder, from before that)
            for n in filter(n -> own(n) && isfile(joinpath(live, n, "run.json")), readdir(live))
                head = match(r"\"commit\"\s*:\s*\"([0-9a-f]{40})\"", read(joinpath(live, n, "run.json"), String))
                head === nothing || head[1] in commits || push!(commits, String(head[1]))
            end
        end
        for commit in commits, machine in machines
            mine = [m for m in files if m[1] == machine && m[2] == commit && (machine, String(m[3])) ∉ done]
            isempty(mine) && continue
            devices = Set{String}()
            for m in mine
                path = String(m.match)
                meta = TOML.parse(show(path * ".meta.toml"))
                add!(rs, m[3], m[4], meta, read_results(IOBuffer(show(path))))
                device = string(get(meta, "device", ""))
                push!(devices, device)
                best[device] = min(get(best, device, typemax(Int)), position)
            end
            sets = unique!([String(m[3]) for m in mine])
            union!(done, [(machine, set) for set in sets])
            for device in devices
                push!(get!(used, device, String[]), "`$(commit[1:8])`" * (merged === nothing ? "" : ", from #$(merged[1])") * ", on " * replace(machine, "_" => " ") * " (" * join(sort(sets), ", ") * ")")
            end
        end
    end
    # A device of this run with no master results on its machines (a cluster's, say) takes the
    # latest master's from another machine; its times say so, as they do not compare.
    mine = Set{String}()
    for (dir, _, _) in walkdir(root), path in values(result_files(dir))
        isfile(path * ".meta.toml") && push!(mine, string(get(read_meta(path), "device", "")))
    end
    # Jobs run on clusters (run-x*, in the uploads root) of a commit on master count as master's
    # results too (Sungho): per device, the newest commit wins, theirs or the archive's. Only their
    # default configuration; their rows from other machines than this run's say so.
    position_of = Dict(String(first(split(line, ' '; limit = 2))) => i for (i, line) in enumerate(history))
    external = Tuple{Int, Int, String, String}[]
    if !isempty(UPLOADS[]) && isdir(UPLOADS[])
        for n in readdir(UPLOADS[])
            (startswith(n, "run-x") && n != name) || continue
            text = try
                read(joinpath(UPLOADS[], n, "run.json"), String)
            catch
                continue
            end
            c = match(r"\"commit\"\s*:\s*\"([0-9a-f]{40})\"", text)
            (c === nothing || !haskey(position_of, c[1])) && continue
            # of several jobs of one commit, the one with the most results first (not a test on a few)
            rows = sum((countlines(p) - 1 for (dir, _, _) in walkdir(joinpath(UPLOADS[], n)) for p in values(result_files(dir)) if endswith(p, "_default.csv")); init = 0)
            push!(external, (position_of[c[1]], -rows, n, String(c[1])))
        end
    end
    for (position, _, n, commit) in sort!(external)
        dir = joinpath(UPLOADS[], n)
        found = Dict{String, Vector{Tuple{String, String, Dict{String, Any}, String}}}()   # device => (machine, set, meta, path)
        for machine in filter(d -> isdir(joinpath(dir, d)), readdir(dir)), ((set, config), path) in result_files(joinpath(dir, machine))
            (config == "default" && isfile(path * ".meta.toml")) || continue
            meta = read_meta(path)
            push!(get!(found, string(get(meta, "device", "")), []), (machine, set, meta, path))
        end
        for (device, entries) in found
            position < get(best, device, typemax(Int)) || continue
            foreach(k -> k[1] == device && delete!(rs.rows, k), collect(keys(rs.rows)))
            for (machine, set, meta, path) in entries
                add!(rs, set, "default", meta, read_results(path); machine = machine in machines ? "" : machine)
            end
            best[device] = position
            used[device] = ["`$(commit[1:8])` from $(n) ($device, cluster nodes)"]
        end
    end
    missing_devices = setdiff(mine, Set(k[1] for k in keys(rs.rows)))
    for device in sort!(collect(missing_devices)), line in split(git("log", "--first-parent", "-n", string(depth), "--format=%H %s", "origin/master"), '\n'; keepempty = false)
        sha = first(split(line, ' '; limit = 2))
        found = false
        for machine in unique!([String(m[1]) for m in files if m[2] == sha])
            mine_files = [m for m in files if m[1] == machine && m[2] == sha]
            metas = [TOML.parse(show(String(m.match) * ".meta.toml")) for m in mine_files]
            any(meta -> get(meta, "device", "") == device, metas) || continue
            for (m, meta) in zip(mine_files, metas)
                get(meta, "device", "") == device || continue
                add!(rs, m[3], m[4], meta, read_results(IOBuffer(show(String(m.match)))); machine)
            end
            used[device] = ["`$(sha[1:8])` on " * replace(machine, "_" => " ") * " ($device, another machine)"]
            found = true
            break
        end
        found && break
    end
    # only the devices this run has: a CPU run is not compared with GPU results
    if !isempty(mine)
        foreach(k -> k[1] in mine || delete!(rs.rows, k), collect(keys(rs.rows)))
        foreach(d -> d in mine || delete!(used, d), collect(keys(used)))
    end
    isempty(rs.rows) && return nothing
    return ResultSet(join(reduce(vcat, [used[d] for d in sort!(collect(keys(used)))]; init = String[]), "; "), rs.rows, rs.machines, rs.revs, rs.harness, rs.logs)
end

# The Ipopt reference of a version of this benchmark, `ipopt/<version>/` on the archive branch:
# one run for all the machines and commits that are compared under that version. A version with
# no reference yet takes the newest earlier one, so that Ipopt is always shown; its version is in
# the report. `nothing` if no version has one.
function ipopt_reference(version)
    archive = archive_revision()
    archive === nothing && return nothing
    show(path) = read(archive_command("show", "$archive:$path"), String)
    have = [String(m[1]) for m in (match(r"^ipopt/([^/]+)/", p) for p in split(archive_git("ls-tree", "-r", "--name-only", archive, "ipopt/"), '\n'; keepempty = false)) if m !== nothing]
    versions = sort!(unique!([v for v in have if tryparse(VersionNumber, v) !== nothing]); by = VersionNumber)
    if !(version in have)
        earlier = filter(v -> tryparse(VersionNumber, version) === nothing || VersionNumber(v) <= VersionNumber(version), versions)
        isempty(earlier) && return nothing
        version = last(earlier)
    end
    rs = ResultSet("Ipopt reference")
    for path in split(archive_git("ls-tree", "-r", "--name-only", archive, "ipopt/$version/"), '\n'; keepempty = false)
        m = match(r"/([a-z]+)_ipopt\.csv$", path)
        m === nothing || add!(rs, m[1], "ipopt", TOML.parse(show(path * ".meta.toml")), read_results(IOBuffer(show(path))))
        log = match(r"/logs/([a-z]+)/(.*)__ipopt\.txt$", path)
        log === nothing || (rs.logs[("cpu", log[1], log[2], "ipopt")] = String(path))
    end
    # an older reference may not record its version: it is the one it is filed under
    foreach(k -> rs.harness[k] == "–" && (rs.harness[k] = version), collect(keys(rs.harness)))
    return isempty(rs.rows) ? nothing : rs
end

# The version of the benchmark a result folder comes from, for its Ipopt reference.
function benchmark_version(root)
    for (dir, _, files) in walkdir(root), file in files
        endswith(file, ".meta.toml") || continue
        version = get(TOML.parsefile(joinpath(dir, file)), "benchmark_version", "")
        occursin(r"^[\w.+-]+$", version) && return version
    end
    return "0.1.0"
end

function locate(arg, label)
    isdir(arg) && return from_directory(arg, something(label, "`$arg`"))
    rs = from_archive(arg)
    return rs === nothing || label === nothing ? rs : ResultSet(label, rs.rows, rs.machines, rs.revs, rs.harness, rs.logs)
end

include("site.jl")
include("serve.jl")

end
