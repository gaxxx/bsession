"""Lightweight HTTP API for agent-browser container.

Exposes bsession commands over HTTP so other containers on the same
Docker network can control browser sessions without docker exec.

Endpoints:
  POST /run          {"command": "list"}
  POST /run          {"command": "run", "args": ["uscis"]}
  POST /run          {"command": "stop", "args": ["uscis"]}
  POST /ab           {"port": 9222, "command": "snapshot"}
  POST /ab           {"port": 9222, "command": "open", "args": ["https://..."]}
  POST /ab           {"port": 9222, "command": "click", "args": ["e5"]}
  POST /chrome/start {"port": 9222, "profile": "/workspace/data/profile-tmp"}
  POST /chrome/stop  {"port": 9222}
  GET  /screenshot?port=9222        — PNG of the active tab (by CDP port)
  GET  /screenshot/<session_id>     — PNG of the active tab (by session name)
  GET  /captcha/screenshot?port=9222         — PNG of captcha area (by CDP port)
  GET  /captcha/screenshot/<session_id>      — PNG of captcha area (by session name)
  GET  /captcha/bounds?port=9222             — captcha bounding box JSON
  GET  /captcha/bounds/<session_id>          — captcha bounding box JSON
  GET  /skills                      — list available skills
  GET  /eval/<session_id>           — run history and summary stats
  GET  /health
"""

import json
import re
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, "/app")
from lib.browser import start_chrome, stop_chrome, chrome_alive, capture_screenshot
from lib.captcha import find_captcha_bounds, capture_captcha_screenshot


DB_PATH = "/workspace/data/ports.db"


def _lookup_port(session_id: str) -> int | None:
    """Resolve a session name to its CDP port from the SQLite registry."""
    import sqlite3
    db_path = DB_PATH
    try:
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT port FROM ports WHERE session_id = ?", (session_id,)
        ).fetchone()
        db.close()
        return row[0] if row else None
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _png_response(self, png_bytes: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png_bytes)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(png_bytes)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json_response(200, {"status": "ok"})

        elif parsed.path.startswith("/screenshot"):
            try:
                port = self._resolve_screenshot_port(parsed)
                if port is None:
                    self._json_response(404, {"error": "session not found or no port assigned"})
                    return
                png_data = capture_screenshot(port)
                self._png_response(png_data)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif parsed.path.startswith("/captcha/screenshot"):
            try:
                port = self._resolve_screenshot_port(parsed)
                if port is None:
                    self._json_response(404, {"error": "session not found or no port assigned"})
                    return
                qs = parse_qs(parsed.query)
                padding = int(qs.get("padding", [10])[0])
                png_data = capture_captcha_screenshot(port, padding=padding)
                self._png_response(png_data)
            except RuntimeError as e:
                self._json_response(404, {"error": str(e)})
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif parsed.path.startswith("/captcha/bounds"):
            try:
                port = self._resolve_screenshot_port(parsed)
                if port is None:
                    self._json_response(404, {"error": "session not found or no port assigned"})
                    return
                bounds = find_captcha_bounds(port)
                if bounds is None:
                    self._json_response(404, {"error": "no captcha element found"})
                    return
                self._json_response(200, {"bounds": bounds})
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif parsed.path == "/skills":
            try:
                from lib.skill import list_skills
                self._json_response(200, {"skills": list_skills()})
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif parsed.path.startswith("/eval/"):
            try:
                session_id = parsed.path.split("/eval/", 1)[1]
                from lib.eval import EvalRecorder
                recorder = EvalRecorder()
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", [20])[0])
                summary = recorder.get_summary(session_id)
                runs = recorder.get_runs(session_id, limit=limit)
                self._json_response(200, {
                    "session_id": session_id,
                    "summary": {
                        "total_runs": summary.total_runs,
                        "success_rate": summary.success_rate,
                        "avg_duration_ms": summary.avg_duration_ms,
                        "p95_duration_ms": summary.p95_duration_ms,
                        "last_error": summary.last_error,
                        "last_run": summary.last_run,
                    },
                    "runs": runs,
                })
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        else:
            self._json_response(404, {"error": "not found"})

    def _resolve_screenshot_port(self, parsed) -> int | None:
        """Resolve CDP port from query param or URL path segment.

        GET /screenshot?port=9222                → use port directly
        GET /screenshot/<session_id>             → look up port from DB
        GET /captcha/screenshot?port=9222        → use port directly
        GET /captcha/screenshot/<session_id>     → look up port from DB
        GET /captcha/bounds?port=9222            → use port directly
        GET /captcha/bounds/<session_id>         → look up port from DB
        """
        qs = parse_qs(parsed.query)
        if "port" in qs:
            return int(qs["port"][0])

        # Extract session_id from path: /screenshot/uscis or /captcha/screenshot/uscis or /captcha/bounds/uscis
        match = re.search(r"/([a-zA-Z0-9_-]+)$", parsed.path)
        if match:
            segment = match.group(1)
            # Skip if segment is a known endpoint name
            if segment not in ("screenshot", "bounds", "captcha"):
                return _lookup_port(segment)

        return None

    def do_POST(self):
        try:
            body = self._read_body()

            if self.path == "/run":
                cmd = body.get("command", "list")
                args = body.get("args", [])
                result = subprocess.run(
                    ["python3", "/app/session.py", cmd] + args,
                    capture_output=True, text=True, timeout=30,
                )
                self._json_response(200, {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                })

            elif self.path == "/ab":
                port = body.get("port", 9222)
                cmd = body.get("command", "snapshot")
                args = body.get("args", [])
                result = subprocess.run(
                    ["agent-browser", "--cdp", str(port), cmd] + args,
                    capture_output=True, text=True, timeout=60,
                )
                self._json_response(200, {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                })

            elif self.path == "/chrome/start":
                port = body.get("port", 9222)
                profile = body.get("profile", "/workspace/data/profile-tmp")
                url = body.get("url", "about:blank")
                pid = start_chrome(port, profile, url)
                self._json_response(200, {"pid": pid, "port": port})

            elif self.path == "/chrome/stop":
                port = body.get("port", 9222)
                stop_chrome(port)
                self._json_response(200, {"stopped": True, "port": port})

            elif self.path == "/chrome/alive":
                port = body.get("port", 9222)
                alive = chrome_alive(port)
                self._json_response(200, {"alive": alive, "port": port})

            # ── Browser tool endpoints (for algo-esc / OpenClaw) ─────
            elif self.path == "/browse":
                port = body.get("port", 9222)
                url = body.get("url", "about:blank")
                wait = body.get("wait", 5)
                subprocess.run(
                    ["agent-browser", "--cdp", str(port), "open", url],
                    capture_output=True, timeout=30,
                )
                import time; time.sleep(wait)
                snap = subprocess.run(
                    ["agent-browser", "--cdp", str(port), "snapshot"],
                    capture_output=True, text=True, timeout=30,
                )
                self._json_response(200, {"url": url, "snapshot": snap.stdout})

            elif self.path == "/click":
                port = body.get("port", 9222)
                ref = body.get("ref", "")
                subprocess.run(
                    ["agent-browser", "--cdp", str(port), "click", ref],
                    capture_output=True, timeout=30,
                )
                import time; time.sleep(1)
                snap = subprocess.run(
                    ["agent-browser", "--cdp", str(port), "snapshot"],
                    capture_output=True, text=True, timeout=30,
                )
                self._json_response(200, {"clicked": ref, "snapshot": snap.stdout})

            elif self.path == "/fill":
                port = body.get("port", 9222)
                ref = body.get("ref", "")
                value = body.get("value", "")
                subprocess.run(
                    ["agent-browser", "--cdp", str(port), "clear", ref],
                    capture_output=True, timeout=30,
                )
                subprocess.run(
                    ["agent-browser", "--cdp", str(port), "fill", ref, value],
                    capture_output=True, timeout=30,
                )
                self._json_response(200, {"filled": ref})

            elif self.path == "/snapshot":
                port = body.get("port", 9222)
                snap = subprocess.run(
                    ["agent-browser", "--cdp", str(port), "snapshot"],
                    capture_output=True, text=True, timeout=30,
                )
                self._json_response(200, {"snapshot": snap.stdout})

            else:
                self._json_response(404, {"error": "not found"})

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass  # suppress default logging


def main():
    port = 8080
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"bsession API listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
