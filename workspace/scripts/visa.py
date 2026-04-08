#!/usr/bin/env python3
"""CEAC nonimmigrant visa status checker.

Navigates to the CEAC Visa Status Check page, fills in the form,
waits for the user to solve the CAPTCHA via VNC, then parses and
prints the visa status result.

Required config:
  LOCATION        — Embassy/consulate (e.g. CHINA, BEIJING)
  APPLICATION_ID  — CEAC application ID (e.g. AA00FD8IMR)
  PASSPORT_NUMBER — Passport number (or NA for pre-2022 forms)
  SURNAME         — First 5 letters of surname (or NA for pre-2022 forms)

Optional config:
  CAPTCHA_WAIT    — Max seconds to wait for CAPTCHA solve (default: 300)
"""

import os
import re
import sys
import time

sys.path.insert(0, "/app")
from lib.browser import (
    ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare,
    make_logger,
)

CEAC_URL = "https://ceac.state.gov/CEACStatTracker/Status.aspx"


def wait_for_captcha_solve(port, log, max_wait=300):
    """Wait for user to solve the CAPTCHA via VNC and submit the form.

    Polls the page every 5s. Returns the snapshot once the page changes
    away from the form (i.e. result page or error), or None on timeout.
    """
    log(f"CAPTCHA detected — solve it via VNC: http://localhost:6080/vnc.html")
    log(f"All fields are pre-filled. Enter the CAPTCHA and click Submit.")
    log(f"Waiting up to {max_wait}s...")

    elapsed = 0
    while elapsed < max_wait:
        time.sleep(5)
        elapsed += 5
        snap = ab(port, "snapshot")

        # Check if result popup appeared (modal overlay on top of form)
        result = parse_ceac_result(snap)
        if result[0]:
            log(f"Result popup detected after {elapsed}s.")
            return snap

        # Check if we left the form page (no more CAPTCHA field = submitted)
        if not find_ref(snap, r'textbox "Enter the code'):
            log(f"Form submitted after {elapsed}s.")
            return snap

        # Still on form — check for error message (wrong CAPTCHA reloads form)
        if re.search(r"code entered does not match", snap, re.IGNORECASE):
            log(f"Wrong CAPTCHA — try again. ({elapsed}s/{max_wait}s)")

        if elapsed % 30 == 0:
            log(f"Still waiting for CAPTCHA... ({elapsed}s/{max_wait}s)")

    log(f"CAPTCHA not solved within {max_wait}s.")
    return None


def parse_ceac_result(snap):
    """Parse the CEAC result popup from an accessibility tree snapshot.

    The result appears as a modal popup with StaticText nodes:
      "NONIMMIGRANT VISA APPLICATION"
      "<Status>"  (e.g. "Refused", "Issued", "Administrative Processing")
      "Application ID or Case Number" ":" "<id>"
      "Case Created:" "<date>"
      "Case Last Updated:" "<date>"
      "<detail paragraph>"

    Returns (status, detail) or ("", "") if no result found.
    """
    lines = snap.splitlines()
    status = ""
    detail_parts = []
    in_result = False

    status_keywords = {
        "refused", "issued", "administrative processing", "ready",
        "approved", "denied", "expired",
    }

    for line in lines:
        st = re.search(r'StaticText "([^"]*)"', line)
        if not st:
            continue
        text = st.group(1).strip()
        if not text:
            continue

        # Detect start of result popup
        if text == "NONIMMIGRANT VISA APPLICATION":
            in_result = True
            continue

        if in_result:
            # First meaningful text after header is the status
            if not status and text.lower() in status_keywords:
                status = text
                continue
            # Collect detail lines (dates, descriptions)
            if text in (":", "Close"):
                continue
            if text.startswith("For more information"):
                detail_parts.append(text)
                break
            detail_parts.append(text)

    detail = "\n".join(detail_parts)
    return status, detail


def check_visa_status(port, location, app_id, passport, surname, log, captcha_wait=300):
    """Navigate to CEAC, fill form, wait for CAPTCHA, parse result.

    Returns (status, detail) or raises on failure.
    """
    log("Opening CEAC Visa Status Check page...")
    ab_quiet(port, "open", CEAC_URL)
    time.sleep(12)

    snap = ab(port, "snapshot")

    # Handle Cloudflare
    if is_cloudflare(snap):
        log("Cloudflare detected.")
        if not wait_for_cloudflare(port, snap, log=log):
            raise RuntimeError("Cloudflare not resolved.")
        log("Cloudflare resolved.")
        time.sleep(3)
        snap = ab(port, "snapshot")

    # Select NONIMMIGRANT VISA (NIV)
    visa_type_ref = (
        find_ref(snap, r'combobox.*Visa Application Type')
        or find_ref(snap, r'combobox.*IMMIGRANT')
    )
    if not visa_type_ref:
        raise RuntimeError(f"Could not find Visa Application Type dropdown.")

    log("Selecting NONIMMIGRANT VISA (NIV)...")
    ab_quiet(port, "select", visa_type_ref, "NONIMMIGRANT VISA (NIV)")
    time.sleep(3)
    snap = ab(port, "snapshot")

    # Select location
    location_ref = find_ref(snap, r'combobox "Select a location"')
    if not location_ref:
        raise RuntimeError(f"Could not find location dropdown.\n{snap}")

    log(f"Selecting location: {location}")
    ab_quiet(port, "select", location_ref, location)
    time.sleep(2)
    snap = ab(port, "snapshot")

    # Fill Application ID
    app_ref = find_ref(snap, r'textbox "Application ID')
    if not app_ref:
        raise RuntimeError(f"Could not find Application ID field.\n{snap}")

    log(f"Entering Application ID: {app_id}")
    ab_quiet(port, "fill", app_ref, app_id)
    time.sleep(1)

    # Fill Passport Number
    passport_ref = find_ref(snap, r'textbox "Passport')
    if passport_ref:
        log(f"Entering Passport Number: {passport}")
        ab_quiet(port, "fill", passport_ref, passport)
        time.sleep(1)

    # Fill Surname
    surname_ref = find_ref(snap, r'textbox "First 5 Letters')
    if surname_ref:
        log(f"Entering Surname: {surname}")
        ab_quiet(port, "fill", surname_ref, surname)
        time.sleep(1)

    # Check if result popup is already showing (from previous session cache)
    snap = ab(port, "snapshot")
    result = parse_ceac_result(snap)
    if result[0]:
        log("Result popup already visible (cached from previous session).")
        return result

    # CAPTCHA — hand off to user via VNC
    snap = wait_for_captcha_solve(port, log, max_wait=captcha_wait)
    if snap is None:
        raise RuntimeError("CAPTCHA was not solved in time.")

    # Wait for result page to fully load, then re-snapshot
    log("CAPTCHA solved, waiting for result page to load...")
    time.sleep(8)
    snap = ab(port, "snapshot")

    result = parse_ceac_result(snap)
    if not result[0] and not result[1]:
        # Fallback: grab all text
        skip = {"BACK", "VISA STATUS CHECK", "Skip to main content", "", " ",
                ":", "U.S. Department of State", "NONIMMIGRANT VISA APPLICATION"}
        all_text = []
        for line in snap.splitlines():
            st = re.search(r'StaticText "([^"]*)"', line)
            if st:
                text = st.group(1).strip()
                if text and text not in skip and not text.startswith("Copyright"):
                    all_text.append(text)
        result = ("Unknown", "\n".join(all_text[:15]))

    return result


def main():
    port = int(os.environ.get("CDP_PORT", 9222))
    session_name = os.environ.get("SESSION_NAME", "visa")
    location = os.environ.get("LOCATION", "CHINA, BEIJING")
    app_id = os.environ.get("APPLICATION_ID", "")
    passport = os.environ.get("PASSPORT_NUMBER", "")
    surname = os.environ.get("SURNAME", "")
    captcha_wait = int(os.environ.get("CAPTCHA_WAIT", 300))

    log = make_logger(session_name)

    if not app_id:
        log("ERROR: APPLICATION_ID not set in conf.")
        sys.exit(1)

    log(f"CEAC Visa Check: location={location}, app_id={app_id}, port={port}")

    try:
        status, detail = check_visa_status(
            port, location, app_id, passport, surname, log,
            captcha_wait=captcha_wait,
        )
    except RuntimeError as e:
        log(f"Failed: {e}")
        sys.exit(1)

    log(f"=== VISA STATUS ===")
    if status:
        log(f"Status: {status}")
    if detail:
        log(f"Detail:\n{detail}")
    log(f"===================")


if __name__ == "__main__":
    main()
