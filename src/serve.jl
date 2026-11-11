# The service behind the website. It keeps the pages of `live` up to date in `site`: a folder
# `pr-<N>` per pull request or `run-<name>` per manual run, whose `run.json` is replaced after every copy of results. All of
# `live` is untrusted. The archive is read from `REPOSITORY[]` and never fetched here.

const DEFAULT_REPOSITORY = "madsuite-org/MadNLP.jl"
const RUNS = Dict{String, NamedTuple}()
# Reports: compositions of runs, `report-<id>` folders in the uploads root with a `report.toml`
# written by the app (title, the runs' folders and their columns' labels).
const REPORTS = Dict{String, NamedTuple}()

function json_field(text, name)
    m = match(Regex("\"$name\"\\s*:\\s*\"([^\"]*)\""), text)
    return m === nothing ? "" : String(m[1])
end

# Replace `path` in one step, so that a reader sees the old file or the new one.
function write_atomic(path, content...)
    tmp = path * ".tmp"
    write(tmp, content...)
    chmod(tmp, 0o644)
    return mv(tmp, path; force = true)
end

# The commit this code was deployed from.
function code_commit()
    root = dirname(@__DIR__)
    return try
        readchomp(`git -c safe.directory=$root -C $root rev-parse HEAD`)
    catch
        "unknown"
    end
end

# a pull request's results are kept per commit, pr-<N>-<sha8>; plain pr-<N> is from before that
is_folder(name) = occursin(r"^(pr-\d+(-[0-9a-f]{8})?|run-[a-z0-9-]+|master-[0-9a-f]{8}|report-\d+)$", name)
is_report(name) = occursin(r"^report-\d+$", name)

# Uploaded results, in a root of their own: only their run-up-* and run-x* (jobs run elsewhere)
# folders are read there, and none of those is read from the live root, so neither can stand in
# for the other.
const UPLOADS = Ref("")
function folders(live)
    uploaded(n) = startswith(n, "run-up-") || startswith(n, "run-x") || startswith(n, "report-")
    out = [(live, n) for n in readdir(live) if is_folder(n) && !uploaded(n)]
    if !isempty(UPLOADS[]) && isdir(UPLOADS[])
        append!(out, [(UPLOADS[], n) for n in readdir(UPLOADS[]) if uploaded(n) && is_folder(n)])
    end
    return out
end
is_run(live, name) = is_folder(name) && isfile(joinpath(live, name, is_report(name) ? "report.toml" : "run.json"))
stale(live, name) = is_report(name) ? (!haskey(REPORTS, name) || REPORTS[name].mtime != report_mtime(live, name)) :
    (!haskey(RUNS, name) || RUNS[name].mtime != mtime(joinpath(live, name, "run.json")))

# A report's spec, `(; title, runs, labels)`, or nothing.
function report_spec(live, name)
    try
        t = TOML.parsefile(joinpath(live, name, "report.toml"))
        runs = [String(r) for r in get(t, "runs", String[]) if r isa AbstractString && is_folder(r) && !is_report(r)]
        labels = [String(l) for l in get(t, "labels", String[])]
        isempty(runs) && return nothing
        return (; title = String(get(t, "title", name)), runs, labels = [i <= length(labels) ? labels[i] : runs[i] for i in eachindex(runs)])
    catch
        return nothing
    end
end
# where a run's folder is: the uploads root for runs made elsewhere, else the live one
# the live root of the runs (pull requests, master) beside the uploads root, set when building
const LIVE = Ref("")
run_root(live, run) = (r = [x for x in [UPLOADS[], LIVE[], live] if !isempty(x) && isdir(joinpath(x, run))]; isempty(r) ? nothing : first(r))
# a report changes with its spec and with every run in it
function report_mtime(live, name)
    spec = report_spec(live, name)
    t = mtime(joinpath(live, name, "report.toml"))
    spec === nothing && return t
    for run in spec.runs
        root = run_root(live, run)
        root === nothing || (t = max(t, mtime(joinpath(root, run, "run.json"))))
    end
    return t
end

function build_report(live, site, name)
    changed = report_mtime(live, name)
    spec = report_spec(live, name)
    spec === nothing && error("$name: bad report.toml")
    first_root = run_root(live, spec.runs[1])
    first_root === nothing && error("$name: no run $(spec.runs[1])")
    states = [json_field(read(joinpath(r, run, "run.json"), String), "state") for run in spec.runs for r in (run_root(live, run),) if r !== nothing]
    base = with_comparisons(nothing, live, name)
    COLUMN_REPORTS[spec.labels[1]] = spec.runs[1]   # the first column links to its run too
    write_report(
        joinpath(site, name), joinpath(first_root, spec.runs[1]);
        base, reference = nothing, wall = 7200.0,
        title = "Report $(name[8:end]): $(spec.title)", own_label = spec.labels[1],
        finished = all(!=("running"), states), assets = "..", live = true, tiles = false,
    )
    REPORTS[name] = (; name, title = spec.title, runs = spec.runs, labels = spec.labels, mtime = changed,
                     updated = Dates.format(now(UTC), "yyyy-mm-ddTHH:MM:SSZ"), state = any(==("running"), states) ? "running" : "finished")
    return
end

# The reports that show `run`, rebuilt when it has been.
function build_reports_of(live, site, run)
    (isempty(UPLOADS[]) || !isdir(UPLOADS[])) && return
    for name in readdir(UPLOADS[])
        is_report(name) || continue
        spec = report_spec(UPLOADS[], name)
        (spec !== nothing && run in spec.runs && stale(UPLOADS[], name)) || continue
        try
            build_report(UPLOADS[], site, name)
        catch err
            @warn "$name: the report was not updated" exception = err
        end
    end
    return
end

function build_run(live, site, name)
    is_report(name) && return build_report(live, site, name)
    path = joinpath(live, name, "run.json")
    changed, text = mtime(path), read(path, String)
    repository, pr = json_field(text, "repository"), json_field(text, "pr")
    manual = startswith(name, "run-") || startswith(name, "master-")
    (occursin(r"^[\w.-]+/[\w.-]+$", repository) && (manual || occursin(r"^\d+$", pr))) || error("$name: bad run.json")
    state, commit = json_field(text, "state"), json_field(text, "commit")
    named = json_field(text, "title")
    at = match(r"^pr-\d+-([0-9a-f]{8})$", name)
    title = manual ? (isempty(named) ? name[5:end] : named) : "Pull request #$pr" * (at === nothing ? "" : " at $(at[1])")
    link = manual ? (occursin(r"^[0-9a-f]{40}$", commit) ? "https://github.com/$repository/commit/$commit" : nothing) : "https://github.com/$repository/pull/$pr"
    # a run's page shows the run alone (Sungho): comparing runs is what reports are for
    counts = write_report(
        joinpath(site, name), joinpath(live, name);
        base = nothing, reference = nothing, wall = 7200.0,
        # a job over a subset of the instances is compared over that subset
        only = (m = match(r"\"only\"\s*:\s*\[([^\]]*)\]", text); m === nothing ? nothing : Set(x[1] for x in eachmatch(r"\"([A-Za-z0-9_.+-]+)\"", m[1]))),
        title, link,
        finished = state != "running", assets = "..", live = true,
    )
    updated = Dates.format(now(UTC), "yyyy-mm-ddTHH:MM:SSZ")
    RUNS[name] = (; name, pr = manual ? "" : pr, title, commit, state, updated, mtime = changed, counts.done, counts.total, counts.devices)
    build_reports_of(live, site, name)
    return
end

# Runs to show beside a report, from comparisons.toml in this repository: `["<folder>"] with = [...]`.
const COMPARISONS = Ref(joinpath(dirname(@__DIR__), "comparisons.toml"))

host_of(rs) = (m = match(r"shin-compute-(\d+)", join(Iterators.flatten(values(rs.machines)), " ")); m === nothing ? "" : m[1])

# the comparisons people choose in the app, in the uploads root, which the app writes; they come on
# top of those of comparisons.toml in this repository
app_comparisons() = isempty(UPLOADS[]) ? "" : joinpath(UPLOADS[], "comparisons.toml")

# The app's comparisons as last read, to tell which reports a change of them concerns.
const APP_CHOSEN = Ref(Dict{String, Any}())
function read_app_comparisons()
    file = app_comparisons()
    isfile(file) || return Dict{String, Any}()
    try
        return TOML.parsefile(file)
    catch err
        @warn "$file: not read" exception = err
        return Dict{String, Any}()
    end
end

function comparisons_of(name)
    entry = Dict{String, Any}()
    # a report: every run after the first is a column of it
    if is_report(name) && !isempty(UPLOADS[])
        spec = report_spec(UPLOADS[], name)
        spec === nothing && return entry
        return Dict{String, Any}("with" => spec.runs[2:end], "names" => spec.labels[2:end])
    end
    for file in (COMPARISONS[], app_comparisons())
        isfile(file) || continue
        try
            haskey(TOML.parsefile(file), name) && (entry = TOML.parsefile(file)[name])
        catch err
            @warn "$file: not read" exception = err
        end
    end
    return entry
end

# The report of each column that is another run, by the column's label, for links.
const COLUMN_REPORTS = Dict{String, String}()

function with_comparisons(base, live, name)
    empty!(COLUMN_REPORTS)
    entry = comparisons_of(name)
    others, names = get(entry, "with", String[]), get(entry, "names", String[])
    isempty(others) && return base
    # a report's own columns are its first run's
    own_dir = is_report(name) ? (spec = report_spec(live, name); r = spec === nothing ? nothing : run_root(live, spec.runs[1]); r === nothing ? joinpath(live, name) : joinpath(r, spec.runs[1])) : joinpath(live, name)
    own = from_directory(own_dir, "")
    for (i, other) in enumerate(others)
        is_folder(other) || continue
        roots = filter(r -> isdir(joinpath(r, other)), filter(!isempty, [live, LIVE[], UPLOADS[]]))
        isempty(roots) && continue
        root = first(roots)
        rs = from_directory(joinpath(root, other), other)
        text = isfile(joinpath(root, other, "run.json")) ? read(joinpath(root, other, "run.json"), String) : ""
        title = json_field(text, "title")
        pr = json_field(text, "pr")
        label = i <= length(names) ? names[i] : isempty(title) ? (isempty(pr) ? other : "#$pr " * other) : title
        host = host_of(rs)
        # the same machine only if its hardware is the same as this run's
        same = host == host_of(own) && Set(Iterators.flatten(values(rs.machines))) == Set(Iterators.flatten(values(own.machines)))
        label = label * (isempty(host) ? "" : " ($host)") * (same ? "" : " †")
        COLUMN_REPORTS[label] = other          # the column's name links to its report
        base === nothing && (base = ResultSet(""))
        for ((device, set, config), rows) in rs.rows
            base.rows[(device, set, "cmp:$label:$config")] = rows
            haskey(rs.revs, (device, config)) && (base.revs[(device, "cmp:$label:$config")] = rs.revs[(device, config)])
            haskey(rs.harness, (device, config)) && (base.harness[(device, "cmp:$label:$config")] = rs.harness[(device, config)])
            haskey(rs.options, (device, config)) && (base.options[(device, "cmp:$label:$config")] = rs.options[(device, config)])
        end
    end
    return base
end

# The list of benchmarks, and `status.json` for the check that watches running benchmarks.
function publish(site; repository = DEFAULT_REPOSITORY)
    write_index(site, collect(values(RUNS)); repository, reports = collect(values(REPORTS)))
    entries = ["\"$(r.name)\":{\"state\":\"$(r.state)\",\"updated\":\"$(r.updated)\",\"done\":$(get(r, :done, 0)),\"total\":$(get(r, :total, 0))}" for r in sort!(collect(values(RUNS)); by = r -> r.name)]
    write_atomic(joinpath(site, "status.json"), "{", join(entries, ","), "}\n")
    return
end

# Build everything once, and report in `version.json` that this code has done so: a deployment
# waits for it. A folder that cannot be built must not stop the site, but code that can build
# no folder at all is broken, and then `version.json` keeps the previous commit.
# The newest Ipopt reference's instances (see `write_instances`), for subsets chosen against it:
# `ipopt-instances.json`, written again only when the archive moves, as its logs come from git.
function write_reference_instances(site)
    archive = archive_revision()
    archive === nothing && return
    rev = readchomp(archive_command("rev-parse", archive))
    stamp = joinpath(site, "ipopt-instances.rev")
    isfile(stamp) && readchomp(stamp) == rev && return
    versions = sort!(unique!([String(m[1]) for m in (match(r"^ipopt/([^/]+)/", p) for p in split(archive_git("ls-tree", "-r", "--name-only", archive, "ipopt/"), '\n'; keepempty = false)) if m !== nothing && tryparse(VersionNumber, m[1]) !== nothing]); by = VersionNumber)
    isempty(versions) && return
    reference = ipopt_reference(last(versions))
    reference === nothing && return
    mktempdir() do tmp
        function log_of(key)
            path = get(reference.logs, key, "")
            isempty(path) && return ""
            file = joinpath(tmp, replace(path, "/" => "_"))
            write(file, read(archive_command("show", "$archive:$path")))
            return file
        end
        write_instances(joinpath(site, "ipopt-instances.json"), reference.rows, log_of)
    end
    write_atomic(stamp, rev, "\n")
    return
end

function build_all(live, site; repository = DEFAULT_REPOSITORY)
    LIVE[] = live
    names = [(root, name) for (root, name) in folders(live) if is_run(root, name)]
    for (root, name) in names
        try
            build_run(root, site, name)
        catch err
            @warn "$name: the report was not updated" exception = err
        end
    end
    isempty(names) || !isempty(RUNS) || error("no report could be built")
    publish(site; repository)
    write_atomic(joinpath(site, "version.json"), "{\"commit\":\"", code_commit(), "\",\"complete\":true}\n")
    return
end

# The archived results of every commit, as pages of the same site. A page is rebuilt when the
# code or the archived files have changed since it was built, as recorded in its `.built`.
function build_archive(site)
    tree = split(archive_git("ls-tree", "-r", "origin/$ARCHIVE_BRANCH"), '\n'; keepempty = false)
    entries = [m for m in (match(r"^\S+ blob (\S+)\t([^/]+)/([0-9a-f]{40})/([^/]+\.(?:csv|toml))$", line) for line in tree) if m !== nothing]
    code = code_commit()
    for commit in unique!([String(m[3]) for m in entries])
        mine = [m for m in entries if m[3] == commit]
        target = joinpath(site, "archive", commit)
        built = code * "\n" * join([m[1] * " " * m[2] * "/" * m[4] for m in mine], "\n") * "\n"
        record = joinpath(target, ".built")
        isfile(record) && read(record, String) == built && continue
        mktempdir() do root
            for m in mine
                write(joinpath(mkpath(joinpath(root, m[2])), m[4]), read(archive_command("show", "origin/$ARCHIVE_BRANCH:$(m[2])/$commit/$(m[4])")))
            end
            write_report(target, root; reference = ipopt_reference(benchmark_version(root)), title = "Commit " * first(commit, 8), link = "https://github.com/$DEFAULT_REPOSITORY/commit/$commit", finished = true, assets = "../..")
        end
        write_atomic(record, built)
    end
    return
end

const REBUILD_EVERY = 5.0

function watch_run(live, site, name; repository = DEFAULT_REPOSITORY)
    while true
        try
            if is_run(live, name) && stale(live, name)
                build_run(live, site, name)
                publish(site; repository)
            end
        catch err
            @warn "$name: the report was not updated" exception = err
        end
        # the copy is complete when run.json has been replaced
        try
            built = time()
            while first(watch_folder(joinpath(live, name))) ∉ ("run.json", "report.toml")
            end
            # at most one rebuild every REBUILD_EVERY seconds: a job of many nodes replaces run.json
            # with each of their uploads, and what arrives meanwhile goes into the next rebuild
            sleep(max(0.0, REBUILD_EVERY - (time() - built)))
            while !isempty(first(watch_folder(joinpath(live, name), 0.05)))
            end
        catch err
            # a folder nobody watches would go stale with the service still up: restart instead
            @error "$name: cannot watch the folder" exception = err
            exit(1)
        end
    end
    return
end

"""
    serve(live, site; repository)

Build every report, then rebuild a report whenever its `run.json` is replaced. Does not return.
"""
function serve(live, site; repository = DEFAULT_REPOSITORY)
    FETCH[] = false
    LIVE[] = live
    mkpath(site)
    APP_CHOSEN[] = read_app_comparisons()
    build_all(live, site; repository)
    watching = Set{String}()
    function watch_new()
        for (root, name) in folders(live)
            if isdir(joinpath(root, name)) && name ∉ watching
                push!(watching, name)
                errormonitor(@async watch_run(root, site, name; repository))
            end
        end
        return
    end
    watch_new()
    # off the way of the reports: it reads every log of the reference from git, once per archive
    errormonitor(@async try
        write_reference_instances(site)
    catch err
        @warn "the Ipopt reference's instances were not written" exception = err
    end)
    try
        build_archive(site)
    catch err
        @warn "the archived results were not built" exception = err
    end
    isempty(UPLOADS[]) || errormonitor(@async while true
        file, _ = watch_folder(UPLOADS[])
        if file == "comparisons.toml"
            # chosen in the app: rebuild the reports whose columns changed, at once
            now_chosen = read_app_comparisons()
            changed = Set(n for n in union(keys(now_chosen), keys(APP_CHOSEN[])) if get(now_chosen, n, nothing) != get(APP_CHOSEN[], n, nothing))
            APP_CHOSEN[] = now_chosen
            for (root, name) in folders(live)
                name in changed || continue
                try
                    is_run(root, name) && build_run(root, site, name)
                catch err
                    @warn "$name: the report was not updated" exception = err
                end
            end
            publish(site; repository)
        end
        watch_new()
    end)
    while true
        watch_folder(live)
        watch_new()
    end
    return
end
