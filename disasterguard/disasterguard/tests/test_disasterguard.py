import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["DG_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["OLLAMA_HOST"] = "http://127.0.0.1:1"  # nothing listens -> offline mode first
os.environ["DG_LLM_TIMEOUT"] = "5"

from app import agents, config, db, llm, server  # noqa: E402


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o):
        b = json.dumps(o).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._j({"models": [{"name": "gemma3:4b"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req.get("format") == "json":
            self._j({"response": "```json\n" + json.dumps({"type": "Flood", "severity": 4, "location": "Ramnagar",
                     "people_affected": 40, "needs": ["rescue", "boat", "bogus"], "summary": "Flood in Ramnagar"}) + "\n```"})
        else:
            self._j({"response": "1. Fake Gemma step"})


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class OfflineFallback(unittest.TestCase):
    def setUp(self):
        config.OLLAMA_HOST = "http://127.0.0.1:1"

    def test_status_offline(self):
        self.assertFalse(llm.status()["online"])

    def test_rule_triage(self):
        t = agents.triage("Building collapsed in Warangal, 30 people trapped, two injured children")
        self.assertEqual(t["engine"], "rules")
        self.assertEqual(t["type"], "building_collapse")
        self.assertGreaterEqual(t["severity"], 3)
        self.assertEqual(t["people"], 30)
        self.assertEqual(t["location"], "Warangal")
        self.assertIn("rescue", t["needs"])
        self.assertIn("medical", t["needs"])

    def test_advisor_retrieval(self):
        r = agents.advise("How do I treat severe bleeding?")
        self.assertEqual(r["engine"], "rules")
        self.assertTrue(any("First Aid" in s for s in r["sources"]))
        r = agents.advise("what to do during an earthquake")
        self.assertTrue(any("Earthquake" in s for s in r["sources"]))

    def test_pipeline_and_release(self):
        before = {r["kind"]: r["available"] for r in db.q("SELECT * FROM resources")}
        out = agents.process_incident("Flood in Ramnagar, 40 people stranded, children, no drinking water")
        self.assertTrue(out["allocations"])
        self.assertIn("PRIORITY", out["plan"])
        mid = {r["kind"]: r["available"] for r in db.q("SELECT * FROM resources")}
        self.assertNotEqual(before, mid)
        agents.release(out["id"])
        after = {r["kind"]: r["available"] for r in db.q("SELECT * FROM resources")}
        self.assertEqual(before, after)

    def test_shortfall(self):
        out = agents.process_incident("Flood at Delta, 5000 people homeless and hungry, no food")
        self.assertTrue(out["shortfalls"])
        agents.release(out["id"])

    def test_empty_input(self):
        with self.assertRaises(ValueError):
            agents.process_incident("  ")

    def test_sitrep(self):
        self.assertIn("Situation Report", agents.sitrep()["markdown"])


class WithGemma(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()

    def setUp(self):
        config.OLLAMA_HOST = f"http://127.0.0.1:{self.fake.server_address[1]}"

    def test_status_online(self):
        s = llm.status()
        self.assertTrue(s["online"] and s["model_installed"])

    def test_gemma_triage_normalised(self):
        t = agents.triage("water rising")
        self.assertEqual(t["engine"], "gemma")
        self.assertEqual(t["type"], "flood")
        self.assertEqual(t["severity"], 4)
        self.assertEqual(t["needs"], ["rescue", "boat"])  # 'bogus' filtered

    def test_gemma_plan(self):
        out = agents.process_incident("water rising near Ramnagar")
        self.assertEqual(out["plan_engine"], "gemma")
        agents.release(out["id"])


class HttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.OLLAMA_HOST = "http://127.0.0.1:1"
        cls.srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_static(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"DisasterGuard", r.read())
        with urllib.request.urlopen(self.base + "/app.js") as r:
            self.assertEqual(r.status, 200)

    def test_path_traversal_blocked(self):
        code, _ = call(self.base, "/../../etc/passwd")
        self.assertEqual(code, 404)
        code, _ = call(self.base, "/%2e%2e/%2e%2e/etc/passwd")
        self.assertEqual(code, 404)

    def test_flow(self):
        code, out = call(self.base, "/api/incidents", {"text": "Fire in Hanamkonda market, 10 people injured"})
        self.assertEqual(code, 201)
        iid = out["id"]
        code, inc = call(self.base, f"/api/incidents/{iid}")
        self.assertEqual(code, 200)
        self.assertTrue(inc["assignments"])
        code, _ = call(self.base, f"/api/incidents/{iid}/resolve", {})
        self.assertEqual(code, 200)
        self.assertEqual(call(self.base, f"/api/incidents/{iid}")[1]["status"], "resolved")
        self.assertEqual(call(self.base, "/api/incidents", {"text": ""})[0], 400)
        self.assertEqual(call(self.base, "/api/incidents/9999")[0], 404)
        for p in ("/api/status", "/api/resources", "/api/logs", "/api/stats", "/api/sitrep", "/api/incidents"):
            self.assertEqual(call(self.base, p)[0], 200, p)
        code, a = call(self.base, "/api/advise", {"question": "fire safety"})
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
