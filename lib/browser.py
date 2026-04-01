"""Shared browser helpers — core agent-browser CLI wrapper and snapshot parsing.

Other modules:
  - lib.chrome    — Chrome lifecycle (start, stop, stealth)
  - lib.bypass    — Restriction bypass strategies (Cloudflare, etc.)
  - lib.notify    — Webhook / notification helpers

For backward compatibility, this module re-exports everything so existing
scripts using `from lib.browser import ...` continue to work.
"""

import base64
import json
import re
import subprocess
import urllib.request
from datetime import datetime


# ── agent-browser CLI wrapper ─────────────────────────────────────────

def ab(cdp_port, command, *args):
    """Run an agent-browser command against a CDP port. Returns stdout."""
    result = subprocess.run(
        ["agent-browser", "--cdp", str(cdp_port), command, *args],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def ab_quiet(cdp_port, command, *args):
    """Run an agent-browser command, discard output."""
    subprocess.run(
        ["agent-browser", "--cdp", str(cdp_port), command, *args],
        capture_output=True,
    )


# ── Snapshot parsing ──────────────────────────────────────────────────

def find_ref(snapshot, pattern):
    """Find the first [ref=xxx] on a line matching pattern (case-insensitive)."""
    for line in snapshot.splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                return m.group(1)
    return None


def find_all_refs(snapshot, pattern):
    """Find all [ref=xxx] on lines matching pattern."""
    refs = []
    for line in snapshot.splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                refs.append(m.group(1))
    return refs


# ── Logging helper ────────────────────────────────────────────────────

def make_logger(session_name, log_file=None):
    """Return a log function that writes to stderr and optionally a file."""
    def log(msg):
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{session_name}] {msg}"
        print(line, flush=True)
        if log_file:
            with open(log_file, "a") as f:
                f.write(line + "\n")
    return log


# ── Screenshot via CDP ────────────────────────────────────────────────

def capture_screenshot(cdp_port: int) -> bytes:
    """Capture a PNG screenshot of the active tab via Chrome DevTools Protocol.

    Uses Node.js (already in the container) to open a WebSocket to Chrome
    and call Page.captureScreenshot.  Returns raw PNG bytes.
    """
    # 1. Get the first target's webSocketDebuggerUrl
    resp = urllib.request.urlopen(
        f"http://localhost:{cdp_port}/json", timeout=5,
    )
    targets = json.loads(resp.read())
    page_targets = [t for t in targets if t.get("type") == "page"]
    if not page_targets:
        raise RuntimeError(f"No page targets on CDP port {cdp_port}")
    ws_url = page_targets[0]["webSocketDebuggerUrl"]

    # 2. Node one-liner: built-in WebSocket (Node 22+) → Page.captureScreenshot → stdout
    node_script = (
        f"const ws=new WebSocket('{ws_url}');"
        "ws.onopen=()=>ws.send(JSON.stringify({id:1,method:'Page.captureScreenshot',params:{format:'png'}}));"
        "ws.onmessage=e=>{const r=JSON.parse(e.data);if(r.id===1){process.stdout.write(r.result.data);ws.close();}};"
        "ws.onerror=e=>{process.stderr.write(String(e));process.exit(1);};"
    )
    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CDP screenshot failed: {result.stderr.decode(errors='replace')}")
    return base64.b64decode(result.stdout)


# ── Backward-compatible re-exports ───────────────────────────────────
# Existing scripts use `from lib.browser import start_chrome, is_cloudflare, ...`
# These re-exports keep them working without changes.

from lib.chrome import (  # noqa: E402, F401
    CHROME_BIN, STEALTH_EXT,
    ensure_stealth_ext, kill_chrome_on_port,
    start_chrome, stop_chrome, chrome_alive,
)
from lib.bypass.cloudflare import (  # noqa: E402, F401
    CF_PATTERNS, is_cloudflare, xdotool_click_turnstile, wait_for_cloudflare,
)
from lib.notify import send_webhook  # noqa: E402, F401
