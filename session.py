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
    return cp.get("session", "script", fallback=None)


def get_skill(cp):
    return cp.get("session", "skill", fallback=None)


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
        skill_val = cp.get("session", "skill", fallback=None)
        script_val = cp.get("session", "script", fallback=None)
        script = f"skill:{skill_val}" if skill_val else os.path.basename(script_val or "?")
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
    skill_name = get_skill(cp)
    script = get_script(cp)
    env_vars = get_env(cp)
    profile = env_vars.get("browser_profile", f"/workspace/data/profile-{session_id}")

    if not skill_name and not script:
        print(f"  [{session_id}] Conf must have 'skill' or 'script' in [session].", file=sys.stderr)
        return

    # 1. Start Chrome
    print(f"  [{session_id}] Starting Chrome on port {port}...")
    try:
        chrome_pid = start_chrome(port, profile)
    except RuntimeError as e:
        print(f"  [{session_id}] {e}", file=sys.stderr)
        return
    write_pid(session_id, "chrome", chrome_pid)
    print(f"  [{session_id}] Chrome ready (PID {chrome_pid})")

    # 2. Build environment
    env = os.environ.copy()
    env.update({k.upper(): str(v) for k, v in env_vars.items()})
    env["CDP_PORT"] = str(port)
    env["SESSION_NAME"] = session_id

    os.makedirs(LOG_DIR, exist_ok=True)
    lp = log_path(session_id)

    # 3. Launch: skill-based or legacy script
    if skill_name:
        env["SKILL_NAME"] = skill_name
        entry_point = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_skill.py")
        cmd = [sys.executable, entry_point, session_id]
        label = f"skill={skill_name}"
    else:
        cmd = [sys.executable, script]
        label = f"script={os.path.basename(script)}"

    with open(lp, "a") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
    write_pid(session_id, "script", proc.pid)
    print(f"  [{session_id}] Started {label} (PID {proc.pid}, log: {lp})")


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


# ── Browser commands (for Claude Code / CLI usage) ───────────────────

def cmd_browse(args):
    """Open a URL and print the accessibility tree snapshot."""
    port = args.port or 9222
    from lib.browser import ab, ab_quiet
    ab_quiet(port, "open", args.url)
    import time; time.sleep(args.wait)
    print(ab(port, "snapshot"))


def cmd_snapshot(args):
    """Print current page snapshot."""
    port = args.port or 9222
    from lib.browser import ab
    print(ab(port, "snapshot"))


def cmd_click(args):
    """Click an element and print the new snapshot."""
    port = args.port or 9222
    from lib.browser import ab, ab_quiet
    ab_quiet(port, "click", args.ref)
    import time; time.sleep(1)
    print(ab(port, "snapshot"))


def cmd_fill(args):
    """Clear and fill an input field."""
    port = args.port or 9222
    from lib.browser import ab_quiet
    ab_quiet(port, "clear", args.ref)
    ab_quiet(port, "fill", args.ref, args.value)
    print(f"Filled ref={args.ref}")


def cmd_type_text(args):
    """Type text into an element."""
    port = args.port or 9222
    from lib.browser import ab_quiet
    ab_quiet(port, "type", args.ref, args.text)
    print(f"Typed into ref={args.ref}")


def cmd_select(args):
    """Select a dropdown option."""
    port = args.port or 9222
    import re
    from lib.browser import ab, ab_quiet, find_ref
    ab_quiet(port, "click", args.ref)
    import time; time.sleep(1)
    snap = ab(port, "snapshot")
    option_ref = find_ref(snap, re.escape(args.value))
    if option_ref:
        ab_quiet(port, "click", option_ref)
        print(f"Selected '{args.value}'")
    else:
        print(f"Option '{args.value}' not found", file=sys.stderr)
        sys.exit(1)


def cmd_screenshot(args):
    """Save a screenshot to a file."""
    port = args.port or 9222
    from lib.browser import capture_screenshot
    png = capture_screenshot(port)
    out = args.output or "/tmp/bsession-screenshot.png"
    with open(out, "wb") as f:
        f.write(png)
    print(out)


def cmd_captcha(args):
    """Screenshot the page, wait for human to write answer, fill it."""
    port = args.port or 9222
    from lib.browser import ab, ab_quiet
    screenshot_path = args.output or "/workspace/data/captcha.png"
    answer_file = args.answer_file or "/workspace/data/captcha-answer.txt"

    # Take screenshot
    ab_quiet(port, "screenshot", screenshot_path)
    print(f"Screenshot: {screenshot_path}")
    print(f"View page:  http://localhost:6080/vnc.html")
    print(f"Write answer to: {answer_file}")
    print("Waiting for answer...")

    # Clean stale answer
    if os.path.exists(answer_file):
        os.remove(answer_file)

    # Poll
    for _ in range(int(args.timeout / 5)):
        if os.path.isfile(answer_file):
            answer = open(answer_file).read().strip()
            if answer:
                if args.ref:
                    ab_quiet(port, "fill", args.ref, answer)
                    print(f"Filled ref={args.ref} with: {answer}")
                else:
                    print(f"Answer: {answer}")
                os.remove(answer_file)
                return
        time.sleep(5)

    print("Timeout waiting for CAPTCHA answer.", file=sys.stderr)
    sys.exit(1)


def cmd_check_cf(args):
    """Check for Cloudflare and attempt bypass."""
    port = args.port or 9222
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


# ── Skill commands ────────────────────────────────────────────────────

def cmd_skill(args):
    """Dispatch skill sub-commands."""
    {
        "list": cmd_skill_list,
        "create": cmd_skill_create,
        "show": cmd_skill_show,
        "run": cmd_skill_run,
        "eval": cmd_skill_eval,
        "export": cmd_skill_export,
    }[args.skill_command](args)


def cmd_skill_list(_args):
    from lib.skill import list_skills
    skills = list_skills()
    if not skills:
        print("No skills. Create one: bsession skill create <name>")
        return
    fmt = "{:<20} {:<40} {:>7} {:>6} {:>5}"
    print(fmt.format("NAME", "DESCRIPTION", "VERSION", "PARAMS", "STEPS"))
    print("-" * 82)
    for s in skills:
        print(fmt.format(
            s["name"], s["description"][:40],
            s.get("version", "?"), s.get("params", "?"), s.get("steps", "?"),
        ))


def cmd_skill_create(args):
    from lib.builder import scaffold_skill
    try:
        path = scaffold_skill(args.name, args.description)
        print(f"  Created skill: {path}")
        print(f"  Edit the YAML to define steps, then: bsession skill run {args.name}")
    except FileExistsError as e:
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)


def cmd_skill_show(args):
    from lib.skill import load_skill_by_name
    try:
        s = load_skill_by_name(args.name)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(f"Name:        {s.name}")
    print(f"Description: {s.description}")
    print(f"Version:     {s.version}")
    print(f"Tags:        {', '.join(s.tags) if s.tags else '-'}")
    print(f"Params:      {len(s.params)}")
    for p in s.params:
        req = "(required)" if p.required else f"(default: {p.default})"
        print(f"  - {p.name}: {p.description} {req}")
    print(f"Steps:       {len(s.steps)}")
    for i, step in enumerate(s.steps, 1):
        print(f"  {i}. {step.action} {step.params}")
    print(f"Monitor:     {'yes' if s.monitor else 'no'}")
    if s.monitor:
        print(f"  interval:  {s.monitor.interval}s")
        print(f"  change:    {s.monitor.change_field}")


def cmd_skill_run(args):
    """Run a skill once (for testing). Uses an existing Chrome or starts one."""
    from lib.skill import load_skill_by_name
    from lib.toolset import create_toolset
    from lib.runner import run_skill_once
    from lib.eval import EvalRecorder

    try:
        skill = load_skill_by_name(args.name)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    port = args.port or 9222
    print(f"  Running skill '{skill.name}' on port {port}...")

    tools = create_toolset(port, session_name=args.name)
    config = {}
    for p in skill.params:
        val = os.environ.get(p.name, p.default or "")
        config[p.name] = val
    config["SESSION_NAME"] = args.name

    result = run_skill_once(skill, tools, config)
    recorder = EvalRecorder()
    recorder.record(skill.name, args.name, result)

    if result.success:
        print(f"  Success ({result.duration_ms}ms)")
        for k, v in result.data.items():
            print(f"    {k}: {v}")
    else:
        print(f"  Failed ({result.duration_ms}ms): {result.error}")
        sys.exit(1)


def cmd_skill_eval(args):
    from lib.eval import EvalRecorder
    recorder = EvalRecorder()

    summary = recorder.get_summary(args.name)
    print(f"  Session: {args.name}")
    print(f"  Total runs:    {summary.total_runs}")
    print(f"  Success rate:  {summary.success_rate}%")
    print(f"  Avg duration:  {summary.avg_duration_ms:.0f}ms")
    print(f"  P95 duration:  {summary.p95_duration_ms:.0f}ms")
    if summary.last_error:
        print(f"  Last error:    {summary.last_error[:80]}")
    if summary.last_run:
        print(f"  Last run:      {summary.last_run}")

    runs = recorder.get_runs(args.name, limit=args.lines)
    if runs:
        print()
        fmt = "{:>4}  {:<19}  {:<8}  {:>8}  {}"
        print(fmt.format("ID", "TIME", "STATUS", "DURATION", "ERROR"))
        print("-" * 72)
        for r in runs:
            err = (r["error"] or "")[:30]
            print(fmt.format(
                r["id"], r["started_at"], r["status"],
                f"{r['duration_ms']}ms", err,
            ))


def cmd_skill_export(args):
    from lib.skill import load_skill_by_name
    from lib.builder import export_claude_skill, export_openclaw_tool
    import json

    try:
        skill = load_skill_by_name(args.name)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if args.format == "claude":
        md = export_claude_skill(skill)
        if args.output:
            with open(args.output, "w") as f:
                f.write(md)
            print(f"  Exported Claude skill to: {args.output}")
        else:
            print(md)
    elif args.format == "openclaw":
        tool_def = export_openclaw_tool(skill)
        out = json.dumps(tool_def, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
            print(f"  Exported OpenClaw tool to: {args.output}")
        else:
            print(out)
    else:
        print(f"  Unknown format: {args.format}. Use 'claude' or 'openclaw'.", file=sys.stderr)
        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="bsession", description="Chrome session manager")
    sub = parser.add_subparsers(dest="command")

    # Session commands
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

    # Browser commands (for Claude Code / CLI)
    p = sub.add_parser("browse", help="Open URL and print snapshot")
    p.add_argument("url", help="URL to open")
    p.add_argument("-p", "--port", type=int, default=None)
    p.add_argument("-w", "--wait", type=int, default=5, help="Wait seconds after load")

    p = sub.add_parser("snapshot", help="Print current page snapshot")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("click", help="Click element and print snapshot")
    p.add_argument("ref", help="Element ref (e.g. e12)")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("fill", help="Clear and fill input field")
    p.add_argument("ref", help="Input field ref")
    p.add_argument("value", help="Text to enter")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("type", help="Type text into element")
    p.add_argument("ref", help="Element ref")
    p.add_argument("text", help="Text to type")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("select", help="Select dropdown option")
    p.add_argument("ref", help="Dropdown ref")
    p.add_argument("value", help="Option text to select")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("screenshot", help="Save screenshot to file")
    p.add_argument("-o", "--output", default=None, help="Output path (default: /tmp/bsession-screenshot.png)")
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("captcha", help="Screenshot + wait for human CAPTCHA solve")
    p.add_argument("--ref", default=None, help="CAPTCHA input ref to fill after solve")
    p.add_argument("-o", "--output", default=None, help="Screenshot path")
    p.add_argument("--answer-file", default=None, help="File to poll for answer")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("-p", "--port", type=int, default=None)

    p = sub.add_parser("check-cf", help="Check and bypass Cloudflare")
    p.add_argument("-p", "--port", type=int, default=None)
    p.add_argument("--max-wait", type=int, default=300)

    # Skill commands
    sp = sub.add_parser("skill", help="Skill builder commands")
    skill_sub = sp.add_subparsers(dest="skill_command")

    skill_sub.add_parser("list", help="List available skills")

    p = skill_sub.add_parser("create", help="Scaffold a new skill")
    p.add_argument("name", help="Skill name (lowercase, no spaces)")
    p.add_argument("-d", "--description", default="", help="Skill description")

    p = skill_sub.add_parser("show", help="Show skill details")
    p.add_argument("name", help="Skill name")

    p = skill_sub.add_parser("run", help="Run a skill once (test mode)")
    p.add_argument("name", help="Skill name")
    p.add_argument("-p", "--port", type=int, default=None, help="CDP port (default: 9222)")

    p = skill_sub.add_parser("eval", help="Show skill run history and stats")
    p.add_argument("name", help="Session/skill name")
    p.add_argument("-n", "--lines", type=int, default=10, help="Number of recent runs")

    p = skill_sub.add_parser("export", help="Export skill as Claude or OpenClaw definition")
    p.add_argument("name", help="Skill name")
    p.add_argument("-f", "--format", default="claude", choices=["claude", "openclaw"])
    p.add_argument("-o", "--output", default=None, help="Output file (prints to stdout if omitted)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "skill":
        if not args.skill_command:
            sp.print_help()
            sys.exit(1)
        cmd_skill(args)
    else:
        {
            "list": cmd_list,
            "status": cmd_list,
            "show": cmd_show,
            "run": cmd_run,
            "stop": cmd_stop,
            "restart": cmd_restart,
            "logs": cmd_logs,
            "browse": cmd_browse,
            "snapshot": cmd_snapshot,
            "click": cmd_click,
            "fill": cmd_fill,
            "type": cmd_type_text,
            "select": cmd_select,
            "screenshot": cmd_screenshot,
            "captcha": cmd_captcha,
            "check-cf": cmd_check_cf,
        }[args.command](args)


if __name__ == "__main__":
    main()
