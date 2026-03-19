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
  GET  /health
"""

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, "/app")
from lib.browser import start_chrome, stop_chrome, chrome_alive


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_response(404, {"error": "not found"})

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
