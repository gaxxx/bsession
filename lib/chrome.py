"""Chrome lifecycle management — launch, stop, health check, stealth setup.

Detects whether we're inside the bsession Docker container or running
natively on the host (BSESSION_LOCAL=1) and adjusts Chrome flags +
default binary path accordingly.
"""

import json
import os
import platform
import subprocess
import time
import urllib.request


IN_DOCKER = os.path.exists("/.dockerenv")


def _default_chrome_bin():
    if IN_DOCKER:
        return "/usr/lib/chromium/chromium"
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    # Linux native
    for p in ("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/lib/chromium/chromium"):
        if os.path.exists(p):
            return p
    return "chromium"


CHROME_BIN = os.environ.get("CHROME_BIN", _default_chrome_bin())
STEALTH_EXT = os.environ.get(
    "STEALTH_EXT_DIR",
    "/workspace/data/stealth-ext" if IN_DOCKER else os.path.expanduser("~/.bsession/state/stealth-ext"),
)


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
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_dir, lock)
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
    ensure_stealth_ext()

    flags = [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars", "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking", "--disable-sync",
        "--window-size=1280,900",
        f"--load-extension={STEALTH_EXT}",
    ]
    env = os.environ.copy()

    if IN_DOCKER:
        # Container-only: Xvfb display, no sandbox (root user), no GPU,
        # spill /dev/shm to disk.
        env["DISPLAY"] = ":99"
        flags += [
            "--no-sandbox", "--disable-gpu", "--test-type",
            "--disable-dev-shm-usage",
        ]

    flags.append(start_url)

    subprocess.Popen(
        flags, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for CDP to be ready
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
