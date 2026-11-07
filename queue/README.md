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
| `POST /queue/api/jobs/<id>/cancel` | cancel a job you queued, or any as an admin |
| `GET /queue/api/pulls` | the open pull requests that can be benchmarked |
| `POST /queue/api/pulls/<n>/update` `{"keep": true}` | run a pull request again at its latest commit, first |

A job has `id, kind, pr, sha, title, by, state, queued, started, ended, keep, cancelling, url,
report`; `state` is `waiting`, `running`, `finished`, `failed`, `cancelled` or `skipped`.

```sh
curl -H "Authorization: Bearer $(gh auth token)" -H "Content-Type: application/json" \
     -d '{"pr": 705}' https://benchmark.madsuite.org/queue/api/jobs
```
