# The service behind the website. It keeps the pages of `live` up to date in `site`: a folder
# `pr-<N>` per pull request or `run-<name>` per manual run, whose `run.json` is replaced after every copy of results. All of
# `live` is untrusted. The archive is read from `REPOSITORY[]` and never fetched here.

const DEFAULT_REPOSITORY = "madsuite-org/MadNLP.jl"
const RUNS = Dict{String, NamedTuple}()

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

is_folder(name) = occursin(r"^(pr-\d+|run-[a-z0-9-]+|master-[0-9a-f]{8})$", name)
is_run(live, name) = is_folder(name) && isfile(joinpath(live, name, "run.json"))
stale(live, name) = !haskey(RUNS, name) || RUNS[name].mtime != mtime(joinpath(live, name, "run.json"))

function build_run(live, site, name)
    path = joinpath(live, name, "run.json")
    changed, text = mtime(path), read(path, String)
    repository, pr = json_field(text, "repository"), json_field(text, "pr")
    manual = startswith(name, "run-") || startswith(name, "master-")
    (occursin(r"^[\w.-]+/[\w.-]+$", repository) && (manual || occursin(r"^\d+$", pr))) || error("$name: bad run.json")
    state, commit = json_field(text, "state"), json_field(text, "commit")
    named = json_field(text, "title")
    title = manual ? "Manual run: " * (isempty(named) ? name[5:end] : named) : "Pull request #$pr"
    link = manual ? (occursin(r"^[0-9a-f]{40}$", commit) ? "https://github.com/$repository/commit/$commit" : nothing) : "https://github.com/$repository/pull/$pr"
    write_report(
        joinpath(site, name), joinpath(live, name);
        base = latest_merged(live, name),
        reference = ipopt_reference(benchmark_version(joinpath(live, name))), wall = 7200.0,
        title, link,
        finished = state != "running", assets = "..", live = true,
    )
    updated = Dates.format(now(UTC), "yyyy-mm-ddTHH:MM:SSZ")
    RUNS[name] = (; name, pr = manual ? "" : pr, title, commit, state, updated, mtime = changed)
    return
end

# The list of benchmarks, and `status.json` for the check that watches running benchmarks.
function publish(site; repository = DEFAULT_REPOSITORY)
    write_index(site, collect(values(RUNS)); repository)
    entries = ["\"$(r.name)\":{\"state\":\"$(r.state)\",\"updated\":\"$(r.updated)\"}" for r in sort!(collect(values(RUNS)); by = r -> r.name)]
    write_atomic(joinpath(site, "status.json"), "{", join(entries, ","), "}\n")
    return
end

# Build everything once, and report in `version.json` that this code has done so: a deployment
# waits for it. A folder that cannot be built must not stop the site, but code that can build
# no folder at all is broken, and then `version.json` keeps the previous commit.
function build_all(live, site; repository = DEFAULT_REPOSITORY)
    names = filter(name -> is_run(live, name), readdir(live))
    for name in names
        try
            build_run(live, site, name)
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
    tree = split(git("ls-tree", "-r", "origin/$ARCHIVE_BRANCH"), '\n'; keepempty = false)
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
                write(joinpath(mkpath(joinpath(root, m[2])), m[4]), read(git_command("show", "origin/$ARCHIVE_BRANCH:$(m[2])/$commit/$(m[4])")))
            end
            write_report(target, root; reference = ipopt_reference(benchmark_version(root)), title = "Commit " * first(commit, 8), link = "https://github.com/$DEFAULT_REPOSITORY/commit/$commit", finished = true, assets = "../..")
        end
        write_atomic(record, built)
    end
    return
end

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
            while first(watch_folder(joinpath(live, name))) != "run.json"
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
    mkpath(site)
    build_all(live, site; repository)
    watching = Set{String}()
    function watch_new()
        for name in readdir(live)
            if is_folder(name) && isdir(joinpath(live, name)) && name ∉ watching
                push!(watching, name)
                errormonitor(@async watch_run(live, site, name; repository))
            end
        end
        return
    end
    watch_new()
    try
        build_archive(site)
    catch err
        @warn "the archived results were not built" exception = err
    end
    while true
        watch_folder(live)
        watch_new()
    end
    return
end
