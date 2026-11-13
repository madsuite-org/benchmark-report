# benchmark-report

The website of the MadSuite benchmarks, https://benchmark.madsuite.org/madnlp/: it coordinates
benchmark jobs of [MadNLP.jl](https://github.com/madsuite-org/MadNLP.jl), collects their results and
shows them as runs and reports. The benchmark itself (the instances, the result format, the node
script) is [MadNLPBenchmark.jl](https://github.com/madsuite-org/MadNLPBenchmark.jl).

## Who can use it

Anyone with write access to MadNLP.jl or who is a member of madsuite-org signs in with GitHub (the
list of runs and reports, creating jobs and reports). A report or run is open to anyone given its
link (Copy link on its page); nothing else is public, and the site is not indexed.

The site runs no benchmark itself: **a job runs on the machines its creator starts nodes on**, and
the site hands out the instances, merges the results and builds the pages.

## Running a benchmark

1. **Push the code** to a branch of madsuite-org/MadNLP.jl (no fork, no unpushed branch), or use
   master, a pull request, or a commit of it.
2. **Create a job** with MadNLPBenchmark.jl, with a GitHub token (`gh auth login`, or
   `BENCHMARK_TOKEN`):

   ```sh
   runner/publish.sh new my-branch --devices=cuda --note="what it is for" --label="short name" \
       --options=preset=ShiftedPreset,kkt_system=SparseCondensedKKTSystem
   ```

   It fixes the commit (the branch's head at that moment), the device (`cpu` or `cuda`, one per
   job), the options (MadNLP options, `name=value,...`, on top of the default configuration) and the
   instances, and writes a job file `benchmark-job-<N>` (keep it private: it is what a node needs).
   - `--sets=acopf,cops,mittelmann,cutest` (default: all).
   - `--subset=TERMS`: instances meeting every term, joined by commas: `size=small+medium+large`,
     `constrained=yes|no`, `failed=SOURCE`, `solved=SOURCE`, `resto=SOURCE` (a source being `ipopt`
     or a run such as `run-x50-1b00be2e`), `suspects` (see below). `--instances=a,b,...` or
     `--instances=@FILE` lists them.
   - `--label` names the job's column in reports; `--note` says why the job exists.
3. **Start nodes** on as many machines as you like, at once or later:

   ```sh
   runner/publish.sh run --job-file=benchmark-job-<N> [--hsl=PATH] [--work=DIR] [--depot=DIR] [--src=DIR]
   ```

   Each node sets up that commit, asks the site for instances one at a time and sends results as
   it goes; it leaves when nothing is left. A node stopped part-way (preempted) carries on when run
   again with the same `--work`; what it held goes to others after 10 minutes. An instance that
   fails for a machine reason (killed, crashed) is tried again, twice at most, on another node.
   On a cluster, give the nodes one `--depot` and `--src` on a shared file system, so that they
   install and compile once. See `runner/publish.sh` for every option.

The job's page (its run) shows progress, each node and worker, and the logs: click a node for its
log, an instance for its solver log, live.

## Runs and reports

A **run** is a job's results; its page shows that run alone. A **report** compares runs: its
columns are the runs it is made of, each named by a label, over the instances every column has.
Make one on the site (Add column on a report's page) or through the API, and share it by its link.

## API

`https://benchmark.madsuite.org/queue/api/`, with `Authorization: Bearer <GitHub token>`, JSON in and
out:

| request | what |
|---|---|
| `GET me` | who you are and whether you can run jobs |
| `GET jobs`, `GET jobs/<id>` | jobs, with each node's state (`setup`, `running`, ...) and seconds since it was heard from (`quiet`) |
| `POST jobs` `{"external": true, "branch"\|"pr"\|"commit"\|"master": ..., "devices", "options", "sets", "subset", "instances", "note", "label"}` | create a job (what `publish.sh new` does); answers its id and token |
| `POST jobs/<id>/cancel`, `.../note`, `.../label`, `.../reopen` | cancel; change its note or label; run a failed job again, keeping its results |
| `GET reports`, `POST reports` `{"title", "runs": [<job>, {"job": <job>, "label": "..."}]}` | list or make reports (the first run is the first column; up to 12) |
| `GET`, `PATCH`, `DELETE reports/<id>` | read, change (title, runs) or delete one |
| `GET suspects?device=cpu\|cuda` | the usual suspects: of the finished full runs, each configuration ranked by instances solved, what the best quarter of them failed at least once |

## Developing

The site is a Python app (`queue/app.py`, standard library only) in front of a Julia builder
(`src/`, `bin/serve.jl`) that turns result folders into pages. Result files are untrusted input.

```sh
python3 -m unittest discover -s queue                       # the app
julia --project test/runtests.jl                            # the builder
julia --project bin/build.jl RESULTS SITE --repo=CLONE       # one report from result folders
```

A pull request from this repository gets a preview on GitHub Pages (`bin/preview.jl`, from the
results in `sample/`). The theme is in `assets/`, with Bootstrap bundled, so the pages load nothing
from elsewhere.

## Deploy

A push to `main` whose Test workflow passes deploys itself: `deploy/launcher.sh`, installed once by
root on the server as `/srv/benchmark-report/deploy/deploy.sh`, finds that commit and runs its
`deploy/deploy.sh`. Changes to the deploy are ordinary pushes; whoever can push to `main` decides
what the deploy runs.
