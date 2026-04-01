# Project Overview

Browser automation engine running inside a Docker container with `agent-browser`, Chromium, and VNC. Automations are defined as **skills** (declarative YAML) executed by a **toolset** (typed browser/bypass/notify wrappers), with **eval** tracking run metrics.

## Stack

- **Runtime**: Node 22-slim Docker image with Chromium + Python 3
- **Browser Control**: `agent-browser` CLI (Playwright-based, talks to Chrome via CDP)
- **Display**: Xvfb (virtual framebuffer) + Fluxbox + x11vnc + noVNC (web VNC at port 6080)
- **Session Manager**: `session.py` — manages Chrome + skill/script lifecycle
- **Skill Engine**: YAML skill definitions → toolset → runner → eval
- **Port Allocation**: SQLite (`/workspace/data/ports.db`) — auto-assigns CDP ports
- **Notifications**: Webhooks (configurable URL)

## Project Structure

```
├── bsession              # Client-side CLI wrapper (runs session.py inside container)
├── session.py            # Session manager (baked into image at /app/)
├── run_skill.py          # Subprocess entry point for skill execution
├── lib/
│   ├── browser.py        # Core agent-browser CLI wrapper + snapshot parsing
│   ├── chrome.py         # Chrome lifecycle (start, stop, stealth)
│   ├── bypass/
│   │   ├── __init__.py
│   │   └── cloudflare.py # 3-tier Cloudflare Turnstile bypass
│   ├── notify.py         # Webhook helpers
│   ├── api.py            # HTTP API (port 8080)
│   ├── toolset.py        # Typed tool wrappers (browser, bypass, notify, chrome, screenshot)
│   ├── skill.py          # Skill data model (YAML loader, variable resolution)
│   ├── runner.py         # Skill step executor + monitor loop
│   ├── eval.py           # SQLite run metrics recorder
│   └── builder.py        # Skill scaffolding + Claude/OpenClaw export
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── workspace/            # Mounted at /workspace — user content only
    ├── conf/             # Session conf files (INI format)
    │   └── uscis.conf
    ├── skills/           # Skill definitions (YAML)
    │   └── uscis_monitor.yaml
    ├── data/             # Runtime data (persists across restarts)
    │   ├── ports.db      # SQLite: port registry + run metrics
    │   ├── pids/         # PID files
    │   ├── logs/         # Session logs
    │   ├── profile-*/    # Chrome profiles
    │   └── stealth-ext/  # Anti-detection extension
    └── scripts/          # Legacy monitor scripts
        └── uscis.py
```

## Architecture

### Skill System (3 layers)

```
Skills (YAML)         ← what: declarative automation definitions
  workspace/skills/     navigate → bypass → find → fill → click → extract

Toolset (Python)      ← how: typed wrappers around browser primitives
  lib/toolset.py        tools.browser.click(ref), tools.bypass.cloudflare()

Engine (Python)       ← run: step executor + monitor loop + eval
  lib/runner.py         walk steps, resolve {{vars}}, track state
  lib/eval.py           record success/failure/duration per run
```

### Skill Format (YAML)

```yaml
name: my_monitor
description: "What this skill does"
version: "1.0.0"
params:
  - name: MY_PARAM
    description: "Required input"
    required: true
steps:
  - action: navigate
    url: "https://example.com"
    wait: 5
  - action: bypass
    type: cloudflare
  - action: find
    name: my_input
    patterns: [textbox, "input.*name"]
  - action: fill
    ref: "{{my_input}}"
    value: "{{MY_PARAM}}"
  - action: click
    ref: "{{submit_button}}"
    wait: 5
  - action: extract
    name: result_text
    pattern: 'heading "([^"]*)"'
result:
  title: "{{result_text}}"
monitor:                    # optional: run as recurring monitor
  interval: "3600"
  change_field: title
  notify:
    webhook: "{{WEBHOOK_URL}}"
```

### Step Actions

| Action | Params | Description |
|--------|--------|-------------|
| `navigate` | `url`, `wait` | Open URL, wait for load |
| `bypass` | `type` (cloudflare) | Handle protection pages |
| `find` | `name`, `patterns`, `optional` | Find element ref by trying patterns |
| `fill` | `ref`, `value` | Clear + fill input field |
| `clear` | `ref` | Clear input field |
| `click` | `ref`, `wait` | Click element |
| `type` | `ref`, `value` | Type text character by character |
| `select` | `ref`, `value`, `skip_if_empty` | Select dropdown option by text |
| `extract` | `name`, `pattern`, `exclude`, `max_lines` | Regex extract from snapshot |
| `wait` | `seconds` | Sleep |
| `wait_for` | `pattern`, `timeout`, `interval` | Poll until pattern appears in snapshot |
| `transform` | `name`, `source`, `operation`, ... | Transform a value (strip, replace, regex_extract, strip_chars) |
| `snapshot` | `name` | Take + store a snapshot |
| `check` | `snapshot_pattern`, `error` | Assert condition |

### Toolset API

```python
tools.browser.navigate(url, wait=3)
tools.browser.snapshot() -> str
tools.browser.click(ref)
tools.browser.fill(ref, value)
tools.browser.find_first(snapshot, patterns) -> str | None
tools.bypass.is_cloudflare(snapshot) -> bool
tools.bypass.cloudflare(snapshot, max_wait=300) -> bool
tools.bypass.is_blocked(snapshot) -> bool
tools.notify.webhook(url, payload) -> bool
tools.chrome.alive() -> bool
tools.screenshot.capture() -> bytes
```

### Skill Builder Workflow (5 steps)

1. **Name & describe**: `bsession skill create my_skill -d "description"`
2. **Define steps**: Edit the generated YAML in `workspace/skills/my_skill.yaml`
3. **Run & test**: `bsession skill run my_skill` — executes once, shows result
4. **Export**: `bsession skill export my_skill -f claude` or `-f openclaw`
5. **Iterate**: Check eval with `bsession skill eval my_skill`, update YAML

### Container Startup

```
docker compose up -d
  → entrypoint.sh:
    1. mkdir -p /workspace/{conf,data,scripts,skills}
    2. Xvfb :99, Fluxbox, x11vnc, noVNC
    3. API server on port 8080
    4. tail -f /dev/null (keep alive)
```

### Session Lifecycle

Sessions are defined by `.conf` files in `workspace/conf/`. Conf supports both `skill` and `script` modes:

```ini
# Skill-based (recommended)
[session]
skill = uscis_monitor

[env]
RECEIPT_NUMBER = IOE0000000000
CHECK_INTERVAL = 1800

# Legacy script-based
[session]
script = /workspace/scripts/uscis.py

[env]
RECEIPT_NUMBER = IOE0000000000
```

```
bsession run <id>     → resolve port → start Chrome → launch skill/script
bsession stop <id>    → kill process group + Chrome
bsession restart <id> → stop + start
```

### Eval System

Run metrics stored in `ports.db`:

```
bsession skill eval uscis
  Session: uscis
  Total runs:    47
  Success rate:  93.6%
  Avg duration:  12340ms
  P95 duration:  18200ms
  Last error:    Cloudflare bypass failed
```

## CLI Reference

```bash
# Session management
./bsession list                          # show all sessions
./bsession run uscis                     # start (skill or script)
./bsession stop uscis                    # stop
./bsession restart uscis                 # restart
./bsession logs uscis -n 100             # tail logs

# Skill builder
./bsession skill list                    # list available skills
./bsession skill create price_watch      # scaffold new skill YAML
./bsession skill show uscis_monitor      # show skill details
./bsession skill run uscis_monitor       # test run (once)
./bsession skill eval uscis              # show run history + stats
./bsession skill export uscis_monitor -f claude    # export as Claude skill
./bsession skill export uscis_monitor -f openclaw  # export as OpenClaw tool
```

## HTTP API (port 8080)

```
POST /run          {"command": "list|run|stop", "args": ["session_id"]}
POST /ab           {"port": 9222, "command": "snapshot|click|open", "args": [...]}
POST /chrome/start {"port": 9222, "profile": "..."}
POST /chrome/stop  {"port": 9222}
GET  /screenshot/<session_id>    — PNG of active tab
GET  /screenshot?port=9222       — PNG by CDP port
GET  /skills                     — list available skills (JSON)
GET  /eval/<session_id>          — run history + summary (JSON)
GET  /health
```

## Key Conventions

- `lib/browser.py` wraps `agent-browser` CLI: `ab(port, "snapshot")`, `ab_quiet(port, "click", ref)`
- `find_ref(snapshot, pattern)` — regex search on accessibility tree lines, extracts `[ref=xxx]`
- Each session: own Chrome instance, CDP port, browser profile, log file
- Skills use `{{variable}}` syntax for parameter interpolation
- Toolset is bound to a CDP port at construction — skills never see port numbers
- Eval records every run automatically (success/failure/duration/error)
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
