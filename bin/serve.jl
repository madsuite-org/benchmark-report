# Build the reports of running benchmarks, on a server outside CI.
#
#     julia --project bin/serve.jl LIVE SITE --repo=CLONE
#
# LIVE holds a folder `pr-<N>` per pull request, kept up to date by the benchmark job, which
# replaces `run.json` in it after every copy. Each new `run.json` rebuilds `SITE/pr-<N>`.
# `SITE/index.html` lists the benchmarks, `SITE/status.json` gives the state of every pull request,
# `SITE/archive/<commit>` holds the archived results, and `SITE/version.json` names the commit of
# this code once it has built everything. CLONE is a clone of the benchmarked repository with
# `origin/benchmark-results`; this script never fetches.

using BenchmarkReport

function main(args)
    options = filter(startswith("--"), args)
    live, site = filter(!startswith("--"), args)
    for option in options
        m = match(r"^--repo=(.*)$", option)
        m === nothing ? error("unknown option $option") : (BenchmarkReport.REPOSITORY[] = m[1])
    end
    return serve(live, site)
end

main(ARGS)
