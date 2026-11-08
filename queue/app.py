"""The run queue of the MadSuite benchmarks.

People with write access to the benchmarked repository queue runs, on the web page or by commenting
`@<bot> benchmark` on a pull request, and every push to master queues one. A runner on the benchmark
machine takes one job at a time by holding a request open to this app, so nothing listens on that
machine and nothing here polls. Standard library only; the GitHub App's tokens are signed with the
`openssl` command.

    python3 queue/app.py            # configured by the environment, see `Config`
"""

import base64, email.utils, hashlib, hmac, html, http.server, json, mimetypes, os, re, secrets, shlex, subprocess, threading, time
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
        self.reports = env.get("QUEUE_REPORTS", "https://benchmark.madsuite.org/madnlp")
        self.bots = [b.strip() for b in env.get("QUEUE_BOTS", "madsuite-benchmark,MadSuiteBenchmarkBot").split(",") if b.strip()]
        self.port = int(env.get("QUEUE_PORT", "8091"))
        self.hold = float(env.get("QUEUE_HOLD", "50"))   # seconds a runner request is held open
        # Where jobs run. Unset: a runner on the machine asks this app for them (`/queue/runner/`).
        # Set: this app starts them itself, over SSH on that host (`local` for this machine), with
        # the commands below, so it can run anywhere with any machine as its remote.
        self.remote = env.get("QUEUE_REMOTE", "")
        self.remote_start = shlex.split(env.get("QUEUE_REMOTE_START", "sudo -n /usr/local/sbin/benchjob-run"))
        self.remote_stop = shlex.split(env.get("QUEUE_REMOTE_STOP", "sudo -n /usr/local/sbin/benchjob-stop"))
        # whether a push to master queues a run of it; off: master runs only when someone queues it
        self.auto_master = env.get("QUEUE_AUTO_MASTER", "") == "1"
        self.live = env.get("QUEUE_LIVE", "/mnt/benchmark-live")
        # the harness's list of configurations and options (configs.toml), to check a job's before it runs
        self.harness_configs = env.get("QUEUE_HARNESS_CONFIGS", "")   # the live folder on the remote
        # The reports: the site the builder writes, served by this app with the rest, and the
        # command that runs the builder (bin/serve.jl), which this app starts and restarts.
        self.site = env.get("QUEUE_SITE", "")
        self.builder = shlex.split(env.get("QUEUE_BUILDER", ""))
        # The live folders as this machine sees them, and how long a running one may go without new
        # data before it is called stalled.
        self.live_local = env.get("QUEUE_LIVE_LOCAL", "")
        self.stall = float(env.get("QUEUE_STALL", "900"))


class GitHub:
    """GitHub's API as the App: its tokens last an hour and are minted from the App's key."""

    def __init__(self, cfg):
        self.cfg, self.token, self.expires = cfg, None, 0

    def call(self, method, path, body=None, token=None, url=None, form=False):
        data = None if body is None else (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
        req = urllib.request.Request(url or "https://api.github.com" + path, method=method, data=data,
                                     headers={"Accept": "application/vnd.github+json" if not url else "application/json",
                                              "User-Agent": "madsuite-benchmark-queue"})
        if data is not None:
            # without it, GitHub's sign-in endpoint reads a JSON body as a form and answers Not Found
            req.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
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

    def login_of(self, token):
        """The login of a person's own GitHub token, for the API."""
        return self.call("GET", "/user", token=token).get("login")

    def user(self, code):
        """The login of the person who signed in with `code`."""
        answer = self.call("POST", "", {"client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret, "code": code},
                           url="https://github.com/login/oauth/access_token")
        tok = answer.get("access_token")
        if not tok:
            print("sign-in refused by GitHub:", answer.get("error"), answer.get("error_description"), flush=True)
        return tok and self.call("GET", "/user", token=tok).get("login")


DEFAULTS = {"config": "default", "options": "", "harness": "", "devices": "cpu,cuda"}


def settings_of(job):
    return {k: job.get(k) or v for k, v in DEFAULTS.items()}


def settings_problem(cfg, settings):
    """Why the settings of a job cannot run, or None. Checked as the machine's wrapper and run.jl
    check them; with a harness branch, its own run.jl checks option names it may add."""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", settings["config"]):
        return "The configuration is not a name."
    if settings["devices"] not in ("cpu", "cuda", "cpu,cuda"):
        return "The devices are cpu, cuda or both."
    h = settings["harness"]
    if h and (not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", h) or ".." in h or h.startswith("-")):
        return "The harness is not a branch name."
    o = settings["options"]
    if o:
        if len(o) > 300 or not re.fullmatch(r"[A-Za-z0-9_.,=+-]+", o):
            return "Options are name=value pairs separated by commas, with letters, digits and _ . + - only."
        names = [pair.split("=", 1)[0] for pair in o.split(",")]
        if any("=" not in pair or not pair.split("=", 1)[1] for pair in o.split(",")) or len(set(names)) != len(names):
            return "Options are name=value pairs, each name once."
    if not h and cfg.harness_configs:
        try:
            import tomllib
            known = tomllib.load(open(cfg.harness_configs, "rb"))
        except (OSError, ValueError):
            known = None
        if known:
            configs = set().union(*(set(c.get("names", [])) for c in known.get("configurations", {}).values()))
            if settings["config"] not in configs:
                return f"There is no configuration {settings['config']}."
            allowed = set(known.get("options", {}).get("madnlp", [])).union(*known.get("options", {}).get("linear_solver", {}).values())
            unknown = [n for n in (pair.split("=", 1)[0] for pair in o.split(",")) if o and n not in allowed]
            if unknown:
                return "Unknown options: " + ", ".join(unknown) + "."
    return None


def variant(job):
    """For a job with other settings than the standard ones, a short name of them: its results then
    have a folder of their own, and never mix with the standard runs. Empty for the standard ones."""
    st = settings_of(job)
    if st == DEFAULTS:
        return ""
    digest = hashlib.sha1(json.dumps(st, sort_keys=True).encode()).hexdigest()[:6]
    head = re.sub(r"[^a-z0-9]+", "-", st["harness"].lower()).strip("-")[:16].strip("-") if st["harness"] else "custom"
    return f"{head}-{digest}"


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

    def add_pr(self, pr, by, keep=True, settings=None):
        settings = {**DEFAULTS, **(settings or {})}
        problem = settings_problem(self.cfg, settings)
        if problem:
            return problem
        p, problem = self.pull(pr)
        if problem:
            return problem
        with self.changed:
            if any(j.get("pr") == pr and settings_of(j) == settings for j in self.jobs("waiting", "running")):
                return "That pull request is already in the queue."
            self.data["jobs"].append({"id": self.data["next"], "kind": "pr", "pr": pr, "title": p.get("title", ""), "by": by,
                                      "state": "waiting", "queued": int(time.time()), "keep": keep, "branch": p["head"].get("ref"), **settings})
            self.data["next"] += 1
            self.save()
        return None

    def open_pulls(self):
        """Open pull requests from branches of the repository, newest first, asked at most every minute."""
        if getattr(self, "_pulls", None) and self._pulls[1] > time.time():
            return self._pulls[0]
        try:
            pulls = self.gh.api("GET", f"/repos/{self.cfg.repo}/pulls?state=open&per_page=100")
        except urllib.error.HTTPError:
            pulls = []
        mine = [{"number": p["number"], "title": p.get("title", ""), "branch": p["head"]["ref"]} for p in pulls
                if ((p.get("head") or {}).get("repo") or {}).get("full_name") == self.cfg.repo]
        self._pulls = (mine, time.time() + 60)
        return mine

    def add_ref(self, ref, by, keep=True, settings=None):
        """A run of a branch of the repository, at its head when the run starts, or of a commit on
        master. Never anything from a fork: a commit must be in master's history."""
        settings = {**DEFAULTS, **(settings or {})}
        problem = settings_problem(self.cfg, settings)
        if problem:
            return None, problem
        ref = ref.strip()
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", ref) or ".." in ref:
            return None, "Give the name of a branch, or a commit."
        try:
            self.gh.api("GET", f"/repos/{self.cfg.repo}/branches/{urllib.parse.quote(ref, safe='')}")
            kind, sha, title = "branch", None, ref
        except urllib.error.HTTPError:
            if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
                return None, f"There is no branch {ref} in the repository."
            try:
                found = self.gh.api("GET", f"/repos/{self.cfg.repo}/compare/{ref}...master")
            except urllib.error.HTTPError:
                return None, f"There is no commit {ref} in the repository."
            if found.get("status") not in ("behind", "identical"):
                return None, "Only commits in master's history can be benchmarked; for others, give their branch."
            kind, sha, title = "commit", found["base_commit"]["sha"], ref[:12]
        with self.changed:
            if any(j["kind"] == kind and j.get("ref") == ref and settings_of(j) == settings for j in self.jobs("waiting", "running")):
                return None, "That is already in the queue."
            job = {"id": self.data["next"], "kind": kind, "pr": None, "ref": ref, "sha": sha, "title": title, "by": by,
                   "state": "waiting", "queued": int(time.time()), "keep": keep, **settings}
            self.data["jobs"].append(job)
            self.data["next"] += 1
            self.save()
        return job, None

    def add_master_now(self, by, settings=None):
        """A run of master at its latest commit, asked for by someone. With other settings than the
        standard ones it is a run of that commit, so that it is never taken for master's results."""
        sha = self.gh.api("GET", f"/repos/{self.cfg.repo}/commits/master")["sha"]
        if settings and {**DEFAULTS, **settings} != DEFAULTS:
            settings = {**DEFAULTS, **settings}
            problem = settings_problem(self.cfg, settings)
            if problem:
                raise ValueError(problem)
            with self.changed:
                job = {"id": self.data["next"], "kind": "commit", "pr": None, "ref": sha, "sha": sha, "title": "master " + sha[:8], "by": by,
                       "state": "waiting", "queued": int(time.time()), "keep": True, **settings}
                self.data["jobs"].append(job)
                self.data["next"] += 1
                self.save()
            return job
        self.add_master(sha, by)
        return next(j for j in self.jobs("waiting") if j["kind"] == "master")

    def add_master(self, sha, by="push"):
        with self.changed:
            waiting = [j for j in self.jobs("waiting") if j["kind"] == "master"]
            if waiting:
                waiting[0]["sha"] = sha     # one master run at a time is enough: the newest
            else:
                self.data["jobs"].append({"id": self.data["next"], "kind": "master", "pr": None, "sha": sha, "title": "master",
                                          "by": by, "state": "waiting", "queued": int(time.time())})
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
                elif job["kind"] == "branch":
                    try:
                        job["sha"] = self.gh.api("GET", f"/repos/{self.cfg.repo}/branches/{urllib.parse.quote(job['ref'], safe='')}")["commit"]["sha"]
                    except urllib.error.HTTPError:
                        job["state"], job["note"], job["ended"] = "skipped", "The branch is gone.", int(time.time())
                        self.save()
                        continue
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


def public(cfg, j):
    """A job as the API gives it."""
    out = {k: j.get(k) for k in ("id", "kind", "pr", "ref", "sha", "title", "by", "state", "queued", "started", "ended", "note")}
    out |= settings_of(j) | {"keep": j.get("keep", True), "cancelling": bool(j.get("cancel")), "url": f"{cfg.base}/job/{j['id']}",
            "report": f"{cfg.reports}/{folder(j)}/" if j.get("sha") and j["state"] != "waiting" else None}
    return out


def folder(job):
    v = variant(job)
    if v:
        target = (f"pr{job['pr']}" if job["kind"] == "pr" else
                  re.sub(r"[^a-z0-9]+", "-", job.get("ref", "").lower()).strip("-")[:12] if job["kind"] == "branch" else (job.get("sha") or "")[:8])
        return f"run-{v}-{target}"[:48].rstrip("-")
    if job["kind"] == "pr":
        return f"pr-{job['pr']}"
    if job["kind"] == "branch":
        return "run-" + (re.sub(r"[^a-z0-9]+", "-", job["ref"].lower()).strip("-")[:40] or "branch")
    if job["kind"] == "commit":
        return f"run-{job['sha'][:8]}"
    return f"master-{job['sha'][:8]}"


class Dispatcher:
    """Runs the queue's jobs on `cfg.remote`, one at a time, as the runner of the machine would."""

    def __init__(self, cfg, q):
        self.cfg, self.q = cfg, q

    def command(self, words):
        if self.cfg.remote == "local":
            return words
        # ssh joins its arguments into one line for the remote shell, so each is quoted
        return ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=60", self.cfg.remote, shlex.join(words)]

    def run(self, job):
        unit = f"benchjob-{job['id']}"
        live = os.path.join(self.cfg.live, folder(job))
        # the machine's own start command takes care of everything around the job there, its
        # preemption of other work included
        # stdin stays open for the whole job: the machine stops the job when it closes, so a lost
        # connection ends the job rather than leaving it running unseen
        # a job started before, and interrupted here, carries on from what it has
        keep = job.get("keep", True) or job.get("dispatched")
        # a job with other settings runs as a run of its branch or commit, so its results go to a folder
        # of their own (the machine lets a pull request's run write only pr-<N>)
        st, kind, pr = settings_of(job), job["kind"], str(job.get("pr") or "")
        if variant(job):
            kind, pr = ("branch" if job["kind"] in ("pr", "branch") else "commit"), ""
        extra = [st["config"], st["options"], st["harness"], st["devices"]] if st != DEFAULTS else []
        proc = subprocess.Popen(self.command([*self.cfg.remote_start, unit, live, job["sha"], kind, pr,
                                              "keep" if keep else "fresh", *extra]), stdin=subprocess.PIPE)
        with self.q.changed:
            job["dispatched"] = True
            self.q.save()
        stopped = False
        while proc.poll() is None:
            self.q.wait(lambda: any(j["id"] == job["id"] and j.get("cancel") for j in self.q.jobs("running")), 5)
            if not stopped and any(j["id"] == job["id"] and j.get("cancel") for j in self.q.jobs("running")):
                subprocess.run(self.command([*self.cfg.remote_stop, unit]))
                stopped = True
        rc = proc.wait()
        proc.stdin.close()
        if rc < 0 and not stopped:
            # the connection was killed on this side, e.g. the app is being stopped: not the job's
            # doing, so it stays running, and is started again, keeping what it has
            return "interrupted"
        state = "cancelled" if stopped else "finished" if rc == 0 else "failed"
        self.q.report(job["id"], state)
        return state

    def serve(self):
        while True:
            self.q.wait(lambda: self.q.jobs("running") or self.q.jobs("waiting"), 3600)
            job = self.q.take()
            if job:
                self.run(job)


class Builder:
    """Keeps the report builder running: started with the app, started again if it stops."""

    def __init__(self, cfg):
        self.cfg, self.proc = cfg, None

    def serve(self):
        while True:
            started = time.time()
            self.proc = subprocess.Popen(self.cfg.builder)
            rc = self.proc.wait()
            print(f"the report builder stopped ({rc}); starting it again", flush=True)
            # one that keeps failing at once is not started in a tight loop
            time.sleep(30 if time.time() - started < 60 else 1)


def stalled(cfg, name):
    """Seconds since the live folder `name` said it is running and last had new data, if that is
    longer than `cfg.stall`; else None."""
    if not cfg.live_local or not re.fullmatch(r"pr-\d+|master-[0-9a-f]{8}|run-[a-z0-9-]+", name):
        return None
    path = os.path.join(cfg.live_local, name, "run.json")
    try:
        state = json.load(open(path)).get("state")
        age = time.time() - os.stat(path).st_mtime
    except (OSError, ValueError, AttributeError):
        return None
    return age if state == "running" and age > cfg.stall else None


def resume_form(cfg, login, name):
    """The button that resumes a stalled run: its pull request again, keeping what it has."""
    e = html.escape
    if not login or not name.startswith("pr-"):
        return ""
    return (f'<form method="post" action="{e(cfg.base)}/update" class="d-inline"><input type="hidden" name="csrf" value="{sign(cfg, "csrf|" + login)}">'
            f'<input type="hidden" name="pr" value="{name[3:]}"><button class="btn btn-sm btn-primary" name="keep" value="1">Resume</button></form>')


def stall_note(cfg, login, name, age):
    action = resume_form(cfg, login, name) or (f'<a href="{html.escape(cfg.base)}/login">Sign in</a> to resume it.' if name.startswith("pr-") else "")
    return (f'<div class="alert alert-warning container mt-2 mb-0">This run is <b>stalled</b>: it says it is running, but '
            f'no new data has arrived for {int(age // 60)} min. {action}</div>')


# The banner's menu as the site writes it, which the app replaces with the signed-in user's
NAV = ('<nav class="app"><details class="menu"><summary aria-label="Menu">☰</summary><div><a href="/queue/">Sign in</a>'
       '<button id="theme" type="button">Light or dark</button></div></details></nav>')


def app_nav(cfg, login):
    """The banner's menu for who is signed in: the same on every page. Collapsed behind one button,
    so the banner stays one line on a phone."""
    e = html.escape
    theme = '<button id="theme" type="button">Light or dark</button>'
    if not login:
        return (f'<nav class="app"><details class="menu"><summary aria-label="Menu">☰</summary><div>'
                f'<a href="{e(cfg.base)}/login">Sign in with GitHub</a><a href="{e(cfg.reports)}/">Reports</a>{theme}</div></details></nav>')
    csrf = sign(cfg, "csrf|" + login)
    return (f'<nav class="app"><details class="menu"><summary aria-label="Menu">{e(login)} ☰</summary><div>'
            f'<a href="{e(cfg.reports)}/">Dashboard</a><a href="{e(cfg.base)}/new">New job</a>{theme}'
            f'<form method="post" action="{e(cfg.base)}/logout"><input type="hidden" name="csrf" value="{csrf}"><button>Sign out</button></form>'
            '</div></details></nav>')


def site_file(cfg, path):
    """The file of the site for a URL path, or None: nothing outside the site, and no links out of it."""
    if not cfg.site or "\0" in path:
        return None
    root = os.path.realpath(cfg.site)
    target = os.path.realpath(os.path.join(root, urllib.parse.unquote(path).lstrip("/")))
    if target != root and not target.startswith(root + os.sep):
        return None
    if os.path.isdir(target):
        target = os.path.join(target, "index.html")
    return target if os.path.isfile(target) else None


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


def shell(cfg, login, title, body, message=""):
    """Every page of the app: the banner of the reports, with the app's links and the signed-in user."""
    e, r = html.escape, html.escape(cfg.reports)
    note = f'<div class="alert alert-warning">{e(message)}</div>' if message else ""
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)}: MadSuite benchmarks</title><script src="{r}/theme.js"></script>'
            f'<link rel="stylesheet" href="{r}/bootstrap.min.css"><link rel="stylesheet" href="{r}/style.css"><body>'
            f'<header class="site"><div class="container"><a class="brand" href="{e(cfg.base)}/">MadSuite benchmarks</a>'
            f'{app_nav(cfg, login)}</div></header>'
            f'<main class="container py-3">{note}{body}</main></body></html>')


def signin(cfg, message=""):
    body = ('<div class="card mx-auto mt-4" style="max-width:32em"><div class="card-body"><h1 class="h4">Sign in</h1>'
            '<p>Sign in with GitHub to see the benchmark jobs and start new ones. Anyone with write access to '
            f'{html.escape(cfg.repo)} can. The <a href="{html.escape(cfg.reports)}/">reports</a> are public.</p>'
            f'<a class="btn btn-primary" href="{html.escape(cfg.base)}/login">Sign in with GitHub</a></div></div>')
    return shell(cfg, None, "Sign in", body, message)


STATE_BADGE = {"running": "primary", "waiting": "secondary", "finished": "success", "failed": "danger", "cancelled": "warning", "skipped": "light"}


def badge(j, cfg=None):
    if cfg is not None and j["state"] == "running" and j.get("sha") and stalled(cfg, folder(j)) is not None:
        return '<span class="badge text-bg-warning">stalled</span>'
    state = "cancelling" if j.get("cancel") and j["state"] == "running" else j["state"]
    return f'<span class="badge text-bg-{STATE_BADGE.get(j["state"], "secondary")}">{html.escape(state)}</span>'


def name(cfg, j):
    return {"pr": f'#{j["pr"]}', "branch": j.get("ref", ""), "commit": (j.get("sha") or j.get("ref", ""))[:8]}.get(j["kind"], "master")


def when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) if t else ""


def actions(cfg, q, login, j):
    """The buttons a signed-in user has on a job: update a pull request, cancel what they may."""
    e, csrf = html.escape, sign(cfg, "csrf|" + login)
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
    out = ""
    if j["kind"] == "pr" and not j.get("cancel"):
        out += (f'<form method="post" action="{e(cfg.base)}/update" class="d-inline">{hidden}<input type="hidden" name="pr" value="{j["pr"]}">'
                '<div class="btn-group btn-group-sm" role="group" aria-label="Update to the latest commit">'
                '<button class="btn btn-outline-primary" name="keep" value="1" title="Run the latest commit, keeping the results so far">Update, keep results</button>'
                '<button class="btn btn-outline-primary" name="keep" value="0" title="Run the latest commit, throwing away the results so far">Update, start over</button></div></form> ')
    if j["state"] in ("waiting", "running") and not j.get("cancel") and (j["by"].lower() == login.lower() or q.permission(login) == "admin"):
        out += (f'<form method="post" action="{e(cfg.base)}/cancel" class="d-inline">{hidden}<input type="hidden" name="id" value="{j["id"]}">'
                '<button class="btn btn-sm btn-outline-danger">Cancel</button></form>')
    return out


def recent(q, n=30):
    """Finished jobs, newest first, with only the latest of each pull request, and none of one that
    is queued or running again."""
    active = {(j["kind"], j.get("pr") or j.get("ref")) for j in q.jobs("waiting", "running") if j["kind"] != "master"}
    seen, out = set(), []
    for j in reversed(q.jobs("finished", "failed", "cancelled", "skipped")):
        key = (j["kind"], j.get("pr") or j.get("ref") or j.get("sha"))
        if key in seen or key in active:
            continue
        seen.add(key)
        out.append(j)
    return out[:n]


WORKER = re.compile(r"worker (\d+): (\S+) (\S*) for (\d+) s, ([\d.]+) GB")


def log_tail(path, lines=80, limit=1 << 20):
    """The end of a job's log as safe text: terminal colours and carriage-return redraws taken out."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            text = f.read().decode(errors="replace")
    except OSError:
        return None
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    return "\n".join(text.rstrip("\n").split("\n")[-lines:])


def workers_page(cfg, login, name):
    """What every worker of a run is on, read from its live folder when asked."""
    e = html.escape
    root = os.path.join(cfg.live_local, name)
    try:
        state = json.load(open(os.path.join(root, "run.json"))).get("state", "")
    except (OSError, ValueError, AttributeError):
        state = ""
    age = stalled(cfg, name)
    if state != "running":
        note = f'<div class="alert alert-secondary">This run is not running: it {e(state or "has no state")}. The workers below are as it left them.</div>'
    elif age is not None:
        note = f'<div class="alert alert-warning">This run is stalled: no new data for {int(age // 60)} min. The workers below are as it left them.</div>'
    else:
        note = ""
    body = f'<h1>Workers</h1><p class="m">Read live from the run, every few seconds. <a href="./">Back to the report</a></p>{note}'
    for machine in sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))):
        try:
            text = open(os.path.join(root, machine, "summary.md")).read()
        except OSError:
            continue
        m = re.match(r"^```\n(.*?)\n```", text, re.S)
        if not m:
            continue
        age = int(time.time() - os.stat(os.path.join(root, machine, "summary.md")).st_mtime)
        rows, notes = [], []
        for line in m[1].split("\n"):
            w = WORKER.search(line)
            if w:
                rows.append("<tr>" + "".join(f'<td{" class=l" if i in (1, 2) else ""}>{e(x)}</td>' for i, x in enumerate(w.groups())) + "</tr>")
            elif line.strip():
                notes.append(f'<p class="m">{e(line.strip())}</p>')
        body += (f'<h2>{e(machine.replace("_", " "))}</h2><p class="m">updated {age} s ago</p>' + "".join(notes)
                 + ('<div class="wide"><table class="table table-sm"><thead><tr><th>worker</th><th class="l">state</th><th class="l">instance</th>'
                    '<th>for (s)</th><th>memory (GB)</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>" if rows else ""))
    # run.jl's own log once the run has one, else the job's, which also has the setup before it
    for log, what in (("run.log", "the run's output: precompilation and the workers"), ("job.log", "the job's output: setup and the run")):
        tail = log_tail(os.path.join(root, log))
        if tail is not None:
            body += f'<h2>Log</h2><p class="m">The last lines of {what}.</p><pre class="log">{e(tail)}</pre>'
            break
    page = shell(cfg, login, f"Workers of {name}", body)
    # the page takes itself again, as the reports do
    return page.replace("<body>", '<body><meta name="live" content="1"><div id="live">', 1).replace("</main></body>", f'</main></div><script src="{e(cfg.reports)}/report.js"></script></body>', 1)


def jobs_section(cfg, q):
    """The jobs: what runs and waits, and what ran lately. On the dashboard, above the reports."""
    e = html.escape
    def row(j):
        report = f'<a href="{e(cfg.reports)}/{folder(j)}/">report</a>' if j.get("sha") and j["state"] != "waiting" else ""
        return (f'<tr><td><a href="{e(cfg.base)}/job/{j["id"]}">{e(name(cfg, j))}</a></td><td class="l">{e(j.get("title", ""))}</td>'
                f'<td class="l">{badge(j, cfg)}</td><td class="l">{e(j["by"])}</td><td>{when(j.get("ended") or j.get("started") or j["queued"])}</td>'
                f'<td class="l">{report}</td></tr>')
    def table(title, jobs, empty):
        head = ('<div class="wide"><table class="table table-sm"><thead><tr><th>run</th><th class="l">title</th><th class="l">state</th>'
                '<th class="l">by</th><th>time (UTC)</th><th class="l"></th></tr></thead><tbody>')
        return f'<h2>{title}</h2>' + (head + "".join(row(j) for j in jobs) + "</tbody></table></div>" if jobs else f'<p class="m">{empty}</p>')
    queue = q.jobs("running") + sorted(q.jobs("waiting"), key=order)
    bot = e(cfg.bots[0]) if cfg.bots else ""
    return (f'<div class="d-flex align-items-center gap-3 mt-3"><h2 class="me-auto my-0">Jobs</h2>'
            f'<a class="btn btn-primary" href="{e(cfg.base)}/new">New job</a></div>'
            '<p class="m">One job runs at a time: updates first, then master, then in the order queued. Master runs when someone queues it. '
            f'A comment <code>@{bot} benchmark</code> on a pull request also queues it.</p>'
            + table("Queue", queue, "Nothing is running or waiting.").replace("<h2>", "<h3>").replace("</h2>", "</h3>")
            + table("Recent jobs", recent(q), "No jobs yet.").replace("<h2>", "<h3>").replace("</h2>", "</h3>"))


def jobs_page(cfg, q, login, message=""):
    body = jobs_section(cfg, q) + f'<p class="mt-3"><a href="{html.escape(cfg.reports)}/">All reports</a></p>'
    return shell(cfg, login, "Jobs", body, message)


def job_page(cfg, q, login, j):
    e = html.escape
    link = (f'https://github.com/{cfg.repo}/tree/{urllib.parse.quote(j.get("ref", ""))}' if j["kind"] == "branch" else
            f'https://github.com/{cfg.repo}/commit/{j.get("sha") or ""}' if j["kind"] == "commit" else
            f'https://github.com/{cfg.repo}/pull/{j["pr"]}' if j["kind"] == "pr" else f'https://github.com/{cfg.repo}/commits/master')
    rows = [("Run", f'<a href="{link}">{e(name(cfg, j))}</a> {e(j.get("title", ""))}'), ("State", badge(j, cfg) + (f' {e(j["note"])}' if j.get("note") else "")),
            ("Commit", f'<a href="https://github.com/{cfg.repo}/commit/{j["sha"]}"><code>{j["sha"][:8]}</code></a>' if j.get("sha") else "resolved when it starts"),
            ("Queued by", e(j["by"])), ("Queued", when(j["queued"])), ("Started", when(j.get("started"))), ("Ended", when(j.get("ended"))),
            ("Results", "keep the results so far" if j.get("keep", True) else "start over")]
    st = settings_of(j)
    if st != DEFAULTS:
        rows.append(("Settings", e(", ".join(f"{k} {v}" for k, v in st.items() if v != DEFAULTS[k]))))
    if j.get("sha") and j["state"] != "waiting":
        rows.append(("Report", f'<a href="{e(cfg.reports)}/{folder(j)}/">{e(cfg.reports)}/{folder(j)}/</a>'))
    table = "".join(f'<tr><th class="l">{k}</th><td class="l">{v}</td></tr>' for k, v in rows)
    body = (f'<h1>Job {j["id"]}: {e(name(cfg, j))}</h1><div class="wide"><table class="table table-sm"><tbody>{table}</tbody></table></div>'
            f'<div class="mt-2">{actions(cfg, q, login, j)}</div><p class="mt-3"><a href="{e(cfg.base)}/">All jobs</a></p>')
    return shell(cfg, login, f"Job {j['id']}", body)


def configs_list(cfg):
    """The configurations the harness has, as suggestions for the field."""
    try:
        import tomllib
        known = tomllib.load(open(cfg.harness_configs, "rb"))
        names = sorted(set().union(*(set(c.get("names", [])) for c in known.get("configurations", {}).values())))
    except (OSError, ValueError, TypeError):
        names = ["default"]
    return '<datalist id="configs">' + "".join(f'<option value="{html.escape(n)}">' for n in names) + "</datalist>"


def new_page(cfg, q, login, message=""):
    e = html.escape
    csrf = sign(cfg, "csrf|" + login)
    options = '<option value="master">master, at its latest commit</option><option value="ref">A branch or a commit…</option>'
    for p in q.open_pulls():
        options += f'<option value="{p["number"]}">#{p["number"]} {e(p["title"])} ({e(p["branch"])})</option>'
    body = (f'<h1>New job</h1><form method="post" action="{e(cfg.base)}/new" class="card"><div class="card-body">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<label class="form-label" for="what">What to benchmark</label><select class="form-select mb-2" id="what" name="what">{options}</select>'
            '<input class="form-control mb-3" name="ref" placeholder="Branch name, or a commit in master\'s history" '
            'aria-label="Branch or commit, if chosen above">'
            '<details class="mb-3"><summary>Settings: configuration, options, harness, devices</summary><div class="mt-2">'
            '<label class="form-label" for="config">Configuration</label>'
            f'<input class="form-control mb-2" id="config" name="config" value="default" list="configs">{configs_list(cfg)}'
            '<label class="form-label" for="options">MadNLP options, on top of the configuration</label>'
            '<input class="form-control mb-2" id="options" name="options" placeholder="tol=1e-6,max_iter=500">'
            '<label class="form-label" for="harness">Harness: a branch of MadNLPBenchmark.jl, for an experiment</label>'
            '<input class="form-control mb-2" id="harness" name="harness" placeholder="main, as deployed">'
            '<label class="form-label" for="devices">Devices</label><select class="form-select mb-2" id="devices" name="devices">'
            '<option value="cpu,cuda">CPU and GPU</option><option value="cpu">CPU only</option><option value="cuda">GPU only</option></select>'
            '<p class="m">A job with other settings than these defaults gets a report of its own, never mixed with the standard runs. '
            'A run of master with other settings runs as a run of its commit.</p></div></details>'
            '<fieldset class="mb-3"><legend class="form-label fs-6">If a report of this pull request exists</legend>'
            '<div class="form-check"><input class="form-check-input" type="radio" name="keep" id="keep1" value="1" checked>'
            '<label class="form-check-label" for="keep1">Keep its results, and solve only what is missing</label></div>'
            '<div class="form-check"><input class="form-check-input" type="radio" name="keep" id="keep0" value="0">'
            '<label class="form-check-label" for="keep0">Throw them away and start over</label></div></fieldset>'
            '<p class="m">A pull request or branch runs its latest commit when the job starts, with the sets, configurations and limits '
            'of the benchmark. Only pull requests and branches of the repository can run, never a fork.</p>'
            '<button class="btn btn-primary">Queue the job</button></div></form>')
    return shell(cfg, login, "New job", body, message)


def handler(cfg, q):
    tokens = {}   # SHA-256 of a GitHub token => (login, expiry)
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
            if cfg.remote:
                return False   # this app runs the jobs itself, so no runner may take one
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return bool(cfg.runner_hash) and hmac.compare_digest(hashlib.sha256(got.encode()).hexdigest(), cfg.runner_hash)

        def api_login(self):
            """Who calls the API: a GitHub token in Authorization, else the app's session cookie."""
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer ") or auth.startswith("token "):
                token = auth.split(" ", 1)[1].strip()
                key = hashlib.sha256(token.encode()).hexdigest()
                cached = tokens.get(key)
                if cached and cached[1] > time.time():
                    return cached[0]
                try:
                    login = q.gh.login_of(token)
                except (urllib.error.HTTPError, urllib.error.URLError):
                    login = None
                tokens[key] = (login, time.time() + 300)
                return login
            return session(cfg, self.cookie("s"))

        def api(self, method, path, body):
            reply = lambda code, data: self.send(code, json.dumps(data), kind="application/json")
            login = self.api_login()
            if not login:
                return reply(401, {"error": "Sign in, or send a GitHub token as Authorization: Bearer <token>."})
            permission = q.permission(login)
            if path == "/queue/api/me":
                return reply(200, {"login": login, "permission": permission, "can_run": permission in WRITE})
            if permission not in WRITE:
                return reply(403, {"error": f"{login} has no write access to {cfg.repo}."})
            if method == "POST" and self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                # a form on another site cannot send this without the browser asking first
                return reply(415, {"error": "Send JSON, with Content-Type: application/json."})
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                return reply(400, {"error": "The body is not JSON."})
            parts = path[len("/queue/api/"):].strip("/").split("/")
            find = lambda i: next((j for j in q.data["jobs"] if i.isdigit() and j["id"] == int(i)), None)
            if parts == ["jobs"] and method == "GET":
                jobs = q.jobs("running") + sorted(q.jobs("waiting"), key=order) + list(reversed(q.jobs("finished", "failed", "cancelled", "skipped")))
                return reply(200, {"jobs": [public(cfg, j) for j in jobs]})
            if parts == ["jobs"] and method == "POST":
                keep = bool(data.get("keep", True))
                settings = {k: str(data.get(k) or DEFAULTS[k]) for k in DEFAULTS}
                problem = settings_problem(cfg, settings)
                if problem:
                    return reply(400, {"error": problem})
                if data.get("master"):
                    try:
                        return reply(201, public(cfg, q.add_master_now(login, settings)))
                    except ValueError as err:
                        return reply(400, {"error": str(err)})
                if isinstance(data.get("branch") or data.get("commit"), str):
                    job, problem = q.add_ref(data.get("branch") or data.get("commit"), login, keep, settings)
                    return reply(400, {"error": problem}) if problem else reply(201, public(cfg, job))
                pr = data.get("pr")
                if not isinstance(pr, int) or pr <= 0:
                    return reply(400, {"error": 'Give {"pr": <number>}, {"branch": "<name>"}, {"commit": "<sha>"} or {"master": true}.'})
                problem = q.add_pr(pr, login, keep, settings)
                if problem:
                    return reply(400, {"error": problem})
                return reply(201, public(cfg, [j for j in q.jobs("waiting") if j.get("pr") == pr][-1]))
            if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
                j = find(parts[1])
                return reply(200, public(cfg, j)) if j else reply(404, {"error": "There is no such job."})
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and method == "POST":
                j = find(parts[1])
                if j is None:
                    return reply(404, {"error": "There is no such job."})
                return reply(200, public(cfg, j)) if q.cancel(j["id"], login) else reply(403, {"error": "That job cannot be cancelled by you."})
            if parts == ["pulls"] and method == "GET":
                return reply(200, {"pulls": q.open_pulls()})
            if len(parts) == 3 and parts[0] == "pulls" and parts[2] == "update" and method == "POST" and parts[1].isdigit():
                problem = q.update(int(parts[1]), login, bool(data.get("keep", True)))
                return reply(400, {"error": problem}) if problem else reply(200, {"jobs": [public(cfg, j) for j in q.jobs("waiting", "running") if j.get("pr") == int(parts[1])]})
            return reply(404, {"error": "No such endpoint."})

        def report_page(self, path, login):
            member_of = lambda who: bool(who) and q.permission(who) in WRITE
            """A page or file of the reports. A report's own banner gets the links of the signed-in user."""
            if path == "/":
                return self.redirect(cfg.reports + "/")
            m = re.fullmatch(re.escape(urllib.parse.urlsplit(cfg.reports).path) + r"/(pr-\d+|master-[0-9a-f]{8}|run-[a-z0-9-]+)/workers\.html", path)
            if m and cfg.live_local and os.path.isdir(os.path.join(cfg.live_local, m[1])):
                return self.send(200, workers_page(cfg, login, m[1]))
            target = site_file(cfg, path)
            if target is None:
                return self.send(404, "Not found", kind="text/plain")
            if os.path.isdir(os.path.realpath(os.path.join(cfg.site, urllib.parse.unquote(path).lstrip("/")))) and not path.endswith("/"):
                return self.redirect(path + "/")
            st = os.stat(target)
            kind = mimetypes.guess_type(target)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
                kind += "; charset=utf-8"
            # a page differs by who is signed in, so its tag does too
            tag = '"%x-%x-%s"' % (st.st_mtime_ns, st.st_size, hashlib.sha256(f"{login}|{int(time.time() // 60)}".encode()).hexdigest()[:8] if target.endswith(".html") else "")
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = open(target, "rb").read()
            if target.endswith(".html"):
                data = data.replace(NAV.encode(), app_nav(cfg, login).encode(), 1)
                if member_of(login) and os.path.realpath(target) == os.path.realpath(os.path.join(cfg.site, urllib.parse.urlsplit(cfg.reports).path.strip("/"), "index.html")):
                    # one dashboard: the jobs above the reports
                    data = data.replace(b'<main class="container">', b'<main class="container">' + jobs_section(cfg, q).encode(), 1)
                parts = path.strip("/").split("/")
                age = stalled(cfg, parts[1]) if len(parts) > 1 else None
                if age is not None:
                    member = bool(login) and q.permission(login) in WRITE
                    data = data.replace(b"</header>", b"</header>" + stall_note(cfg, login if member else None, parts[1], age).encode(), 1)
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", tag)
            self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
            self.send_header("Cache-Control", "private, no-cache" if target.endswith(".html") else "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            args = urllib.parse.parse_qs(query)
            if path.startswith("/queue/api/"):
                return self.api("GET", path, b"")
            login = session(cfg, self.cookie("s"))
            member = bool(login) and q.permission(login) in WRITE
            if path in ("/queue", "/queue/", "/queue/new") or path.startswith("/queue/job/"):
                # only the reports are public: every page of the app needs someone who may run jobs
                if not login:
                    return self.send(200, signin(cfg))
                if not member:
                    return self.send(403, shell(cfg, login, "No access", f"<p>{html.escape(login)} has no write access to {html.escape(cfg.repo)}, so cannot see or start jobs.</p>"))
                if path == "/queue/new":
                    return self.send(200, new_page(cfg, q, login))
                if path.startswith("/queue/job/"):
                    job_id = path[len("/queue/job/"):]
                    j = next((j for j in q.data["jobs"] if job_id.isdigit() and j["id"] == int(job_id)), None)
                    return self.send(200, job_page(cfg, q, login, j)) if j else self.send(404, jobs_page(cfg, q, login, "There is no such job."))
                return self.redirect(cfg.reports + "/") if cfg.site else self.send(200, jobs_page(cfg, q, login))
            if path == "/queue/login":
                state = secrets.token_urlsafe(16)
                url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(
                    {"client_id": cfg.client_id, "redirect_uri": cfg.base + "/callback", "state": state, "allow_signup": "false"})
                return self.redirect(url, [("Set-Cookie", f"o={state}; Path=/queue; HttpOnly; Secure; SameSite=Lax; Max-Age=600")])
            if path == "/queue/callback":
                state = args.get("state", [""])[0]
                if not state or not hmac.compare_digest(state, self.cookie("o")):
                    return self.send(400, signin(cfg, "The sign-in did not match. Please try again."))
                try:
                    login = q.gh.user(args.get("code", [""])[0])
                except urllib.error.HTTPError:
                    login = None
                if not login:
                    return self.send(400, signin(cfg, "GitHub did not confirm the sign-in. Please try again."))
                return self.redirect(cfg.base + "/", [("Set-Cookie", f"s={new_session(cfg, login)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={14 * 86400}")])
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
            return self.report_page(path, login)

        def do_POST(self):
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1 << 20))
            if self.path.startswith("/queue/api/"):
                return self.api("POST", self.path.partition("?")[0], body)
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
                elif event == "push" and cfg.auto_master and data.get("ref") == "refs/heads/master" and not data.get("deleted"):
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
                return self.send(403, signin(cfg, "Please sign in again."))
            if self.path == "/queue/logout":
                return self.redirect(cfg.base + "/", [("Set-Cookie", "s=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0")])
            if q.permission(login) not in WRITE:
                return self.send(403, shell(cfg, login, "No access", "<p>Only people with write access to the repository can do that.</p>"))
            if self.path == "/queue/add":
                pr = get("pr").strip().lstrip("#")
                if not pr.isdigit() or len(pr) > 7:
                    return self.send(400, jobs_page(cfg, q, login, "Give the number of a pull request."))
                problem = q.add_pr(int(pr), login)
                return self.send(400, jobs_page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
            if self.path == "/queue/new":
                what, keep = get("what"), get("keep") != "0"
                settings = {k: get(k).strip() or DEFAULTS[k] for k in DEFAULTS}
                problem = settings_problem(cfg, settings)
                if problem:
                    return self.send(400, new_page(cfg, q, login, problem))
                if what == "master":
                    try:
                        q.add_master_now(login, settings)
                    except ValueError as err:
                        return self.send(400, new_page(cfg, q, login, str(err)))
                    return self.redirect(cfg.base + "/")
                if what == "ref":
                    _, problem = q.add_ref(get("ref"), login, keep, settings)
                    return self.send(400, new_page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
                if not what.isdigit():
                    return self.send(400, new_page(cfg, q, login, "Choose what to benchmark."))
                problem = q.add_pr(int(what), login, keep, settings)
                return self.send(400, new_page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
            if self.path == "/queue/update":
                pr = get("pr")
                if not pr.isdigit():
                    return self.send(400, jobs_page(cfg, q, login, "Give the number of a pull request."))
                problem = q.update(int(pr), login, get("keep") != "0")
                return self.send(400, jobs_page(cfg, q, login, problem)) if problem else self.redirect(cfg.base + "/")
            if self.path == "/queue/cancel":
                job_id = get("id")
                if not job_id.isdigit() or not q.cancel(int(job_id), login):
                    return self.send(403, jobs_page(cfg, q, login, "That job cannot be cancelled by you."))
                return self.redirect(cfg.base + "/")
            return self.send(404, "Not found", kind="text/plain")

    return H


if __name__ == "__main__":
    cfg = Config()
    q = Queue(cfg, GitHub(cfg))
    if cfg.remote:
        threading.Thread(target=Dispatcher(cfg, q).serve, daemon=True).start()
    if cfg.builder:
        threading.Thread(target=Builder(cfg).serve, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", cfg.port), handler(cfg, q))
    server.daemon_threads = True
    server.serve_forever()
