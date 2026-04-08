---
name: browser
description: Browser automation — setup the bsession environment, fetch info from a website (one-shot), create new capabilities, or follow existing capability instructions. Works from any repo.
argument-hint: "[list | setup | new <name> | <name>]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(bsession *), Bash(~/.bsession/bsession *), Bash(./bsession *), Bash(docker *), Bash(bash ~/.claude/skills/browser/scripts/*), Bash(chmod *), Bash(command -v *), Bash(which *), Bash(ls *)
---

# /browser skill

You help users automate browsers inside the bsession Docker container — whether it's initial setup, a quick interactive fetch, creating a new capability, or running an existing one.

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

- **`$ARGUMENTS` is empty or `list`** → List mode (show capabilities and sessions)
- **`$ARGUMENTS` starts with `setup`** → Setup mode (install and configure bsession)
- **`$ARGUMENTS` starts with `new`** → New mode (interactively explore a site and generate a capability)
- **Otherwise** → Capability mode (load and follow a registered capability's instructions)

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

Show all registered capabilities and active sessions.

### Step 1: List capabilities

```bash
bsession cap list
```

### Step 2: List sessions

```bash
bsession list
```

### Step 3: Present results

Combine the output from both commands into a clean summary. Append available commands:

```
Commands:
  /browser <name>           run a capability
  /browser new <name>       create a new capability
  /browser fetch <url>      quick one-shot fetch
  /browser list             show this view
```

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

## New mode (`/browser new <name>`)

Interactively explore a website, then generate a full capability (instructions + data form + conf).

The capability name is: `$ARGUMENTS` → strip `new ` prefix → that's `<name>`.

Capabilities are created in the bsession workspace: `~/.bsession/workspace/`.

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

### Step 5: Stop the exploration session

```bash
bsession stop <name>
```

### Step 6: Tell the user

```
Created capability "<name>":
  conf:         workspace/conf/<name>.toml
  instructions: workspace/instructions/<name>.md
  data form:    workspace/forms/<name>.default.toml

Run anytime:
  bsession <name>
  /browser <name>

Run with different data:
  bsession <name> forms/<name>.profile.toml

Edit the data form with your values:
  workspace/forms/<name>.default.toml
```

---

## Capability mode (`/browser <name>`)

Run a registered capability's instructions automatically using the execution engine.

The capability name is `$ARGUMENTS` (trimmed). May include a task name and/or data file override.

### Step 1: Run the capability

The execution engine (`lib/engine.py`) handles parsing instructions, loading data, and driving the browser. Use the task execution CLI:

```bash
# Run default task with default data (auto-starts Chrome)
bsession <name>

# Run default task with specific data file
bsession <name> forms/<name>.<profile>.toml

# Run a named task (for multi-task instructions like visa)
bsession <name> <task>

# Run a named task with specific data
bsession <name> <task> forms/<name>.<profile>.toml
```

Before running, verify the data form has real values (not placeholders):

```bash
# Check the data form
cat workspace/forms/<name>.default.toml
```

If any required fields are empty or have placeholder values, ask the user to provide values first.

### Step 2: Interpret results

The engine prints extracted variables at the end. Present results to the user.

### Step 3: Cleanup (optional)

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

**For format specs, snapshot reference, available actions, and browser API details**, read `CLAUDE.md` in the bsession project root (resolve via `BSESSION_HOME` or workspace parent). It is the single source of truth for:
- Instruction format (syntax, available actions table)
- Snapshot format (accessibility tree structure, `[ref=N]` conventions)
- Browser layer API (`ab()`, `find_ref()`, etc.)
- Cloudflare bypass strategy

**Never attempt to solve CAPTCHAs programmatically.** If a CAPTCHA appears, direct the user to VNC at `http://localhost:6080/vnc.html`.
