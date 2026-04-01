"""Chrome lifecycle management — launch, stop, health check, stealth setup."""

import json
import os
import subprocess
import time
import urllib.request


CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/lib/chromium/chromium")
STEALTH_EXT = "/workspace/data/stealth-ext"


def ensure_stealth_ext():
    """Create the stealth extension if it doesn't exist."""
    manifest = os.path.join(STEALTH_EXT, "manifest.json")
    if os.path.isfile(manifest):
        return
    os.makedirs(STEALTH_EXT, exist_ok=True)
    with open(manifest, "w") as f:
        json.dump({
            "name": "Stealth", "version": "1.0", "manifest_version": 3,
            "content_scripts": [{
                "matches": ["<all_urls>"], "js": ["stealth.js"],
                "run_at": "document_start", "world": "MAIN",
            }],
        }, f)
    with open(os.path.join(STEALTH_EXT, "stealth.js"), "w") as f:
        f.write(
            'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});\n'
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;\n"
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;\n"
            "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;\n"
        )


def kill_chrome_on_port(port):
    subprocess.run(["pkill", "-f", f"remote-debugging-port={port}"], capture_output=True)


def start_chrome(port, profile_dir, start_url="about:blank"):
    """Start Chrome with stealth flags. Returns PID."""
    kill_chrome_on_port(port)
    time.sleep(1)
    os.makedirs(profile_dir, exist_ok=True)
    # Remove stale profile locks (e.g. from container restart)
    # Use lexists() — SingletonLock is a symlink that becomes broken when Chrome dies
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_dir, lock)
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
    ensure_stealth_ext()

    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--no-first-run",
            "--no-default-browser-check", "--no-sandbox",
            "--disable-gpu", "--test-type",
            "--disable-background-networking", "--disable-sync",
            "--window-size=1280,900",
            f"--load-extension={STEALTH_EXT}",
            start_url,
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for CDP to be ready (Chromium may fork, so check CDP not PID)
    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
            break
        except Exception:
            continue
    else:
        raise RuntimeError(f"Chrome CDP not responding on port {port}")

    # Get the actual Chrome PID from the process listening on the port
    result = subprocess.run(
        ["pgrep", "-f", f"remote-debugging-port={port}"],
        capture_output=True, text=True,
    )
    pids = result.stdout.strip().splitlines()
    return int(pids[0]) if pids else 0


def stop_chrome(port):
    kill_chrome_on_port(port)


def chrome_alive(port):
    """Check if Chrome CDP is responding."""
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False
