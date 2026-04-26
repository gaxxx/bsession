# Project Overview

Browser automation engine running inside a Docker container with `agent-browser`,
Chromium, and VNC. Skills are **Claude Code skills** (`.claude/skills/<name>/`)
containing a `SKILL.md` (Claude-facing routing), an optional `run.sh`
(orchestration), and `forms/*.toml` (per-instance config). The `bsession` CLI
exposes browser primitives — Claude/run.sh chains them to drive automation.

## Stack

- **Runtime**: Node 22-slim Docker image with Chromium + Python 3
- **Browser Control**: `agent-browser` CLI (Playwright-based, talks to Chrome via CDP)
- **Display**: Xvfb + Fluxbox + x11vnc + noVNC (web VNC at port 6080)
- **bsession CLI**: Primitive browser commands; manages per-profile Chrome lifecycle
- **State**: SQLite at `/workspace/.bsession-state/state.db` (chromes table, LRU)

## Project Structure

```
├── bsession                 # Host wrapper (rsync skill → workspace; docker exec lib.cli)
├── lib/
│   ├── cli.py               # bsession primitive CLI (entrypoint inside container)
│   ├── state.py             # SQLite chromes registry + LRU
│   ├── ab.py                # agent-browser session-scoped runner
│   ├── form.py              # form.toml resolution → (skill_id, form_id, profile)
│   ├── chrome.py            # Chrome lifecycle + stealth ext
│   ├── browser.py           # legacy ab() wrapper (kept for back-compat)
│   ├── bypass/cloudflare.py # Cloudflare detection + bypass helpers
│   ├── notify.py            # webhook helper
│   ├── captcha.py           # captcha bounding box / screenshot
│   └── api.py               # HTTP API on container port 8080
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── .claude/skills/
    └── uscis-check/
        ├── SKILL.md         # Claude routing + usage
        ├── run.sh           # orchestration script (chains bsession primitives)
        └── forms/
            └── example.toml
```

## How a skill runs

```
user: "check my USCIS case"
   ↓ Claude reads .claude/skills/uscis-check/SKILL.md, picks form
   ↓ Claude runs: bash .claude/skills/uscis-check/run.sh forms/example.toml
   ↓
run.sh:
  export BSESSION_FORM=...
  bsession nav https://egov.uscis.gov/...    # primitive
  bsession bypass cloudflare                  # primitive
  bsession find textbox                       # primitive
  bsession fill <ref> <receipt>               # primitive
  bsession click <ref>                        # primitive
  bsession extract 'heading "Case ([^"]*)"'   # primitive
  ...
   ↓ each `bsession` invocation:
      host: rsync skill dir → ~/.bsession/workspace/<skill>/
      docker exec agent-browser python3 -m lib.cli <subcommand>
   ↓
lib.cli: ensure_chrome(profile) → run agent-browser cmd → print result
```

## Skill conventions

- **Skill dir layout**: `<skill>/SKILL.md` + `<skill>/forms/<basename>.toml` + optional `<skill>/run.sh`
- **Profile**: defaults to `skill_id` (parent dir name) — same-skill forms share one Chrome + cookies. Override per form with `_bsession_profile = "..."` in toml.
- **Form schema**: any TOML fields. Reserved keys start with `_bsession_*`.
- **Output**: run.sh prints JSON to stdout (one line per case).

## bsession primitives

```
bsession nav <url> [--wait N]
bsession snapshot [-i] [-c] [-d N]
bsession find <pattern> [--all]
bsession click <ref> [--wait N]
bsession fill <ref> <value>
bsession type <ref> <value>
bsession select <ref> <value>
bsession extract <regex> [--max-lines N] [--exclude P]
bsession wait <seconds>
bsession wait-for <pattern> [--timeout N] [--interval N]
bsession bypass cloudflare [--max-wait N]
bsession screenshot [--output FILE]
bsession notify <url> --json '...'

bsession form get <key>
bsession form dump
bsession form list

bsession session list [--json]
bsession session close <profile>
bsession session forget <profile>            # close + delete profile dir
```

`BSESSION_FORM` env var sets the form context for all primitives.
`BSESSION_PROFILE` overrides the profile (otherwise from form).

## Session model

- One Chrome process per profile (LRU evicted, cap = `BSESSION_MAX_PROFILES`, default 5)
- Each profile has its own user-data-dir at `/workspace/.bsession-state/profiles/<profile>/`
- agent-browser is invoked with `--session bs-<profile>` so profiles don't interfere
- Chrome started with `--remote-allow-origins=*` so CDP HTTP endpoints work

## Cloudflare bypass

- Tier 1: CDP iframe click on the Turnstile checkbox (works most of the time with stealth flags)
- Tier 2: hand off to manual VNC at http://localhost:6080/vnc.html, poll until resolved
- **Visual CAPTCHAs** (image grids, distorted text): always go straight to VNC. Don't try programmatically.

## Anti-detection

- `--remote-allow-origins=*` on Chrome (needed for CDP HTTP)
- `--disable-blink-features=AutomationControlled` removes automation banner
- Stealth extension at `/workspace/.bsession-state/stealth-ext/` patches `navigator.webdriver`
- Persistent profile per skill — Cloudflare cookies survive container restarts

## Container lifecycle

```
docker compose up -d
  → entrypoint.sh:
    1. mkdir /workspace
    2. Xvfb :99, Fluxbox, x11vnc, noVNC
    3. python3 /app/lib/api.py (port 8080)
    4. tail -f /dev/null
```

Long-running container; Chrome processes started on demand by `bsession`.

## Legacy

`bsession --legacy <cmd>` forwards to the container's older `session.py`
(TOML-based session manager with `instructions`/`data` fields). Used by
existing skills that haven't been migrated to the `.claude/skills/` format yet.

## HTTP API (port 8080)

```
POST /run          {"command": "list|run|stop", "args": [...]}      # legacy session.py wrapper
POST /ab           {"port": 9222, "command": "snapshot", "args": [...]}
POST /chrome/start {"port": 9222, "profile": "..."}
POST /chrome/stop  {"port": 9222}
POST /browse       {"port": 9222, "url": "...", "wait": 5}
POST /click        {"port": 9222, "ref": "e5"}
POST /fill         {"port": 9222, "ref": "e3", "value": "..."}
POST /snapshot     {"port": 9222}
GET  /screenshot?port=9222           — PNG of active tab
GET  /screenshot/<session_id>        — PNG by session name (legacy)
GET  /captcha/screenshot?port=9222   — PNG of captcha bounding box
GET  /captcha/bounds?port=9222       — captcha bounding box JSON
GET  /health
```
