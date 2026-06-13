#!/usr/bin/env python3
"""Drive bsession across an Edge password-export CSV to exercise the stack
and surface breakages — a stress-test harness, not a credential tool.

Phase 1 (default): reachability + login-detection sweep. For every item it
runs the real bsession process — nav → cloudflare bypass → snapshot — then
classifies what it found (login form? settings link? cloudflare/captcha?
error?). It NEVER submits credentials in this phase, so it can't lock anyone
out. Passwords are never read, logged, or stored.

Results go to tools/edge_probe_results.jsonl (one redacted line per item:
domain + outcome only). Resumable: already-recorded domains are skipped.

Usage:
  python3 tools/edge_probe.py --limit 15            # probe next 15 unprobed
  python3 tools/edge_probe.py --limit 15 --offset 0
  python3 tools/edge_probe.py --summary             # print outcome tally

Drives the container via its HTTP /cli endpoint (documented API), reusing a
single profile so we run one Chrome, not hundreds.
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.request

CSV_PATH = os.path.expanduser("~/Downloads/Edge_Pass.csv")
RESULTS = os.path.join(os.path.dirname(__file__), "edge_probe_results.jsonl")
API = os.environ.get("BSESSION_API", "http://localhost:18000")
PROFILE = "edge-probe"

# Domains where an automated login attempt is costly/irreversible (lockouts,
# fraud holds, 2FA spam). Phase-1 never submits anywhere; this list gates the
# later login phase so these are probe-only.
HIGH_RISK = re.compile(
    r"bank|chase|fidelity|schwab|vanguard|capitalone|wellsfargo|bofa|"
    r"\bmtb\b|usbank|citi|amex|americanexpress|discover|paypal|venmo|"
    r"coinbase|crypto|robinhood|etrade|\.gov\b|gov\.cn|irs|ssa|dmv|"
    r"ezpass|treasury|fcps|\.edu\b|health|insur|aetna|cigna|kaiser|"
    r"medicare|medicaid|verizon|att\.com|t-mobile|xfinity|comcast",
    re.I,
)


def domain(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].split(":")[0]
    # Keep full IPv4 addresses (LAN devices) instead of collapsing to last 2 octets.
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        return host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def load_rows():
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def cli(argv, timeout=120):
    body = json.dumps({"profile": PROFILE, "argv": argv}).encode()
    req = urllib.request.Request(API + "/cli", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


CF = re.compile(r"verify you are human|just a moment|cf-turnstile|"
                r"challenges\.cloudflare|performing security verification", re.I)
CAPTCHA = re.compile(r"recaptcha|hcaptcha|captcha|select all images", re.I)
LOGIN = re.compile(r"\bpassword\b|sign[ -]?in|log[ -]?in|textbox.*(email|user)", re.I)
SETTINGS = re.compile(r"settings|account|profile|preferences|管理|设置", re.I)


def classify(snap):
    tags = []
    if not snap or not snap.strip():
        return ["empty-snapshot"]
    if CF.search(snap):
        tags.append("cloudflare")
    if CAPTCHA.search(snap):
        tags.append("captcha")
    if LOGIN.search(snap):
        tags.append("login-form")
    if SETTINGS.search(snap):
        tags.append("settings-link")
    if not tags:
        tags.append("loaded-other")
    return tags


def probe_one(url):
    out = {"steps": {}}
    nav = cli(["nav", url, "--wait", "6"])
    out["steps"]["nav"] = nav.get("code")
    if nav.get("code") != 0:
        out["steps"]["nav_err"] = (nav.get("stderr") or "")[:300]
    cli(["bypass", "cloudflare", "--max-wait", "15"])
    snap = cli(["snapshot", "-c"])
    out["steps"]["snapshot"] = snap.get("code")
    text = snap.get("stdout", "")
    out["tags"] = classify(text)
    out["snap_len"] = len(text)
    return out


def done_domains():
    if not os.path.isfile(RESULTS):
        return set()
    seen = set()
    with open(RESULTS) as f:
        for line in f:
            try:
                seen.add(json.loads(line)["domain"])
            except Exception:
                pass
    return seen


def summary():
    if not os.path.isfile(RESULTS):
        print("no results yet"); return
    import collections
    tally = collections.Counter()
    errs = []
    n = 0
    with open(RESULTS) as f:
        for line in f:
            r = json.loads(line); n += 1
            for t in r.get("tags", []):
                tally[t] += 1
            if r.get("error") or r.get("steps", {}).get("nav") not in (0, None):
                errs.append((r["domain"], r.get("error") or r["steps"]))
    print(f"probed: {n}")
    for t, c in tally.most_common():
        print(f"  {c:4d}  {t}")
    if errs:
        print(f"errors/nav-failures: {len(errs)}")
        for d, e in errs[:20]:
            print(f"  {d}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    if args.summary:
        return summary()

    rows = load_rows()
    # one row per domain (first occurrence), stable order
    seen_dom, uniq = set(), []
    for r in rows:
        d = domain(r["url"])
        if d not in seen_dom:
            seen_dom.add(d); uniq.append((d, r["url"]))

    done = done_domains()
    todo = [(d, u) for d, u in uniq if d not in done][args.offset:args.offset + args.limit]
    print(f"{len(uniq)} unique domains, {len(done)} done, probing {len(todo)} now")

    with open(RESULTS, "a") as out:
        for d, u in todo:
            rec = {"domain": d, "high_risk": bool(HIGH_RISK.search(u))}
            try:
                rec.update(probe_one(u))
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
            out.write(json.dumps(rec) + "\n"); out.flush()
            print(f"  {d:28s} {'[HR]' if rec['high_risk'] else '    '} "
                  f"{rec.get('tags') or rec.get('error')}")


if __name__ == "__main__":
    main()
