#!/usr/bin/env python3
"""Session manager — conf-file based, like supervisorctl.

Each session is a .conf file in /workspace/conf/. Example:

    [session]
    script = /workspace/scripts/monitors/uscis.py

    [env]
    RECEIPT_NUMBER = IOE0000000000
    CHECK_INTERVAL = 1800

Ports are auto-assigned via SQLite unless explicitly set in the conf:

    [session]
    script = /workspace/scripts/monitors/uscis.py
    port = 9222

Usage:
    session list                  # list all sessions with status
    session run <id>              # start one session
    session run all               # start all sessions
    session stop <id>             # stop one session
    session stop all              # stop all sessions
    session restart <id>          # stop + start
    session show <id>             # show conf
    session logs <id> [-n N]      # tail logs
"""

import argparse
import configparser
import glob
import os
import signal
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.browser import start_chrome, stop_chrome, chrome_alive

CONF_DIR = os.environ.get("SESSION_CONF_DIR", "/workspace/conf")
PID_DIR = os.environ.get("SESSION_PID_DIR", "/workspace/data/pids")
LOG_DIR = os.environ.get("SESSION_LOG_DIR", "/workspace/data/logs")
DB_PATH = os.environ.get("SESSION_DB", "/workspace/data/ports.db")
BASE_PORT = 9222


def conf_path(session_id):
    return os.path.join(CONF_DIR, f"{session_id}.conf")


def pid_path(session_id, kind):
    return os.path.join(PID_DIR, f"{session_id}.{kind}.pid")


def log_path(session_id):
    return os.path.join(LOG_DIR, f"{session_id}.log")


def all_session_ids():
    os.makedirs(CONF_DIR, exist_ok=True)
    return sorted(
        os.path.basename(f).replace(".conf", "")
        for f in glob.glob(os.path.join(CONF_DIR, "*.conf"))
    )


def read_conf(session_id):
    path = conf_path(session_id)
    if not os.path.isfile(path):
        print(f"Session '{session_id}' not found: {path}", file=sys.stderr)
        sys.exit(1)
    cp = configparser.ConfigParser()
    cp.read(path)
    return cp


def get_script(cp):
    return cp.get("session", "script")


def get_env(cp):
    return dict(cp.items("env")) if cp.has_section("env") else {}


# ── PID helpers ──────────────────────────────────────────────────────

def read_pid(session_id, kind):
    path = pid_path(session_id, kind)
    if os.path.isfile(path):
        try:
            pid = int(open(path).read().strip())
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError, ProcessLookupError):
            os.remove(path)
    return None


def write_pid(session_id, kind, pid):
    os.makedirs(PID_DIR, exist_ok=True)
    with open(pid_path(session_id, kind), "w") as f:
        f.write(str(pid))


def clear_pid(session_id, kind):
    path = pid_path(session_id, kind)
    if os.path.isfile(path):
        os.remove(path)


def is_running(session_id):
    return read_pid(session_id, "script") is not None


# ── Port registry (SQLite) ──────────────────────────────────────────

def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS ports (
        session_id TEXT PRIMARY KEY,
        port INTEGER UNIQUE NOT NULL
    )""")
    db.commit()
    return db


def resolve_port(session_id, cp):
    """Get port for a session: conf explicit > existing DB > auto-assign."""
    # 1. Explicit port in conf
    explicit = cp.get("session", "port", fallback=None)
    if explicit:
        port = int(explicit)
        db = _get_db()
        # Upsert: make sure DB reflects the explicit port
        db.execute(
            "INSERT INTO ports (session_id, port) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET port = ?",
            (session_id, port, port))
        db.commit()
        db.close()
        return port

    # 2. Already allocated in DB
    db = _get_db()
    row = db.execute("SELECT port FROM ports WHERE session_id = ?",
                     (session_id,)).fetchone()
    if row:
        db.close()
        return row[0]

    # 3. Auto-assign next available
    max_row = db.execute("SELECT MAX(port) FROM ports").fetchone()
    port = max((max_row[0] or (BASE_PORT - 1)), BASE_PORT - 1) + 1
    db.execute("INSERT INTO ports (session_id, port) VALUES (?, ?)",
               (session_id, port))
    db.commit()
    db.close()
    return port


def get_port(session_id):
    """Look up port from DB, or None."""
    db = _get_db()
    row = db.execute("SELECT port FROM ports WHERE session_id = ?",
                     (session_id,)).fetchone()
    db.close()
    return row[0] if row else None


def release_port(session_id):
    db = _get_db()
    db.execute("DELETE FROM ports WHERE session_id = ?", (session_id,))
    db.commit()
    db.close()


# ── Commands ──────────────────────────────────────────────────────────

def cmd_list(_args):
    ids = all_session_ids()
    if not ids:
        print("No sessions. Create a .conf file in", CONF_DIR)
        return
    port_map = {}
    db = _get_db()
    for row in db.execute("SELECT session_id, port FROM ports"):
        port_map[row[0]] = row[1]
    db.close()

    fmt = "{:<14} {:>5}  {:<9} {:<30} {}"
    print(fmt.format("SESSION", "PORT", "STATUS", "SCRIPT", "ENV"))
    print("-" * 95)
    for sid in ids:
        cp = configparser.ConfigParser()
        cp.read(conf_path(sid))
        port = port_map.get(sid, "-")
        script = os.path.basename(cp.get("session", "script", fallback="?"))
        env = dict(cp.items("env")) if cp.has_section("env") else {}
        env_short = " ".join(f"{k}={v}" for k, v in env.items())
        if len(env_short) > 30:
            env_short = env_short[:27] + "..."
        status = "running" if is_running(sid) else "stopped"
        print(fmt.format(sid, port, status, script, env_short))


def cmd_show(args):
    path = conf_path(args.session_id)
    if not os.path.isfile(path):
        print(f"Session '{args.session_id}' not found.", file=sys.stderr)
        sys.exit(1)
    port = get_port(args.session_id)
    if port:
        print(f"# port (from db): {port}")
    print(open(path).read())


def _start(session_id):
    if is_running(session_id):
        print(f"  '{session_id}' already running. Skipping.")
        return

    cp = read_conf(session_id)
    port = resolve_port(session_id, cp)
    script = get_script(cp)
    env_vars = get_env(cp)
    profile = env_vars.get("browser_profile", f"/workspace/data/profile-{session_id}")

    # 1. Start Chrome
    print(f"  [{session_id}] Starting Chrome on port {port}...")
    try:
        chrome_pid = start_chrome(port, profile)
    except RuntimeError as e:
        print(f"  [{session_id}] {e}", file=sys.stderr)
        return
    write_pid(session_id, "chrome", chrome_pid)
    print(f"  [{session_id}] Chrome ready (PID {chrome_pid})")

    # 2. Launch monitor
    env = os.environ.copy()
    env.update({k.upper(): str(v) for k, v in env_vars.items()})
    # CDP_PORT always comes from SQLite, not conf [env]
    env["CDP_PORT"] = str(port)
    env["SESSION_NAME"] = session_id

    os.makedirs(LOG_DIR, exist_ok=True)
    lp = log_path(session_id)
    lf = open(lp, "a")

    proc = subprocess.Popen(
        [sys.executable, script],
        stdout=lf, stderr=subprocess.STDOUT,
        env=env, start_new_session=True,
    )
    write_pid(session_id, "script", proc.pid)
    print(f"  [{session_id}] Monitor started (PID {proc.pid}, log: {lp})")


def _stop(session_id):
    # Kill script
    script_pid = read_pid(session_id, "script")
    if script_pid:
        try:
            os.killpg(script_pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    clear_pid(session_id, "script")

    # Kill Chrome
    chrome_pid = read_pid(session_id, "chrome")
    if chrome_pid:
        try:
            os.kill(chrome_pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    clear_pid(session_id, "chrome")

    # Also kill by port from DB
    port = get_port(session_id)
    if port:
        stop_chrome(port)

    print(f"  Stopped '{session_id}'.")


def cmd_run(args):
    if args.session_id == "all":
        ids = all_session_ids()
        if not ids:
            print("No sessions.")
            return
        for sid in ids:
            _start(sid)
    else:
        _start(args.session_id)


def cmd_stop(args):
    if args.session_id == "all":
        ids = all_session_ids()
        for sid in ids:
            if is_running(sid):
                _stop(sid)
        if not ids:
            print("No sessions.")
    else:
        _stop(args.session_id)


def cmd_restart(args):
    if args.session_id == "all":
        for sid in all_session_ids():
            if is_running(sid):
                _stop(sid)
                time.sleep(1)
            _start(sid)
    else:
        if is_running(args.session_id):
            _stop(args.session_id)
            time.sleep(1)
        _start(args.session_id)


def cmd_logs(args):
    lp = log_path(args.session_id)
    if not os.path.isfile(lp):
        print(f"No log file for '{args.session_id}'.", file=sys.stderr)
        sys.exit(1)
    with open(lp) as f:
        lines = f.readlines()
    for line in lines[-args.lines:]:
        print(line, end="")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="bsession", description="Chrome session manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all sessions")
    sub.add_parser("status", help="Same as list")

    p = sub.add_parser("show", help="Show session conf")
    p.add_argument("session_id")

    p = sub.add_parser("run", help="Start session(s)")
    p.add_argument("session_id", help="Session ID or 'all'")

    p = sub.add_parser("stop", help="Stop session(s)")
    p.add_argument("session_id", help="Session ID or 'all'")

    p = sub.add_parser("restart", help="Restart session(s)")
    p.add_argument("session_id", help="Session ID or 'all'")

    p = sub.add_parser("logs", help="Tail session logs")
    p.add_argument("session_id")
    p.add_argument("-n", "--lines", type=int, default=50)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {
        "list": cmd_list,
        "status": cmd_list,
        "show": cmd_show,
        "run": cmd_run,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "logs": cmd_logs,
    }[args.command](args)


if __name__ == "__main__":
    main()
