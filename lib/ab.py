"""agent-browser CLI runner, scoped to a per-profile session.

Each profile gets its own agent-browser session named "bs-<profile>" so
its CDP-port binding is isolated from other profiles and from
agent-browser's "default" session used by legacy code.
"""

import subprocess
import urllib.request


def session_name(profile):
    return f"bs-{profile}"


def bind(profile, port):
    """Pin agent-browser session for `profile` to `port`. Idempotent."""
    subprocess.run(
        ["agent-browser", "--session", session_name(profile), "connect", str(port)],
        capture_output=True, timeout=10,
    )


def run(profile, *args, capture=True, timeout=120):
    """Run agent-browser command in profile's session. Returns stdout."""
    result = subprocess.run(
        ["agent-browser", "--session", session_name(profile), *args],
        capture_output=capture, text=True, timeout=timeout,
    )
    return (result.stdout or "").strip()


def close_session(profile):
    """Close profile's agent-browser session, terminating its daemon.

    Each session spawns a long-lived node daemon; without an explicit
    close they accumulate (and leak memory) after their Chrome is gone.
    """
    subprocess.run(
        ["agent-browser", "--session", session_name(profile), "close"],
        capture_output=True, timeout=10,
    )


def run_quiet(profile, *args, timeout=120):
    """Run agent-browser command, discard output."""
    subprocess.run(
        ["agent-browser", "--session", session_name(profile), *args],
        capture_output=True, timeout=timeout,
    )


def cdp_alive(port):
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2).read()
        return True
    except Exception:
        return False
