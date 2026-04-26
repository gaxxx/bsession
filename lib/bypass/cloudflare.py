"""Cloudflare Turnstile detection and bypass strategies.

Three-tier approach:
  1. CDP iframe click (most reliable) — find Turnstile iframe ref in snapshot
  2. xdotool (fallback) — real X11 mouse events, human-like movement
  3. Manual VNC (last resort) — polls while user solves via VNC
"""

import os
import re
import subprocess
import time

from lib.browser import ab, ab_quiet, find_ref


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
