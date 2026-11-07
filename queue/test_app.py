"""Tests of the run queue, with GitHub replaced by a fake.

    python3 -m unittest discover -s queue
"""

import hashlib, hmac, http.client, json, os, sys, tempfile, threading, time, unittest, urllib.error
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

    def api(self, method, path, body=None):
        if path.startswith(f"/repos/{REPO}/pulls/"):
            n = int(path.rsplit("/", 1)[1])
            if n not in self.pulls:
                raise urllib.error.HTTPError(path, 404, "", {}, None)
            state, head, sha, title = self.pulls[n]
            return {"state": state, "head": {"repo": {"full_name": head}, "sha": sha}, "title": title}
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
        forged = app.new_session(self.cfg, "alice")[:-1] + "0"
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
        cookie = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        self.assertEqual(app.session(self.cfg, cookie), "alice")


if __name__ == "__main__":
    unittest.main()
