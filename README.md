# bsession

Headed browser automation for Claude Code skills. A visible Chromium runs inside a Docker container, and a `bsession` CLI exposes browser primitives (`nav`, `find`, `click`, `fill`, `extract`, …) that your skills chain together. Built for sites that defeat headless scraping — Cloudflare Turnstile, CAPTCHAs, JS-heavy SPAs, anything that needs persistent login cookies or a human handoff via VNC.

bsession ships **as a skill itself** — once installed, Claude reads its [`SKILL.md`](.claude/skills/bsession/SKILL.md) and can scaffold new browser-automation skills on demand.

## When to use it

- Site has **Cloudflare Turnstile, hCaptcha**, or similar bot detection
- A step **needs human input** at runtime (CAPTCHA, 2FA) — you want to hand off to VNC and resume automatically
- You need **persistent login cookies** that survive across runs
- The target is a **JS-heavy SPA** where headless detection bites

**Don't** use it for sites with a public API, plain HTML, or anything you can scrape with `requests + bs4` or `playwright headless` — those are faster and lighter.

## Setup

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Claude Code](https://claude.ai/claude-code)

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash
```

Options:

```bash
curl -fsSL .../install.sh | bash -s -- --vnc-password secret
curl -fsSL .../install.sh | bash -s -- --workspace ~/work/bsession
```

The installer:

1. Clones the repo to `~/.bsession/source/` (re-running updates via `git pull`)
2. Starts the Docker container (Chromium + agent-browser + VNC + noVNC)
3. Symlinks `bsession` to `~/.local/bin/bsession`
4. Symlinks the bsession skill to `~/.claude/skills/bsession/` so Claude Code can invoke it from any project

### Manual install

```bash
git clone https://github.com/gaxxx/bsession.git ~/playground/bsession
cd ~/playground/bsession
bash .claude/skills/bsession/scripts/install.sh
```

### Uninstall

```bash
bash ~/.bsession/source/.claude/skills/bsession/scripts/install.sh --uninstall
```

(Stops the container, removes `bsession` from PATH, removes the skill symlink. Workspace data is preserved.)

### OpenClaw integration

If `~/.openclaw/workspace/skills/` exists, the installer also symlinks the skill into `~/.openclaw/workspace/skills/bsession/` so OpenClaw can invoke it.

To run bsession alongside the OpenClaw gateway, use the OpenClaw compose file (mounts `~/.openclaw/workspace/bsession` instead of `~/.bsession/workspace`):

```bash
cd ~/.bsession/source
docker compose -f docker-compose.openclaw.yml up -d
```

Point the host CLI at the right workspace:

```bash
# in your shell rc
export BSESSION_WORKSPACE="$HOME/.openclaw/workspace/bsession"
```

The `bsession` command honors `BSESSION_WORKSPACE` for the rsync target so forms get staged to the path the OpenClaw container actually mounts.

## Quickstart

After install, run the example USCIS skill:

```bash
bash ~/.bsession/source/.claude/skills/uscis-check/run.sh \
     ~/.bsession/source/.claude/skills/uscis-check/forms/example.toml
```

Output:

```json
{
  "person": "Example Person",
  "case_type": "I-765 EAD",
  "receipt_number": "WAC1234567890",
  "status": "(unknown)",
  "detail": ""
}
```

The placeholder receipt won't return a real status. Edit `forms/example.toml` (or copy it to a new file) with your own receipt number, run again.

## Building your own skill

In any project where you've installed bsession, ask Claude:

> Build a skill that monitors XYZ price daily.

Claude reads bsession's [`SKILL.md`](.claude/skills/bsession/SKILL.md) and scaffolds a new skill at `.claude/skills/<name>/` with `SKILL.md` + `run.sh` + `forms/example.toml`. Templates live at [`.claude/skills/bsession/templates/`](.claude/skills/bsession/templates/).

A skill is just three files:

```
.claude/skills/<your-skill>/
  SKILL.md          # frontmatter + routing for Claude
  run.sh            # bash that chains bsession primitives, prints JSON
  forms/
    <name>.toml     # one per instance (case / account / target)
```

`run.sh` skeleton:

```bash
#!/usr/bin/env bash
set -euo pipefail
export BSESSION_FORM="${1:?form path required}"

URL=$(bsession form get url)
bsession nav "$URL" --wait 5
bsession bypass cloudflare
RESULT=$(bsession extract '<your regex>' --max-lines 1)
bsession form dump | jq --arg r "$RESULT" '. + {result: $r}'
```

Each `bsession` invocation auto-rsyncs your skill dir into `~/.bsession/workspace/<skill>/` so the container can read it via the existing `/workspace` mount — your skills can live anywhere on disk.

## bsession primitives

```
# Browser
bsession nav <url> [--wait N]
bsession snapshot [-i] [-c]
bsession find <pattern> [--all]
bsession click <ref> [--wait N]
bsession fill <ref> <value>
bsession type <ref> <value>
bsession select <ref> <value>
bsession extract <regex> [--max-lines N] [--exclude P]
bsession wait <seconds>
bsession wait-for <pattern> [--timeout N]
bsession screenshot [--output FILE]

# Bypass + capture
bsession bypass cloudflare [--max-wait N]
bsession captcha bounds
bsession captcha screenshot [--padding N] [--output FILE]

# Form access (reads $BSESSION_FORM)
bsession form get <key>
bsession form dump
bsession form list

# Session admin
bsession session list [--json]
bsession session close <profile>
bsession session forget <profile>     # close + delete profile dir
```

`BSESSION_FORM` env var sets the form context; `BSESSION_PROFILE` overrides the profile (default = skill name; same-skill forms share Chrome + cookies).

## Architecture

- **One Chrome process per profile** (LRU evicted, default cap 5) inside the container; each profile has its own `user-data-dir` so cookies persist across runs.
- **agent-browser** speaks CDP to Chrome; `lib/cli.py` invokes it with a per-profile `--session bs-<profile>` so profiles don't interfere.
- **Forms get rsynced** from project location → `~/.bsession/workspace/<skill_id>/` on every `bsession` call, so skills can stay in your project repo while the container reads them through the workspace mount.
- **State** at `~/.bsession/workspace/.bsession-state/`: SQLite chrome registry, profile dirs, optional captcha PNG dumps.

See [`CLAUDE.md`](CLAUDE.md) for code-level details.

## VNC

Live view of the browser: <http://localhost:6080/vnc.html>

Use it to:
- Watch a skill in action
- Solve a Cloudflare/CAPTCHA challenge that auto-bypass missed (`bsession bypass cloudflare` polls until the challenge clears)
- Debug "why is the button not where I expect"

If you set `--vnc-password` during install, log in with that password.

## Troubleshooting

- **`Container 'agent-browser' is not running`** — `docker compose -f ~/.bsession/source/docker-compose.yml up -d`
- **`bsession: command not found`** — `~/.local/bin` not on `$PATH`. Add it: `export PATH="$HOME/.local/bin:$PATH"` (in shell rc).
- **Cloudflare doesn't auto-pass** — open VNC and click the checkbox manually; bsession polls every 5 s and resumes when cleared.
- **Stuck Chrome / weird state** — `bsession session forget <profile>` nukes the Chrome process + profile dir; next run starts fresh.

## License

MIT — see [LICENSE](LICENSE).
