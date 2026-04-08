#!/usr/bin/env python3
"""Browser command dispatcher — session-name-first CLI.

Each session is a .toml (preferred) or .conf file in /workspace/conf/.

TOML format:
    [session]
    name = "uscis"
    instructions = "instructions/uscis.md"
    data = "forms/uscis.default.toml"
    # port = 9222  # optional override

INI fallback (.conf):
    [session]
    port = 9222

Usage:
    session list                           # list sessions + status
    session start <name>                   # start Chrome
    session stop <name>                    # stop Chrome
    session show <name>                    # show conf + instructions path

    session <name> navigate <url> [-w N]   # open URL
    session <name> snapshot                # print accessibility tree
    session <name> click <ref>             # click element
    session <name> fill <ref> <value>      # fill input
    session <name> select <ref> <value>    # select dropdown
    session <name> type <ref> <text>       # type text
    session <name> clear <ref>             # clear input
    session <name> bypass [--max-wait N]   # handle Cloudflare
    session <name> screenshot [-o path]    # take screenshot

    session cap list                       # list capabilities
    session cap show <name>                # show capability details
"""

import argparse
import configparser
import glob
import os
import signal
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.browser import start_chrome, stop_chrome

CONF_DIR = os.environ.get("SESSION_CONF_DIR", "/workspace/conf")
PID_DIR = os.environ.get("SESSION_PID_DIR", "/workspace/data/pids")
LOG_DIR = os.environ.get("SESSION_LOG_DIR", "/workspace/data/logs")
DB_PATH = os.environ.get("SESSION_DB", "/workspace/data/ports.db")
BASE_PORT = 9222


# ── Config reading ───────────────────────────────────────────────────

def all_session_ids():
    """Find both .toml and .conf files in CONF_DIR."""
    os.makedirs(CONF_DIR, exist_ok=True)
    ids = set()
    for f in glob.glob(os.path.join(CONF_DIR, "*.toml")):
        ids.add(os.path.basename(f).replace(".toml", ""))
    for f in glob.glob(os.path.join(CONF_DIR, "*.conf")):
        ids.add(os.path.basename(f).replace(".conf", ""))
    return sorted(ids)


def read_conf(session_id):
    """Try .toml first, fall back to .conf. Return normalized dict."""
    toml_path = os.path.join(CONF_DIR, f"{session_id}.toml")
    conf_path = os.path.join(CONF_DIR, f"{session_id}.conf")

    if os.path.isfile(toml_path):
        import tomllib
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        session = data.get("session", {})
        return {
            "name": session.get("name", session_id),
            "instructions": session.get("instructions"),
            "data": session.get("data"),
            "port": session.get("port"),
        }

    if os.path.isfile(conf_path):
        cp = configparser.ConfigParser()
        cp.read(conf_path)
        port = cp.get("session", "port", fallback=None)
        return {
            "name": session_id,
            "instructions": None,
            "data": None,
            "port": int(port) if port else None,
        }

    print(f"Session '{session_id}' not found: no .toml or .conf in {CONF_DIR}", file=sys.stderr)
    sys.exit(1)


# ── PID helpers ──────────────────────────────────────────────────────

def pid_path(session_id, kind):
    return os.path.join(PID_DIR, f"{session_id}.{kind}.pid")


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


def resolve_port(session_id, conf):
    """Get port for a session: conf explicit > existing DB > auto-assign."""
    # 1. Explicit port in conf
    explicit = conf.get("port")
    if explicit:
        port = int(explicit)
        db = _get_db()
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


# ── Port resolution for browser commands ─────────────────────────────

def _resolve_port(name_or_port, port_override=None):
    """Resolve CDP port from session name or explicit port."""
    if port_override:
        return port_override
    port = get_port(name_or_port)
    if not port:
        print(f"Session '{name_or_port}' has no port assigned. Run: bsession start {name_or_port}", file=sys.stderr)
        sys.exit(1)
    return port


# ── Top-level commands ───────────────────────────────────────────────

def cmd_list(_args):
    ids = all_session_ids()
    if not ids:
        print("No sessions. Create a .toml or .conf file in", CONF_DIR)
        return
    port_map = {}
    db = _get_db()
    for row in db.execute("SELECT session_id, port FROM ports"):
        port_map[row[0]] = row[1]
    db.close()

    fmt = "{:<14} {:>5}  {:<9}  {:<30}  {}"
    print(fmt.format("SESSION", "PORT", "STATUS", "INSTRUCTIONS", "DATA"))
    print("-" * 85)
    for sid in ids:
        conf = read_conf(sid)
        port = port_map.get(sid, "-")
        chrome_pid = read_pid(sid, "chrome")
        status = "running" if chrome_pid else "stopped"
        instructions = conf.get("instructions") or "-"
        data = conf.get("data") or "-"
        print(fmt.format(sid, port, status, instructions, data))


def cmd_start(args):
    session_id = args.session_id
    conf = read_conf(session_id)
    port = resolve_port(session_id, conf)
    profile = f"/workspace/data/profile-{session_id}"

    # Check if already running
    existing = read_pid(session_id, "chrome")
    if existing:
        print(f"Chrome for '{session_id}' already running (PID {existing}, port {port}).")
        return

    try:
        chrome_pid = start_chrome(port, profile)
    except RuntimeError as e:
        print(f"Failed to start Chrome: {e}", file=sys.stderr)
        sys.exit(1)
    write_pid(session_id, "chrome", chrome_pid)
    print(f"Started Chrome for '{session_id}' on port {port} (PID {chrome_pid})")


def cmd_stop(args):
    session_id = args.session_id
    chrome_pid = read_pid(session_id, "chrome")
    if chrome_pid:
        try:
            os.kill(chrome_pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    clear_pid(session_id, "chrome")
    port = get_port(session_id)
    if port:
        stop_chrome(port)
    print(f"Stopped '{session_id}'.")


def cmd_show(args):
    session_id = args.session_id
    conf = read_conf(session_id)
    port = get_port(session_id)

    print(f"Session: {conf['name']}")
    if port:
        print(f"Port:    {port}")
    if conf.get("instructions"):
        print(f"Instructions: {conf['instructions']}")
    if conf.get("data"):
        print(f"Data:    {conf['data']}")

    # Print the raw config file
    toml_path = os.path.join(CONF_DIR, f"{session_id}.toml")
    conf_path_str = os.path.join(CONF_DIR, f"{session_id}.conf")
    for p in (toml_path, conf_path_str):
        if os.path.isfile(p):
            print(f"\n# {p}")
            print(open(p).read())
            break


# ── Cap commands ─────────────────────────────────────────────────────

def cmd_cap_list(_args):
    """List all capabilities from TOML confs."""
    ids = all_session_ids()
    if not ids:
        print("No capabilities. Create a .toml file in", CONF_DIR)
        return

    fmt = "{:<14}  {:<35}  {}"
    print(fmt.format("NAME", "INSTRUCTIONS", "DATA"))
    print("-" * 70)
    for sid in ids:
        conf = read_conf(sid)
        instructions = conf.get("instructions") or "-"
        data = conf.get("data") or "-"
        print(fmt.format(conf["name"], instructions, data))


def cmd_cap_show(args):
    """Show capability details: instructions path, data path, data contents."""
    session_id = args.name
    conf = read_conf(session_id)

    print(f"Name:         {conf['name']}")
    if conf.get("instructions"):
        print(f"Instructions: {conf['instructions']}")
        # Try to show instructions file content
        inst_path = os.path.join("/workspace", conf["instructions"])
        if os.path.isfile(inst_path):
            print(f"\n--- {inst_path} ---")
            print(open(inst_path).read())
    else:
        print("Instructions: (none)")

    if conf.get("data"):
        print(f"Data:         {conf['data']}")
        data_path = os.path.join("/workspace", conf["data"])
        if os.path.isfile(data_path):
            print(f"\n--- {data_path} ---")
            print(open(data_path).read())
    else:
        print("Data:         (none)")


# ── Session browser commands ─────────────────────────────────────────

def cmd_navigate(session_name, args_list):
    """Open a URL and print the accessibility tree snapshot."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} navigate")
    parser.add_argument("url", help="URL to open")
    parser.add_argument("-w", "--wait", type=int, default=5, help="Wait seconds after load")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab, ab_quiet
    ab_quiet(port, "open", args.url)
    time.sleep(args.wait)
    print(ab(port, "snapshot"))


def cmd_snapshot(session_name, args_list):
    """Print current page snapshot."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} snapshot")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab
    print(ab(port, "snapshot"))


def cmd_click(session_name, args_list):
    """Click an element and print the new snapshot."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} click")
    parser.add_argument("ref", help="Element ref (e.g. e12)")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab, ab_quiet
    ab_quiet(port, "click", args.ref)
    time.sleep(1)
    print(ab(port, "snapshot"))


def cmd_fill(session_name, args_list):
    """Clear and fill an input field."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} fill")
    parser.add_argument("ref", help="Input field ref")
    parser.add_argument("value", help="Text to enter")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab_quiet
    ab_quiet(port, "clear", args.ref)
    ab_quiet(port, "fill", args.ref, args.value)
    print(f"Filled ref={args.ref}")


def cmd_type_text(session_name, args_list):
    """Type text into an element."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} type")
    parser.add_argument("ref", help="Element ref")
    parser.add_argument("text", help="Text to type")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab_quiet
    ab_quiet(port, "type", args.ref, args.text)
    print(f"Typed into ref={args.ref}")


def cmd_select(session_name, args_list):
    """Select a dropdown option."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} select")
    parser.add_argument("ref", help="Dropdown ref")
    parser.add_argument("value", help="Option text to select")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    import re
    from lib.browser import ab, ab_quiet, find_ref
    ab_quiet(port, "click", args.ref)
    time.sleep(1)
    snap = ab(port, "snapshot")
    option_ref = find_ref(snap, re.escape(args.value))
    if option_ref:
        ab_quiet(port, "click", option_ref)
        print(f"Selected '{args.value}'")
    else:
        print(f"Option '{args.value}' not found", file=sys.stderr)
        sys.exit(1)


def cmd_clear(session_name, args_list):
    """Clear an input field."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} clear")
    parser.add_argument("ref", help="Input field ref")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab_quiet
    ab_quiet(port, "clear", args.ref)
    print(f"Cleared ref={args.ref}")


def cmd_bypass(session_name, args_list):
    """Check for Cloudflare and attempt bypass."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} bypass")
    parser.add_argument("--max-wait", type=int, default=300)
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import ab, is_cloudflare, wait_for_cloudflare
    snap = ab(port, "snapshot")
    if not is_cloudflare(snap):
        print("No Cloudflare detected.")
        return
    print("Cloudflare detected. Attempting bypass...")
    ok = wait_for_cloudflare(port, snap, max_wait=args.max_wait)
    print("Resolved." if ok else "Failed.")
    if not ok:
        sys.exit(1)


def cmd_screenshot_session(session_name, args_list):
    """Save a screenshot to a file."""
    parser = argparse.ArgumentParser(prog=f"bsession {session_name} screenshot")
    parser.add_argument("-o", "--output", default=None, help="Output path")
    parser.add_argument("-p", "--port", type=int, default=None)
    args = parser.parse_args(args_list)

    port = _resolve_port(session_name, args.port)
    from lib.browser import capture_screenshot
    png = capture_screenshot(port)
    out = args.output or f"/tmp/bsession-{session_name}-screenshot.png"
    with open(out, "wb") as f:
        f.write(png)
    print(out)


# ── Session command dispatch ─────────────────────────────────────────

SESSION_COMMANDS = {
    "navigate": cmd_navigate,
    "snapshot": cmd_snapshot,
    "click": cmd_click,
    "fill": cmd_fill,
    "select": cmd_select,
    "type": cmd_type_text,
    "clear": cmd_clear,
    "bypass": cmd_bypass,
    "screenshot": cmd_screenshot_session,
}


# ── Task execution ───────────────────────────────────────────────────

def _run_task(session_name, conf, task_name=None, data_path=None):
    """Run an instruction task for a session."""
    port = resolve_port(session_name, conf)

    # Auto-start Chrome if not running
    from lib.chrome import chrome_alive
    if not chrome_alive(port):
        print(f"Starting Chrome for '{session_name}' on port {port}...")
        try:
            chrome_pid = start_chrome(port, f"/workspace/data/profile-{session_name}")
        except RuntimeError as e:
            print(f"Failed to start Chrome: {e}", file=sys.stderr)
            sys.exit(1)
        write_pid(session_name, "chrome", chrome_pid)

    # Execute
    from lib.engine import run
    try:
        variables = run(session_name, task_name, data_path, conf, port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Execution failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print results
    if variables:
        print("\nResults:")
        for k, v in variables.items():
            print(f"  {k}: {v}")


# ── CLI ───────────────────────────────────────────────────────────────

RESERVED = {"list", "start", "stop", "show", "cap"}

USAGE = """\
usage: bsession <command> [args...]

Top-level commands:
  list                           List sessions + status
  start <name>                   Start Chrome for session
  stop <name>                    Stop Chrome for session
  show <name>                    Show session config

  cap list                       List capabilities
  cap show <name>                Show capability details

Task execution:
  <name>                         Run default task with default data
  <name> <data.toml>             Run default task with specific data
  <name> <task>                  Run named task with default data
  <name> <task> <data.toml>     Run named task with specific data

Session commands (bsession <name> <command>):
  navigate <url> [-w N]          Open URL
  snapshot                       Print accessibility tree
  click <ref>                    Click element
  fill <ref> <value>             Fill input
  select <ref> <value>           Select dropdown
  type <ref> <text>              Type text
  clear <ref>                    Clear input
  bypass [--max-wait N]          Handle Cloudflare
  screenshot [-o path]           Take screenshot
"""


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    first = sys.argv[1]

    if first in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    if first in RESERVED:
        # Parse as top-level command
        if first == "list":
            cmd_list(None)

        elif first == "start":
            parser = argparse.ArgumentParser(prog="bsession start")
            parser.add_argument("session_id", help="Session name")
            args = parser.parse_args(sys.argv[2:])
            cmd_start(args)

        elif first == "stop":
            parser = argparse.ArgumentParser(prog="bsession stop")
            parser.add_argument("session_id", help="Session name")
            args = parser.parse_args(sys.argv[2:])
            cmd_stop(args)

        elif first == "show":
            parser = argparse.ArgumentParser(prog="bsession show")
            parser.add_argument("session_id", help="Session name")
            args = parser.parse_args(sys.argv[2:])
            cmd_show(args)

        elif first == "cap":
            if len(sys.argv) < 3:
                print("usage: bsession cap <list|show> [args...]")
                sys.exit(1)
            cap_cmd = sys.argv[2]
            if cap_cmd == "list":
                cmd_cap_list(None)
            elif cap_cmd == "show":
                parser = argparse.ArgumentParser(prog="bsession cap show")
                parser.add_argument("name", help="Capability/session name")
                args = parser.parse_args(sys.argv[3:])
                cmd_cap_show(args)
            else:
                print(f"Unknown cap command: {cap_cmd}. Use 'list' or 'show'.", file=sys.stderr)
                sys.exit(1)

    else:
        # Parse as <name> [command|task|data] [args...]
        session_name = first
        conf = read_conf(session_name)

        if len(sys.argv) < 3:
            # bsession <name> → run default task with default data
            _run_task(session_name, conf)
            return

        second = sys.argv[2]

        # If second arg is a known browser command → dispatch as before
        if second in SESSION_COMMANDS:
            SESSION_COMMANDS[second](session_name, sys.argv[3:])
            return

        # If second arg looks like a data file (contains / or ends .toml)
        if '/' in second or second.endswith('.toml'):
            # bsession <name> <data.toml>
            _run_task(session_name, conf, data_path=second)
            return

        # Otherwise it's a task name
        task_name = second
        data_path = None
        if len(sys.argv) > 3:
            # bsession <name> <task> <data.toml>
            data_path = sys.argv[3]

        _run_task(session_name, conf, task_name=task_name, data_path=data_path)


if __name__ == "__main__":
    main()
