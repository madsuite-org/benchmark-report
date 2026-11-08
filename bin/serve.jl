# Build the reports of running benchmarks, on a server outside CI.
#
#     julia --project bin/serve.jl LIVE SITE --repo=CLONE [--archive=CLONE --archive-url=URL] [--uploads=DIR]
#
# LIVE holds a folder `pr-<N>` per pull request, kept up to date by the benchmark job, which
# replaces `run.json` in it after every copy. Each new `run.json` rebuilds `SITE/pr-<N>`.
# `SITE/index.html` lists the benchmarks, `SITE/status.json` gives the state of every pull request,
# `SITE/archive/<commit>` holds the archived results, and `SITE/version.json` names the commit of
# this code once it has built everything. `--repo` is a clone of the benchmarked repository, for
# its master; the archived results are on `origin/benchmark-results` of `--archive`, by default the
# same clone, and `--archive-url` is that repository's address for links. This script never fetches.

using BenchmarkReport

function main(args)
    options = filter(startswith("--"), args)
    live, site = filter(!startswith("--"), args)
    for option in options
        m = match(r"^--(repo|archive|archive-url|uploads)=(.*)$", option)
        m === nothing && error("unknown option $option")
        ref = Dict("repo" => BenchmarkReport.REPOSITORY, "archive" => BenchmarkReport.ARCHIVE, "archive-url" => BenchmarkReport.ARCHIVE_URL,
                   "uploads" => BenchmarkReport.UPLOADS)[m[1]]
        ref[] = m[2]
    end
    return serve(live, site)
end

main(ARGS)
