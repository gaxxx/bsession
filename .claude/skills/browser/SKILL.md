---
name: browser
description: Browser automation — setup the bsession environment, fetch info from a website (one-shot), create scripted automations (one-shot or recurring), or debug existing sessions. Works from any repo. Use when the user wants to set up bsession, scrape/extract data from a URL, build a browser automation script, or troubleshoot a running session.
argument-hint: "[list | setup | fetch <url> | new <name> | run <name> | <session-id>]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(bsession *), Bash(~/.bsession/bsession *), Bash(./bsession *), Bash(docker *), Bash(bash ~/.claude/skills/browser/scripts/*), Bash(chmod *), Bash(command -v *), Bash(which *), Bash(ls *)
---

# /browser skill

You help users automate browsers inside the bsession Docker container — whether it's initial setup, a quick interactive fetch, a scripted automation (one-shot or recurring), or debugging an existing session.

**This is a global skill** — it works from any repo. bsession is installed at `~/.bsession/`, and the `bsession` CLI is on PATH.

## Resolve paths

Before doing anything, determine how to reach bsession. Check in this order:

1. `bsession` on PATH → use `bsession`
2. `~/.bsession/bsession` exists → use `~/.bsession/bsession`
3. `./bsession` in current directory → use `./bsession`
4. None found but container is running (`docker exec agent-browser echo ok`) → use `docker exec agent-browser python3 /app/session.py` as the CLI

```bash
# Resolve CLI
if command -v bsession &>/dev/null; then BSESSION_CLI="bsession"
elif [ -x ~/.bsession/bsession ]; then BSESSION_CLI="~/.bsession/bsession"
elif [ -x ./bsession ]; then BSESSION_CLI="./bsession"
elif docker exec agent-browser echo ok &>/dev/null 2>&1; then BSESSION_CLI="docker exec agent-browser python3 /app/session.py"
fi
```

Similarly, resolve workspace:
1. `~/.bsession/workspace/` exists → use it
2. `./workspace/` in current directory → use it
3. Ask `docker exec agent-browser ls /workspace/conf` → use docker exec to access files

Use these resolved paths for **all** commands throughout the session. Store them as `BSESSION_CLI` and `WORKSPACE_PATH`.

## Constants (defaults)

- **BSESSION_HOME**: `~/.bsession/` — where bsession source + docker-compose live
- **WORKSPACE**: `~/.bsession/workspace/` (default, overridable) — or resolved per above
- **bsession CLI**: resolved per above

## Routing

- **`$ARGUMENTS` is empty or `list`** → List mode (show all available scripts and sessions)
- **`$ARGUMENTS` starts with `setup`** → Setup mode (install and configure bsession)
- **`$ARGUMENTS` starts with `fetch`** → Fetch mode (interactive one-shot extraction, with option to persist)
- **`$ARGUMENTS` starts with `new`** → Create mode (scaffold a script — one-shot or recurring)
- **`$ARGUMENTS` starts with `run`** → Run mode (execute a saved session and show results)
- **Otherwise** → Debug mode (inspect/fix an existing session)

## Pre-check (all modes except setup)

Before running any mode except setup, verify the container is running:

```bash
docker exec agent-browser echo ok 2>/dev/null
```

If this fails, tell the user to either:
- Run `/browser setup` for a fresh install, or
- Run `docker compose up -d` from the bsession project directory

---

## List mode (`/browser` or `/browser list`)

Show all available scripts, their status, and what they do.

### Step 1: Get session status

```bash
bsession list
```

### Step 2: Read script docstrings

For each `.py` file in `~/.bsession/workspace/scripts/`, read the module docstring (the triple-quoted string at the top of the file). This contains:
- What the script does
- Required config variables
- Optional config variables

```bash
# Get all scripts
ls ~/.bsession/workspace/scripts/*.py 2>/dev/null

# For each script, extract the docstring (first triple-quoted block)
```

Use the Read tool to read the first ~20 lines of each script to get the docstring.

### Step 3: Read conf files

For each `.conf` file in `~/.bsession/workspace/conf/`, read the `[env]` section to show current configuration.

### Step 4: Present as a table

Display a summary like:

```
Session       Status    Type        Description
─────────────────────────────────────────────────────────────────
uscis         running   recurring   USCIS case status monitor
price-check   stopped   one-shot    Amazon product price scraper
login-test    stopped   one-shot    Login flow smoke test

Available commands:
  /browser <name>           debug a session
  /browser new <name>       create a new automation
  /browser fetch <url>      quick one-shot fetch
```

For each session, determine the type by checking if the script has a `while True` loop (recurring) or not (one-shot).

---

## Setup mode (`/browser setup`)

Install and configure bsession on a new system. Runs the install script which handles everything.

### Run the install script

The install script is at `~/.claude/skills/browser/scripts/install.sh`. It accepts options:

```bash
# Basic install (copies source from current repo to ~/.bsession/)
bash ~/.claude/skills/browser/scripts/install.sh

# Clone from a git remote
bash ~/.claude/skills/browser/scripts/install.sh --repo https://github.com/user/bsession.git

# Custom workspace directory
bash ~/.claude/skills/browser/scripts/install.sh --workspace /path/to/my/workspace

# With VNC password
bash ~/.claude/skills/browser/scripts/install.sh --vnc-password mysecret

# Build only, don't start
bash ~/.claude/skills/browser/scripts/install.sh --no-start
```

### Before running

Ask the user:
1. Where is the bsession source? (current directory, or a git URL via `--repo`)
2. Custom workspace path? (default: `~/.bsession/workspace`)
3. VNC password? (default: none)

The script will:
1. Check Docker is installed and running
2. Install `uv` + Python 3.12 if not present
3. Copy/clone bsession source to `~/.bsession/`
4. Set up workspace directories
5. Configure `.env`
6. Build the Docker image
7. Start the container
8. Symlink `bsession` CLI to `~/.local/bin/`
9. Save bsession home path to `~/.claude/skills/browser/.bsession-home`
10. Verify the full stack

### If the script fails

Read the error output and help the user fix it. Common issues:
- **Docker not installed** — guide to Docker Desktop (macOS) or docker-ce (Linux)
- **Docker not running** — start Docker Desktop or `sudo systemctl start docker`
- **Source not found** — run from the bsession repo, or use `--repo <url>`
- **Port conflict** — another service on 5900/6080

---

## Fetch mode (`/browser fetch <url>`)

One-shot: open a URL, extract information, return it. No script, no conf file, no loop.

### Step 1: Find an available CDP port

```bash
docker exec agent-browser python3 -c "
import urllib.request
try:
    urllib.request.urlopen('http://localhost:9222/json/version', timeout=2)
    print('IN_USE')
except:
    print('FREE')
"
```

If 9222 is in use, try 9223, 9224, etc. Once you have a free port, start a temporary Chrome:

```bash
docker exec agent-browser python3 -c "
import sys; sys.path.insert(0, '/app')
from lib.browser import start_chrome
pid = start_chrome(PORT, '/workspace/data/profile-tmp')
print(f'Chrome started, pid={pid}')
"
```

### Step 2: Navigate and extract

```bash
docker exec agent-browser agent-browser --cdp PORT open "URL"
sleep 5
docker exec agent-browser agent-browser --cdp PORT snapshot
```

If the snapshot shows Cloudflare, handle it:

```bash
docker exec agent-browser python3 -c "
import sys; sys.path.insert(0, '/app')
from lib.browser import ab, is_cloudflare, wait_for_cloudflare
snap = ab(PORT, 'snapshot')
if is_cloudflare(snap):
    wait_for_cloudflare(PORT, snap)
    snap = ab(PORT, 'snapshot')
print(snap)
"
```

### Step 3: Parse and interact

Based on the snapshot (accessibility tree), use `find_ref` to locate elements:

```bash
docker exec agent-browser agent-browser --cdp PORT fill REF "value"
docker exec agent-browser agent-browser --cdp PORT click REF
docker exec agent-browser agent-browser --cdp PORT snapshot
```

### Step 4: Return results

Parse the relevant information from the final snapshot and present it cleanly.

### Step 5: Offer to persist

After returning results, **always ask**:

> Want me to save this as a reusable script? You can re-run it anytime with `bsession run <name>`.

If the user says yes (or provides a name), create a one-shot script + conf that replays the exact same steps:

1. Pick a session name (from user input, or derive from the URL domain, e.g. `uscis-check`)
2. Create `~/.bsession/workspace/conf/<name>.conf`:
   ```ini
   [session]
   script = /workspace/scripts/<name>.py

   [env]
   # Any values that were used during the fetch (receipt numbers, search terms, etc.)
   ```
3. Create `~/.bsession/workspace/scripts/<name>.py` — a one-shot script (no `while True` loop) that:
   - Reproduces the exact navigation steps from the fetch (open URL, handle Cloudflare, fill forms, click buttons, parse results)
   - Logs the result and exits
   - Extracts any user-provided values (receipt numbers, search terms) into env vars so they're configurable in the conf

4. Tell the user:
   ```
   Saved as "<name>". Re-run anytime:
     bsession run <name>
     bsession logs <name>

   To make it a recurring monitor, add CHECK_INTERVAL to the conf
   and I can convert the script to loop mode.
   ```

If the user says no, proceed to cleanup.

### Step 6: Cleanup

```bash
docker exec agent-browser python3 -c "
import sys; sys.path.insert(0, '/app')
from lib.browser import stop_chrome
stop_chrome(PORT)
"
```

---

## Create mode (`/browser new <name>`)

The session name is: `$ARGUMENTS` → strip `new ` prefix → that's `<name>`.

Scripts and confs are created in the bsession workspace: `~/.bsession/workspace/`.

### Step 1: Gather requirements

Ask the user (briefly, in one message):
1. What URL(s) to target
2. What to do — extract data once, or monitor for changes over time?
3. If multi-step: what pages/forms/buttons to navigate through
4. What to do with results — print, save to file, webhook, etc.
5. Any env vars needed (credentials, intervals, etc.)

Based on the answer, determine the **execution mode**:
- **One-shot** — run once, extract/do something, exit. No `while True` loop.
- **Recurring** — loop forever with `CHECK_INTERVAL` sleep.

### Step 2: Scaffold the conf file

Create `~/.bsession/workspace/conf/<name>.conf`:

```ini
[session]
script = /workspace/scripts/<name>.py

[env]
# env vars the script needs
# Only include CHECK_INTERVAL for recurring scripts
```

### Step 3: Scaffold the script

Create `~/.bsession/workspace/scripts/<name>.py` using these conventions:

**Imports & setup:**
```python
import os, re, sys, time
sys.path.insert(0, "/app")
from lib.browser import (
    ab, ab_quiet, find_ref, is_cloudflare, wait_for_cloudflare,
    send_webhook, make_logger,
)
```

**Config from env vars** (set by session.py at launch):
```python
port = int(os.environ.get("CDP_PORT", 9222))
session_name = os.environ.get("SESSION_NAME", "<name>")
webhook_url = os.environ.get("N8N_WEBHOOK_URL", "")
# Only for recurring:
check_interval = int(os.environ.get("CHECK_INTERVAL", 1800))
```

**Core automation pattern (both modes):**
1. `ab_quiet(port, "open", url)` → navigate
2. `time.sleep(N)` → wait for page load
3. `snap = ab(port, "snapshot")` → get accessibility tree
4. Handle Cloudflare: `if is_cloudflare(snap): wait_for_cloudflare(port, snap, log=log)`
5. `find_ref(snap, pattern)` → locate elements
6. `ab_quiet(port, "click", ref)` / `ab_quiet(port, "fill", ref, value)` → interact
7. Parse results from snapshot with regex

**One-shot scripts:**
- Execute steps, output/save results, then `sys.exit(0)`
- Can navigate multiple pages sequentially
- Save output to `/workspace/data/<name>-output.json` or similar
- No `while True` loop

**Recurring scripts (monitors):**
- Wrap in `while True` with `time.sleep(check_interval)`
- Compare with previous state, send webhook on change
- Save last-known state to `/workspace/data/<name>-{session_name}-last-status.txt`
- Retry up to 5 times with exponential backoff, then exit

### Step 4: Verify

```bash
bsession run <name>
bsession logs <name>
```

---

## Run mode (`/browser run <name>`)

Execute a saved session (typically a one-shot script previously saved from fetch mode) and show the results.

The session name is: `$ARGUMENTS` → strip `run ` prefix → that's `<name>`.

### Step 1: Verify the session exists

```bash
bsession show <name>
```

If it doesn't exist, tell the user and suggest `/browser new <name>` or `/browser fetch <url>` instead.

### Step 2: Run the session

```bash
bsession run <name>
```

### Step 3: Wait and show results

For one-shot scripts (no `while True` loop), the process will exit after completion. Tail the logs to show output:

```bash
# Wait a moment for the script to complete, then show logs
sleep 10
bsession logs <name> -n 50
```

If the script is still running after a reasonable time, show the latest logs and let the user know it's still working.

### Step 4: Present results

Parse the log output and present the results cleanly to the user. If the script saved output to a file (e.g., `/workspace/data/<name>-output.json`), read and display that too.

If the script failed, switch to debug mode behavior: diagnose the issue from the logs and offer to fix it.

---

## Debug mode (`/browser <session-id>`)

The session ID is `$ARGUMENTS` (trimmed).

### Step 1: Gather state

Run in parallel:
1. `bsession list` — is the session running?
2. `bsession show <id>` — conf + port
3. Read the log: `~/.bsession/workspace/data/logs/<id>.log` (tail last 100 lines)
4. Read the script from conf path

### Step 2: Diagnose

Common problems:
- **Cloudflare stuck** — repeated "Cloudflare detected" with no resolution
- **Element not found** — snapshot changed, `find_ref` patterns need updating. Take a fresh snapshot: `docker exec agent-browser agent-browser --cdp <port> snapshot`
- **Crash/exit** — Python tracebacks in logs
- **Wrong data parsed** — regex patterns don't match current page structure
- **Process not running** — Chrome died or script exited; suggest `bsession restart <id>`

### Step 3: Fix

Edit scripts at `~/.bsession/workspace/scripts/<id>.py` or conf at `~/.bsession/workspace/conf/<id>.conf`. After fixing:
```bash
bsession restart <id>
```

---

## Example walkthrough: One-shot USCIS status check (fetch mode)

This is the canonical example for fetch mode — a quick one-time check with form interaction.

**User says:** `/browser fetch https://egov.uscis.gov/casestatus/mycasestatus.do`
> Enter receipt number IOE1234567890 and tell me the current case status.

**You do (step by step):**
1. Start a temporary Chrome on a free CDP port
2. Navigate to the URL, wait for load
3. Take a snapshot, check for Cloudflare — handle if needed
4. Find the textbox (`find_ref(snap, "textbox")`)
5. Fill the receipt number: `ab_quiet(port, "fill", ref, "IOE1234567890")`
6. Find and click "Check Status" button
7. Take a new snapshot, parse the status title and details
8. Clean up (stop temporary Chrome)

**You respond:**
> Your USCIS case IOE1234567890:
>
> **Case Was Approved**
>
> On March 15, 2026, we approved your Form I-140, Immigrant Petition for Alien Workers...

No files created. No processes left running. The user gets their answer right in the conversation.

---

## Example walkthrough: USCIS case status monitor (create mode)

This is the canonical example to follow when a user wants to create a recurring monitor with webhook alerts.

**User says:** `/browser new uscis`

**You ask:**
> A few quick questions:
> 1. What URL to monitor?
> 2. What to detect — what counts as a "change"?
> 3. Where to send alerts? (webhook URL)
> 4. Any env vars needed? (receipt number, check interval, etc.)

**User answers:** Monitor https://egov.uscis.gov/casestatus/mycasestatus.do, enter a receipt number, click Check Status, detect when the status text changes. Send webhook. Env vars: RECEIPT_NUMBER, CHECK_INTERVAL=1800, N8N_WEBHOOK_URL.

**You create two files:**

1. `~/.bsession/workspace/conf/uscis.conf`:
```ini
[session]
script = /workspace/scripts/uscis.py

[env]
RECEIPT_NUMBER = IOE0000000000
CHECK_INTERVAL = 1800
N8N_WEBHOOK_URL = https://your-webhook-url.com/webhook/uscis
```

2. `~/.bsession/workspace/scripts/uscis.py` — following the pattern from the reference uscis.py. Key elements:
   - `check_status(port, receipt, log)` function: open page → handle Cloudflare → find textbox → fill receipt → click Check Status → parse result title + detail
   - `main()`: load previous status from file → loop with `check_status()` → compare → webhook on change → save state → sleep
   - Use multiple `find_ref` fallback patterns for resilience
   - Webhook payload: `{session, receipt, previous_status, new_status, detail, timestamp}`
   - Exponential backoff: retry up to 5 times, then exit

**You tell the user:**
```
Created workspace/conf/uscis.conf and workspace/scripts/uscis.py.

Edit the conf with your real values:
  RECEIPT_NUMBER = <your receipt number>
  N8N_WEBHOOK_URL = <your webhook URL>

Then run:
  bsession run uscis
  bsession logs uscis

When the status changes, a webhook fires with the old/new status.
If something goes wrong: /browser uscis
```

---

## Reference: uscis.py (canonical example — recurring monitor)

Read `~/.bsession/workspace/scripts/uscis.py` when you need to see the full recurring monitor pattern. Key structure:

- `check_status(port, receipt, log)` — single check cycle, returns parsed data or raises
- `main()` — reads env vars, runs infinite loop with retry logic
- Uses `find_ref` with multiple fallback patterns for resilience
- Saves state to file, compares on each cycle, webhooks on change

For one-shot scripts, follow the same conventions but without the `while True` loop.

## Reference: lib/browser.py (available functions)

Read `~/.bsession/lib/browser.py` for the full API. Key functions:
- `ab(port, cmd, *args)` / `ab_quiet(port, cmd, *args)` — run agent-browser commands
- `find_ref(snapshot, pattern)` / `find_all_refs(snapshot, pattern)` — parse accessibility tree
- `is_cloudflare(snapshot)` / `wait_for_cloudflare(port, snapshot, ...)` — Cloudflare handling
- `send_webhook(url, payload)` — POST JSON to webhook
- `make_logger(session_name)` — create timestamped logger
