# Build the whole site from the results in `sample/`, to look at a change before it is merged.
#
#     julia --project bin/preview.jl SITE
#
# The results are real, but the commits they are filed under are made up here.

using BenchmarkReport

function preview(site)
    sample = joinpath(dirname(@__DIR__), "sample")
    mktempdir() do root
        clone, live = joinpath(root, "clone"), mkpath(joinpath(root, "live"))
        git(args...) = readchomp(`git -C $clone -c user.name=preview -c user.email=preview@localhost $args`)
        run(`git init -q -b main $clone`)
        git("commit", "-q", "--allow-empty", "-m", "base")
        base = git("rev-parse", "HEAD")
        # the archive, filed under the base commit
        git("checkout", "-q", "--orphan", "results")
        cp(joinpath(sample, "archive"), joinpath(clone, "archive"))
        for machine in readdir(joinpath(clone, "archive"))
            from = joinpath(clone, "archive", machine)
            if machine == "ipopt"
                mv(from, joinpath(clone, machine))
                continue
            end
            for commit in readdir(from)
                mkpath(joinpath(clone, machine))
                mv(joinpath(from, commit), joinpath(clone, machine, base))
            end
        end
        rm(joinpath(clone, "archive"); recursive = true)
        git("add", "-A")
        git("commit", "-q", "-m", "results")
        git("update-ref", "refs/remotes/origin/benchmark-results", "HEAD")
        cp(joinpath(sample, "live"), joinpath(live, "pr-1"))
        write(joinpath(live, "pr-1", "run.json"), """{"repository":"madsuite-org/MadNLP.jl","pr":"1","commit":"$("0"^40)","base":"$base","state":"running"}""")
        BenchmarkReport.REPOSITORY[] = clone
        BenchmarkReport.FETCH[] = false
        BenchmarkReport.build_all(live, site)
        BenchmarkReport.build_archive(site)
    end
    return
end

preview(only(ARGS))
