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
