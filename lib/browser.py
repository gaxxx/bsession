"""Shared browser helpers — Python replacement for lib/chrome.sh.

Wraps agent-browser CLI and provides Chrome launch, snapshot parsing,
Cloudflare handling, and webhook utilities.
"""

import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from datetime import datetime


CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/lib/chromium/chromium")
STEALTH_EXT = "/workspace/data/stealth-ext"


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


# ── Cloudflare detection & bypass ─────────────────────────────────────

CF_PATTERNS = [
    r'Verify you are human',
    r'Performing security verification',
    r'challenges\.cloudflare',
    r'cf-turnstile',
    r'Just a moment',
    r'security service.*bot',
]


def is_cloudflare(snapshot):
    """Check if a snapshot shows a Cloudflare challenge page."""
    for pat in CF_PATTERNS:
        if re.search(pat, snapshot, re.IGNORECASE):
            return True
    return False


def xdotool_click_turnstile():
    """Try to click the Cloudflare Turnstile checkbox using xdotool."""
    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    def xdo(*args):
        subprocess.run(["xdotool", *args], capture_output=True, env=env)

    # Find Chrome window
    result = subprocess.run(
        ["xdotool", "search", "--class", "chrome"],
        capture_output=True, text=True, env=env,
    )
    wins = result.stdout.strip().splitlines()
    if not wins:
        return False
    win = wins[0]

    # Human-like mouse movement toward checkbox area
    for x, y, delay in [(600, 500, 0.3), (400, 420, 0.2), (250, 390, 0.3), (200, 370, 0.5)]:
        xdo("mousemove", "--window", win, str(x), str(y))
        time.sleep(delay)

    # Click grid around the checkbox
    for y in (360, 370, 380, 390):
        for x in (190, 200, 210):
            xdo("mousemove", "--window", win, str(x), str(y))
            time.sleep(0.1)
            xdo("mousedown", "1")
            time.sleep(0.07)
            xdo("mouseup", "1")
            time.sleep(0.3)

    return True


def wait_for_cloudflare(cdp_port, snapshot, max_wait=300, log=None):
    """Wait for Cloudflare to resolve. Tries xdotool, then waits for VNC solve.

    Args:
        cdp_port: CDP port of the Chrome instance.
        snapshot: Current page snapshot.
        max_wait: Max seconds to wait.
        log: Optional logging function.

    Returns True if resolved, False if timed out.
    """
    if not is_cloudflare(snapshot):
        return True

    _log = log or (lambda msg: print(f"[cf] {msg}"))
    _log("Cloudflare Turnstile detected.")

    elapsed = 0

    # Try clicking the Turnstile iframe ref via agent-browser (most reliable)
    iframe_ref = find_ref(snapshot, r"Iframe.*Cloudflare|Iframe.*challenge|Iframe.*Widget")
    if iframe_ref:
        _log(f"Clicking Turnstile iframe ref={iframe_ref} via CDP...")
        ab_quiet(cdp_port, "click", iframe_ref)
        time.sleep(8)
        snap = ab(cdp_port, "snapshot")
        if not is_cloudflare(snap):
            _log("Cloudflare resolved via iframe click!")
            return True
        _log("iframe click didn't pass.")
        elapsed = 10

    # Fallback: try xdotool
    if subprocess.run(["which", "xdotool"], capture_output=True).returncode == 0:
        _log("Attempting automated click via xdotool...")
        xdotool_click_turnstile()
        time.sleep(10)
        snap = ab(cdp_port, "snapshot")
        if not is_cloudflare(snap):
            _log("Cloudflare resolved via xdotool!")
            return True
        _log("xdotool didn't pass.")
        elapsed = max(elapsed, 15)

    _log("Solve via VNC: http://localhost:6080/vnc.html")

    while elapsed < max_wait:
        time.sleep(5)
        elapsed += 5
        snap = ab(cdp_port, "snapshot")
        if not is_cloudflare(snap):
            _log(f"Cloudflare resolved after {elapsed}s.")
            return True
        if elapsed % 30 == 0:
            _log(f"Waiting... ({elapsed}s/{max_wait}s)")

    _log(f"Cloudflare not solved within {max_wait}s.")
    return False


# ── Chrome lifecycle ──────────────────────────────────────────────────

def ensure_stealth_ext():
    """Create the stealth extension if it doesn't exist."""
    manifest = os.path.join(STEALTH_EXT, "manifest.json")
    if os.path.isfile(manifest):
        return
    os.makedirs(STEALTH_EXT, exist_ok=True)
    with open(manifest, "w") as f:
        json.dump({
            "name": "Stealth", "version": "1.0", "manifest_version": 3,
            "content_scripts": [{
                "matches": ["<all_urls>"], "js": ["stealth.js"],
                "run_at": "document_start", "world": "MAIN",
            }],
        }, f)
    with open(os.path.join(STEALTH_EXT, "stealth.js"), "w") as f:
        f.write(
            'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});\n'
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;\n"
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;\n"
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;\n"
        )


def kill_chrome_on_port(port):
    subprocess.run(["pkill", "-f", f"remote-debugging-port={port}"], capture_output=True)


def start_chrome(port, profile_dir, start_url="about:blank"):
    """Start Chrome with stealth flags. Returns PID."""
    kill_chrome_on_port(port)
    time.sleep(1)
    os.makedirs(profile_dir, exist_ok=True)
    # Remove stale profile locks (e.g. from container restart)
    # Use lexists() — SingletonLock is a symlink that becomes broken when Chrome dies
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_dir, lock)
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
    ensure_stealth_ext()

    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--no-first-run",
            "--no-default-browser-check", "--no-sandbox",
            "--disable-gpu", "--test-type",
            "--disable-background-networking", "--disable-sync",
            "--window-size=1280,900",
            f"--load-extension={STEALTH_EXT}",
            start_url,
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for CDP to be ready (Chromium may fork, so check CDP not PID)
    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
            break
        except Exception:
            continue
    else:
        raise RuntimeError(f"Chrome CDP not responding on port {port}")

    # Get the actual Chrome PID from the process listening on the port
    result = subprocess.run(
        ["pgrep", "-f", f"remote-debugging-port={port}"],
        capture_output=True, text=True,
    )
    pids = result.stdout.strip().splitlines()
    return int(pids[0]) if pids else 0


def stop_chrome(port):
    kill_chrome_on_port(port)


def chrome_alive(port):
    """Check if Chrome CDP is responding."""
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


# ── Webhook ───────────────────────────────────────────────────────────

def send_webhook(url, payload):
    """POST a JSON payload to a webhook URL. Returns True on success."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


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
