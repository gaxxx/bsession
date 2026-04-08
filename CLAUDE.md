# Project Overview

Browser automation engine running inside a Docker container with `agent-browser`, Chromium, and VNC. Automations are defined as **capabilities** — a TOML config, Markdown instructions, and TOML data forms — driven by the CLI and browser commands.

## Stack

- **Runtime**: Node 22-slim Docker image with Chromium + Python 3
- **Browser Control**: `agent-browser` CLI (Playwright-based, talks to Chrome via CDP)
- **Display**: Xvfb (virtual framebuffer) + Fluxbox + x11vnc + noVNC (web VNC at port 6080)
- **Session Manager**: `session.py` — browser command dispatcher
- **Port Allocation**: SQLite (`/workspace/data/ports.db`) — auto-assigns CDP ports
- **Notifications**: Webhooks (configurable URL)

## Project Structure

```
├── bsession              # Client-side CLI wrapper (runs session.py inside container)
├── session.py            # Session manager (browser command dispatcher)
├── lib/
│   ├── browser.py        # Core agent-browser CLI wrapper + snapshot parsing
│   ├── engine.py         # Instruction execution engine (parse + run tasks)
│   ├── chrome.py         # Chrome lifecycle (start, stop, stealth)
│   ├── bypass/
│   │   ├── __init__.py
│   │   └── cloudflare.py # 3-tier Cloudflare Turnstile bypass
│   ├── notify.py         # Webhook helpers
│   └── api.py            # HTTP API (port 8080)
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── workspace/            # Mounted at /workspace — user content only
    ├── conf/             # Session conf files (TOML format)
    │   └── uscis.toml
    ├── instructions/     # Step-by-step guides (Markdown)
    │   └── uscis.md
    ├── forms/            # Data input files (TOML)
    │   ├── uscis.default.toml
    │   └── uscis.john.toml
    ├── data/             # Runtime data (persists across restarts)
    │   ├── ports.db      # SQLite: port registry
    │   ├── pids/         # PID files
    │   ├── logs/         # Session logs
    │   ├── profile-*/    # Chrome profiles
    │   └── stealth-ext/  # Anti-detection extension
    └── scripts/          # Legacy monitor scripts
        └── uscis.py
```

## Architecture

### Capability Model (3 files per capability)

```
Capabilities
├── conf/<name>.toml         ← session config (Chrome port, file paths)
├── instructions/<name>.md   ← step-by-step guide (Markdown)
└── forms/<name>.*.toml      ← data inputs (TOML, multiple profiles)
```

**Conf** defines a session and links to its instructions and data. **Instructions** are human-readable Markdown guides describing the automation steps. **Forms** hold the data inputs (one per profile) used to fill forms during automation.

### Conf Format (TOML)

```toml
[session]
name = "uscis"
instructions = "instructions/uscis.md"
data = "forms/uscis.default.toml"
# port = 9222  # optional override
```

### Browser Layer

`lib/browser.py` wraps the `agent-browser` CLI:

- `ab(port, "snapshot")` — run a browser command and return output
- `ab_quiet(port, "click", ref)` — run a browser command silently
- `find_ref(snapshot, pattern)` — regex search on accessibility tree lines, extracts `[ref=xxx]`

### Container Startup

```
docker compose up -d
  → entrypoint.sh:
    1. mkdir -p /workspace/{conf,data,scripts,instructions,forms}
    2. Xvfb :99, Fluxbox, x11vnc, noVNC
    3. API server on port 8080
    4. tail -f /dev/null (keep alive)
```

### Session Lifecycle

Sessions are defined by `.toml` files in `workspace/conf/`. Start only launches Chrome:

```
bsession start <name>   → resolve port → start Chrome
bsession stop <name>    → kill Chrome
```

## CLI Reference

```bash
# Session management
bsession list                           # Show all sessions
bsession start <name>                   # Start Chrome
bsession stop <name>                    # Stop Chrome
bsession show <name>                    # Show conf details

# Task execution (auto-starts Chrome)
bsession <name>                         # Run default task, default data
bsession <name> <data.toml>             # Run default task, specific data
bsession <name> <task>                  # Run named task, default data
bsession <name> <task> <data.toml>     # Run named task, specific data

# Browser commands (session-name-first)
bsession <name> navigate <url> [-w N]   # Open URL
bsession <name> snapshot                # Print accessibility tree
bsession <name> click <ref>             # Click element
bsession <name> fill <ref> <value>      # Fill input
bsession <name> select <ref> <value>    # Select dropdown
bsession <name> type <ref> <text>       # Type text
bsession <name> clear <ref>             # Clear input
bsession <name> bypass                  # Handle Cloudflare
bsession <name> screenshot [-o path]    # Take screenshot

# Capabilities
bsession cap list                       # List capabilities
bsession cap show <name>                # Show capability details
```

## Instruction Format

Instructions are parseable Markdown files in `workspace/instructions/`. The engine (`lib/engine.py`) parses and executes them.

```markdown
# Title (informational)

## task_name
1. action <pattern> {data.section.key} -w N
2. ...
```

### Syntax

- `# Title` — informational heading
- `## task_name` — defines a task section (first = default)
- `N. action [args]` — numbered step (only numbered lines are parsed)
- `<pat1|pat2>` — element search patterns (pipe-separated, tried in order via `find_ref`)
- `{data.section.key}` — value from TOML data file
- `{var.name}` — reference to previously extracted variable
- `-w N` — wait N seconds after action

### Available Actions

| Action | Args | Description |
|---|---|---|
| `navigate` | `<url> [-w N]` | Open URL, optional wait, refresh snapshot |
| `bypass` | | Check/handle Cloudflare, refresh snapshot |
| `fill` | `<pattern> <value>` | Clear + fill input field |
| `click` | `<pattern> [-w N]` | Click element, optional wait, refresh snapshot |
| `select` | `<pattern> <value>` | Click dropdown, find option, click it |
| `type` | `<pattern> <value>` | Type text into element |
| `clear` | `<pattern>` | Clear input field |
| `extract` | `<name> <regex>` | Regex match on snapshot, store in variables |
| `wait` | `<seconds>` | Sleep, invalidate snapshot |
| `screenshot` | | Save screenshot to /tmp |
| `snapshot` | | Force snapshot refresh |
| `js` | `<element_id> <value>` | Set dropdown value via JS + dispatch change event |
| `click_all_no` | | Click all unchecked "No" radio buttons on the page |

## HTTP API (port 8080)

```
POST /run          {"command": "list|start|stop", "args": ["session_id"]}
POST /ab           {"port": 9222, "command": "snapshot|click|open", "args": [...]}
POST /chrome/start {"port": 9222, "profile": "..."}
POST /chrome/stop  {"port": 9222}
GET  /screenshot/<session_id>    — PNG of active tab
GET  /screenshot?port=9222       — PNG by CDP port
GET  /capabilities               — list registered capabilities (JSON)
GET  /health
```

## Key Conventions

- `lib/browser.py` wraps `agent-browser` CLI: `ab(port, "snapshot")`, `ab_quiet(port, "click", ref)`
- `find_ref(snapshot, pattern)` — regex search on accessibility tree lines, extracts `[ref=xxx]`
- Each session: own Chrome instance, CDP port, browser profile, log file
- Legacy scripts still work via `script = ...` in conf files

## Anti-Detection

- **No `--enable-automation` flag** — Chrome launched manually
- **`--disable-blink-features=AutomationControlled`** — removes automation banner
- **Stealth extension** (`/workspace/data/stealth-ext/`): patches `navigator.webdriver`
- **Persistent browser profile** — Cloudflare cookies survive restarts

## Cloudflare Bypass Strategy (3 tiers)

1. **CDP iframe click** (most reliable): Find Turnstile iframe in snapshot → click ref
2. **xdotool** (fallback): Real X11 mouse events with human-like movement
3. **Manual VNC** (last resort): Polls while user solves at `http://localhost:6080/vnc.html`
