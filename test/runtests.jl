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
