"""Unit tests for the cloakbrowser/chrome backend selection in lib.chrome.

Pure-function tests — no Chrome, no cloakbrowser install, no network needed.
Run: python3 tests/test_chrome_backend.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import chrome


# ── resolve_browser ──────────────────────────────────────────────────

def test_auto_prefers_cloak_when_available():
    path, backend = chrome.resolve_browser(
        env={}, cloak_resolver=lambda: "/cache/chromium-146/chrome")
    assert backend == "cloak", backend
    assert path == "/cache/chromium-146/chrome", path


def test_auto_falls_back_to_chrome_when_cloak_missing():
    path, backend = chrome.resolve_browser(env={}, cloak_resolver=lambda: None)
    assert backend == "chrome", backend


def test_explicit_chrome_bin_wins_in_auto():
    # An explicit CHROME_BIN means the user picked a specific Chrome; honor it
    # as the plain-chrome backend even if cloak is available.
    path, backend = chrome.resolve_browser(
        env={"CHROME_BIN": "/usr/bin/google-chrome"},
        cloak_resolver=lambda: "/cache/chrome")
    assert backend == "chrome", backend
    assert path == "/usr/bin/google-chrome", path


def test_pref_chrome_ignores_cloak():
    path, backend = chrome.resolve_browser(
        env={"BSESSION_BROWSER": "chrome"},
        cloak_resolver=lambda: "/cache/chrome")
    assert backend == "chrome", backend


def test_pref_cloak_uses_cloak():
    path, backend = chrome.resolve_browser(
        env={"BSESSION_BROWSER": "cloak"},
        cloak_resolver=lambda: "/cache/chrome")
    assert backend == "cloak", backend


def test_pref_cloak_raises_when_unavailable():
    try:
        chrome.resolve_browser(
            env={"BSESSION_BROWSER": "cloak"}, cloak_resolver=lambda: None)
    except RuntimeError:
        return
    assert False, "expected RuntimeError when cloak requested but unavailable"


# ── fingerprint seed ─────────────────────────────────────────────────

def test_fingerprint_seed_is_deterministic():
    assert chrome._fingerprint_seed("reddit") == chrome._fingerprint_seed("reddit")


def test_fingerprint_seed_differs_per_profile():
    assert chrome._fingerprint_seed("reddit") != chrome._fingerprint_seed("uscis-check")


def test_fingerprint_seed_is_int():
    assert isinstance(chrome._fingerprint_seed("reddit"), int)


# ── build_flags ──────────────────────────────────────────────────────

def test_cloak_flags_have_stable_fingerprint_no_stealth_ext():
    flags = chrome.build_flags(
        "cloak", "/cache/chrome", 9222, "/profiles/reddit",
        in_docker=True, stealth_ext="/ext")
    joined = " ".join(flags)
    seed = chrome._fingerprint_seed("reddit")
    assert f"--fingerprint={seed}" in flags, flags
    assert "--fingerprint-platform=windows" in flags or "--fingerprint-platform=macos" in flags
    # cloak owns stealth: no extension load, no automation flag, no forced GPU off
    assert "--load-extension" not in joined, joined
    assert "AutomationControlled" not in joined, joined
    assert "--disable-gpu" not in joined, joined
    # container essentials still present
    assert "--no-sandbox" in flags
    assert "--disable-dev-shm-usage" in flags
    assert f"--remote-debugging-port=9222" in flags
    assert "--user-data-dir=/profiles/reddit" in flags


def test_chrome_flags_keep_stealth_ext_and_automation_off():
    flags = chrome.build_flags(
        "chrome", "/usr/bin/chromium", 9223, "/profiles/uscis",
        in_docker=True, stealth_ext="/ext/stealth")
    joined = " ".join(flags)
    assert "--load-extension=/ext/stealth" in flags, flags
    assert "--disable-blink-features=AutomationControlled" in flags
    assert "--disable-gpu" in flags
    assert "--fingerprint" not in joined, joined


def test_chrome_flags_no_docker_extras_when_native():
    flags = chrome.build_flags(
        "chrome", "/usr/bin/chromium", 9224, "/profiles/x",
        in_docker=False, stealth_ext="/ext")
    assert "--no-sandbox" not in flags
    assert "--disable-dev-shm-usage" not in flags


def test_binary_is_first_flag_and_url_is_last():
    flags = chrome.build_flags(
        "cloak", "/cache/chrome", 9222, "/profiles/reddit",
        in_docker=False, stealth_ext="/ext", start_url="https://reddit.com")
    assert flags[0] == "/cache/chrome"
    assert flags[-1] == "https://reddit.com"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
