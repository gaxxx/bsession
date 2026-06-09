---
name: bsession
description: Build *headed* browser automation skills (visible Chromium with VNC). Use only when the target needs a real browser fingerprint or human handoff — Cloudflare Turnstile, CAPTCHA, JS-heavy SPA login flows, persistent browser cookies. For unprotected sites with public HTML or APIs, prefer headless tools (playwright headless, requests + bs4, curl). Triggers on "Cloudflare", "Turnstile", "CAPTCHA-aware", "USCIS-style monitoring", "site behind bot protection", "建个浏览器监控", "需要登录的站点".
---

# Building skills with bsession

bsession is a **headed browser automation engine** — Chromium runs with a visible UI inside a Docker container, accessible via VNC. This is deliberate: it lets the browser pass anti-bot fingerprinting, and lets a human take over via VNC when something asks for human verification.

## When to use bsession

- Target site has **Cloudflare Turnstile, hCaptcha, or similar bot detection**
- A step **needs human input** at runtime (CAPTCHA, 2FA prompt) and you want VNC handoff
- You need **persistent login cookies** that survive across runs
- The target is a JS-heavy SPA where headless detection or rendering quirks bite

## When NOT to use bsession

- Site has a clean **public API** → just `curl` / `requests`
- Page is plain HTML with no anti-bot → headless `playwright` or `requests + bs4` is faster + lighter
- You need to scale to **thousands of pages in parallel** → bsession's per-profile Chrome model isn't designed for that
- Just downloading static files → `wget` / `curl`

bsession trades efficiency for survival on protected sites. Don't pay that cost when you don't have to.

## What this skill provides

Primitives (a CLI), conventions (skill dir layout), and templates. You author the per-skill `SKILL.md`, `run.sh`, and `forms/*.toml`.

## Setup

Two things must be in place before any `bsession` primitive works:

**1. The `bsession` command.** This plugin puts a `bsession` shim on the Bash
`PATH` when enabled (it forwards CLI commands to the engine's HTTP API). If
`command -v bsession` is empty, the plugin isn't enabled — enable it in your
`settings.json` (see the plugin README / `docs/bsession-integration.md`).

**2. The bsession engine (a Docker container) must be running.** Primitives talk
to its HTTP API at `$BSESSION_API_URL` (default `http://host.docker.internal:18000`
inside a container, `http://localhost:18000` on a host). Always health-check
first, and bootstrap if it's down:

```bash
API="${BSESSION_API_URL:-http://localhost:18000}"
if curl -fsS "$API/health" >/dev/null 2>&1; then
  echo "bsession engine: up"
elif command -v docker >/dev/null 2>&1; then
  # On a host with Docker — build + start it (one-time per host; long-running)
  git clone https://github.com/gaxxx/bsession ~/.bsession-src 2>/dev/null \
    || git -C ~/.bsession-src pull --ff-only
  ( cd ~/.bsession-src && docker compose up -d --build )
  for i in $(seq 1 30); do curl -fsS "$API/health" >/dev/null 2>&1 && break; sleep 2; done
else
  echo "bsession engine is down and Docker isn't available here." >&2
  echo "Start it on the host:  cd <bsession repo> && docker compose up -d --build" >&2
fi
```

Note: a containerized consumer **without Docker access** (e.g. the persona
assistant) can't start the engine itself — it must be running on the host. The
container is long-running; this is a one-time setup per host.

## Skill anatomy

A bsession skill is a directory under `.claude/skills/<name>/`:

```
.claude/skills/<name>/
  SKILL.md              # frontmatter + routing for Claude (this file's analog)
  run.sh                # bash that chains bsession primitives, prints JSON
  forms/                # one *.toml per instance (per case / account / target)
    <basename>.toml
```

See `.claude/skills/uscis-check/` in the bsession repo for a complete worked example.

## Steps to build a new skill

1. **Pick a name** (kebab-case, e.g., `stock-watch`, `flight-price`).
2. **Scaffold from templates**:
   ```bash
   SKILL=stock-watch
   mkdir -p .claude/skills/$SKILL/forms
   cp .claude/skills/bsession/templates/SKILL.md.template     .claude/skills/$SKILL/SKILL.md
   cp .claude/skills/bsession/templates/run.sh.template       .claude/skills/$SKILL/run.sh
   cp .claude/skills/bsession/templates/form.toml.template    .claude/skills/$SKILL/forms/example.toml
   chmod +x .claude/skills/$SKILL/run.sh
   ```
3. **Edit `forms/example.toml`** — define the fields each instance needs (e.g., ticker, URL, threshold).
4. **Edit `run.sh`** — chain bsession primitives to navigate, find, fill, click, extract. Print JSON.
5. **Edit `SKILL.md`** — fill in the description with trigger phrases, routing table, output shape.
6. **Test**: `bash .claude/skills/$SKILL/run.sh .claude/skills/$SKILL/forms/example.toml`
7. **Iterate**: tweak regex / waits / selectors based on what real snapshots return.

## bsession primitive reference

`bsession` is in PATH (see Setup). Each command resolves the form context from `BSESSION_FORM`, ensures a Chrome process for the form's profile, and runs.

```bash
# Browser
bsession nav <url> [--wait N]              # open URL in form's profile Chrome
bsession snapshot [-i] [-c] [-d N]         # accessibility tree (-i = interactive only, -c = compact)
bsession find <pattern> [--all]            # regex on snapshot lines, returns ref(s)
bsession click <ref> [--wait N]
bsession fill <ref> <value>                # clear + fill input
bsession type <ref> <value>                # type char by char
bsession select <ref> <value>              # pick dropdown option
bsession extract <regex> [--max-lines N] [--exclude P]   # regex extract from snapshot
bsession wait <seconds>
bsession wait-for <pattern> [--timeout N] [--interval N]
bsession screenshot [--output FILE]

# Bypass
bsession bypass cloudflare [--max-wait N]  # auto-click Turnstile, fall back to VNC

# Form access (reads BSESSION_FORM TOML)
bsession form get <key>
bsession form dump                         # whole toml as JSON (for piping into jq)
bsession form list                         # list field names

# Session admin
bsession session list [--json]
bsession session close <profile>
bsession session forget <profile>          # close + delete profile dir (cookie reset)

# Misc
bsession notify <url> --json '...'         # POST to webhook
```

## Conventions

- **Profile = skill name**: same-skill forms share one Chrome process + cookies (good for Cloudflare clearance reuse). Override per form with `_bsession_profile = "..."` in the toml.
- **Output = JSON to stdout**: typically `bsession form dump | jq --arg s "$STATUS" '. + {status: $s}'`. One JSON line per form.
- **Stderr for progress**: anything informational (cloudflare detected, etc.) goes to stderr so JSON output is clean.
- **Exit code**: 0 for success, non-zero for failure.

## Cloudflare

- `bsession bypass cloudflare` tries CDP iframe click first (works with stealth flags), falls back to manual VNC.
- Cookies persist in profile, so first run of the day might block; subsequent runs pass.
- For visual CAPTCHAs (image grids, distorted text) — never auto-click. Bsession will detect and hand off to VNC.

## VNC

If a skill stalls on Cloudflare or asks for human interaction, the user opens http://localhost:6080/vnc.html, sees the browser, solves manually. bsession polls and resumes when the challenge clears.

## Standalone install (without the plugin)

The recommended path is the Claude Code **plugin** (see Setup above) — it ships
this skill and puts `bsession` on PATH. If you instead want bsession directly on
a host *without* the plugin, the host wrapper drives the container over
`docker exec`:

```bash
# 1. Clone the repo
git clone https://github.com/gaxxx/bsession ~/playground/bsession

# 2. Put the host wrapper on PATH
ln -s ~/playground/bsession/.claude/skills/bsession/bsession /usr/local/bin/bsession

# 3. Start the container (one-time; long-running)
cd ~/playground/bsession && docker compose up -d --build
```

Verify: `bsession session list` prints `(no active sessions)` or the active profiles.

Don't enable both the plugin and this symlink — they'd put two different
`bsession` commands on PATH.

## Templates

Available in `templates/`:
- `SKILL.md.template`
- `run.sh.template`
- `form.toml.template`

Each is heavily commented; fill in the bracketed placeholders.

## CAPTCHA human hand-off (channel-agnostic)

The cloak backend auto-resolves non-interactive Turnstile with no human needed.
When a real challenge appears, bsession provides the image; the *consumer*
supplies the human channel and feeds the answer back. Contract:

1. Detect: `bsession captcha bounds` (exit 1 = no captcha → proceed).
2. Capture: `bsession captcha screenshot --output <png>` (or
   `GET $BSESSION_API_URL/captcha/screenshot?profile=<p>` for bytes).
3. Classify by looking at the image:
   - **Text-entry** (distorted characters you can type) → out-of-band channel.
   - **Click-grid / interactive** (reCAPTCHA tiles, hCaptcha) → VNC hand-off
     (`$BSESSION_VNC_URL`, default `http://localhost:6080/vnc.html`).
4. Out-of-band answer: send the PNG to the human over your channel, get the
   typed answer, then `bsession fill <ref> <answer>` and submit.
5. Fallbacks: wrong answer → retry up to 2×; grid/interactive or timeout →
   send the VNC URL for manual solve.

### Reference channel: Telegram (persona)

```bash
PNG=/tmp/captcha.png
bsession captcha screenshot --output "$PNG"
bun run bin/tg-send-photo.ts "$TELEGRAM_CHAT_ID" "$PNG" "Solve this captcha; reply with the text."
# poll bin/tg-pull.ts until a reply arrives (timeout ~5 min):
ANSWER=$(bun run bin/tg-pull.ts | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m[-1]["text"] if m else "")')
[ -n "$ANSWER" ] && bsession fill "$REF" "$ANSWER"
```
