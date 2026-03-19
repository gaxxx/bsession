# bsession

Browser automation sessions running inside Docker. Each session gets its own Chrome instance managed via CDP, with Cloudflare bypass, persistent profiles, and VNC access for debugging.

Ships as a [Claude Code](https://claude.ai/claude-code) `/browser` skill — fetch data from websites, create recurring monitors, or debug sessions from any repo.

## Setup

### One-Line Install

```bash
curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash
```

With options:
```bash
curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash -s -- --vnc-password secret
curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash -s -- --workspace ~/my-workspace
```

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Claude Code](https://claude.ai/claude-code)

### Manual Install

```bash
git clone https://github.com/gaxxx/bsession.git
cd bsession
bash .claude/skills/browser/scripts/install.sh
```

This installs bsession globally to `~/.bsession/` and registers the `/browser` skill in Claude Code. The installer handles:

1. [uv](https://github.com/astral-sh/uv) + Python 3.12 (if missing)
2. Copy source to `~/.bsession/`
3. Docker image build (Chromium + VNC + agent-browser)
4. Container start
5. `bsession` CLI symlinked to `~/.local/bin/`
6. `/browser` skill installed to `~/.claude/skills/browser/`

Options:
```bash
bash .claude/skills/browser/scripts/install.sh --workspace ~/my-workspace
bash .claude/skills/browser/scripts/install.sh --vnc-password secret
bash .claude/skills/browser/scripts/install.sh --repo https://github.com/gaxxx/bsession.git
```

Or from within Claude Code:
```
/browser setup
```

### Verify

```bash
bsession list
```

VNC web access at http://localhost:6080/vnc.html

### Skill Commands

After installation, `/browser` is available globally in Claude Code:

```
/browser                          # list all sessions and scripts
/browser setup                    # install on a new system
/browser fetch <url>              # one-shot: open URL, extract data (offers to save)
/browser run <name>               # re-run a saved session and show results
/browser new <name>               # scaffold a new automation (one-shot or recurring)
/browser <session-id>             # debug an existing session
```

## Use Case: USCIS Case Status Check

This example shows the full lifecycle: **fetch → save → run → monitor → debug**.

### 1. Quick check

```
/browser fetch https://egov.uscis.gov/casestatus/mycasestatus.do
```
> Enter receipt number WAC26900XXXXX and tell me the current case status.

Claude opens the page, bypasses Cloudflare, fills the receipt number, clicks "Check Status", and returns:

> **WAC26900XXXXX — Case Was Received**
>
> On November 5, 2025, we received your Form I-765, Application for Employment Authorization...

Then Claude asks: *Want me to save this as a reusable script?*

### 2. Save as a reusable script

Say `save it as uscis-check`. Claude creates:

**`workspace/conf/uscis-check.conf`:**
```ini
[session]
script = /workspace/scripts/uscis-check.py

[env]
RECEIPT_NUMBER = WAC26900XXXXX
```

**`workspace/scripts/uscis-check.py`** — a one-shot script that replays the exact same steps and exits.

### 3. Re-run anytime

```
/browser run uscis-check
```

```
[2026-03-19 01:54:53] [uscis-check] Opening USCIS case status page...
[2026-03-19 01:55:02] [uscis-check] Cloudflare detected.
[2026-03-19 01:55:10] [uscis-check] Cloudflare resolved via iframe click!
[2026-03-19 01:55:13] [uscis-check] Entering receipt number: WAC26900XXXXX
[2026-03-19 01:55:15] [uscis-check] Clicking Check Status...
[2026-03-19 01:55:20] [uscis-check] Status: Was Received
```

### 4. Upgrade to recurring monitor with webhook alerts

```
/browser new uscis-monitor
```
> Same flow as uscis-check, but loop every 30 minutes and webhook when status changes.

Claude scaffolds a recurring version:

```ini
# workspace/conf/uscis-monitor.conf
[session]
script = /workspace/scripts/uscis-monitor.py

[env]
RECEIPT_NUMBER = WAC26900XXXXX
CHECK_INTERVAL = 1800
N8N_WEBHOOK_URL = https://your-webhook-url.com/webhook/uscis
```

```bash
bsession run uscis-monitor    # runs forever, checks every 30min
```

Webhook payload on status change:
```json
{
  "session": "uscis-monitor",
  "receipt": "WAC26900XXXXX",
  "previous_status": "Case Was Received",
  "new_status": "Case Was Approved",
  "detail": "On March 15, 2026, we approved your Form I-140...",
  "timestamp": "2026-03-18 14:30:00"
}
```

Connect to n8n, Slack, Discord, Telegram, email — anything that accepts a webhook POST.

### 5. Debug if something breaks

```
/browser uscis-monitor
```

Claude reads logs, takes a screenshot, diagnoses the issue, and fixes the script.

## Disclaimer

This project is provided for **educational and personal use only**.

- **USCIS**: The USCIS case status check example is for personal case tracking only. This tool is not affiliated with, endorsed by, or associated with U.S. Citizenship and Immigration Services (USCIS) or the Department of Homeland Security (DHS). Users are responsible for complying with USCIS terms of service. Do not use this tool to scrape data at scale, overload government servers, or for any commercial purpose.
- **Cloudflare**: The Cloudflare bypass functionality is intended for accessing your own accounts and data on sites you are authorized to use. Do not use this to circumvent security measures on sites where you are not authorized.
- **General**: The authors are not responsible for any misuse of this software. Users must comply with all applicable laws, terms of service, and website policies. Automated access to websites may violate their terms of service — use at your own risk.

## Architecture

```
Docker Container (agent-browser)
├── Xvfb :99          virtual display (1280x900)
├── Fluxbox           window manager
├── x11vnc :5900      VNC server
├── noVNC :6080       web VNC proxy
└── Per session:
    ├── Chromium      own instance, CDP port, persistent profile
    └── Python script monitor/automation
```

### How Sessions Work

1. `bsession run <id>` reads the conf file
2. Resolves a CDP port (explicit in conf, or auto-assigned via SQLite)
3. Launches Chrome with stealth flags + persistent profile
4. Starts the Python script as a detached process
5. Script uses `agent-browser` CLI via CDP to control Chrome

### Browser Control API

Scripts use `lib/browser.py` which wraps the `agent-browser` CLI:

```python
from lib.browser import ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare

ab_quiet(port, "open", "https://example.com")     # navigate
snap = ab(port, "snapshot")                        # accessibility tree
ref = find_ref(snap, "button.*Submit")             # find element
ab_quiet(port, "click", ref)                       # click
ab_quiet(port, "fill", ref, "some value")          # fill input

if is_cloudflare(snap):
    wait_for_cloudflare(port, snap)                # 3-tier bypass
```

### Anti-Detection

- No `--enable-automation` flag
- `--disable-blink-features=AutomationControlled`
- Stealth extension patches `navigator.webdriver`
- Persistent browser profiles (cookies survive restarts)

### Cloudflare Bypass (3-tier)

1. **CDP iframe click** — find Turnstile iframe ref, click via agent-browser
2. **xdotool** — real X11 mouse events with human-like movement
3. **Manual VNC** — polls while user solves at `http://localhost:6080/vnc.html`

### Project Structure

```
bsession/
├── bsession              # CLI wrapper (bash → docker exec)
├── session.py            # Session manager (port allocation, lifecycle)
├── lib/browser.py        # Browser helpers (Chrome, snapshots, Cloudflare, webhooks)
├── Dockerfile            # Node 22-slim + Chromium + VNC + Python
├── docker-compose.yml    # Container orchestration
├── entrypoint.sh         # Starts Xvfb, Fluxbox, VNC, noVNC
├── .claude/skills/browser/
│   ├── SKILL.md          # Claude Code skill definition
│   └── scripts/
│       └── install.sh    # Self-contained installer
└── workspace/            # Mounted at /workspace in container
    ├── conf/             # Session configs (.conf files)
    ├── scripts/          # Automation scripts (.py files)
    └── data/             # Runtime: logs, PIDs, profiles, ports.db
```

## Contributing Scripts

The `workspace/scripts/` directory holds all automation scripts. We welcome contributions!

### How to contribute a script

1. Create your script in `workspace/scripts/<name>.py`
2. Create a matching conf in `workspace/conf/<name>.conf`
3. Follow the conventions below
4. Submit a PR

### Script conventions

Every script should:

- Start with a docstring describing what it does, required config, and optional config
- Import from `lib/browser.py` (available at `/app/lib/` inside the container)
- Read all config from environment variables (set via the conf `[env]` section)
- Use `make_logger(session_name)` for timestamped logging

```python
#!/usr/bin/env python3
"""Short description of what this monitors/fetches.

Required config:
  SOME_VAR — what it's for

Optional config:
  CHECK_INTERVAL — seconds between checks (default: 3600)
  N8N_WEBHOOK_URL — webhook for alerts
"""
import os, re, sys, time
sys.path.insert(0, "/app")
from lib.browser import (
    ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare,
    send_webhook, make_logger,
)
```

**One-shot scripts** — run once and exit. Good for data extraction, form submission.

**Recurring scripts** — loop with `time.sleep(check_interval)`. Good for monitoring. Should compare with previous state, webhook on change, retry with backoff.

### Ideas for scripts

- Price tracker (Amazon, Best Buy, etc.)
- Appointment availability checker
- Job posting monitor
- Flight price tracker
- Government form status checker
- Stock/crypto alert
- Website change detector

If you build something useful, please open a PR!

## License

MIT
