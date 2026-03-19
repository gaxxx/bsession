# Project Overview

Browser automation monitors running inside a Docker container with `agent-browser`, Chromium, and VNC. Each monitor gets its own Chrome instance managed by a Python session manager (`bsession`).

## Stack

- **Runtime**: Node 22-slim Docker image with Chromium + Python 3
- **Browser Control**: `agent-browser` CLI (Playwright-based, talks to Chrome via CDP)
- **Display**: Xvfb (virtual framebuffer) + Fluxbox + x11vnc + noVNC (web VNC at port 6080)
- **Session Manager**: `session.py` — baked into Docker image, manages Chrome + monitor lifecycle
- **Port Allocation**: SQLite (`/workspace/data/ports.db`) — auto-assigns CDP ports unless explicitly set in conf
- **Monitors**: Python scripts in `workspace/scripts/` using `/app/lib/browser.py`
- **Notifications**: Webhooks (configurable URL)

## Project Structure

```
├── bsession              # Client-side CLI wrapper (runs session.py inside container)
├── session.py            # Session manager (baked into image at /app/)
├── lib/
│   └── browser.py        # Shared browser helpers (baked into image at /app/lib/)
├── Dockerfile
├── docker-compose.yml    # Mounts ./workspace:/workspace
├── entrypoint.sh         # Starts Xvfb, Fluxbox, VNC, noVNC, ensures workspace dirs
└── workspace/            # Mounted at /workspace — user content only
    ├── conf/             # Session conf files (INI format, user-managed)
    │   └── uscis.conf
    ├── data/             # Runtime data (persists across restarts)
    │   ├── ports.db      # SQLite port registry
    │   ├── pids/         # PID files: {id}.chrome.pid, {id}.script.pid
    │   ├── logs/         # Monitor logs: {id}.log
    │   ├── profile-*/    # Chrome profiles (cookies, cache)
    │   └── stealth-ext/  # Chrome extension (patches navigator.webdriver)
    └── scripts/          # User monitor scripts
        └── uscis.py      # USCIS case status monitor
```

## Architecture

### Container Startup

```
docker compose up -d
  → entrypoint.sh:
    1. mkdir -p /workspace/{conf,data,scripts}
    2. Xvfb :99 (1280x900 virtual screen)
    3. Fluxbox (window manager)
    4. x11vnc on :5900
    5. noVNC on :6080 (web proxy → VNC)
    6. tail -f /dev/null (keep alive)
```

Container provides display infrastructure. No monitors run until you start them.

### Session Lifecycle

Sessions are defined by `.conf` files in `workspace/conf/`. No `add`/`remove` commands — just create or delete conf files directly.

```
bsession run <id>     → resolve port (SQLite) → start Chrome → launch monitor script
bsession stop <id>    → kill monitor process group + Chrome
bsession restart <id> → stop + start
```

### Port Resolution (`resolve_port`)

1. **Explicit in conf** — if `[session]` has `port = 9225`, use that
2. **Already in DB** — if this session ran before, reuse its port
3. **Auto-assign** — next available starting from 9222

### `bsession run` in Detail

1. **Start Chrome** (`lib/browser.py:start_chrome`):
   - Kill any existing Chrome on that CDP port
   - Remove stale profile locks (`SingletonLock`, etc.)
   - Create stealth extension if needed
   - Launch `/usr/lib/chromium/chromium` with stealth flags
   - Poll `http://localhost:{port}/json/version` until CDP responds
   - Get actual PID via `pgrep`

2. **Launch monitor** as detached subprocess:
   - Export `[env]` vars, then set `CDP_PORT` (from SQLite, always wins) and `SESSION_NAME`
   - `subprocess.Popen([python3, script], start_new_session=True)`
   - stdout/stderr → `/workspace/data/logs/{id}.log`

### Cloudflare Bypass Strategy (3 tiers)

1. **CDP iframe click** (most reliable): Find Turnstile iframe `[ref=eXX]` in snapshot → `ab("click", ref)`
2. **xdotool** (fallback): Real X11 mouse events, simulates human-like movement
3. **Manual VNC** (last resort): Polls every 5s for 300s while user solves at `http://localhost:6080/vnc.html`

### Anti-Detection

- **No `--enable-automation` flag** — Chrome launched manually
- **`--disable-blink-features=AutomationControlled`** — removes automation banner
- **Stealth extension** (`/workspace/data/stealth-ext/`): patches `navigator.webdriver`
- **Persistent browser profile** — Cloudflare cookies survive restarts

## Session Manager Usage

```bash
# Create a conf file (workspace/conf/uscis.conf):
#   [session]
#   script = /workspace/scripts/uscis.py
#
#   [env]
#   RECEIPT_NUMBER = IOE0000000000
#   CHECK_INTERVAL = 1800

# Manage sessions
./bsession list                    # show all sessions with status/port
./bsession show uscis              # show conf + assigned port
./bsession run uscis               # start Chrome + monitor
./bsession run all                 # start all sessions
./bsession stop uscis              # stop Chrome + monitor
./bsession stop all                # stop everything
./bsession restart uscis           # stop + start
./bsession logs uscis -n 100       # tail logs
```

## Environment Variables

Defined in `.env` (passed into container):

- `VNC_PASSWORD` — Optional VNC password

Per-session config goes in the conf file's `[env]` section (e.g. `RECEIPT_NUMBER`, `CHECK_INTERVAL`, `N8N_WEBHOOK_URL`).

## Key Conventions

- `lib/browser.py` wraps `agent-browser` CLI: `ab(port, "snapshot")`, `ab_quiet(port, "click", ref)`
- `find_ref(snapshot, pattern)` — regex search on accessibility tree lines, extracts `[ref=xxx]`
- Each session: own Chrome instance, CDP port, browser profile, log file
- Monitors import from `/app/lib/browser.py` via `sys.path.insert(0, "/app")`
- Monitors read all config from env vars (set by `session.py run`)
- Logs go to `/workspace/data/logs/{session_id}.log` (captured stdout)
