"""Idle Chrome reaper — closes Chromes idle beyond BSESSION_IDLE_TTL.

Cookies and the cloak fingerprint seed live in the on-disk profile, so
closing an idle Chrome loses nothing: ensure_chrome() relaunches it
transparently (~2s) on the next command. Without this, a Chrome parked on
a live page (SPA feeds poll forever) grows its renderers unboundedly —
the LRU cap only evicts when a *new* profile needs a slot, never on idle.

Also drops rows whose Chrome already died (e.g. container restart) and
closes their leaked agent-browser session daemons.

BSESSION_IDLE_TTL: seconds of idleness before close (default 1800, 0 disables).
"""

import os
import sys
import time

from lib import state
from lib.ab import close_session
from lib.chrome import stop_chrome, chrome_alive

IDLE_TTL = int(os.environ.get("BSESSION_IDLE_TTL", "1800"))


def reap_idle(ttl=None, now=None):
    """Close idle/dead Chromes + their agent-browser daemons. Returns reaped profiles."""
    ttl = IDLE_TTL if ttl is None else ttl
    if ttl <= 0:
        return []
    now = time.time() if now is None else now

    reaped = []
    for profile, port, _pid, last_used in state.list_chromes():
        idle = now - (last_used or 0)
        if idle < ttl and chrome_alive(port):
            continue
        stop_chrome(port)
        close_session(profile)
        state.remove_chrome(profile)
        reaped.append(profile)
    return reaped


def run_loop(interval=60):
    """Blocking reap loop; meant for a daemon thread in api.py."""
    while True:
        time.sleep(interval)
        try:
            reaped = reap_idle()
            if reaped:
                print(f"[reaper] closed idle: {', '.join(reaped)}",
                      file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[reaper] error: {e}", file=sys.stderr, flush=True)
