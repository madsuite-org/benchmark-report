# Run queue

`app.py` is the run queue at https://benchmark.madsuite.org/queue, next to the reports at `/madnlp/`: people with write access to
MadNLP.jl queue benchmarks there, or with a comment `@MadSuiteBenchmarkBot benchmark` on a pull
request, and every push to master queues one. One job runs at a time, master first. A runner on the
benchmark machine takes a job by holding `GET /queue/runner/next` open, runs it, and reports with
`POST /queue/runner/status`. Only pull requests from branches of the repository are run, never from
a fork, at their head when the run starts.

It needs Python 3 and `openssl`, nothing else. The environment sets `QUEUE_REPO`, `QUEUE_STATE`,
`QUEUE_BASE`, `QUEUE_REPORTS`, `QUEUE_PORT`, the GitHub App (`GITHUB_APP_ID`,
`GITHUB_APP_KEY_FILE`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET_FILE`), `WEBHOOK_SECRET_FILE`,
`SESSION_KEY_FILE` and `RUNNER_TOKEN_HASH_FILE`, the SHA-256 of the runner's token.

    python3 -m unittest discover -s queue

## One app

The app serves all of the site: the reports from the directory the builder writes (`QUEUE_SITE`),
and the jobs and the API. Every page has the same banner, and a report's banner shows who is signed
in. With `QUEUE_BUILDER` set to the builder's command (`julia --project bin/serve.jl LIVE SITE ...`)
the app also starts the builder and starts it again if it stops; on the server the builder runs as
its own sandboxed process instead, and the app only reads its directory.

## Where jobs run

Each machine in `QUEUE_NODES` (a JSON list of `{"name", "remote", "live"}`) has its own lane: one
job at a time on each, all at once across them. A job may name its machine (`node` in the API, or
the form's Machine); otherwise the first free machine takes it. Two jobs never write the same report
folder at once. Results are kept per machine, so comparisons only ever use the same machine.

With `QUEUE_REMOTE` set, the app runs the jobs itself: one at a time, it starts each on that host
over SSH (`local` for the machine it runs on), with `QUEUE_REMOTE_START` and `QUEUE_REMOTE_STOP`
(by default the sudo wrappers of the benchmark machine) and the live folder under `QUEUE_LIVE`.
Preemption of other work on that machine belongs to the machine's start wrapper, not to the app. So it can run anywhere, a laptop included, with any machine as its remote:

    QUEUE_REMOTE=005 QUEUE_STATE=/tmp/q.json ... python3 queue/app.py

Unset, a runner on the benchmark machine (MadNLPBenchmark.jl, `runner/`) asks the app for jobs
instead, and the app needs no login to any machine.

## API

JSON under `/queue/api/`, for anyone with write access to the repository. Authenticate with the
app's sign-in, or send your own GitHub token as `Authorization: Bearer <token>`. Send JSON bodies
with `Content-Type: application/json`.

| request | does |
|:---|:---|
| `GET /queue/api/me` | who you are and whether you can run jobs |
| `GET /queue/api/jobs` | the running job, the waiting ones in order, then the latest finished |
| `GET /queue/api/jobs/<id>` | one job |
| `POST /queue/api/jobs` `{"pr": 705, "keep": true}` | queue a pull request; `keep: false` throws its earlier results away |
| `POST /queue/api/jobs` `{"master": true}` | queue master at its latest commit |
| `POST /queue/api/jobs` `{"branch": "name"}` | queue a branch of the repository, at its head when it starts |
| `POST /queue/api/jobs` `{"commit": "sha"}` | queue a commit in master's history |
| any of these with `"node": "001"` | run it on that machine only |
| `POST /queue/api/jobs/<id>/cancel` | cancel a job you queued, or any as an admin |
| `GET /queue/api/pulls` | the open pull requests that can be benchmarked |
| `POST /queue/api/pulls/<n>/update` `{"keep": true}` | run a pull request again at its latest commit, first |

Each of these may also give `config` (a configuration of the harness, `default`), `options`
(`name=value,...`, extra MadNLP options), `harness` (a branch of MadNLPBenchmark.jl, for an
experiment) and `devices` (`cpu`, `cuda` or `cpu,cuda`). A job with other settings than these
defaults gets a report of its own, never mixed with the standard runs.

A job has `config, options, harness, devices, id, kind, pr, ref, sha, title, by, state, queued, started, ended, keep, cancelling, url,
report`; `state` is `waiting`, `running`, `finished`, `failed`, `cancelled` or `skipped`.

```sh
curl -H "Authorization: Bearer $(gh auth token)" -H "Content-Type: application/json" \
     -d '{"pr": 705}' https://benchmark.madsuite.org/queue/api/jobs
```

## Uploading results

Results run anywhere, with the harness's `run.jl`, can be shown on the site: pack the results root
(the directory with one folder per machine) and send it, with a GitHub token of someone with
write access to the repository and a name for the report:

```sh
tar czf results.tar.gz -C results .
curl -H "Authorization: Bearer $(gh auth token)" --data-binary @results.tar.gz \
     "https://benchmark.madsuite.org/queue/api/results?name=shifted-ldl-a100"
```

The answer gives the report's address, `/madnlp/run-up-<name>/`. A run still going can upload as it
goes with `&state=running`, which keeps the report live; its last upload says `finished` (the
default) or `failed`. Only regular files of the kinds a
run writes (`.csv`, `.toml`, `.md`, `.txt`, `.log`) are taken, under safe names and within
limits, and there must be at least one `<machine>/<set>_<config>.csv` with its `.meta.toml`. Sending
the same name again replaces your own upload; another person's cannot be replaced. Uploads are
kept apart from the queue's runs (`QUEUE_UPLOADS`, read by the builder with `--uploads`) and are
never used as master's results for comparisons.

## Deploying

`GET /queue/version` gives the commit the app runs. While the file `QUEUE_DRAIN` exists, no machine
takes a new job, and running jobs carry on: a restart sets it, waits for the running jobs to end,
restarts the app and removes it, with nothing interrupted.
