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
