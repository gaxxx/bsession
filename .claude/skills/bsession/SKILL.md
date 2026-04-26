---
name: bsession
description: Build browser automation skills using bsession primitives (Chrome + Cloudflare-aware). Use when the user wants to create a new browser automation, monitor a website, scrape data, or build a recurring check. Triggers on "build a skill", "monitor a website", "scrape", "automate browser", "建个监控", "写个 skill", "自动化".
---

# Building skills with bsession

Use this skill when the user wants to create a new browser automation. It provides primitives + conventions; you author the SKILL.md, run.sh, and forms for the new skill.

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

## Setup (per machine, one-time)

The bsession skill itself + the bsession docker container need to be installed:

```bash
# 1. Clone the bsession repo somewhere
git clone https://github.com/gaxxx/bsession ~/playground/bsession

# 2. Install bsession command to PATH
ln -s ~/playground/bsession/.claude/skills/bsession/bsession /usr/local/bin/bsession

# 3. Start the container (one-time)
cd ~/playground/bsession && docker compose up -d
```

## Setup (per project, one-time)

```bash
cd <your-project>
mkdir -p .claude/skills

# Pick one:
git submodule add ../bsession/.claude/skills/bsession .claude/skills/bsession   # if both repos sibling
ln -s ~/playground/bsession/.claude/skills/bsession .claude/skills/bsession      # symlink
cp -r ~/playground/bsession/.claude/skills/bsession .claude/skills/              # copy (no auto-update)
```

Verify: `bsession session list` should print `(no active sessions)` (or list active profiles).

## Templates

Available in `templates/`:
- `SKILL.md.template`
- `run.sh.template`
- `form.toml.template`

Each is heavily commented; fill in the bracketed placeholders.
