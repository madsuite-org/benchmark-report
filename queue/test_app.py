"""Tests of the run queue, with GitHub replaced by a fake.

    python3 -m unittest discover -s queue
"""

import hashlib, hmac, http.client, http.server, json, os, re, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import app

REPO = "o/r"


class FakeGitHub:
    def __init__(self):
        self.pulls = {}            # number => (state, head repo, sha, title)
        self.perms = {"alice": "write", "boss": "admin", "eve": "read"}
        self.statuses = []
        self.users = {"code-alice": "alice", "code-eve": "eve"}
        self.branches = {"feature/x": "b" * 40}
        self.commits = {"c0ffee1": ("c0ffee1" + "0" * 33, "behind"), "f0f0f0f": ("f0f0f0f" + "0" * 33, "diverged")}

    def api(self, method, path, body=None):
        if path.startswith(f"/repos/{REPO}/pulls/"):
            n = int(path.rsplit("/", 1)[1])
            if n not in self.pulls:
                raise urllib.error.HTTPError(path, 404, "", {}, None)
            state, head, sha, title = self.pulls[n]
            return {"state": state, "head": {"repo": {"full_name": head}, "sha": sha}, "title": title}
        if path.startswith(f"/repos/{REPO}/branches/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if name not in self.branches:
                raise urllib.error.HTTPError(path, 404, "", {}, None)
            return {"commit": {"sha": self.branches[name]}}
        if path.startswith(f"/repos/{REPO}/compare/"):
            ref = path.split("/compare/")[1].split("...")[0]
            if ref not in self.commits:
                raise urllib.error.HTTPError(path, 404, "", {}, None)
            sha, status = self.commits[ref]
            return {"status": status, "base_commit": {"sha": sha}}
        if "/collaborators/" in path:
            return {"permission": self.perms.get(path.split("/")[-2], "none")}
        if path.startswith(f"/repos/{REPO}/pulls?"):
            return getattr(self, "open", [])
        if path == f"/repos/{REPO}/commits/master":
            return {"sha": "9" * 40}
        if "/statuses/" in path:
            self.statuses.append((path.rsplit("/", 1)[1], body["state"], body["target_url"]))
            return {}
        raise AssertionError(path)

    def user(self, code):
        return self.users.get(code)

    def login_of(self, token):
        if token not in {"tok-alice": 1, "tok-eve": 1}:
            raise urllib.error.HTTPError("/user", 401, "", {}, None)
        return token[4:]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        env = {"QUEUE_REPO": REPO, "QUEUE_STATE": os.path.join(self.dir, "q.json"), "QUEUE_HOLD": "0.5",
               "QUEUE_BASE": "https://x/queue", "QUEUE_REPORTS": "https://x/benchmark"}
        for name, value in (("WEBHOOK_SECRET_FILE", "hooksecret"), ("RUNNER_TOKEN_HASH_FILE", hashlib.sha256(b"runtok").hexdigest()),
                            ("SESSION_KEY_FILE", "k" * 32)):
            path = os.path.join(self.dir, name)
            open(path, "w").write(value)
            env[name] = path
        self.cfg = app.Config(env)
        self.gh = FakeGitHub()
        self.gh.pulls = {7: ("open", REPO, "a" * 40, "Seven"), 8: ("open", "fork/r", "b" * 40, "Fork"), 9: ("closed", REPO, "c" * 40, "Shut")}
        self.q = app.Queue(self.cfg, self.gh)


class TestQueue(Base):
    def test_only_open_branch_pull_requests(self):
        self.assertIsNone(self.q.add_pr(7, "alice"))
        self.assertIn("branches", self.q.add_pr(8, "alice"))
        self.assertIn("not open", self.q.add_pr(9, "alice"))
        self.assertIn("no such", self.q.add_pr(99, "alice"))
        self.assertIn("already", self.q.add_pr(7, "alice"))

    def test_master_first_and_head_at_start(self):
        self.q.add_pr(7, "alice")
        self.q.add_master("d" * 40)
        self.q.add_master("e" * 40)       # the newest master replaces the waiting one
        self.assertEqual(len(self.q.jobs("waiting")), 2)
        job = self.q.take()
        self.assertEqual((job["kind"], job["sha"]), ("master", "e" * 40))
        self.assertIs(self.q.take(), job)  # one at a time: the running job again
        self.assertTrue(self.q.report(job["id"], "finished"))
        self.gh.pulls[7] = ("open", REPO, "f" * 40, "Seven")   # pushed while it waited
        job = self.q.take()
        self.assertEqual((job["pr"], job["sha"]), (7, "f" * 40))
        self.assertEqual(self.gh.statuses[-1], ("f" * 40, "pending", "https://x/benchmark/pr-7/"))

    def test_skips_a_pull_request_closed_while_waiting(self):
        self.q.add_pr(7, "alice")
        self.gh.pulls[7] = ("closed", REPO, "a" * 40, "Seven")
        self.assertIsNone(self.q.take())
        self.assertEqual(self.q.jobs("skipped")[0]["pr"], 7)

    def test_cancel(self):
        self.q.add_pr(7, "alice")
        job = self.q.jobs("waiting")[0]
        self.assertFalse(self.q.cancel(job["id"], "eve"))
        self.assertTrue(self.q.cancel(job["id"], "boss"))
        self.assertEqual(job["state"], "cancelled")
        self.q.add_master("d" * 40)
        running = self.q.take()
        self.assertTrue(self.q.cancel(running["id"], "boss"))
        self.assertEqual(running["state"], "running")   # the runner stops it
        self.assertTrue(running["cancel"])

    def test_update(self):
        self.q.add_master("d" * 40)
        self.q.add_pr(7, "alice")
        waiting = self.q.jobs("waiting")[1]
        self.assertIsNone(self.q.update(7, "alice", keep=False))      # waiting: only the choice changes
        self.assertEqual((len(self.q.jobs("waiting")), waiting["keep"]), (2, False))
        self.q.take()                                                 # master runs
        self.q.report(self.q.jobs("running")[0]["id"], "finished")
        running = self.q.take()
        self.assertEqual(running["pr"], 7)
        self.q.add_master("e" * 40)
        self.gh.pulls[7] = ("open", REPO, "f" * 40, "Seven")
        self.assertIsNone(self.q.update(7, "alice", keep=True))       # running: stopped, and the update goes first
        self.assertTrue(running["cancel"])
        self.q.report(running["id"], "cancelled")
        nxt = self.q.take()
        self.assertEqual((nxt["pr"], nxt["sha"], nxt["keep"]), (7, "f" * 40, True))
        self.assertIn("not open", self.q.update(9, "alice", keep=True))

    def test_recent_shows_a_pull_request_once(self):
        self.q.add_pr(7, "alice")
        first = self.q.take(); self.q.report(first["id"], "failed")
        self.q.add_pr(7, "alice")
        second = self.q.take(); self.q.report(second["id"], "finished")
        self.assertEqual([j["id"] for j in app.recent(self.q)], [second["id"]])
        self.q.add_pr(7, "alice")           # queued again: not in recent at all
        self.assertEqual(app.recent(self.q), [])

    def test_branches_and_commits(self):
        job, problem = self.q.add_ref("feature/x", "alice")
        self.assertIsNone(problem)
        self.assertEqual((job["kind"], job["sha"], app.folder(job)), ("branch", None, "run-feature-x"))
        self.assertIsNotNone(self.q.add_ref("feature/x", "alice")[1])        # already queued
        job2, problem = self.q.add_ref("c0ffee1", "alice")
        self.assertEqual((job2["kind"], job2["sha"][:7], app.folder(job2)), ("commit", "c0ffee1", "run-c0ffee10"))
        self.assertIn("master's history", self.q.add_ref("f0f0f0f", "alice")[1])   # e.g. from a fork
        for bad in ("no-such-branch", "../x", "a b", ""):
            self.assertIsNotNone(self.q.add_ref(bad, "alice")[1], bad)
        self.gh.branches["feature/x"] = "e" * 40                                # pushed while waiting
        started = self.q.take()
        self.assertEqual((started["kind"], started["sha"]), ("branch", "e" * 40))

    def test_settings(self):
        bad = [{"config": "a;b"}, {"devices": "gpu"}, {"harness": "../x"}, {"harness": "-x"}, {"options": "tol='1'"},
               {"options": "tol=1,tol=2"}, {"options": "tol"}, {"options": "x" * 301}]
        for b in bad:
            self.assertIsNotNone(app.settings_problem(self.cfg, {**app.DEFAULTS, **b}), b)
        self.assertIsNone(app.settings_problem(self.cfg, {**app.DEFAULTS, "options": "tol=1e-6,max_iter=5", "harness": "exp/x", "devices": "cpu"}))
        # with the harness's list: names checked, unless a harness branch may bring its own
        path = os.path.join(self.dir, "configs.toml")
        open(path, "w").write('[configurations.cpu]\nnames = ["default", "ipopt"]\n[options]\nmadnlp = ["tol"]\n[options.linear_solver]\nMumpsSolver = ["mumps_pivtol"]\n')
        self.cfg.harness_configs = path
        self.assertIsNone(app.settings_problem(self.cfg, {**app.DEFAULTS, "config": "ipopt", "options": "tol=1e-6,mumps_pivtol=0.1"}))
        self.assertIn("no configuration", app.settings_problem(self.cfg, {**app.DEFAULTS, "config": "nope"}))
        self.assertIn("Unknown options: linear_solver", app.settings_problem(self.cfg, {**app.DEFAULTS, "options": "linear_solver=X"}))
        self.assertIsNone(app.settings_problem(self.cfg, {**app.DEFAULTS, "options": "linear_solver=X", "harness": "exp"}))

    def test_jobs_with_settings_get_reports_of_their_own(self):
        self.q.add_pr(7, "alice")
        self.assertIsNone(self.q.add_pr(7, "alice", settings={"options": "tol=1e-6"}))   # another job, not a duplicate
        plain, custom = self.q.jobs("waiting")
        self.assertEqual(app.folder({**plain, "sha": "a" * 40}), "pr-7")
        f = app.folder({**custom, "sha": "a" * 40})
        self.assertTrue(re.fullmatch(r"run-custom-[0-9a-f]{6}-pr7", f), f)
        job, problem = self.q.add_ref("feature/x", "alice", settings={"harness": "shifted-cholmod-experiment", "devices": "cpu"})
        self.assertTrue(re.fullmatch(r"run-shifted-cholmod-[0-9a-f]{6}-feature-x", app.folder(job)), app.folder(job))
        m = self.q.add_master_now("alice", {"config": "ipopt", "devices": "cpu"})
        self.assertEqual((m["kind"], app.folder(m)[:4]), ("commit", "run-"))    # never taken for master's results
        with self.assertRaises(ValueError):
            self.q.add_master_now("alice", {"config": "a;b"})

    def test_state_survives_a_restart(self):
        self.q.add_pr(7, "alice")
        again = app.Queue(self.cfg, self.gh)
        self.assertEqual(again.jobs("waiting")[0]["pr"], 7)

    def test_session_expires(self):
        self.assertEqual(app.session(self.cfg, app.new_session(self.cfg, "alice")), "alice")
        self.assertIsNone(app.session(self.cfg, app.new_session(self.cfg, "alice", days=-1)))

    def test_app_token_is_signed_with_the_key(self):
        import base64, subprocess
        key, pub = os.path.join(self.dir, "key.pem"), os.path.join(self.dir, "pub.pem")
        subprocess.run(["openssl", "genrsa", "-out", key, "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
        self.cfg.app_id, self.cfg.app_key = "123", key
        head, claims, sig = app.GitHub(self.cfg).jwt().split(".")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.assertEqual(json.loads(pad(claims))["iss"], "123")
        sigfile = os.path.join(self.dir, "sig")
        open(sigfile, "wb").write(pad(sig))
        check = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sigfile], input=f"{head}.{claims}".encode(), capture_output=True)
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_command(self):
        yes = ["@MadSuiteBenchmarkBot benchmark", "Looks good.\n@madsuitebenchmarkbot   benchmark\n", "@madsuite-benchmark benchmark",
               "@MadSuiteBenchmarkBot[bot] benchmark"]
        no = ["@MadSuiteBenchmarkBot benchmark please", "please @MadSuiteBenchmarkBot benchmark", "@someoneelse benchmark", "", None]
        for body in yes:
            self.assertTrue(app.command(self.cfg, body), body)
        for body in no:
            self.assertFalse(app.command(self.cfg, body), body)


class TestGitHubCall(unittest.TestCase):
    def test_json_bodies_say_so(self):
        seen = {}
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_POST(self):
                seen["type"] = self.headers.get("Content-Type")
                seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            gh = app.GitHub(app.Config({}))
            gh.call("POST", "", {"code": "x"}, url=f"http://127.0.0.1:{server.server_address[1]}/")
            self.assertEqual((seen["type"], json.loads(seen["body"])), ("application/json", {"code": "x"}))
        finally:
            server.shutdown()


class TestDispatcher(Base):
    def setUp(self):
        super().setUp()
        import stat
        self.log = os.path.join(self.dir, "log")
        def script(name, body):
            path = os.path.join(self.dir, name)
            open(path, "w").write("#!/bin/bash\n" + body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            return path
        self.cfg.remote, self.cfg.live = "local", "/live"
        self.cfg.remote_start = [script("start", f'echo "start $@" >> {self.log}; read -t 1 x; [ $? -gt 128 ] && echo stdin-open >> {self.log}; sleep "${{SLEEP:-0}}"; exit "${{RC:-0}}"\n')]
        self.cfg.remote_stop = [script("stop", f'echo "stop $@" >> {self.log}; pkill -f "{self.dir}/start" || true\n')]
        self.d = app.Dispatcher(self.cfg, self.q)

    def test_runs_a_job(self):
        self.q.add_pr(7, "alice", keep=False)
        self.assertEqual(self.d.run(self.q.take()), "finished")
        lines = open(self.log).read().splitlines()
        self.assertEqual(lines, [f"start benchjob-1 /live/pr-7 {'a' * 40} pr 7 fresh", "stdin-open"])
        self.assertEqual(self.q.jobs("finished")[0]["pr"], 7)

    def test_failure_and_cancel(self):
        os.environ["RC"] = "3"
        try:
            self.q.add_master("d" * 40)
            self.assertEqual(self.d.run(self.q.take()), "failed")
        finally:
            del os.environ["RC"]
        os.environ["SLEEP"] = "30"
        try:
            self.q.add_master("e" * 40)
            job = self.q.take()
            threading.Timer(0.5, lambda: self.q.cancel(job["id"], "boss")).start()
            t = time.time()
            self.assertEqual(self.d.run(job), "cancelled")
            self.assertLess(time.time() - t, 15)
        finally:
            del os.environ["SLEEP"]
        self.assertIn("stop benchjob-2", open(self.log).read())

    def test_an_interrupted_job_resumes(self):
        os.environ["SLEEP"] = "30"
        try:
            self.q.add_pr(7, "alice", keep=False)
            job = self.q.take()
            t = threading.Thread(target=lambda: setattr(self, "state", self.d.run(job)))
            t.start()
            deadline = time.time() + 10
            while time.time() < deadline and "start benchjob" not in (open(self.log).read() if os.path.exists(self.log) else ""):
                time.sleep(0.1)
            subprocess.run(["pkill", "-TERM", "-f", f"{self.dir}/start"])     # as when the app's service stops
            t.join(10)
        finally:
            del os.environ["SLEEP"]
        self.assertEqual(self.state, "interrupted")
        self.assertEqual(job["state"], "running")
        self.assertIs(self.q.take(), job)
        self.assertEqual(self.d.run(job), "finished")
        starts = [l for l in open(self.log).read().splitlines() if l.startswith("start")]
        self.assertEqual([l.rsplit(" ", 1)[1] for l in starts], ["fresh", "keep"])   # fresh the first time only

    def test_a_job_with_settings_runs_as_its_branch(self):
        self.q.add_pr(7, "alice", settings={"options": "tol=1e-6", "devices": "cpu"})
        job = self.q.take()
        self.d.run(job)
        start = [l for l in open(self.log).read().splitlines() if l.startswith("start")][0]
        self.assertEqual(start, f"start benchjob-{job['id']} /live/{app.folder(job)} {'a' * 40} branch  keep default tol=1e-6  cpu")
        self.assertTrue(app.folder(job).startswith("run-custom-"))

    def test_ssh_quotes_each_word(self):
        self.cfg.remote = "node"
        cmd = self.d.command(["sudo", "-n", "x", "a b", "$(rm -rf /)"])
        self.assertEqual(cmd[:-1], ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=60", "node"])
        import shlex
        self.assertEqual(shlex.split(cmd[-1]), ["sudo", "-n", "x", "a b", "$(rm -rf /)"])


class TestServer(Base):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(self.cfg, self.q))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()

    def request(self, method, path, body=b"", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def hook(self, event, payload, secret=b"hooksecret"):
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return self.request("POST", "/queue/hook", body, {"X-GitHub-Event": event, "X-Hub-Signature-256": sig, "Content-Length": str(len(body))})

    def comment(self, login, body, number=7, repo=REPO):
        return {"action": "created", "repository": {"full_name": repo}, "issue": {"number": number, "pull_request": {}},
                "comment": {"body": body, "user": {"login": login}}}

    def test_hook_signature(self):
        self.assertEqual(self.hook("ping", {}, secret=b"wrong")[0], 401)
        self.assertEqual(self.hook("ping", {"repository": {"full_name": REPO}})[0], 204)

    def test_comment_queues_only_for_write_access(self):
        self.hook("issue_comment", self.comment("eve", "@MadSuiteBenchmarkBot benchmark"))
        self.assertEqual(self.q.jobs("waiting"), [])
        self.hook("issue_comment", self.comment("alice", "@MadSuiteBenchmarkBot benchmark", repo="other/r"))
        self.assertEqual(self.q.jobs("waiting"), [])
        self.hook("issue_comment", self.comment("alice", "@MadSuiteBenchmarkBot benchmark", number=8))
        self.assertEqual(self.q.jobs("waiting"), [])      # a fork
        self.hook("issue_comment", self.comment("alice", "@MadSuiteBenchmarkBot benchmark"))
        self.assertEqual([j["pr"] for j in self.q.jobs("waiting")], [7])

    def test_push_to_master(self):
        self.hook("push", {"ref": "refs/heads/master", "after": "c" * 40, "repository": {"full_name": REPO}})
        self.assertEqual(self.q.jobs("waiting"), [])        # by default master runs only when queued by hand
        self.cfg.auto_master = True
        self.hook("push", {"ref": "refs/heads/master", "after": "d" * 40, "repository": {"full_name": REPO}})
        self.hook("push", {"ref": "refs/heads/other", "after": "e" * 40, "repository": {"full_name": REPO}})
        self.assertEqual([j["sha"] for j in self.q.jobs("waiting")], ["d" * 40])

    def test_runner(self):
        auth = {"Authorization": "Bearer runtok"}
        self.assertEqual(self.request("GET", "/queue/runner/next", headers={"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.request("GET", "/queue/runner/next", headers=auth)[0], 204)   # held, then nothing
        # a job queued while the runner waits is handed over at once
        threading.Timer(0.2, lambda: self.q.add_master("d" * 40)).start()
        t = time.time()
        code, _, data = self.request("GET", "/queue/runner/next", headers={**auth})
        self.assertEqual(code, 200)
        self.assertLess(time.time() - t, 0.45)
        job = json.loads(data)
        self.assertEqual((job["kind"], job["folder"]), ("master", "master-dddddddd"))
        self.assertEqual(json.loads(self.request("GET", f"/queue/runner/cancel?id={job['id']}", headers=auth)[2]), {"cancel": False})
        self.q.cancel(job["id"], "boss")
        self.assertEqual(json.loads(self.request("GET", f"/queue/runner/cancel?id={job['id']}", headers=auth)[2]), {"cancel": True})
        body = json.dumps({"id": job["id"], "state": "cancelled"}).encode()
        self.assertEqual(self.request("POST", "/queue/runner/status", body, {**auth, "Content-Length": str(len(body))})[0], 204)
        self.assertEqual(self.q.jobs("cancelled")[0]["id"], job["id"])
        self.assertEqual(self.gh.statuses[-1][1], "error")

    def test_web_add_needs_session_csrf_and_write_access(self):
        def post(path, login, fields):
            cookie = f"s={app.new_session(self.cfg, login)}" if login else ""
            body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
            return self.request("POST", path, body, {"Cookie": cookie, "Content-Length": str(len(body)), "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(post("/queue/add", None, {"pr": "7"})[0], 403)
        self.assertEqual(post("/queue/add", "alice", {"pr": "7", "csrf": "bad"})[0], 403)
        self.assertEqual(post("/queue/add", "eve", {"pr": "7", "csrf": app.sign(self.cfg, "csrf|eve")})[0], 403)
        self.assertEqual(post("/queue/add", "alice", {"pr": "7", "csrf": app.sign(self.cfg, "csrf|alice")})[0], 303)
        self.assertEqual([j["pr"] for j in self.q.jobs("waiting")], [7])
        # a forged session is no session
        real = app.new_session(self.cfg, "alice")
        forged = real[:-1] + ("1" if real[-1] == "0" else "0")   # always a different signature
        self.assertIsNone(app.session(self.cfg, forged))

    def get(self, path, login=None):
        headers = {"Cookie": f"s={app.new_session(self.cfg, login)}"} if login else {}
        code, _, data = self.request("GET", path, headers=headers)
        return code, data.decode()

    def test_only_members_see_the_app(self):
        self.gh.pulls[7] = ("open", REPO, "a" * 40, "<script>x</script>")
        self.q.add_pr(7, "alice")
        for path in ("/queue/", "/queue/new", "/queue/job/1"):
            code, page = self.get(path)
            self.assertEqual(code, 200)
            self.assertIn("Sign in with GitHub", page)
            self.assertNotIn("script&gt;x", page)        # nothing of the queue before signing in
            self.assertEqual(self.get(path, "eve")[0], 403)
        code, page = self.get("/queue/", "alice")
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>x", page)
        self.assertIn("New job", page)
        code, page = self.get("/queue/job/1", "alice")
        self.assertIn("Update, start over", page)
        self.assertEqual(self.get("/queue/job/99", "alice")[0], 404)

    def test_new_job(self):
        self.gh.open = [{"number": 7, "title": "Seven", "head": {"ref": "b7", "repo": {"full_name": REPO}}},
                        {"number": 8, "title": "Fork", "head": {"ref": "b8", "repo": {"full_name": "fork/r"}}}]
        page = self.get("/queue/new", "alice")[1]
        self.assertIn("#7 Seven (b7)", page)
        self.assertNotIn("Fork", page)
        def post(fields):
            fields = {"csrf": app.sign(self.cfg, "csrf|alice"), **fields}
            body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
            return self.request("POST", "/queue/new", body, {"Cookie": f"s={app.new_session(self.cfg, 'alice')}", "Content-Length": str(len(body))})
        self.assertEqual(post({"what": "7", "keep": "0"})[0], 303)
        self.assertEqual(post({"what": "master"})[0], 303)
        self.assertEqual(post({"what": "8"})[0], 400)
        jobs = self.q.jobs("waiting")
        self.assertEqual([(j["kind"], j.get("pr"), j["keep"] if j["kind"] == "pr" else None, j["by"]) for j in jobs],
                         [("pr", 7, False, "alice"), ("master", None, None, "alice")])
        self.assertEqual(jobs[1]["sha"], "9" * 40)

    def call_api(self, method, path, data=None, token=None, kind="application/json"):
        body = b"" if data is None else json.dumps(data).encode()
        headers = {"Content-Length": str(len(body)), "Content-Type": kind}
        if token:
            headers["Authorization"] = "Bearer " + token
        code, _, out = self.request(method, path, body, headers)
        return code, json.loads(out)

    def test_api(self):
        self.assertEqual(self.call_api("GET", "/queue/api/jobs")[0], 401)
        self.assertEqual(self.call_api("GET", "/queue/api/jobs", token="forged")[0], 401)
        self.assertEqual(self.call_api("GET", "/queue/api/me", token="tok-eve")[1], {"login": "eve", "permission": "read", "can_run": False})
        self.assertEqual(self.call_api("GET", "/queue/api/jobs", token="tok-eve")[0], 403)
        self.assertEqual(self.call_api("POST", "/queue/api/jobs", {"pr": 7}, token="tok-alice", kind="text/plain")[0], 415)
        code, job = self.call_api("POST", "/queue/api/jobs", {"pr": 7, "keep": False}, token="tok-alice")
        self.assertEqual((code, job["pr"], job["state"], job["keep"], job["by"]), (201, 7, "waiting", False, "alice"))
        self.assertEqual(self.call_api("POST", "/queue/api/jobs", {"pr": 8}, token="tok-alice")[0], 400)   # a fork
        self.assertEqual(self.call_api("POST", "/queue/api/jobs", {"pr": "7"}, token="tok-alice")[0], 400)
        code, master = self.call_api("POST", "/queue/api/jobs", {"master": True}, token="tok-alice")
        self.assertEqual((code, master["sha"]), (201, "9" * 40))
        code, listed = self.call_api("GET", "/queue/api/jobs", token="tok-alice")
        self.assertEqual([j["id"] for j in listed["jobs"]], [master["id"], job["id"]])   # master first
        self.assertEqual(self.call_api("GET", f"/queue/api/jobs/{job['id']}", token="tok-alice")[1]["title"], "Seven")
        self.assertEqual(self.call_api("GET", "/queue/api/jobs/99", token="tok-alice")[0], 404)
        code, updated = self.call_api("POST", "/queue/api/pulls/7/update", {"keep": True}, token="tok-alice")
        self.assertEqual((code, updated["jobs"][0]["keep"]), (200, True))
        self.assertEqual(self.call_api("POST", f"/queue/api/jobs/{job['id']}/cancel", {}, token="tok-alice")[1]["state"], "cancelled")
        self.assertEqual(self.call_api("GET", "/queue/api/nothing", token="tok-alice")[0], 404)
        # the session of the app works too
        headers = {"Cookie": f"s={app.new_session(self.cfg, 'alice')}"}
        code, _, out = self.request("GET", "/queue/api/me", headers=headers)
        self.assertEqual(json.loads(out)["login"], "alice")

    def test_login(self):
        code, headers, _ = self.request("GET", "/queue/login")
        self.assertEqual(code, 303)
        state = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        self.assertEqual(self.request("GET", "/queue/callback?code=code-alice&state=other", headers={"Cookie": f"o={state}"})[0], 400)
        code, headers, _ = self.request("GET", f"/queue/callback?code=code-alice&state={state}", headers={"Cookie": f"o={state}"})
        self.assertEqual(code, 303)
        self.assertIn("Path=/;", headers["Set-Cookie"])     # the reports see who is signed in too
        cookie = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        self.assertEqual(app.session(self.cfg, cookie), "alice")


if __name__ == "__main__":
    unittest.main()


class TestSite(Base):
    """The app serves the reports too, with the banner of who is signed in."""

    def setUp(self):
        super().setUp()
        site = os.path.join(self.dir, "site")
        os.makedirs(os.path.join(site, "madnlp", "pr-1"))
        page = '<html><header class="site">' + app.NAV + '</header><main>report</main></html>'
        for path in ("madnlp/index.html", "madnlp/pr-1/index.html"):
            open(os.path.join(site, path), "w").write(page)
        open(os.path.join(site, "madnlp", "style.css"), "w").write("body{}")
        open(os.path.join(self.dir, "secret"), "w").write("secret")
        os.symlink(os.path.join(self.dir, "secret"), os.path.join(site, "madnlp", "link.css"))
        self.cfg.site, self.cfg.reports = site, "/madnlp"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(self.cfg, self.q))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        data = r.read().decode(errors="replace")
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_pages_and_banner(self):
        code, headers, page = self.get("/madnlp/")
        self.assertEqual(code, 200)
        self.assertIn("report", page)
        self.assertIn("/queue/login", page)
        self.assertNotIn(app.NAV, page)
        code, _, page = self.get("/madnlp/pr-1/", {"Cookie": f"s={app.new_session(self.cfg, 'alice')}"})
        self.assertIn("New job", page)
        self.assertIn("alice", page)
        self.assertEqual(self.get("/madnlp/pr-1")[0], 303)
        self.assertEqual(self.get("/")[1]["Location"], "/madnlp/")
        self.assertEqual(self.get("/madnlp/style.css")[2], "body{}")

    def test_nothing_outside_the_site(self):
        for path in ("/madnlp/../secret", "/madnlp/%2e%2e/%2e%2e/secret", "/madnlp/link.css", "/madnlp/nothing.html", "/..%2fsecret"):
            code, _, body = self.get(path)
            self.assertEqual(code, 404, path)
            self.assertNotIn("secret", body)

    def test_not_modified_and_by_login(self):
        _, headers, _ = self.get("/madnlp/")
        tag = headers["ETag"]
        self.assertEqual(self.get("/madnlp/", {"If-None-Match": tag})[0], 304)
        signed = {"Cookie": f"s={app.new_session(self.cfg, 'alice')}", "If-None-Match": tag}
        self.assertEqual(self.get("/madnlp/", signed)[0], 200)      # another banner, so another page

    def test_stalled_run(self):
        live = os.path.join(self.dir, "live")
        os.makedirs(os.path.join(live, "pr-1"))
        run = os.path.join(live, "pr-1", "run.json")
        open(run, "w").write('{"state":"running"}')
        self.cfg.live_local, self.cfg.stall = live, 600
        self.assertNotIn("stalled", self.get("/madnlp/pr-1/")[2])
        old = time.time() - 1200
        os.utime(run, (old, old))
        page = self.get("/madnlp/pr-1/")[2]
        self.assertIn("stalled", page)
        self.assertIn("Sign in</a> to resume", page)
        page = self.get("/madnlp/pr-1/", {"Cookie": f"s={app.new_session(self.cfg, 'alice')}"})[2]
        self.assertIn(">Resume</button>", page)
        open(run, "w").write('{"state":"finished"}')
        os.utime(run, (old, old))
        self.assertNotIn("stalled", self.get("/madnlp/pr-1/")[2])

    def test_workers_are_read_live(self):
        live = os.path.join(self.dir, "live")
        os.makedirs(os.path.join(live, "pr-1", "Some_GPU"))
        summary = os.path.join(live, "pr-1", "Some_GPU", "summary.md")
        open(summary, "w").write("```\nstatus after 3 min: 5 of 9 instances done\n  worker 2: solving cutest/<b>X for 12 s, 1.5 GB\n```\n")
        self.cfg.live_local = live
        code, _, page = self.get("/madnlp/pr-1/workers.html")
        self.assertEqual(code, 200)
        self.assertIn("5 of 9 instances done", page)
        self.assertIn("cutest/&lt;b&gt;X", page)
        open(summary, "w").write("```\nstatus after 4 min: 6 of 9 instances done\n```\n")
        self.assertIn("6 of 9", self.get("/madnlp/pr-1/workers.html")[2])
        self.assertEqual(self.get("/madnlp/pr-9/workers.html")[0], 404)
        open(os.path.join(live, "pr-1", "job.log"), "w").write("old\n" * 200 + "\x1b[32mPrecompiling\x1b[0m 3/9\r  9/9 <script>x</script>\n")
        page = self.get("/madnlp/pr-1/workers.html")[2]
        self.assertIn("9/9 &lt;script&gt;", page)
        self.assertNotIn("3/9", page)          # the line as the terminal last showed it
        self.assertNotIn("\x1b", page)
        self.assertNotIn("<script>x", page)
        open(os.path.join(live, "pr-1", "run.json"), "w").write('{"state":"failed"}')
        self.assertIn("not running: it failed", self.get("/madnlp/pr-1/workers.html")[2])

    def test_one_dashboard(self):
        os.makedirs(os.path.join(self.cfg.site, "madnlp"), exist_ok=True)
        open(os.path.join(self.cfg.site, "madnlp", "index.html"), "w").write('<html><header class="site">' + app.NAV + '</header><main class="container"><h2>Reports</h2></main></html>')
        self.q.add_pr(7, "alice")
        public = self.get("/madnlp/")[2]
        self.assertNotIn("Recent jobs", public)                 # jobs are for members only
        mine = self.get("/madnlp/", {"Cookie": f"s={app.new_session(self.cfg, 'alice')}"})[2]
        self.assertLess(mine.index("Recent jobs"), mine.index("<h2>Reports</h2>"))
        self.assertIn("Seven", mine)
        code, headers, _ = self.get("/queue/", {"Cookie": f"s={app.new_session(self.cfg, 'alice')}"})
        self.assertEqual((code, headers["Location"]), (303, "/madnlp/"))

    def test_builder_is_started(self):
        mark = os.path.join(self.dir, "built")
        self.cfg.builder = ["sh", "-c", f"echo x >> {mark}; sleep 30"]
        b = app.Builder(self.cfg)
        threading.Thread(target=b.serve, daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.exists(mark):
            time.sleep(0.1)
        self.assertTrue(os.path.exists(mark))
        b.proc.kill()
