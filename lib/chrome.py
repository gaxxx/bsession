"""Chrome lifecycle management — launch, stop, health check, stealth setup.

Detects whether we're inside the bsession Docker container or running
natively on the host (BSESSION_LOCAL=1) and adjusts Chrome flags +
default binary path accordingly.

Browser backend
---------------
bsession can drive either plain Chromium ("chrome" backend, stealth via a
JS extension) or cloakbrowser's source-patched stealth Chromium ("cloak"
backend, stealth compiled into the binary). Selection is via the
BSESSION_BROWSER env (auto|cloak|chrome, default auto):

  - auto:   cloak if the cloakbrowser binary is resolvable, else chrome.
            An explicit CHROME_BIN always means the user picked a specific
            Chrome, so auto honors it as the chrome backend.
  - cloak:  require cloakbrowser (error if not installed).
  - chrome: plain Chromium + stealth extension.

On the cloak backend the stealth extension and AutomationControlled flag
are dropped (cloak owns those at the C++ level), and each profile gets a
stable --fingerprint seed derived from its name so the device fingerprint
persists across restarts the same way its cookies do.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
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


# ── Browser backend selection ───────────────────────────────────────

def _cloak_binary():
    """Path to the cloakbrowser stealth Chromium, or None if unavailable.

    ensure_binary() downloads on first use; in the container it's pre-baked
    at build time so this returns the cached path instantly.
    """
    try:
        from cloakbrowser.download import ensure_binary
        return ensure_binary()
    except Exception:
        return None


def resolve_browser(env=None, cloak_resolver=_cloak_binary):
    """Return (binary_path, backend) where backend is "cloak" or "chrome"."""
    env = os.environ if env is None else env
    pref = env.get("BSESSION_BROWSER", "auto").lower()
    explicit_chrome = env.get("CHROME_BIN")

    if pref == "chrome":
        return (explicit_chrome or _default_chrome_bin(), "chrome")
    if pref == "cloak":
        path = cloak_resolver()
        if not path:
            raise RuntimeError(
                "BSESSION_BROWSER=cloak but cloakbrowser is not installed "
                "(pip install cloakbrowser)")
        return (path, "cloak")

    # auto: an explicit CHROME_BIN means the user picked a Chrome — honor it.
    if explicit_chrome:
        return (explicit_chrome, "chrome")
    path = cloak_resolver()
    if path:
        return (path, "cloak")
    return (_default_chrome_bin(), "chrome")


def _fingerprint_seed(profile):
    """Deterministic cloak fingerprint seed for a profile name.

    Same profile → same device fingerprint across restarts, matching how
    its persistent cookies survive — a profile that presents a *different*
    device each launch would itself look suspicious.
    """
    return int(hashlib.sha256(profile.encode()).hexdigest()[:8], 16)


def build_flags(backend, binary, port, profile_dir, *, in_docker,
                stealth_ext, start_url="about:blank"):
    """Build the launch argv for a backend. Pure — no side effects."""
    profile = os.path.basename(profile_dir.rstrip("/"))
    flags = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--disable-infobars", "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking", "--disable-sync",
    ]

    if backend == "cloak":
        seed = _fingerprint_seed(profile)
        fp_platform = "macos" if platform.system() == "Darwin" else "windows"
        # cloak owns stealth: no stealth extension, no AutomationControlled
        # flag, no forced software GL. GPU/screen/window are auto-generated
        # from the seed, so we don't pin --window-size or --disable-gpu.
        flags += [
            f"--fingerprint={seed}",
            f"--fingerprint-platform={fp_platform}",
            "--no-sandbox",
        ]
        if in_docker:
            flags += ["--test-type", "--disable-dev-shm-usage"]
    else:  # chrome
        flags += [
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,900",
            f"--load-extension={stealth_ext}",
        ]
        if in_docker:
            flags += ["--no-sandbox", "--disable-gpu", "--test-type",
                      "--disable-dev-shm-usage"]

    flags.append(start_url)
    return flags


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
    """Start the browser with stealth flags. Returns PID."""
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

    binary, backend = resolve_browser()
    if backend == "chrome":
        ensure_stealth_ext()
    print(f"[bsession] browser backend: {backend} ({binary})", file=sys.stderr)

    flags = build_flags(
        backend, binary, port, profile_dir,
        in_docker=IN_DOCKER, stealth_ext=STEALTH_EXT, start_url=start_url,
    )
    env = os.environ.copy()
    if IN_DOCKER:
        env["DISPLAY"] = ":99"

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
