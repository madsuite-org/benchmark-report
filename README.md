# benchmark-report

The code of https://benchmark.madsuite.org/madnlp/, the website of the MadSuite benchmarks. How to
use the site is on the site, [How it works](https://benchmark.madsuite.org/queue/help); how to run a
benchmark and its nodes is in [MadNLPBenchmark.jl](https://github.com/madsuite-org/MadNLPBenchmark.jl#readme),
which also defines the instances and the result format. This README is for working on the site.

## What is where

- `queue/app.py`: the app (Python, standard library only). Sign-in with GitHub (members of the
  organization, or write access to the repository), the API under `/queue/api/`, jobs and their
  nodes (the per-job token, claims, uploads, retries, log streams), reports, share links, and the
  pages it serves around the built reports (the dashboard, the workers page, a report's header). It
  runs no benchmark itself. Tests: `queue/test_app.py`.
- `src/`, `bin/serve.jl`: the builder (Julia), which turns result folders (a run's, merged from its
  nodes, in the uploads root; the archive of master's results) into the pages: a run's or a
  report's summary, a page per set, profiles, the list of runs and reports. It rebuilds a page when
  its folder's `run.json` (or a report's `report.toml`) changes. Tests: `test/runtests.jl`.
- `assets/`: the theme and the pages' script, with Bootstrap bundled, so the pages load nothing
  from elsewhere.
- `deploy/`: the deploy (below).

Result files and uploads are untrusted input: the pages are shared by link.

## Developing

```sh
python3 -m unittest discover -s queue                       # the app
julia --project test/runtests.jl                            # the builder
julia --project bin/build.jl RESULTS SITE --repo=CLONE       # one report from result folders
```

`RESULTS` holds a folder of result files per machine; `CLONE` is a clone of the benchmarked
repository with the archive of results (or `--archive=CLONE2 --archive-url=URL` where the archive is
in another repository, as MadNLP's is in MadNLPBenchmark.jl). A pull request from this repository
gets a preview on GitHub Pages (`bin/preview.jl`, from the results in `sample/`).

## Deploy

A push to `main` whose Test workflow passes deploys itself: `deploy/launcher.sh`, installed once by
root on the server as `/srv/benchmark-report/deploy/deploy.sh`, finds that commit and runs its
`deploy/deploy.sh`. Changes to the deploy are ordinary pushes; whoever can push to `main` decides
what the deploy runs.
