#!/usr/bin/env python3
"""USCIS case status monitor.

Checks case status periodically, detects changes, sends webhook notifications.

Required config:
  RECEIPT_NUMBER  — USCIS receipt number (e.g. IOE0000000000)

Optional config:
  CHECK_INTERVAL  — seconds between checks (default: 3600)
  N8N_WEBHOOK_URL — webhook for status change alerts
"""

import os
import re
import sys
import time

sys.path.insert(0, "/app")
from lib.browser import (
    ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare,
    send_webhook, make_logger,
)

USCIS_URL = "https://egov.uscis.gov/casestatus/mycasestatus.do"


def check_status(port, receipt, log):
    """Navigate to USCIS, enter receipt, click Check Status, parse result.

    Returns (title, detail) or raises on failure.
    """
    log("Opening USCIS case status page...")
    ab_quiet(port, "open", USCIS_URL)
    time.sleep(8)

    snap = ab(port, "snapshot")

    # Handle Cloudflare
    if is_cloudflare(snap):
        log("Cloudflare detected.")
        if not wait_for_cloudflare(port, snap, log=log):
            raise RuntimeError("Cloudflare not resolved.")
        log("Cloudflare resolved.")

    time.sleep(3)
    snap = ab(port, "snapshot")

    # Check if blocked
    if re.search(r"you have been blocked|unable to access", snap, re.IGNORECASE):
        raise RuntimeError("IP blocked by Cloudflare.")

    # Find receipt number input
    input_ref = (
        find_ref(snap, "textbox")
        or find_ref(snap, r"text.*receipt")
        or find_ref(snap, "input")
    )
    if not input_ref:
        raise RuntimeError(f"Could not find receipt input. Snapshot:\n{snap}")

    log(f"Entering receipt number: {receipt}")
    ab_quiet(port, "clear", input_ref)
    ab_quiet(port, "fill", input_ref, receipt)
    time.sleep(1)

    # Find and click Check Status
    snap = ab(port, "snapshot")
    submit_ref = (
        find_ref(snap, r"[Cc]heck [Ss]tatus")
        or find_ref(snap, r"button.*[Ss]ubmit")
        or find_ref(snap, r"button.*[Cc]heck")
    )
    if not submit_ref:
        raise RuntimeError(f"Could not find Check Status button. Snapshot:\n{snap}")

    log("Clicking Check Status...")
    ab_quiet(port, "click", submit_ref)
    time.sleep(5)

    # Parse result
    snap = ab(port, "snapshot")

    titles = re.findall(r'heading "Case ([^"]*)"', snap)
    title = next((t for t in titles if "Status Online" not in t), "")

    detail_lines = []
    for line in snap.splitlines():
        if re.search(r"text:.*Form I-|text:.*approved|text:.*received|text:.*denied|text:.*transferred|text:.*petition", line, re.IGNORECASE):
            detail_lines.append(re.sub(r".*- text: ", "", line).strip())
            if len(detail_lines) >= 3:
                break
    detail = "\n".join(detail_lines)

    if not title and not detail:
        raise RuntimeError(f"Could not parse status. Snapshot:\n{snap}")

    return title, detail


def main():
    port = int(os.environ.get("CDP_PORT", 9222))
    receipt = os.environ.get("RECEIPT_NUMBER") or os.environ.get("USCIS_RECEIPT_NUMBER", "IOE0000000000")
    session_name = os.environ.get("SESSION_NAME", "uscis")
    check_interval = int(os.environ.get("CHECK_INTERVAL", 3600))
    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "https://example.com/webhook/uscis-status-change")
    last_status_file = os.environ.get("LAST_STATUS_FILE", f"/workspace/data/uscis-{session_name}-last-status.txt")

    log = make_logger(session_name)

    log(f"USCIS Monitor: receipt={receipt}, port={port}, interval={check_interval}s")

    # Load previous status
    previous_status = ""
    if os.path.isfile(last_status_file):
        previous_status = open(last_status_file).read().strip()
        log(f"Previous status: {previous_status}")

    check_count = 0
    consecutive_failures = 0

    while True:
        check_count += 1
        log(f"Check #{check_count} starting...")

        try:
            title, detail = check_status(port, receipt, log)
            consecutive_failures = 0
        except RuntimeError as e:
            consecutive_failures += 1
            retry_delay = check_interval * consecutive_failures
            log(f"Check failed (#{consecutive_failures}): {e}")
            if consecutive_failures >= 5:
                log("Too many failures. Exiting.")
                sys.exit(1)
            log(f"Retrying in {retry_delay // 60} min.")
            time.sleep(retry_delay)
            continue

        log(f"Status: {title}")

        with open(last_status_file, "w") as f:
            f.write(title)

        if previous_status and title != previous_status:
            log(f"*** STATUS CHANGED! *** {previous_status} -> {title}")
            ok = send_webhook(webhook_url, {
                "session": session_name,
                "receipt": receipt,
                "previous_status": previous_status,
                "new_status": title,
                "detail": " ".join(detail.splitlines()[:3]),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            log("Webhook notified." if ok else "Warning: webhook failed.")

            with open(f"/workspace/data/uscis-{session_name}-history.txt", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}|{title}\n")
        else:
            log("Status unchanged.")

        previous_status = title
        time.sleep(check_interval)


if __name__ == "__main__":
    main()
