# benchmark-report

The website of the MadSuite benchmarks at https://madnlp.madsuite.org/benchmark/.

It reads the result files that a benchmark run writes and builds static pages: a list of all
benchmarks, a report per pull request, and the archived results. It has only standard-library
dependencies, and treats result files as untrusted input, since the pages are public.

```sh
julia --project bin/build.jl RESULTS SITE --repo=CLONE       # one report
julia --project bin/serve.jl LIVE SITE --repo=CLONE          # reports of running benchmarks
julia --project test/runtests.jl
```

`RESULTS` holds one directory of result files per machine. `LIVE` holds a folder `pr-<N>` per pull
request, or `run-<name>` per manual run, whose `run.json` also gives a `title`, kept up to date by the benchmark job, which replaces `run.json` in it after every copy.
`SITE/version.json` names the commit of this code once it has built everything.
`CLONE` is a clone of the benchmarked repository with `origin/benchmark-results`, the archive of
results; `bin/serve.jl` never fetches it. The result format and the benchmark itself are defined
in [MadNLP.jl](https://github.com/madsuite-org/MadNLP.jl), under `benchmark/`.

A pull request from this repository gets a preview on GitHub Pages, linked in a comment. It is
built by `bin/preview.jl` from the results in `sample/`, which are a copy of real results.

The theme is in `assets/`, with Bootstrap bundled, so the pages load nothing from elsewhere.
