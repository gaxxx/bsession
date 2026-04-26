"""bsession primitive CLI — runs inside the agent-browser container.

Each command resolves the form context (BSESSION_FORM env or --form arg),
ensures a Chrome process is alive for the form's profile, and runs the
underlying agent-browser command against that profile's session.

profile defaults to skill_id, so all forms in the same skill folder share
one Chrome (and its cookies). Set _bsession_profile in form.toml to override.

Subcommands:
  Browser:  nav, snapshot, find, click, fill, type, select, extract,
            wait, wait-for, screenshot
  Bypass:   bypass cloudflare
  Form:     form get|dump|list
  Session:  session list|close|forget
  Misc:     notify
"""

import argparse
import json
import os
import re
import sys
import time

from lib import state, ab, form
from lib.chrome import start_chrome, stop_chrome, chrome_alive


PROFILES_ROOT = os.environ.get("BSESSION_PROFILES_DIR", "/workspace/.bsession-state/profiles")


# ── Session resolution ──────────────────────────────────────────────

def _profile_dir(profile):
    return os.path.join(PROFILES_ROOT, profile)


def ensure_chrome(profile):
    """Return live CDP port for `profile`. Starts Chrome if needed, evicts LRU if at cap."""
    row = state.get_chrome(profile)
    if row:
        port, _pid = row
        if chrome_alive(port):
            state.touch_chrome(profile)
            ab.bind(profile, port)
            return port
        # stale row — Chrome died
        stop_chrome(port)
        state.remove_chrome(profile)

    if state.chrome_count() >= state.MAX_PROFILES:
        victim = state.lru_chrome()
        if victim and victim != profile:
            v_row = state.get_chrome(victim)
            if v_row:
                stop_chrome(v_row[0])
            state.remove_chrome(victim)
            print(f"[bsession] evicted LRU profile: {victim}", file=sys.stderr)

    port = state.allocate_port()
    pid = start_chrome(port, _profile_dir(profile))
    state.insert_chrome(profile, port, pid)
    ab.bind(profile, port)
    return port


# ── Snapshot regex helpers ──────────────────────────────────────────

def find_ref(snapshot, pattern):
    for line in snapshot.splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            m = re.search(r"\[ref=(\w+)\]", line)
            if m:
                return m.group(1)
    return None


def find_all_refs(snapshot, pattern):
    out = []
    for line in snapshot.splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            m = re.search(r"\[ref=(\w+)\]", line)
            if m:
                out.append(m.group(1))
    return out


# ── Browser commands ────────────────────────────────────────────────

def cmd_nav(args, ctx):
    ensure_chrome(ctx.profile)
    ab.run_quiet(ctx.profile, "open", args.url)
    if args.wait > 0:
        time.sleep(args.wait)


def cmd_snapshot(args, ctx):
    ensure_chrome(ctx.profile)
    extra = []
    if args.interactive: extra.append("-i")
    if args.compact: extra.append("-c")
    if args.depth: extra.extend(["-d", str(args.depth)])
    print(ab.run(ctx.profile, "snapshot", *extra))


def cmd_find(args, ctx):
    ensure_chrome(ctx.profile)
    snap = ab.run(ctx.profile, "snapshot")
    if args.all:
        refs = find_all_refs(snap, args.pattern)
        if not refs:
            sys.exit(1)
        for r in refs:
            print(r)
    else:
        ref = find_ref(snap, args.pattern)
        if not ref:
            print(f"no match for: {args.pattern}", file=sys.stderr)
            sys.exit(1)
        print(ref)


def cmd_click(args, ctx):
    ensure_chrome(ctx.profile)
    ab.run_quiet(ctx.profile, "click", args.ref)
    if args.wait > 0:
        time.sleep(args.wait)


def cmd_fill(args, ctx):
    ensure_chrome(ctx.profile)
    ab.run_quiet(ctx.profile, "fill", args.ref, args.value)


def cmd_type(args, ctx):
    ensure_chrome(ctx.profile)
    ab.run_quiet(ctx.profile, "type", args.ref, args.value)


def cmd_select(args, ctx):
    ensure_chrome(ctx.profile)
    ab.run_quiet(ctx.profile, "select", args.ref, args.value)


def cmd_extract(args, ctx):
    ensure_chrome(ctx.profile)
    snap = ab.run(ctx.profile, "snapshot")
    matches = []
    for line in snap.splitlines():
        if args.exclude and re.search(args.exclude, line):
            continue
        m = re.search(args.regex, line)
        if m:
            matches.append(m.group(1) if m.groups() else m.group(0))
        if args.max_lines and len(matches) >= args.max_lines:
            break
    if not matches:
        sys.exit(1)
    for m in matches:
        print(m)


def cmd_wait(args, _ctx):
    time.sleep(args.seconds)


def cmd_wait_for(args, ctx):
    ensure_chrome(ctx.profile)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        snap = ab.run(ctx.profile, "snapshot")
        if re.search(args.pattern, snap, re.IGNORECASE):
            return
        time.sleep(args.interval)
    print(f"timed out waiting for: {args.pattern}", file=sys.stderr)
    sys.exit(1)


def cmd_screenshot(args, ctx):
    ensure_chrome(ctx.profile)
    out = args.output or f"/tmp/bsession-{ctx.profile}.png"
    ab.run_quiet(ctx.profile, "screenshot", out)
    print(out)


# ── Bypass ──────────────────────────────────────────────────────────

_CF_PATTERNS = [
    r"Verify you are human",
    r"Performing security verification",
    r"challenges\.cloudflare",
    r"cf-turnstile",
    r"Just a moment",
    r"security service.*bot",
]


def _is_cloudflare(snap):
    return any(re.search(p, snap, re.IGNORECASE) for p in _CF_PATTERNS)


def cmd_bypass(args, ctx):
    if args.kind != "cloudflare":
        print(f"unknown bypass: {args.kind}", file=sys.stderr)
        sys.exit(2)
    ensure_chrome(ctx.profile)
    snap = ab.run(ctx.profile, "snapshot")
    if not _is_cloudflare(snap):
        return

    # Tier 1: try clicking the Turnstile iframe checkbox via CDP. The click
    # itself isn't the challenge — Cloudflare uses fingerprinting + timing —
    # so a stealth-flagged Chrome usually passes.
    iframe_ref = find_ref(snap, r"Iframe.*Cloudflare|Iframe.*challenge|Iframe.*Widget")
    if iframe_ref:
        print(f"[bsession] clicking Turnstile iframe ref={iframe_ref}", file=sys.stderr)
        ab.run_quiet(ctx.profile, "click", iframe_ref)
        time.sleep(8)
        snap = ab.run(ctx.profile, "snapshot")
        if not _is_cloudflare(snap):
            print("[bsession] resolved via iframe click.", file=sys.stderr)
            return

    # Tier 2: hand off to manual VNC solve.
    print("[bsession] auto-click didn't pass. Solve manually at:", file=sys.stderr)
    print("[bsession]   http://localhost:6080/vnc.html", file=sys.stderr)
    deadline = time.time() + args.max_wait
    while time.time() < deadline:
        time.sleep(5)
        snap = ab.run(ctx.profile, "snapshot")
        if not _is_cloudflare(snap):
            print("[bsession] Cloudflare resolved.", file=sys.stderr)
            return
    print(f"[bsession] timeout after {args.max_wait}s", file=sys.stderr)
    sys.exit(1)


# ── Form ────────────────────────────────────────────────────────────

def cmd_form_get(args, ctx):
    print(form.get_field(ctx, args.key))


def cmd_form_dump(_args, ctx):
    print(json.dumps(form.public_fields(ctx)))


def cmd_form_list(_args, ctx):
    for k in form.public_fields(ctx):
        print(k)


# ── Session admin ───────────────────────────────────────────────────

def cmd_session_list(args, _ctx):
    chromes = state.list_chromes()
    if args.json:
        out = [{
            "profile": p, "port": port, "pid": pid, "last_used": last,
            "alive": chrome_alive(port),
        } for p, port, pid, last in chromes]
        print(json.dumps(out, indent=2))
        return

    if not chromes:
        print("(no active sessions)")
        return

    now = time.time()
    print(f"{'PROFILE':<24} {'PORT':>6}  {'PID':>6}  {'STATUS':<8}  LAST")
    print("-" * 60)
    for profile, port, pid, last in chromes:
        status = "alive" if chrome_alive(port) else "dead"
        ago = _ago(now - (last or now))
        print(f"{profile:<24} {port:>6}  {pid:>6}  {status:<8}  {ago}")


def cmd_session_close(args, _ctx):
    profile = args.profile
    row = state.get_chrome(profile)
    if not row:
        print(f"no such profile: {profile}", file=sys.stderr)
        sys.exit(1)
    stop_chrome(row[0])
    state.remove_chrome(profile)
    print(f"closed {profile}")


def cmd_session_forget(args, _ctx):
    profile = args.profile
    row = state.get_chrome(profile)
    if row:
        stop_chrome(row[0])
        state.remove_chrome(profile)
    pdir = _profile_dir(profile)
    if os.path.isdir(pdir):
        import shutil
        shutil.rmtree(pdir)
        print(f"forgot {profile} (deleted {pdir})")
    else:
        print(f"{profile} had no on-disk state")


def _ago(seconds):
    if seconds < 60: return f"{int(seconds)}s"
    if seconds < 3600: return f"{int(seconds/60)}m"
    if seconds < 86400: return f"{int(seconds/3600)}h"
    return f"{int(seconds/86400)}d"


# ── Notify ──────────────────────────────────────────────────────────

def cmd_notify(args, _ctx):
    from lib.notify import send_webhook
    payload = json.loads(args.json)
    if not send_webhook(args.url, payload):
        sys.exit(1)


# ── argparse wiring ─────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="bsession", description="bsession primitive CLI")
    p.add_argument("--form", help="path to form.toml (defaults to $BSESSION_FORM)")
    p.add_argument("--profile", help="override profile (defaults to form's _bsession_profile or skill_id)")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("nav"); n.add_argument("url"); n.add_argument("--wait", type=float, default=3.0)

    s = sub.add_parser("snapshot")
    s.add_argument("-i", "--interactive", action="store_true")
    s.add_argument("-c", "--compact", action="store_true")
    s.add_argument("-d", "--depth", type=int, default=None)

    f = sub.add_parser("find"); f.add_argument("pattern"); f.add_argument("--all", action="store_true")
    c = sub.add_parser("click"); c.add_argument("ref"); c.add_argument("--wait", type=float, default=1.0)
    fi = sub.add_parser("fill"); fi.add_argument("ref"); fi.add_argument("value")
    t = sub.add_parser("type"); t.add_argument("ref"); t.add_argument("value")
    se = sub.add_parser("select"); se.add_argument("ref"); se.add_argument("value")

    e = sub.add_parser("extract")
    e.add_argument("regex"); e.add_argument("--max-lines", type=int, default=None); e.add_argument("--exclude", default=None)

    w = sub.add_parser("wait"); w.add_argument("seconds", type=float)

    wf = sub.add_parser("wait-for")
    wf.add_argument("pattern"); wf.add_argument("--timeout", type=float, default=60); wf.add_argument("--interval", type=float, default=2)

    sc = sub.add_parser("screenshot"); sc.add_argument("--output", default=None)

    b = sub.add_parser("bypass"); b.add_argument("kind", choices=["cloudflare"]); b.add_argument("--max-wait", type=int, default=300)

    fo = sub.add_parser("form"); fo_sub = fo.add_subparsers(dest="form_cmd", required=True)
    fg = fo_sub.add_parser("get"); fg.add_argument("key")
    fo_sub.add_parser("dump"); fo_sub.add_parser("list")

    sn = sub.add_parser("session"); sn_sub = sn.add_subparsers(dest="session_cmd", required=True)
    sl = sn_sub.add_parser("list"); sl.add_argument("--json", action="store_true")
    sc2 = sn_sub.add_parser("close"); sc2.add_argument("profile")
    sf = sn_sub.add_parser("forget"); sf.add_argument("profile")

    no = sub.add_parser("notify"); no.add_argument("url"); no.add_argument("--json", required=True)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    ctx = form.resolve(form_path=args.form, profile_override=args.profile)

    handlers = {
        "nav": cmd_nav, "snapshot": cmd_snapshot, "find": cmd_find,
        "click": cmd_click, "fill": cmd_fill, "type": cmd_type, "select": cmd_select,
        "extract": cmd_extract, "wait": cmd_wait, "wait-for": cmd_wait_for,
        "screenshot": cmd_screenshot, "bypass": cmd_bypass, "notify": cmd_notify,
    }
    if args.cmd in handlers:
        return handlers[args.cmd](args, ctx)
    if args.cmd == "form":
        return {"get": cmd_form_get, "dump": cmd_form_dump, "list": cmd_form_list}[args.form_cmd](args, ctx)
    if args.cmd == "session":
        return {"list": cmd_session_list, "close": cmd_session_close, "forget": cmd_session_forget}[args.session_cmd](args, ctx)


if __name__ == "__main__":
    main()
