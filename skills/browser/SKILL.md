---
name: browser
description: Browser automation — setup the bsession environment, fetch info from a website (one-shot), create new capabilities, or follow existing capability instructions. Works from any repo.
user-invocable: true
metadata: {"openclaw":{"requires":{"bins":["curl"]}}}
---

# /browser skill

You help users automate browsers inside the bsession Docker container — whether it's initial setup, a quick interactive fetch, creating a new capability, or running an existing one.

**This is a global skill** — it works from any repo.

## Resolve access method

Before doing anything, determine how to reach the agent-browser container. Try in order:

1. **HTTP API** (container-to-container): `curl -sf http://agent-browser:8080/health` → if this works, use the API
2. **HTTP API** (host): `curl -sf http://localhost:8080/health` → use the API via localhost
3. **docker exec** (host with Docker): `docker exec agent-browser echo ok` → use docker exec
4. **bsession CLI** (host): `command -v bsession` or `~/.bsession/bsession` → use the CLI

Store the chosen method as **ACCESS_MODE** (`api-container`, `api-host`, `docker-exec`, or `cli`) and use it for **all** commands.

### How to call commands in each mode

**Session commands** (list, start, stop, show, cap):

| Mode | Command |
|---|---|
| `api-container` | `curl -s -X POST http://agent-browser:8080/run -d '{"command":"list"}'` |
| `api-host` | `curl -s -X POST http://localhost:8080/run -d '{"command":"list"}'` |
| `docker-exec` | `docker exec agent-browser python3 /app/session.py list` |
| `cli` | `bsession list` |

**Task execution** (run a capability's instructions via the engine):

| Mode | Command |
|---|---|
| `docker-exec` | `docker exec agent-browser python3 /app/session.py <name> [task] [data.toml]` |
| `cli` | `bsession <name> [task] [data.toml]` |

**Browser commands** (navigate, snapshot, click, fill, bypass):

| Mode | Command |
|---|---|
| `api-container` | `curl -s -X POST http://agent-browser:8080/ab -d '{"port":9222,"command":"snapshot"}'` |
| `api-host` | `curl -s -X POST http://localhost:8080/ab -d '{"port":9222,"command":"snapshot"}'` |
| `docker-exec` | `docker exec agent-browser python3 /app/session.py <name> snapshot` |
| `cli` | `bsession <name> snapshot` |

**Chrome lifecycle** (start/stop):

| Mode | Command |
|---|---|
| `api-*` | `curl -s -X POST http://ENDPOINT:8080/chrome/start -d '{"port":9222}'` |
| `docker-exec` / `cli` | `bsession start <name>` / `bsession stop <name>` |

**API responses** are always JSON:
```json
{"stdout": "...", "stderr": "...", "returncode": 0}
```
Parse the `stdout` field to get the result. Check `returncode` for errors.

## Routing

Parse the user's slash command arguments:

- **No arguments or `list`** → List mode
- **`setup`** → Setup mode
- **`new <name>`** → New mode (interactively explore a site and generate a capability)
- **Otherwise** → Capability mode (run a registered capability)

## Pre-check (all modes except setup)

Verify the agent-browser is reachable using the resolve logic above. If none work, tell the user to run `/browser setup`.

---

## List mode (`/browser` or `/browser list`)

### Step 1: Get capabilities and sessions

```bash
bsession cap list
bsession list
```

### Step 2: Present results

Combine the output into a clean summary. Append available commands:

```
Commands:
  /browser <name>           run a capability
  /browser new <name>       create a new capability
  /browser list             show this view
```

---

## Setup mode (`/browser setup`)

Run the install script:

```bash
bash ~/.claude/skills/browser/scripts/install.sh
```

Options: `--workspace <path>`, `--vnc-password <pw>`, `--repo <git-url>`

---

## New mode (`/browser new <name>`)

Interactively explore a website, then generate a full capability (instructions + data form + conf).

The capability name is: `$ARGUMENTS` → strip `new ` prefix → that's `<name>`.

### Step 1: Gather requirements

Ask the user (briefly, in one message):
1. What URL(s) to target
2. What to do — extract data, fill a form, check a status?
3. If multi-step: what pages/forms/buttons to navigate through
4. What data inputs are needed? (receipt numbers, search terms, credentials, etc.)

### Step 2: Interactive exploration

Start a session and explore the target site:

```bash
bsession start <name>
bsession <name> navigate "<url>" -w 5
bsession <name> snapshot
```

Walk through the site step by step:
- Read snapshots to understand page structure
- Fill forms, click buttons, handle Cloudflare
- Note every step needed to reach the goal
- Identify which values are user-supplied inputs (these become form fields)

### Step 3: Generate the capability files

**Read existing examples first.** Before writing files, read:
- `CLAUDE.md` in the bsession project root — "Instruction Format" section for the full spec
- 1-2 existing instruction files from `workspace/instructions/` as format examples
- The matching conf and form files to see naming conventions

Then create three files following the patterns from the examples:
- `workspace/conf/<name>.toml` — session config
- `workspace/instructions/<name>.md` — parseable engine instructions (convert your exploration steps into numbered action lines)
- `workspace/forms/<name>.default.toml` — data form

### Step 4: Test the capability

Run it to verify:

```bash
bsession <name>
```

If it fails, adjust patterns or add `wait`/`snapshot` steps as needed.

### Step 5: Stop and tell the user

```bash
bsession stop <name>
```

```
Created capability "<name>":
  conf:         workspace/conf/<name>.toml
  instructions: workspace/instructions/<name>.md
  data form:    workspace/forms/<name>.default.toml

Run anytime:
  bsession <name>
  /browser <name>
```

---

## Capability mode (`/browser <name>`)

Run a registered capability's instructions automatically using the execution engine.

The capability name is `$ARGUMENTS` (trimmed). May include a task name and/or data file override.

### Step 1: Check the data form

```bash
bsession cap show <name>
```

Review the data form values. If any required fields have placeholders or are empty:
- If the user provided values in their message, **update the form file** before running
- Otherwise, ask the user to provide values first

### Step 2: Run the capability

The execution engine (`lib/engine.py`) handles parsing instructions, loading data, and driving the browser. It auto-starts Chrome if needed.

```bash
# Run default task with default data
bsession <name>

# Run default task with specific data file
bsession <name> forms/<name>.<profile>.toml

# Run a named task (for multi-task instructions)
bsession <name> <task>

# Run a named task with specific data
bsession <name> <task> forms/<name>.<profile>.toml
```

### Step 3: Interpret results

The engine prints extracted variables at the end. Present results to the user.

**If extracts are empty or wrong**, the instruction patterns likely don't match the actual page. Debug and fix:

1. Take a snapshot to see the real page structure:
   ```bash
   bsession <name> snapshot
   ```
2. Search the snapshot for the expected content (grep for keywords)
3. Update the `extract` patterns in `workspace/instructions/<name>.md` to match
4. Stop and re-run to verify:
   ```bash
   bsession stop <name>
   bsession <name>
   ```

### Step 4: Cleanup (optional)

```bash
bsession stop <name>
```

### Manual fallback

If the engine fails or you need finer control, you can still run individual browser commands:

```bash
bsession <name> navigate "<url>" -w 5
bsession <name> snapshot
bsession <name> bypass
bsession <name> fill <ref> "value"
bsession <name> click <ref>
```

---

## Discovering capabilities and references

**Do not rely on hardcoded capability lists.** Discover dynamically:

```bash
bsession cap list                        # all registered capabilities
bsession cap show <name>                 # conf, instructions, data paths + contents
```

**For format specs, snapshot reference, available actions, and browser API details**, read `CLAUDE.md` in the bsession project root. It is the single source of truth for:
- Instruction format (syntax, available actions table)
- Snapshot format (accessibility tree structure, `[ref=N]` conventions)
- Browser layer API (`ab()`, `find_ref()`, etc.)
- Cloudflare bypass strategy

**Never attempt to solve CAPTCHAs programmatically.** If a CAPTCHA appears, direct the user to VNC at `http://localhost:6080/vnc.html`.

## Reference: HTTP API endpoints

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/health` | GET | — | `{"status":"ok"}` |
| `/run` | POST | `{"command":"list\|start\|stop","args":[]}` | `{"stdout":"...","stderr":"...","returncode":0}` |
| `/ab` | POST | `{"port":9222,"command":"snapshot\|click\|open","args":[]}` | `{"stdout":"...","stderr":"...","returncode":0}` |
| `/chrome/start` | POST | `{"port":9222,"profile":"..."}` | `{"pid":123,"port":9222}` |
| `/chrome/stop` | POST | `{"port":9222}` | `{"stopped":true,"port":9222}` |
| `/chrome/alive` | POST | `{"port":9222}` | `{"alive":true,"port":9222}` |
| `/capabilities` | GET | — | JSON list of registered capabilities |
| `/screenshot/<session_id>` | GET | — | PNG image |
