---
name: browser
description: Browser automation — setup the bsession environment, fetch info from a website (one-shot), create scripted automations (one-shot or recurring), or debug existing sessions. Works from any repo.
user-invocable: true
metadata: {"openclaw":{"requires":{"bins":["curl"]}}}
---

# /browser skill

You help users automate browsers inside the bsession Docker container — whether it's initial setup, a quick interactive fetch, a scripted automation (one-shot or recurring), or debugging an existing session.

**This is a global skill** — it works from any repo.

## Resolve access method

Before doing anything, determine how to reach the agent-browser container. Try in order:

1. **HTTP API** (container-to-container): `curl -sf http://agent-browser:8080/health` → if this works, use the API
2. **HTTP API** (host): `curl -sf http://localhost:8080/health` → use the API via localhost
3. **docker exec** (host with Docker): `docker exec agent-browser echo ok` → use docker exec
4. **bsession CLI** (host): `command -v bsession` or `~/.bsession/bsession` → use the CLI

Store the chosen method as **ACCESS_MODE** (`api-container`, `api-host`, `docker-exec`, or `cli`) and use it for **all** commands.

### How to call commands in each mode

**Session commands** (list, show, run, stop, logs):

| Mode | Command |
|---|---|
| `api-container` | `curl -s -X POST http://agent-browser:8080/run -d '{"command":"list"}'` |
| `api-host` | `curl -s -X POST http://localhost:8080/run -d '{"command":"list"}'` |
| `docker-exec` | `docker exec agent-browser python3 /app/session.py list` |
| `cli` | `bsession list` |

**Agent-browser commands** (open, snapshot, click, fill):

| Mode | Command |
|---|---|
| `api-container` | `curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"snapshot"}'` |
| `api-host` | `curl -s -X POST http://localhost:8080/ab -d '{"port":9222,"command":"snapshot"}'` |
| `docker-exec` | `docker exec agent-browser agent-browser --cdp 9222 snapshot` |
| `cli` | `docker exec agent-browser agent-browser --cdp 9222 snapshot` |

**Chrome lifecycle** (start/stop):

| Mode | Command |
|---|---|
| `api-*` | `curl -s -X POST http://ENDPOINT:8080/chrome/start -d '{"port":9222}'` |
| `docker-exec` | `docker exec agent-browser python3 -c "..."` (see examples below) |

**API responses** are always JSON:
```json
{"stdout": "...", "stderr": "...", "returncode": 0}
```
Parse the `stdout` field to get the result. Check `returncode` for errors.

## Routing

Parse the user's slash command arguments:

- **No arguments or `list`** → List mode
- **`setup`** → Setup mode
- **`fetch <url>`** → Fetch mode (one-shot extraction, offers to persist)
- **`new <name>`** → Create mode (scaffold a script)
- **`run <name>`** → Run mode (execute and show results)
- **Otherwise** → Debug mode

## Pre-check (all modes except setup)

Verify the agent-browser is reachable using the resolve logic above. If none work, tell the user to run `/browser setup`.

---

## List mode (`/browser` or `/browser list`)

### Step 1: Get session status

```bash
# API mode
curl -s -X POST http://agent-browser:8080/run -d '{"command":"list"}'

# CLI mode
bsession list
```

### Step 2: Present as a table

Parse the stdout and display sessions with status, type, and description.

---

## Setup mode (`/browser setup`)

Run the install script:

```bash
bash ~/.openclaw/workspace/skills/browser/scripts/install.sh
```

Options: `--workspace <path>`, `--vnc-password <pw>`, `--repo <git-url>`

---

## Fetch mode (`/browser fetch <url>`)

One-shot: open a URL, extract information, return it.

### Step 1: Start a temporary Chrome

```bash
# API mode
curl -s -X POST http://agent-browser:8080/chrome/start -d '{"port":9222,"profile":"/workspace/data/profile-tmp"}'

# docker-exec mode
docker exec agent-browser python3 -c "
import sys; sys.path.insert(0, '/app')
from lib.browser import start_chrome
pid = start_chrome(9222, '/workspace/data/profile-tmp')
print(f'Chrome started, pid={pid}')
"
```

### Step 2: Navigate

```bash
# API mode
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"open","args":["URL"]}'
sleep 5
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"snapshot"}'

# docker-exec mode
docker exec agent-browser agent-browser --cdp 9222 open "URL"
sleep 5
docker exec agent-browser agent-browser --cdp 9222 snapshot
```

### Step 3: Handle Cloudflare

Check the snapshot for Cloudflare patterns (`Verify you are human`, `Just a moment`, `cf-turnstile`). If detected:

```bash
# API mode — find the Turnstile iframe ref in the snapshot, then click it
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"click","args":["IFRAME_REF"]}'
sleep 8
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"snapshot"}'
```

### Step 4: Interact

```bash
# API mode
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"fill","args":["REF","value"]}'
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"click","args":["REF"]}'
curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"snapshot"}'
```

### Step 5: Return results

Parse the snapshot stdout and present the information cleanly.

### Step 6: Offer to persist

Ask if the user wants to save as a reusable script. If yes, create conf + script in the workspace.

### Step 7: Cleanup

```bash
# API mode
curl -s -X POST http://agent-browser:8080/chrome/stop -d '{"port":9222}'

# docker-exec mode
docker exec agent-browser python3 -c "
import sys; sys.path.insert(0, '/app')
from lib.browser import stop_chrome
stop_chrome(9222)
"
```

---

## Create mode (`/browser new <name>`)

Ask the user what to build, then scaffold conf + script in the workspace. Same conventions as the Claude Code skill — see the Script conventions section below.

For **API mode**, create files via the host filesystem or use the run endpoint to write them:
```bash
curl -s -X POST http://agent-browser:8080/run -d '{"command":"show","args":["name"]}'
```

---

## Run mode (`/browser run <name>`)

```bash
# API mode
curl -s -X POST http://agent-browser:8080/run -d '{"command":"run","args":["name"]}'
sleep 15
curl -s -X POST http://agent-browser:8080/run -d '{"command":"logs","args":["name","-n","50"]}'

# CLI mode
bsession run name
sleep 15
bsession logs name -n 50
```

Parse logs and present results. If failed, switch to debug mode.

---

## Debug mode (`/browser <session-id>`)

1. Get status and logs via the run endpoint
2. Diagnose from log output
3. Fix the script or conf, then restart

---

## Script conventions

**Imports:**
```python
import os, re, sys, time
sys.path.insert(0, "/app")
from lib.browser import (
    ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare,
    send_webhook, make_logger,
)
```

**Config from env vars:**
```python
port = int(os.environ.get("CDP_PORT", 9222))
session_name = os.environ.get("SESSION_NAME", "<name>")
webhook_url = os.environ.get("N8N_WEBHOOK_URL", "")
check_interval = int(os.environ.get("CHECK_INTERVAL", 1800))
```

**Core pattern:** open URL → wait → snapshot → handle Cloudflare → find elements → interact → parse results

**One-shot:** execute and exit. **Recurring:** `while True` with sleep, compare state, webhook on change.

## Reference: lib/browser.py

- `ab(port, cmd, *args)` / `ab_quiet(port, cmd, *args)` — run agent-browser commands
- `find_ref(snapshot, pattern)` / `find_all_refs(snapshot, pattern)` — parse accessibility tree
- `is_cloudflare(snapshot)` / `wait_for_cloudflare(port, snapshot, ...)` — Cloudflare handling
- `send_webhook(url, payload)` — POST JSON to webhook
- `make_logger(session_name)` — create timestamped logger

## Reference: HTTP API endpoints

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/health` | GET | — | `{"status":"ok"}` |
| `/run` | POST | `{"command":"list","args":[]}` | `{"stdout":"...","stderr":"...","returncode":0}` |
| `/ab` | POST | `{"port":9222,"command":"snapshot","args":[]}` | `{"stdout":"...","stderr":"...","returncode":0}` |
| `/chrome/start` | POST | `{"port":9222,"profile":"/workspace/data/profile-tmp"}` | `{"pid":123,"port":9222}` |
| `/chrome/stop` | POST | `{"port":9222}` | `{"stopped":true,"port":9222}` |
| `/chrome/alive` | POST | `{"port":9222}` | `{"alive":true,"port":9222}` |
