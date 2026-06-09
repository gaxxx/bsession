"""Lightweight HTTP API for agent-browser container.

Exposes browser primitives over HTTP so other containers on the same
Docker network can drive Chrome sessions without docker exec.

Endpoints:
  POST /ab                {"port": 9222, "command": "snapshot", "args": [...]}
  POST /chrome/{start,stop,alive}
  POST /{browse,click,fill,snapshot}
  GET  /screenshot?port=9222
  GET  /captcha/screenshot?port=9222
  GET  /captcha/bounds?port=9222
  GET  /health
"""

import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, "/app")
from lib.browser import start_chrome, stop_chrome, chrome_alive, capture_screenshot
from lib.captcha import find_captcha_bounds, capture_captcha_screenshot
from lib import state


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

        else:
            self._json_response(404, {"error": "not found"})

    def _resolve_screenshot_port(self, parsed) -> int | None:
        """Resolve CDP port from ?port= or ?profile= query param. None if neither."""
        qs = parse_qs(parsed.query)
        if "port" in qs:
            return int(qs["port"][0])
        if "profile" in qs:
            row = state.get_chrome(qs["profile"][0])  # (port, pid) or None
            return int(row[0]) if row else None
        return None

    def do_POST(self):
        try:
            body = self._read_body()

            if self.path == "/ab":
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


def _api_port():
    return int(os.environ.get("BSESSION_API_PORT", "18000"))


def main():
    port = _api_port()
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"bsession API listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
