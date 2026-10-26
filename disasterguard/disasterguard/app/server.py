"""DisasterGuard HTTP server (standard library only - no pip install needed)."""
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agents, config, db, llm

STATIC = Path(__file__).resolve().parent / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "DisasterGuard/1.0"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ---- helpers
    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/status":
                return self._send(200, {"llm": llm.status(), "emergency_number": agents.EMERGENCY_NUMBER})
            if path == "/api/incidents":
                rows = db.q("SELECT * FROM incidents ORDER BY (status='open') DESC, severity DESC, id DESC")
                return self._send(200, [db.incident_row(r) for r in rows])
            m = re.fullmatch(r"/api/incidents/(\d+)", path)
            if m:
                rows = db.q("SELECT * FROM incidents WHERE id=?", (int(m.group(1)),))
                if not rows:
                    return self._send(404, {"error": "not found"})
                inc = db.incident_row(rows[0])
                inc["assignments"] = db.q(
                    "SELECT r.name, a.qty, r.base FROM assignments a JOIN resources r ON r.id=a.resource_id WHERE a.incident_id=?",
                    (inc["id"],))
                return self._send(200, inc)
            if path == "/api/resources":
                return self._send(200, db.q("SELECT * FROM resources ORDER BY id"))
            if path == "/api/logs":
                return self._send(200, db.q("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50"))
            if path == "/api/stats":
                return self._send(200, agents.stats())
            if path == "/api/sitrep":
                return self._send(200, agents.sitrep())
            return self._static(path)
        except Exception as e:  # noqa
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/incidents":
                return self._send(201, agents.process_incident(self._body().get("text", "")))
            if path == "/api/advise":
                return self._send(200, agents.advise(self._body().get("question", "")))
            m = re.fullmatch(r"/api/incidents/(\d+)/resolve", path)
            if m:
                iid = int(m.group(1))
                if not db.q("SELECT id FROM incidents WHERE id=?", (iid,)):
                    return self._send(404, {"error": "not found"})
                agents.release(iid)
                db.run("UPDATE incidents SET status='resolved' WHERE id=?", (iid,))
                db.log("Monitor", f"incident #{iid} resolved; resources released", "rules", iid)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa
            return self._send(500, {"error": str(e)})

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        f = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))


def make_server(host=None, port=None):
    return ThreadingHTTPServer((host or config.HOST, port if port is not None else config.PORT), Handler)


def main():
    db.conn()
    srv = make_server()
    st = llm.status()
    print(f"DisasterGuard running at http://{config.HOST}:{config.PORT}")
    if st["online"] and st["model_installed"]:
        print(f"  AI: Ollama online, model '{st['model']}' ready")
    elif st["online"]:
        print(f"  AI: Ollama online but model '{st['model']}' missing -> run: ollama pull {st['model']}  (rule-based fallback active)")
    else:
        print("  AI: Ollama not reachable -> run: ollama serve  (rule-based fallback active)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
