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
        @test occursin("<td>1/2</td>", index)
        @test occursin("<td>2/2</td>", index)
    end
end

@testset "a solve past the limit of a GPU counts as stopped there" begin
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
        write_report(site, results; wall = 7200.0, finished = true)
        @test occursin("<td class=\"l fail\"", read(joinpath(site, "cuda-smoke.html"), String))
        @test !occursin("<td class=\"l fail\"", read(joinpath(site, "cpu-smoke.html"), String))
        # the result files are left as they were
        @test occursin(",900,", read(joinpath(site, "data", "machine", "smoke_default.csv"), String))
        # a limit stated by the run wins
        write(joinpath(machine, "smoke_default.csv.meta.toml"), meta * "wall_limit_s = 1000.0\n")
        write_report(site, results; wall = 7200.0, finished = true)
        @test !occursin("<td class=\"l fail\"", read(joinpath(site, "cuda-smoke.html"), String))
    end
end

@testset "a device that has not run a set counts none of it" begin
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
        @test occursin("<td>0/1</td>", read(joinpath(site, "index.html"), String))
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

reset_runs() = empty!(BenchmarkReport.RUNS)

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
        @test !occursin("#7", BenchmarkReport.latest_merged(live, "1").label)
        live_run(live, "pr-7", "finished")
        run_json = joinpath(live, "pr-7", "run.json")
        write(run_json, replace(read(run_json, String), "b"^40 => head))
        rs = BenchmarkReport.latest_merged(live, "1")
        @test rs !== nothing && occursin("`dddddddd`, from #7, on machine (smoke)", rs.label)
        @test occursin("`$(first(older, 8))`, from #6, on machine (other)", rs.label)
        @test !occursin("elsewhere", rs.label)
        # a pull request is not its own master
        @test !occursin("#7", BenchmarkReport.latest_merged(live, "7").label)
        reset_runs()
        BenchmarkReport.build_all(live, site)
        report = read(joinpath(site, "pr-1", "index.html"), String)
        @test occursin(">master<", report)
        @test occursin("from #7", report)
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
