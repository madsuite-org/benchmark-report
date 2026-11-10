# The report is public and its input comes from benchmark jobs: nothing in a result folder may
# reach a page as markup, and only result files may be published.
#
#     julia --project test/runtests.jl

using BenchmarkReport, Test

function hostile_results(root)
    dir = mkpath(joinpath(root, "machine"))
    columns = [
        "case", "status", "iters", "nvar", "ncon", "nnzj", "nnzh", "obj", "primal_feas", "dual_feas", "n_fact",
        "n_backsolve", "n_ir", "n_obj", "n_grad", "n_con", "n_jac", "n_hess", "t_total", "t_linsolve", "t_eval", "t_init",
        "t_compile", "n_solves", "viol_eq", "viol_con", "viol_var",
    ]
    row = fill("1", length(columns))
    row[1] = "<script>alert(1)</script>"
    row[3] = "<img src=x onerror=alert(2)>"
    write(joinpath(dir, "smoke_default.csv"), join(columns, ",") * "\n" * join(row, ",") * "\n")
    write(
        joinpath(dir, "smoke_default.csv.meta.toml"), """
        device = "cuda"
        host = "<script>alert(3)</script>"
        cpu_model = "x"
        cpu_threads = 1
        memory_gb = 1.0
        gpu_name = "x"
        gpu_memory_gb = 1.0
        gpu_driver = "x"
        cuda_runtime = "x"
        madnlp_commit = "<script>alert(4)</script>"
        """
    )
    write(joinpath(dir, "summary.md"), "```\nstatus <script>\n  worker 2: <b>x <img> for 3 s, 1.0 GB\n```\n")
    write(joinpath(dir, "evil.html"), "<script>alert(5)</script>")
    symlink("/etc/passwd", joinpath(dir, "secret.txt"))
    return
end

markup(page) = occursin(r"<(img|b>x|iframe|script>)", read(page, String))

mktempdir() do root
    results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
    hostile_results(results)
    write_report(site, results; wall = 60.0, title = "<i>title</i>", finished = false)
    pages = filter(endswith(".html"), readdir(site; join = true))
    @test length(pages) >= 3
    @test !any(markup, pages)
    published = joinpath(site, "data", "machine")
    @test isfile(joinpath(published, "smoke_default.csv"))
    @test !ispath(joinpath(published, "evil.html"))
    @test !ispath(joinpath(published, "secret.txt"))
    @test isfile(joinpath(site, "status.json"))
    # the server lists no directories: every link to raw data needs a page
    listing = read(joinpath(site, "data", "index.html"), String)
    @test occursin("href=\"machine/\"", listing)
    machine = read(joinpath(published, "index.html"), String)
    @test occursin("href=\"smoke_default.csv\"", machine)
    @test !occursin("href=\"index.html\">index.html", machine)
    @test !markup(joinpath(published, "index.html"))
    @test occursin("href=\"../../bootstrap.min.css\"", machine)
    # both themes come from the stylesheet: a colour written into a page would ignore them
    summary = read(joinpath(site, "index.html"), String)
    @test occursin("src=\"./theme.js\"", summary)
    @test isfile(joinpath(site, "theme.js"))
    @test !occursin(r"(stroke|fill|background)[=:]\\?\"?#", summary)
end

@testset "a set shows its problems out of the whole set" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        other = mkpath(joinpath(results, "other"))
        table = readlines(joinpath(results, "machine", "smoke_default.csv"))
        write(joinpath(other, "smoke_default.csv"), join([table; replace(table[2], r"^[^,]*" => "second")], "\n") * "\n")
        meta = read(joinpath(results, "machine", "smoke_default.csv.meta.toml"), String)
        write(joinpath(other, "smoke_default.csv.meta.toml"), replace(meta, "device = \"cuda\"" => "device = \"cpu\""))
        write_report(site, results; wall = 60.0, finished = true)
        index = read(joinpath(site, "index.html"), String)
        # one row for the set, over the instance both solvers (GPU and host) have
        @test occursin("<td>1/2</td>", index)
        @test !occursin("<td>2/2</td>", index)
    end
end

@testset "a solve past its run's limit counts as stopped there" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        lines = readlines(joinpath(machine, "smoke_default.csv"))
        row = split(lines[2], ",")
        row[findfirst(==("t_total"), split(lines[1], ","))] = "900"
        write(joinpath(machine, "smoke_default.csv"), lines[1] * "\n" * join(row, ",") * "\n")
        other = mkpath(joinpath(results, "other"))
        cp(joinpath(machine, "smoke_default.csv"), joinpath(other, "smoke_default.csv"))
        meta = read(joinpath(machine, "smoke_default.csv.meta.toml"), String)
        write(joinpath(other, "smoke_default.csv.meta.toml"), replace(meta, "device = \"cuda\"" => "device = \"cpu\""))
        # a GPU run from when a GPU solve had 600 s
        write(joinpath(machine, "smoke_default.csv.meta.toml"), meta * "wall_limit_s = 600.0\n")
        write_report(site, results; wall = 7200.0, finished = true)
        # one page with both: the GPU solve past its 600 s failed, the same time on the host did not
        page = read(joinpath(site, "smoke.html"), String)
        @test count("<td class=\"l fail\"", page) == 1
        @test occursin("current (GPU)", page) && occursin("current (CPU)", page)
        @test occursin("url=smoke.html", read(joinpath(site, "cuda-smoke.html"), String))   # the old address
        # the result files are left as they were
        @test occursin(",900,", read(joinpath(site, "data", "machine", "smoke_default.csv"), String))
        # the benchmark's limit, 1800 s on a GPU and on the host, and a run's lower one
        @test BenchmarkReport.device_walls(results, 7200.0)["cuda"] == 600.0
        @test BenchmarkReport.device_walls(results, 7200.0)["cpu"] == 1800.0
        write(joinpath(machine, "smoke_default.csv.meta.toml"), meta * "wall_limit_s = 7200.0\n")
        @test BenchmarkReport.device_walls(results, 7200.0)["cuda"] == 1800.0
        write(joinpath(machine, "smoke_default.csv.meta.toml"), meta)
        @test BenchmarkReport.device_walls(results, 7200.0)["cuda"] == 1800.0
    end
end

@testset "a run taken up by another machine shows that machine's results" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        # a second machine of the same device, its solve past the GPU limit
        later = mkpath(joinpath(results, "a-later-machine"))   # sorts first
        lines = readlines(joinpath(machine, "smoke_default.csv"))
        row = split(lines[2], ",")
        row[findfirst(==("t_total"), split(lines[1], ","))] = "2000"
        write(joinpath(later, "smoke_default.csv"), lines[1] * "\n" * join(row, ",") * "\n")
        cp(joinpath(machine, "smoke_default.csv.meta.toml"), joinpath(later, "smoke_default.csv.meta.toml"))
        mkpath(joinpath(later, "logs", "smoke"))
        write(joinpath(later, "logs", "smoke", "case14_ieee_polar__default.txt"), "the later log")
        t = time()
        stamp(path, at) = run(`touch -d @$(round(Int, at)) $path`)
        stamp(joinpath(machine, "smoke_default.csv"), t - 60)
        stamp(joinpath(later, "smoke_default.csv"), t)
        write_report(site, results; wall = 7200.0, finished = true)
        @test occursin("<td class=\"l fail\"", read(joinpath(site, "smoke.html"), String))
        @test BenchmarkReport.current_machines(results)["cuda"] == normpath(later)
        # and the first machine's, once it is the one written to last
        stamp(joinpath(machine, "smoke_default.csv"), t + 60)
        write_report(site, results; wall = 7200.0, finished = true)
        @test !occursin("<td class=\"l fail\"", read(joinpath(site, "smoke.html"), String))
    end
end

@testset "a job run on several kinds of node merges their rows and marks where each ran" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "run-x7-master")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        other = mkpath(joinpath(results, "other-gpu"))
        lines = readlines(joinpath(machine, "smoke_default.csv"))
        row = split(lines[2], ",")
        row[1] = "another_case"
        write(joinpath(other, "smoke_default.csv"), lines[1] * "\n" * join(row, ",") * "\n")
        cp(joinpath(machine, "smoke_default.csv.meta.toml"), joinpath(other, "smoke_default.csv.meta.toml"))
        # each machine's solver logs
        for (m, case) in (("other-gpu", "another_case"),)
            mkpath(joinpath(results, m, "logs", "smoke"))
            write(joinpath(results, m, "logs", "smoke", "$(case)__default.txt"), "log of $case")
        end
        rs = BenchmarkReport.from_directory(results, "job")
        rows = rs.rows[("cuda", "smoke", "default")]
        @test length(rows) == 2
        @test Set(r.machine for r in values(rows)) == Set(["machine", "other-gpu"])
        write_report(site, results; wall = 7200.0, finished = true)
        @test occursin("<sup>m</sup>", read(joinpath(site, "smoke.html"), String))
        @test occursin("data/other-gpu/logs/smoke/another_case__default.txt", read(joinpath(site, "smoke.html"), String))   # every machine's logs
        # each instance for choosing subsets: the log of this one shows the restoration phase
        write(joinpath(results, "other-gpu", "logs", "smoke", "another_case__default.txt"), "iter objective\n   0 1.0\n  12r 2.0\n")
        write_report(site, results; wall = 7200.0, finished = true)
        instances = read(joinpath(site, "instances.json"), String)
        @test occursin(r"\"cuda/smoke/another_case/default\":\{\"class\":\"(small|medium|large)\",\"constrained\":(true|false),\"solved\":(true|false),\"iters\":(\d+|null),\"resto\":true\}", instances)
        # one kind of node: nothing to mark
        rm(other; recursive = true)
        write_report(site, results; wall = 7200.0, finished = true)
        @test !occursin("<sup>m</sup>", read(joinpath(site, "smoke.html"), String))
    end
end

@testset "a run that stopped writing long ago is listed as stalled" begin
    @test BenchmarkReport.shown_state((; state = "running", mtime = time() - 7200)) == "stalled"
    @test BenchmarkReport.shown_state((; state = "running", mtime = time() - 60)) == "running"
    @test BenchmarkReport.shown_state((; state = "finished", mtime = time() - 7200)) == "finished"
end

@testset "a run of Ipopt shows its own results, beside the previous Ipopt run" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        meta = read(joinpath(machine, "smoke_default.csv.meta.toml"), String)
        mv(joinpath(machine, "smoke_default.csv"), joinpath(machine, "smoke_ipopt.csv"))
        rm(joinpath(machine, "smoke_default.csv.meta.toml"))
        write(joinpath(machine, "smoke_ipopt.csv.meta.toml"), replace(meta, "device = \"cuda\"" => "device = \"cpu\""))
        # an archived reference of the same set, which the run's own results come before
        reference = BenchmarkReport.ResultSet("Ipopt reference")
        BenchmarkReport.add!(reference, "smoke", "ipopt", BenchmarkReport.read_meta(joinpath(machine, "smoke_ipopt.csv")), [(; BenchmarkReport.read_results(joinpath(machine, "smoke_ipopt.csv"))[1]..., iters = 99999)])
        write_report(site, results; wall = 7200.0, finished = true, reference)
        page = read(joinpath(site, "smoke.html"), String)
        @test occursin("Ipopt reference (CPU)", page)     # the previous run, a column of its own
        @test occursin("99999", page)                     # with its own results
        @test occursin("Ipopt (CPU)", page)               # and this run's beside it
        @test count("99999", page) == 1                   # its results only in the reference's column
    end
end

@testset "a job over a subset is compared over the subset" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        case = first(BenchmarkReport.read_results(joinpath(machine, "smoke_default.csv"))).case
        reference = BenchmarkReport.ResultSet("Ipopt reference")
        rows = BenchmarkReport.read_results(joinpath(machine, "smoke_default.csv"))
        more = [rows; [merge(rows[1], (; case = "elsewhere_$i")) for i in 1:5]]
        meta = BenchmarkReport.read_meta(joinpath(machine, "smoke_default.csv"))
        BenchmarkReport.add!(reference, "smoke", "ipopt", merge(meta, Dict("device" => "cpu")), more)
        counts = write_report(site, results; wall = 7200.0, finished = false, reference, only = Set([string(case)]))
        @test counts.total == 1        # the subset's one instance, not the reference's six
    end
end

@testset "instances that could not be run are flagged, not hidden among failures" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        lines = readlines(joinpath(machine, "smoke_default.csv"))
        row = split(lines[2], ",")
        row[1] = "not_downloaded"
        row[findfirst(==("status"), split(lines[1], ","))] = "-101"
        write(joinpath(machine, "smoke_default.csv"), join([lines; join(row, ",")], "\n") * "\n")
        write_report(site, results; wall = 7200.0, finished = true)
        summary = read(joinpath(site, "index.html"), String)
        @test occursin("1 instance(s) could not be run", summary) && occursin("smoke: not_downloaded", summary)
        page = read(joinpath(site, "smoke.html"), String)
        @test occursin("could not be run", page) && occursin("not run (error)", page)
        @test occursin(">e<", page)
    end
end

@testset "a solve that threw (-102) is a solver failure, not an instance that could not be run" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        lines = readlines(joinpath(machine, "smoke_default.csv"))
        row = split(lines[2], ",")
        row[1] = "solver_threw"
        row[findfirst(==("status"), split(lines[1], ","))] = "-102"
        write(joinpath(machine, "smoke_default.csv"), join([lines; join(row, ",")], "\n") * "\n")
        write_report(site, results; wall = 7200.0, finished = true)
        summary = read(joinpath(site, "index.html"), String)
        page = read(joinpath(site, "smoke.html"), String)
        @test occursin("solver_threw", page)           # the row is there
        @test !occursin("could not be run", summary) && !occursin("could not be run", page)
        @test occursin("solver error", page)
    end
end

@testset "a run with no results yet shows no reference" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "empty")), joinpath(root, "site")
        other = mkpath(joinpath(root, "other"))
        hostile_results(other)
        machine = joinpath(other, "machine")
        reference = BenchmarkReport.ResultSet("Ipopt reference")
        BenchmarkReport.add!(reference, "smoke", "ipopt", BenchmarkReport.read_meta(joinpath(machine, "smoke_default.csv")), BenchmarkReport.read_results(joinpath(machine, "smoke_default.csv")))
        write_report(site, results; wall = 7200.0, finished = false, reference)
        @test !occursin("Ipopt</span>", read(joinpath(site, "index.html"), String))   # no Ipopt column
    end
end

@testset "a set's row counts only what every column has (Sungho)" begin
    mktempdir() do root
        results, site = mkpath(joinpath(root, "results")), joinpath(root, "site")
        hostile_results(results)
        machine = joinpath(results, "machine")
        other = mkpath(joinpath(results, "other"))
        cp(joinpath(machine, "smoke_default.csv"), joinpath(other, "second_default.csv"))
        meta = read(joinpath(machine, "smoke_default.csv.meta.toml"), String)
        write(joinpath(other, "second_default.csv.meta.toml"), replace(meta, "device = \"cuda\"" => "device = \"cpu\""))
        reference = BenchmarkReport.ResultSet("Ipopt reference")
        path = joinpath(other, "second_default.csv")
        BenchmarkReport.add!(reference, "second", "ipopt", BenchmarkReport.read_meta(path), BenchmarkReport.read_results(path))
        write_report(site, results; wall = 60.0, finished = true, reference)
        index = read(joinpath(site, "index.html"), String)
        @test !occursin("<td>1/1</td>", index)             # second has no GPU result, smoke no CPU one:
        @test count("<td>0/1</td>", index) == 2            # no instance is in every column
        @test occursin("Ipopt (CPU)", index)
    end
end

# A clone with an archive branch, as the service gets it.
function archive_clone(root)
    origin, clone = joinpath(root, "origin"), joinpath(root, "clone")
    run(`git init -q -b benchmark-results $origin`)
    git(args...) = run(`git -C $origin -c user.name=x -c user.email=x@x $args`)
    commit = "a"^40
    results = mkpath(joinpath(origin, "machine", commit))
    hostile_results(root)
    for file in ("smoke_default.csv", "smoke_default.csv.meta.toml", "summary.md")
        cp(joinpath(root, "machine", file), joinpath(results, file))
    end
    git("add", "-A")
    git("commit", "-qm", "results")
    run(`git clone -q $origin $clone`)
    return origin, clone, commit
end

function live_run(live, name, state)
    dir = joinpath(live, name)
    isdir(dir) || hostile_results(mkpath(dir))
    write(joinpath(dir, "run.json.tmp"), """{"repository":"o/r","pr":"$(name[4:end])","commit":"$("b"^40)","state":"$state"}""")
    return mv(joinpath(dir, "run.json.tmp"), joinpath(dir, "run.json"); force = true)
end

reset_runs() = (empty!(BenchmarkReport.RUNS); empty!(BenchmarkReport.REPORTS))

@testset "first build after a start" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        live_run(live, "pr-1", "running")
        live_run(live, "pr-2", "finished")
        reset_runs()
        BenchmarkReport.build_all(live, site)
        status = read(joinpath(site, "status.json"), String)
        @test occursin("pr-1", status) && occursin("pr-2", status)
        index = read(joinpath(site, "index.html"), String)
        @test occursin("pr-1/", index) && occursin("pr-2/", index)
        version = read(joinpath(site, "version.json"), String)
        @test occursin("\"commit\":\"$(BenchmarkReport.code_commit())\"", version)
        @test occursin("\"complete\":true", version)
        @test isfile(joinpath(site, "bootstrap.min.css"))
    end
end

@testset "a run with no archived ancestor is compared with the latest merged pull request" begin
    mktempdir() do root
        origin, clone, _ = archive_clone(root)
        git(args...) = run(`git -C $origin -c user.name=x -c user.email=x@x $args`)
        # #7 is merged, and was benchmarked at an earlier head that master does not contain
        head = "d"^40
        git("commit", "-q", "--allow-empty", "-m", "Older change (#6)")
        git("commit", "-q", "--allow-empty", "-m", "A change (#7)")
        older = readchomp(`git -C $origin rev-parse "HEAD~1"`)
        git("branch", "master")
        # #6 has a set that #7 has not, and an older result of the set they share
        to = mkpath(joinpath(origin, "machine", older))
        for (from, name) in (("smoke_default.csv", "smoke_default.csv"), ("smoke_default.csv.meta.toml", "smoke_default.csv.meta.toml"), ("smoke_default.csv", "other_default.csv"), ("smoke_default.csv.meta.toml", "other_default.csv.meta.toml"))
            cp(joinpath(root, "machine", from), joinpath(to, name))
        end
        for (machine, commit) in (("machine", head), ("elsewhere", "c"^40))
            to = mkpath(joinpath(origin, machine, commit))
            for file in ("smoke_default.csv", "smoke_default.csv.meta.toml")
                cp(joinpath(root, "machine", file), joinpath(to, file))
            end
        end
        git("add", "-A")
        git("commit", "-q", "-m", "results")
        run(`git -C $clone fetch -q origin`)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        live_run(live, "pr-1", "running")
        # #7 has not been benchmarked yet: its results are unknown, and #6 stands in
        @test !occursin("#7", BenchmarkReport.latest_merged(live, "pr-1").label)
        live_run(live, "pr-7", "finished")
        run_json = joinpath(live, "pr-7", "run.json")
        write(run_json, replace(read(run_json, String), "b"^40 => head))
        rs = BenchmarkReport.latest_merged(live, "pr-1")
        @test rs !== nothing && occursin("`dddddddd`, from #7, on machine (smoke)", rs.label)
        @test occursin("`$(first(older, 8))`, from #6, on machine (other)", rs.label)
        @test !occursin("elsewhere", rs.label)
        # a pull request is not its own master
        @test !occursin("#7", BenchmarkReport.latest_merged(live, "pr-7").label)
        # a cluster job (run-x*, uploaded) of a newer master commit is master's results for its device
        git("checkout", "-q", "master")
        git("commit", "-q", "--allow-empty", "-m", "Newest change (#8)")
        newest = readchomp(`git -C $origin rev-parse HEAD`)
        git("checkout", "-q", "benchmark-results")
        run(`git -C $clone fetch -q origin`)
        uploads = mkpath(joinpath(root, "uploads"))
        job = mkpath(joinpath(uploads, "run-x5-newest"))
        hostile_results(job)
        mv(joinpath(job, "machine"), joinpath(job, "cluster-gpu"))
        write(joinpath(job, "run.json"), """{"repository":"o/r","pr":"","commit":"$newest","state":"running"}""")
        BenchmarkReport.UPLOADS[] = uploads
        @test !occursin("run-x5-newest", BenchmarkReport.latest_merged(live, "pr-1").label)   # not until it has finished
        write(joinpath(job, "run.json"), """{"repository":"o/r","pr":"","commit":"$newest","state":"finished","only":["x"]}""")
        @test !occursin("run-x5-newest", BenchmarkReport.latest_merged(live, "pr-1").label)   # nor over a subset
        write(joinpath(job, "run.json"), """{"repository":"o/r","pr":"","commit":"$newest","state":"finished"}""")
        rs = BenchmarkReport.latest_merged(live, "pr-1")
        @test occursin("`$(first(newest, 8))` from run-x5-newest (cuda, cluster nodes)", rs.label)
        @test !occursin("from #7", rs.label)
        @test all(r -> r.machine == "cluster-gpu", values(rs.rows[("cuda", "smoke", "default")]))   # other hardware, marked
        @test !occursin("run-x5-newest", BenchmarkReport.latest_merged(uploads, "run-x5-newest").label)   # never its own
        BenchmarkReport.UPLOADS[] = ""
        rm(job; recursive = true)
        # a run on a machine master has no results for takes the latest master's from another, marked
        live_run(live, "run-x9-cluster", "running")
        mv(joinpath(live, "run-x9-cluster", "machine"), joinpath(live, "run-x9-cluster", "cluster"))
        rs = BenchmarkReport.latest_merged(live, "run-x9-cluster")
        @test rs !== nothing && occursin("another machine", rs.label)
        @test all(r -> hasproperty(r, :machine), Iterators.flatten(values(v) for v in values(rs.rows)))
        reset_runs()
        BenchmarkReport.build_all(live, site)
        report = read(joinpath(site, "pr-1", "index.html"), String)
        @test !occursin(">master (GPU)", report)            # a run's page shows the run alone; reports compare
        @test !occursin("<script>alert", report)
    end
end

@testset "a manual run" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        dir = mkpath(joinpath(live, "run-try-this"))
        hostile_results(dir)
        write(joinpath(dir, "run.json"), """{"repository":"o/r","title":"<b>x try","commit":"$("b"^40)","state":"finished"}""")
        write(joinpath(mkpath(joinpath(live, "other")), "run.json"), "{}")
        reset_runs()
        BenchmarkReport.build_all(live, site)
        index = read(joinpath(site, "index.html"), String)
        @test occursin("<h2>Runs</h2>", index) && occursin("href=\"run-try-this/\"", index)   # one list of every run
        @test !occursin("Manual run", index)
        @test occursin("<th class=\"l\">device</th>", index) && occursin("<span class=\"tag cuda\">CUDA</span>", index)   # the device, a column
        @test occursin("id=\"runsearch\"", index) && occursin("id=\"runpages\"", index) && occursin("class=\"trunc\"", index)   # search, pages, long names cut
        @test !occursin("<b>x", index)
        @test !occursin("Finished", index)
        report = read(joinpath(site, "run-try-this", "index.html"), String)
        @test occursin("&lt;b&gt;x try", report) && !occursin("Manual run", report)
        @test occursin("o/r/commit/" * "b"^40, report)
        @test !isdir(joinpath(site, "other"))
        # a run of master from the queue, under its commit
        dir = mkpath(joinpath(live, "master-bbbbbbbb"))
        hostile_results(dir)
        write(joinpath(dir, "run.json"), """{"repository":"o/r","pr":"","title":"master bbbbbbbb","commit":"$("b"^40)","state":"running"}""")
        reset_runs()
        BenchmarkReport.build_all(live, site)
        @test occursin("<title>master bbbbbbbb</title>", read(joinpath(site, "master-bbbbbbbb", "index.html"), String))
        @test occursin("run-try-this", read(joinpath(site, "status.json"), String))
    end
end

@testset "results archived in another repository" begin
    mktempdir() do root
        _, archive, commit = archive_clone(root)
        code = joinpath(root, "code")
        run(`git init -q -b master $code`)
        run(`git -C $code -c user.name=x -c user.email=x@x commit -q --allow-empty -m code`)
        BenchmarkReport.REPOSITORY[] = code
        BenchmarkReport.ARCHIVE[] = archive
        BenchmarkReport.ARCHIVE_URL[] = "https://example.org/results"
        BenchmarkReport.FETCH[] = false
        try
            site = mkpath(joinpath(root, "site"))
            BenchmarkReport.build_archive(site)
            @test isfile(joinpath(site, "archive", commit, "index.html"))
            reset_runs()
            BenchmarkReport.build_all(mkpath(joinpath(root, "live")), site)
            @test occursin("archive/$commit/", read(joinpath(site, "index.html"), String))
        finally
            BenchmarkReport.ARCHIVE[] = ""
            BenchmarkReport.ARCHIVE_URL[] = ""
        end
    end
end

@testset "Ipopt is shown even before a version has its reference" begin
    mktempdir() do root
        origin, clone, _ = archive_clone(root)
        to = mkpath(joinpath(origin, "ipopt", "0.1.0"))
        cp(joinpath(root, "machine", "smoke_default.csv"), joinpath(to, "smoke_ipopt.csv"))
        write(joinpath(to, "smoke_ipopt.csv.meta.toml"), replace(read(joinpath(root, "machine", "smoke_default.csv.meta.toml"), String), "device = \"cuda\"" => "device = \"cpu\"") * "")
        run(`git -C $origin add -A`)
        run(`git -C $origin -c user.name=x -c user.email=x@x commit -q -m ipopt`)
        run(`git -C $clone fetch -q origin`)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        @test BenchmarkReport.ipopt_reference("0.1.0") !== nothing
        @test only(values(BenchmarkReport.ipopt_reference("0.2.0").harness)) == "0.1.0"   # the newest earlier one, named
        @test BenchmarkReport.ipopt_reference("0.0.9") === nothing      # never a later one
    end
end

@testset "uploaded results, from a root of their own" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, uploads, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "uploads")), mkpath(joinpath(root, "site"))
        for (dir, name) in ((uploads, "run-up-try"), (uploads, "pr-9"), (live, "run-up-fake"))
            d = mkpath(joinpath(dir, name))
            hostile_results(d)
            write(joinpath(d, "run.json"), """{"repository":"o/r","pr":"","title":"try (uploaded by alice)","commit":"","state":"finished"}""")
        end
        BenchmarkReport.UPLOADS[] = uploads
        try
            reset_runs()
            BenchmarkReport.build_all(live, site)
            @test isfile(joinpath(site, "run-up-try", "index.html"))
            @test !isdir(joinpath(site, "pr-9"))          # only run-up-* from the uploads
            @test !isdir(joinpath(site, "run-up-fake"))   # and no run-up-* from the live root
            @test occursin("try (uploaded by alice)", read(joinpath(site, "index.html"), String))
        finally
            BenchmarkReport.UPLOADS[] = ""
        end
    end
end

@testset "a pull request's results per commit" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        for sha in ("d89f8eeb", "95f40cad")
            d = mkpath(joinpath(live, "pr-704-$sha"))
            hostile_results(d)
            write(joinpath(d, "run.json"), """{"repository":"o/r","pr":"704","commit":"$(sha * "0"^32)","state":"finished"}""")
        end
        reset_runs()
        BenchmarkReport.build_all(live, site)
        @test occursin("Pull request #704 at 95f40cad", read(joinpath(site, "pr-704-95f40cad", "index.html"), String))
        @test isfile(joinpath(site, "pr-704-d89f8eeb", "index.html"))      # the earlier commit's report stays
        index = read(joinpath(site, "index.html"), String)
        @test occursin("#704 at d89f8eeb", index) && occursin("#704 at 95f40cad", index)
    end
end

@testset "a report shows the runs it is made of, its times only on the same machine" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        for (name, host) in (("run-new", "shin-compute-001"), ("run-old", "shin-compute-005"))
            d = mkpath(joinpath(live, name))
            hostile_results(d)
            meta = joinpath(d, "machine", "smoke_default.csv.meta.toml")
            write(meta, replace(read(meta, String), "host = \"<script>alert(3)</script>\"" => "host = \"$host\""))
            write(joinpath(d, "run.json"), """{"repository":"o/r","pr":"","title":"$name","commit":"","state":"finished"}""")
        end
        uploads = mkpath(joinpath(root, "uploads"))
        write(joinpath(mkpath(joinpath(uploads, "report-1")), "report.toml"), "title = \"new vs old\"\nruns = [\"run-new\", \"run-old\"]\nlabels = [\"new\", \"the old one\"]\n")
        BenchmarkReport.UPLOADS[] = uploads
        try
            reset_runs()
            BenchmarkReport.build_all(live, site)
            page = read(joinpath(site, "report-1", "smoke.html"), String)
            @test occursin("new (GPU)", page)                                                       # the first run, by its label
            @test occursin("<a href=\"../run-old/\">the old one (005) † (GPU)</a>", page)            # the others linked to their runs
            @test occursin("its times are of different hardware", page)
            @test occursin("Report 1: new vs old", read(joinpath(site, "index.html"), String))       # listed
            @test !occursin("the old one", read(joinpath(site, "run-new", "index.html"), String))    # runs alone
            # a change of a run rebuilds the report
            before = BenchmarkReport.REPORTS["report-1"].mtime
            sleep(1.1)
            touch(joinpath(live, "run-old", "run.json"))
            BenchmarkReport.build_run(live, site, "run-old")
            @test BenchmarkReport.REPORTS["report-1"].mtime > before
        finally
            BenchmarkReport.UPLOADS[] = ""
        end
    end
end

@testset "no pull requests" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        reset_runs()
        BenchmarkReport.build_all(live, site)
        @test isfile(joinpath(site, "index.html"))
        @test isfile(joinpath(site, "version.json"))
    end
end

@testset "a bad folder does not stop the others, but no build at all fails" begin
    mktempdir() do root
        _, clone, _ = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        live, site = mkpath(joinpath(root, "live")), mkpath(joinpath(root, "site"))
        write(joinpath(mkpath(joinpath(live, "pr-9")), "run.json"), "{}")
        reset_runs()
        @test_throws ErrorException BenchmarkReport.build_all(live, site)
        @test !isfile(joinpath(site, "version.json"))
        live_run(live, "pr-1", "running")
        reset_runs()
        BenchmarkReport.build_all(live, site)
        @test isfile(joinpath(site, "version.json"))
        @test occursin("pr-1", read(joinpath(site, "status.json"), String))
        @test !occursin("pr-9", read(joinpath(site, "status.json"), String))
    end
end

@testset "archive pages follow the code and the archive" begin
    mktempdir() do root
        origin, clone, commit = archive_clone(root)
        BenchmarkReport.REPOSITORY[] = clone
        site = mkpath(joinpath(root, "site"))
        page = joinpath(site, "archive", commit, "index.html")
        BenchmarkReport.build_archive(site)
        @test isfile(page)
        # unchanged: left alone
        write(page, "marker")
        BenchmarkReport.build_archive(site)
        @test read(page, String) == "marker"
        # built by other code: rebuilt
        record = joinpath(dirname(page), ".built")
        write(record, replace(read(record, String), BenchmarkReport.code_commit() => "c"^40))
        BenchmarkReport.build_archive(site)
        @test read(page, String) != "marker"
        # archived files replaced: rebuilt
        write(page, "marker")
        open(joinpath(origin, "machine", commit, "summary.md"), "a") do io
            println(io, "more")
        end
        open(joinpath(origin, "machine", commit, "smoke_default.csv.meta.toml"), "a") do io
            println(io, "# changed")
        end
        run(`git -C $origin -c user.name=x -c user.email=x@x commit -qam more`)
        run(`git -C $clone fetch -q origin`)
        BenchmarkReport.build_archive(site)
        @test read(page, String) != "marker"
    end
end

@testset "the service" begin
    mktempdir() do root
        _, clone, commit = archive_clone(root)
        live, site = mkpath(joinpath(root, "live")), joinpath(root, "site")
        live_run(live, "pr-1", "running")
        code = dirname(@__DIR__)
        log = joinpath(root, "log")
        service = run(pipeline(`$(Base.julia_cmd()) --project=$code $code/bin/serve.jl $live $site --repo=$clone`; stdout = log, stderr = log); wait = false)
        waitfor(f) = timedwait(f, 120.0; pollint = 0.2) == :ok
        try
            @test waitfor(() -> isfile(joinpath(site, "version.json")))
            @test waitfor(() -> isfile(joinpath(site, "archive", commit, "index.html")))
            @test occursin("\"state\":\"running\"", read(joinpath(site, "status.json"), String))
            # a replaced run.json rebuilds the report
            live_run(live, "pr-1", "finished")
            @test waitfor(() -> occursin("\"state\":\"finished\"", read(joinpath(site, "status.json"), String)))
            # so does a folder that appears later
            live_run(live, "pr-2", "running")
            @test waitfor(() -> occursin("pr-2", read(joinpath(site, "status.json"), String)))
            @test isfile(joinpath(site, "pr-2", "index.html"))
        finally
            kill(service)
            wait(service)
        end
        @test !occursin("was not updated", read(log, String))
    end
end
