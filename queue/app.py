"""The run queue of the MadSuite benchmarks.

People with write access to the benchmarked repository queue runs, on the web page or by commenting
`@<bot> benchmark` on a pull request, and every push to master queues one. A runner on the benchmark
machine takes one job at a time by holding a request open to this app, so nothing listens on that
machine and nothing here polls. Standard library only; the GitHub App's tokens are signed with the
`openssl` command.

    python3 queue/app.py            # configured by the environment, see `Config`
"""

import base64, hashlib, hmac, html, http.server, json, os, re, secrets, subprocess, threading, time
import urllib.error, urllib.parse, urllib.request

WRITE = ("admin", "maintain", "write")


def read(path):
    return open(path).read().strip() if path else ""


class Config:
    def __init__(self, env=os.environ):
        self.repo = env.get("QUEUE_REPO", "madsuite-org/MadNLP.jl")
        self.state = env.get("QUEUE_STATE", "queue.json")
        self.app_id = env.get("GITHUB_APP_ID", "")
        self.app_key = env.get("GITHUB_APP_KEY_FILE", "")
        self.client_id = env.get("GITHUB_CLIENT_ID", "")
        self.client_secret = read(env.get("GITHUB_CLIENT_SECRET_FILE"))
        self.hook_secret = read(env.get("WEBHOOK_SECRET_FILE")).encode()
        # the runner's token is kept as its SHA-256, so this file cannot be used as one
        self.runner_hash = read(env.get("RUNNER_TOKEN_HASH_FILE"))
        self.session_key = (read(env.get("SESSION_KEY_FILE")) or secrets.token_hex(32)).encode()
        self.base = env.get("QUEUE_BASE", "https://benchmark.madsuite.org/queue")
        self.reports = env.get("QUEUE_REPORTS", "https://benchmark.madsuite.org/benchmark")
        self.bots = [b.lower() for b in env.get("QUEUE_BOTS", "MadSuiteBenchmarkBot,madsuite-benchmark").split(",") if b]
        self.port = int(env.get("QUEUE_PORT", "8091"))
        self.hold = float(env.get("QUEUE_HOLD", "50"))   # seconds a runner request is held open


class GitHub:
    """GitHub's API as the App: its tokens last an hour and are minted from the App's key."""

    def __init__(self, cfg):
        self.cfg, self.token, self.expires = cfg, None, 0

    def call(self, method, path, body=None, token=None, url=None, form=False):
        data = None if body is None else (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
        req = urllib.request.Request(url or "https://api.github.com" + path, method=method, data=data,
                                     headers={"Accept": "application/vnd.github+json" if not url else "application/json",
                                              "User-Agent": "madsuite-benchmark-queue"})
        if token:
            req.add_header("Authorization", ("Bearer " if not token.startswith("Basic ") else "") + token)
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read()
            return json.loads(text) if text else {}

    def jwt(self):
        now = int(time.time())
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
        msg = b64(b'{"alg":"RS256","typ":"JWT"}') + b"." + b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.cfg.app_id}).encode())
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.cfg.app_key], input=msg, capture_output=True, check=True).stdout
        return (msg + b"." + b64(sig)).decode()

    def api(self, method, path, body=None):
        if time.time() > self.expires:
            inst = self.call("GET", f"/repos/{self.cfg.repo}/installation", token=self.jwt())
            tok = self.call("POST", f"/app/installations/{inst['id']}/access_tokens", {}, token=self.jwt())
            self.token, self.expires = tok["token"], time.time() + 3000
        return self.call(method, path, body, token=self.token)

    def user(self, code):
        """The login of the person who signed in with `code`."""
        tok = self.call("POST", "", {"client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret, "code": code},
                        url="https://github.com/login/oauth/access_token").get("access_token")
        return tok and self.call("GET", "/user", token=tok).get("login")


class Queue:
    """Jobs in one JSON file: waiting ones in order, at most one running, and the latest finished."""

    def __init__(self, cfg, gh):
        self.cfg, self.gh = cfg, gh
        self.changed = threading.Condition()
        self.data = json.load(open(cfg.state)) if os.path.exists(cfg.state) else {"next": 1, "jobs": []}
        self.perms = {}

    def save(self):
        done = [j for j in self.data["jobs"] if j["state"] not in ("waiting", "running")]
        keep = {id(j) for j in done[-50:]}
        self.data["jobs"] = [j for j in self.data["jobs"] if j["state"] in ("waiting", "running") or id(j) in keep]
        tmp = self.cfg.state + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.cfg.state)
        self.changed.notify_all()

    def jobs(self, *states):
        return [j for j in self.data["jobs"] if j["state"] in states]

    def permission(self, login):
        """The access of `login` to the repository, asked of GitHub at most every five minutes."""
        cached = self.perms.get(login.lower())
        if cached and cached[1] > time.time():
            return cached[0]
        try:
            p = self.gh.api("GET", f"/repos/{self.cfg.repo}/collaborators/{login}/permission").get("permission", "none")
        except urllib.error.HTTPError:
            p = "none"
        self.perms[login.lower()] = (p, time.time() + 300)
        return p

    def pull(self, pr):
        """The head of open pull request `pr` from a branch of the repository, else a reason not to run it."""
        try:
            p = self.gh.api("GET", f"/repos/{self.cfg.repo}/pulls/{pr}")
        except urllib.error.HTTPError:
            return None, "There is no such pull request."
        if p.get("state") != "open":
            return None, "That pull request is not open."
        if ((p.get("head") or {}).get("repo") or {}).get("full_name") != self.cfg.repo:
            # the benchmark machine runs the code: never from a fork
            return None, "Only pull requests from branches of the repository can be benchmarked."
        return p, None

    def add_pr(self, pr, by, keep=True):
        p, problem = self.pull(pr)
        if problem:
            return problem
        with self.changed:
            if any(j.get("pr") == pr for j in self.jobs("waiting", "running")):
                return "That pull request is already in the queue."
            self.data["jobs"].append({"id": self.data["next"], "kind": "pr", "pr": pr, "title": p.get("title", ""), "by": by,
                                      "state": "waiting", "queued": int(time.time()), "keep": keep})
            self.data["next"] += 1
            self.save()
        return None

    def add_master(self, sha):
        with self.changed:
            waiting = [j for j in self.jobs("waiting") if j["kind"] == "master"]
            if waiting:
                waiting[0]["sha"] = sha     # one master run at a time is enough: the newest
            else:
                self.data["jobs"].append({"id": self.data["next"], "kind": "master", "pr": None, "sha": sha, "title": "master",
                                          "by": "push", "state": "waiting", "queued": int(time.time())})
                self.data["next"] += 1
            self.save()

    def update(self, pr, login, keep):
        """Run pull request `pr` again at its head: in place of its waiting or running job, keeping
        the results so far or starting over. A running job is stopped, and the new one goes first."""
        p, problem = self.pull(pr)
        if problem:
            return problem
        with self.changed:
            for j in self.jobs("waiting", "running"):
                if j.get("pr") == pr:
                    if j["state"] == "waiting":
                        j["keep"] = keep
                        self.save()
                        return None
                    j["cancel"] = True
            self.data["jobs"].append({"id": self.data["next"], "kind": "pr", "pr": pr, "title": p.get("title", ""), "by": login,
                                      "state": "waiting", "queued": int(time.time()), "keep": keep, "first": True})
            self.data["next"] += 1
            self.save()
        return None

    def cancel(self, job_id, login):
        with self.changed:
            for j in self.jobs("waiting", "running"):
                if j["id"] == job_id and (j["by"].lower() == login.lower() or self.permission(login) == "admin"):
                    if j["state"] == "waiting":
                        j["state"], j["ended"] = "cancelled", int(time.time())
                    else:
                        j["cancel"] = True
                    self.save()
                    return True
        return False

    def status(self, job, state, description):
        if job.get("sha"):
            try:
                self.gh.api("POST", f"/repos/{self.cfg.repo}/statuses/{job['sha']}",
                            {"state": state, "context": "benchmark", "description": description[:140],
                             "target_url": f"{self.cfg.reports}/{folder(job)}/"})
            except urllib.error.HTTPError:
                pass

    def take(self):
        """The job the runner should work on: the running one, else the next waiting one, master first."""
        with self.changed:
            running = self.jobs("running")
            if running:
                return running[0]
            while True:
                waiting = sorted(self.jobs("waiting"), key=order)
                if not waiting:
                    return None
                job = waiting[0]
                if job["kind"] == "pr":
                    # the head when the run starts, not when it was queued
                    p, problem = self.pull(job["pr"])
                    if problem:
                        job["state"], job["note"], job["ended"] = "skipped", problem, int(time.time())
                        self.save()
                        continue
                    job["sha"] = p["head"]["sha"]
                job["state"], job["started"] = "running", int(time.time())
                self.save()
                break
        self.status(job, "pending", "Benchmark running")
        return job

    def report(self, job_id, state):
        with self.changed:
            job = next((j for j in self.jobs("running") if j["id"] == job_id), None)
            if job is None or state not in ("finished", "failed", "cancelled"):
                return False
            job["state"], job["ended"] = state, int(time.time())
            self.save()
        self.status(job, "success" if state == "finished" else "error", {"finished": "Benchmark finished", "failed": "Benchmark failed", "cancelled": "Benchmark cancelled"}[state])
        return True

    def wait(self, predicate, hold):
        with self.changed:
            self.changed.wait_for(predicate, timeout=hold)


def order(job):
    """Updated pull requests first, then master, then in the order queued."""
    return (not job.get("first"), job["kind"] != "master", job["id"])


def folder(job):
    return f"pr-{job['pr']}" if job["kind"] == "pr" else f"master-{job['sha'][:8]}"


def sign(cfg, value):
    return hmac.new(cfg.session_key, value.encode(), hashlib.sha256).hexdigest()


def session(cfg, value):
    """The login of a session cookie, or None if it is forged or expired."""
    try:
        payload, mac = value.rsplit(".", 1)
        if not hmac.compare_digest(sign(cfg, payload), mac):
            return None
        login, expires = base64.urlsafe_b64decode(payload).decode().split("|")
        return login if int(expires) > time.time() else None
    except (ValueError, UnicodeDecodeError):
        return None


def new_session(cfg, login, days=14):
    payload = base64.urlsafe_b64encode(f"{login}|{int(time.time()) + days * 86400}".encode()).decode()
    return payload + "." + sign(cfg, payload)


def command(cfg, body):
    """Whether a comment asks for a benchmark: `@<bot> benchmark` on a line of its own."""
    names = "|".join(re.escape(b) for b in cfg.bots)
    return re.search(rf"^\s*@({names})(\[bot\])?\s+benchmark\s*$", body or "", re.I | re.M) is not None


def page(cfg, q, login, message=""):
    e = html.escape
    allowed = bool(login) and q.permission(login) in WRITE
    admin = bool(login) and q.permission(login) == "admin"
    csrf = sign(cfg, "csrf|" + login) if login else ""
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'

    def row(j):
        name = f'#{j["pr"]}' if j["kind"] == "pr" else "master"
        link = f'https://github.com/{cfg.repo}/pull/{j["pr"]}' if j["kind"] == "pr" else f'https://github.com/{cfg.repo}/commits/master'
        report = f'<a href="{e(cfg.reports)}/{folder(j)}/">report</a>' if j.get("sha") and j["state"] != "waiting" else ""
        state = {"running": '<span class="state on">running</span>'}.get(j["state"], e(j["state"]))
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(j.get("ended") or j.get("started") or j["queued"]))
        update = ""
        if j["kind"] == "pr" and allowed and not j.get("cancel"):
            update = (f'<form method="post" action="update" class="update">{hidden}<input type="hidden" name="pr" value="{j["pr"]}">'
                      '<span class="m">Update to the latest commit:</span> <button name="keep" value="1">keep the results so far</button> '
                      '<button name="keep" value="0">start over</button></form>')
        cancel = ""
        if j["state"] in ("waiting", "running") and allowed and (admin or j["by"].lower() == login.lower()) and not j.get("cancel"):
            cancel = f'<form method="post" action="cancel">{hidden}<input type="hidden" name="id" value="{j["id"]}"><button>Cancel</button></form>'
        return (f'<tr><td><a href="{link}">{e(name)}</a></td><td class="l">{e(j.get("title", ""))}</td><td class="l">{e(j["by"])}</td>'
                f'<td class="l">{state}{" (cancelling)" if j.get("cancel") else ""}</td><td>{when}</td><td class="l">{report}</td><td>{cancel}{update}</td></tr>')

    def table(title, jobs):
        if not jobs:
            return ""
        return (f'<h2>{title}</h2><div class="wide"><table class="table table-sm"><thead><tr><th>run</th><th class="l">title</th>'
                '<th class="l">queued by</th><th class="l">state</th><th>time (UTC)</th><th class="l"></th><th></th></tr></thead><tbody>'
                + "".join(row(j) for j in jobs) + "</tbody></table></div>")

    queue = q.jobs("running") + sorted(q.jobs("waiting"), key=order)
    done = list(reversed(q.jobs("finished", "failed", "cancelled", "skipped")))[:20]
    if allowed:
        form = (f'<p>Signed in as {e(login)}. <form method="post" action="logout" style="display:inline">{hidden}<button>Sign out</button></form></p>'
                f'<form method="post" action="add">{hidden}<input name="pr" inputmode="numeric" placeholder="Pull request number" required> <button>Queue a benchmark</button></form>')
    elif login:
        form = f'<p>Signed in as {e(login)}, who has no write access to {e(cfg.repo)}, so cannot queue runs.</p>'
    else:
        form = '<p><a href="login">Sign in with GitHub</a> to queue a run. Anyone with write access to the repository can.</p>'
    bot = e(cfg.bots[0]) if cfg.bots else ""
    body = (f'<h1>Run queue</h1><p class="m">One benchmark runs at a time, master first, then in the order queued. '
            f'On a pull request, a comment <code>@{bot} benchmark</code> also queues it.</p>'
            + (f'<p class="fail">{e(message)}</p>' if message else "") + form
            + (table("Queue", queue) or '<p class="m">Nothing is running or waiting.</p>') + table("Recent", done))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Run queue: MadNLP benchmarks</title><script src="../benchmark/theme.js"></script>'
            '<link rel="stylesheet" href="../benchmark/bootstrap.min.css"><link rel="stylesheet" href="../benchmark/style.css"><body>'
            '<header class="site"><div class="container"><a class="brand" href="../benchmark/">MadNLP benchmarks</a>'
            '<span class="trail">Run queue</span><button id="theme" type="button" aria-label="Switch between light and dark">Theme</button></div></header>'
            f'<main class="container">{body}</main></body></html>')


def handler(cfg, q):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def cookie(self, name):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
            return ""

        def send(self, code, body="", headers=(), kind="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, to, headers=()):
            self.send(303, "", [("Location", to), *headers])

        def runner(self):
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return bool(cfg.runner_hash) and hmac.compare_digest(hashlib.sha256(got.encode()).hexdigest(), cfg.runner_hash)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            args = urllib.parse.parse_qs(query)
            if path in ("/queue", "/queue/"):
                return self.send(200, page(cfg, q, session(cfg, self.cookie("s"))))
            if path == "/queue/login":
                state = secrets.token_urlsafe(16)
                url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(
                    {"client_id": cfg.client_id, "redirect_uri": cfg.base + "/callback", "state": state, "allow_signup": "false"})
                return self.redirect(url, [("Set-Cookie", f"o={state}; Path=/queue; HttpOnly; Secure; SameSite=Lax; Max-Age=600")])
            if path == "/queue/callback":
                state = args.get("state", [""])[0]
                if not state or not hmac.compare_digest(state, self.cookie("o")):
                    return self.send(400, page(cfg, q, None, "The sign-in did not match. Please try again."))
                try:
                    login = q.gh.user(args.get("code", [""])[0])
                except urllib.error.HTTPError:
                    login = None
                if not login:
                    return self.send(400, page(cfg, q, None, "GitHub did not confirm the sign-in. Please try again."))
                return self.redirect(cfg.base + "/", [("Set-Cookie", f"s={new_session(cfg, login)}; Path=/queue; HttpOnly; Secure; SameSite=Lax; Max-Age={14 * 86400}")])
            if path == "/queue/runner/next":
                if not self.runner():
                    return self.send(401, "", kind="text/plain")
                q.wait(lambda: q.jobs("running") or q.jobs("waiting"), cfg.hold)
                job = q.take()
                if job is None:
                    return self.send(204, kind="text/plain")
                return self.send(200, json.dumps({k: job.get(k) for k in ("id", "kind", "pr", "sha")} | {"folder": folder(job), "keep": job.get("keep", True)}), kind="application/json")
            if path == "/queue/runner/cancel":
                if not self.runner():
                    return self.send(401, "", kind="text/plain")
                job_id = int(args.get("id", ["0"])[0] or 0)
                asked = lambda: any(j["id"] == job_id and j.get("cancel") for j in q.jobs("running")) or not any(j["id"] == job_id for j in q.jobs("running"))
                q.wait(asked, cfg.hold)
                return self.send(200, json.dumps({"cancel": asked()}), kind="application/json")
            return self.send(404, "Not found", kind="text/plain")

        def do_POST(self):
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1 << 20))
            if self.path == "/queue/hook":
                mac = "sha256=" + hmac.new(cfg.hook_secret, body, hashlib.sha256).hexdigest()
                if not cfg.hook_secret or not hmac.compare_digest(mac, self.headers.get("X-Hub-Signature-256", "")):
                    return self.send(401, "", kind="text/plain")
                event, data = self.headers.get("X-GitHub-Event"), json.loads(body or b"{}")
                if (data.get("repository") or {}).get("full_name") != cfg.repo:
                    return self.send(204, kind="text/plain")
                if event == "issue_comment" and data.get("action") == "created" and "pull_request" in (data.get("issue") or {}) \
                        and command(cfg, (data.get("comment") or {}).get("body")):
                    login = data["comment"]["user"]["login"]
                    if q.permission(login) in WRITE:
                        q.add_pr(int(data["issue"]["number"]), login)
                elif event == "push" and data.get("ref") == "refs/heads/master" and not data.get("deleted"):
                    q.add_master(data["after"])
                return self.send(204, kind="text/plain")
            if self.path == "/queue/runner/status":
                if not self.runner():
                    return self.send(401, "", kind="text/plain")
                msg = json.loads(body or b"{}")
                ok = q.report(int(msg.get("id", 0)), msg.get("state"))
                return self.send(204 if ok else 409, kind="text/plain")
            login = session(cfg, self.cookie("s"))
            form = urllib.parse.parse_qs(body.decode(errors="replace"))
            get = lambda k: form.get(k, [""])[0]
            if not login or not hmac.compare_digest(get("csrf"), sign(cfg, "csrf|" + login)):
                return self.send(403, page(cfg, q, None, "Please sign in again."))
            if self.path == "/queue/logout":
                return self.redirect(cfg.base + "/", [("Set-Cookie", "s=; Path=/queue; HttpOnly; Secure; SameSite=Lax; Max-Age=0")])
            if q.permission(login) not in WRITE:
                return self.send(403, page(cfg, q, login, "Only people with write access to the repository can do that."))
            if self.path == "/queue/add":
                pr = get("pr").strip().lstrip("#")
                if not pr.isdigit() or len(pr) > 7:
                    return self.send(400, page(cfg, q, login, "Give the number of a pull request."))
                problem = q.add_pr(int(pr), login)
                return self.send(400, page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
            if self.path == "/queue/update":
                pr = get("pr")
                if not pr.isdigit():
                    return self.send(400, page(cfg, q, login, "Give the number of a pull request."))
                problem = q.update(int(pr), login, get("keep") != "0")
                return self.send(400, page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
            if self.path == "/queue/cancel":
                job_id = get("id")
                if not job_id.isdigit() or not q.cancel(int(job_id), login):
                    return self.send(403, page(cfg, q, login, "That run cannot be cancelled by you."))
                return self.redirect(cfg.base + "/")
            return self.send(404, "Not found", kind="text/plain")

    return H


if __name__ == "__main__":
    cfg = Config()
    q = Queue(cfg, GitHub(cfg))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", cfg.port), handler(cfg, q))
    server.daemon_threads = True
    server.serve_forever()
