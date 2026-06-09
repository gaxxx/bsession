# bsession ↔ persona integration with Telegram CAPTCHA handoff

**Date:** 2026-06-09
**Status:** Approved (design)
**Repos touched:** bsession (API + skill), packaged as a Claude Code plugin;
persona just enables the plugin

## Goal

Let persona (a Telegram-driven Claude Code assistant in its own Docker
container) drive bsession browser automations, and — when a site shows a
**text-entry CAPTCHA** — send the challenge image to the user over Telegram,
take the typed answer back, and fill it in. Interactive/grid captchas fall back
to the existing VNC hand-off.

Keep bsession decoupled from any specific channel: bsession owns the primitives
and a documented, channel-agnostic hand-off contract; persona is one consumer of
it (the Telegram channel). Deliver the consumer side as a **Claude Code plugin**
(skill + `bin/bsession` shim) so any instance — persona included — enables it
with one command; no persona-specific spec, bin suite, or bespoke skill.

## Background / current state

- persona container: cwd `/workspace`, no `bsession` on PATH, **no docker CLI or
  socket**. Has Telegram tools `bin/tg-send-photo.ts` and `bin/tg-pull.ts`. Its
  `uscis-check`/`ds160` skills already *call* `bsession …` primitives but have no
  working `bsession` command in-container (they only ran from the host).
- bsession container (`agent-browser`): publishes its HTTP API, `:6080`
  (noVNC), and `:5900` (VNC) to the host. This design moves the API to port
  **18000** (was 8080). Has CAPTCHA primitives
  (`captcha screenshot` → PNG, `captcha bounds`) and the per-profile CLI.
- The two containers are on isolated docker networks.

## Decision: transport is `host.docker.internal`, no network changes

bsession publishes its API port to the host. Docker Desktop gives every
container `host.docker.internal`, so persona reaches bsession at
`http://host.docker.internal:18000` with **no docker-network changes** on either
side. (Verified the `host.docker.internal` path works from inside the persona
container against the running API; this design fixes the port at 18000.)

**Port:** the API listens on **18000** (was 8080); compose publishes
`18000:18000`. The API port is configurable via `BSESSION_API_PORT`
(default 18000).

Rejected alternatives:
- **Shared docker network** — works, but requires editing persona's compose.
- **`network_mode: host` on the agent-browser container** — on Docker Desktop
  for macOS host networking is a flaky beta and doesn't share the Mac stack the
  way Linux does; it also widens bsession's host exposure. The published-port +
  `host.docker.internal` path Just Works and changes nothing.
- **docker socket in persona** — lets the host wrapper's `docker exec` work, but
  gives persona full control of the Docker daemon (security smell).

Portability note: `host.docker.internal` is automatic on Docker Desktop
(Mac/Windows). On a native Linux host, the consumer adds one line —
`extra_hosts: ["host.docker.internal:host-gateway"]`. On the current Mac setup,
nothing.

## Components

### 1. `POST /cli` endpoint (bsession `lib/api.py`)

Request: `{"profile": "<name>", "argv": ["nav", "<url>", "--wait", "8"]}`.
Runs `python3 -m lib.cli --profile <profile> <argv…>` as a subprocess inside the
agent-browser container; returns `{"stdout": …, "stderr": …, "code": N}`.

- `argv` is passed as a **list** to the subprocess (no shell) → no shell
  injection. The subprocess only ever invokes the bsession CLI.
- One endpoint exposes the entire primitive surface (nav/find/fill/type/select/
  extract/wait/wait-for/bypass/screenshot/session/captcha) with the profile +
  LRU model intact — no per-command API sprawl.
- Auth: reachable by anything that can hit the host port. Treated as an internal
  bridge for now. **Future hardening (not MVP):** optional shared-token header.

### 2. `?profile=` on the captcha endpoints (bsession `lib/api.py`)

`GET /captcha/screenshot?profile=<p>` and `GET /captcha/bounds?profile=<p>`
resolve the profile → CDP port via `lib.state`, then reuse the existing
port-based capture. `screenshot` returns PNG **bytes** so a consumer can forward
the image. (The existing `?port=` form stays for back-compat.)

### 3. `bin/bsession` shim (persona drop-in, on PATH)

A ~30-line script mirroring the host wrapper's contract so persona's existing
`run.sh` scripts work unchanged:

- Resolve profile like `form.resolve`: `BSESSION_PROFILE` → form's
  `_bsession_profile` → skill_id (derived from `BSESSION_FORM` path).
- `form get|dump|list` → read the **local** TOML directly (forms live in
  persona's repo; no round-trip, no staging).
- Everything else → `POST $BSESSION_API_URL/cli` with `{profile, argv}`; print
  `stdout`, echo `stderr`, exit with `code`.
- `BSESSION_API_URL` defaults to `http://host.docker.internal:18000`.

### 4. CAPTCHA hand-off contract (bsession `SKILL.md`)

Generalize the existing "VNC handoff" line into a documented, channel-agnostic
contract any consumer can implement:

> **CAPTCHA hand-off.** `bsession captcha screenshot` → PNG of the challenge.
> Route that image to a human over any channel, get the answer, feed it back via
> `bsession fill <ref> <answer>` + submit. Two reference channels:
> **VNC** (interactive/grid captchas — manual solve via the noVNC URL) and
> **out-of-band image→text** (text-entry captchas — send the picture, type the
> answer back). Non-interactive Turnstile is auto-resolved by the cloak backend
> with no human involvement.

No generic `--send/--recv` hook is added to the CLI (premature coupling).

### 5. Telegram channel (persona — emergent, no new skill)

persona implements the out-of-band channel using tools it already owns; the
behavior is driven by the contract in (4) plus persona's `CLAUDE.md`, not a new
skill or spec:

1. Run the bsession flow; after submit, detect a captcha
   (`bsession captcha bounds`).
2. Fetch the captcha PNG (`GET /captcha/screenshot?profile=<p>`) → temp file.
3. persona **looks at the image** and classifies: typeable text → Telegram path;
   click-grid/interactive → VNC fallback.
4. Text path: `bun run bin/tg-send-photo.ts <png> "Solve this captcha and reply
   with the text."` → poll `bin/tg-pull.ts` for the reply (timeout, default
   5 min) → `bsession fill <ref> "<reply>"` → submit.
5. Fallbacks: wrong answer → retry up to 2 times; grid/interactive or timeout →
   Telegram-send the VNC URL for manual solve.

## Delivery: a Claude Code plugin (recommended)

Rather than hand-copying files into persona, package the consumer side as a
**Claude Code plugin**. A plugin's `bin/` directory is added to the Bash tool's
`PATH` while the plugin is enabled, which cleanly solves the one awkward part —
getting the `bsession` shim onto PATH so bare `bsession …` resolves in existing
`run.sh` scripts.

```
bsession-plugin/
├── .claude-plugin/plugin.json
├── bin/
│   └── bsession            # the HTTP shim (Component 3) — on PATH when enabled
└── skills/
    └── bsession/           # the skill spec incl. the CAPTCHA hand-off contract
        ├── SKILL.md
        └── templates/
```

Consuming from persona (or any Claude Code instance):
1. Enable the plugin (`claude --plugin-dir <path>` in dev, or install from a
   git marketplace: `claude plugin install bsession@<marketplace>`).
2. Set `BSESSION_API_URL` if not the default; the container prerequisite (the
   `agent-browser` container must be running) is documented in the skill.

Notes:
- Skills can call bare `bsession` (plugin enabled) or
  `"${CLAUDE_PLUGIN_ROOT}"/bin/bsession` explicitly.
- The plugin ships the **generic** bsession skill + shim only. The user's
  **personal** skills and real forms (e.g. persona's `uscis-check` cases) stay
  in persona's own repo/vault — they just gain a working `bsession` command from
  the plugin. This matches persona's "personal skills live outside the shared
  harness" philosophy.
- Tradeoff vs. drop-ins: a plugin is reusable across any Claude Code instance and
  needs no file copying, but bare `bsession` only resolves while the plugin is
  enabled. Acceptable; the skill documents the prerequisite.

Everything server-side (`/cli`, captcha-by-profile, port 18000) lives in bsession
and is reusable by any consumer regardless of delivery.

## Configuration

- `BSESSION_API_PORT` (bsession API) — default `18000`; compose publishes
  `18000:18000`.
- `BSESSION_API_URL` (persona shim) — default `http://host.docker.internal:18000`.
- `BSESSION_VNC_URL` — the human-reachable noVNC URL sent on fallback; default
  `http://localhost:6080/vnc.html`. Must point at a reachable address for a
  remote user (an `frpc` tunnel container is already present to expose it).

## Error handling

- agent-browser down → shim returns a clear "browser service unreachable"; the
  flow reports it to the user.
- `captcha bounds` returns none → cloak already passed; proceed.
- Reply timeout or repeated wrong answers → VNC fallback over Telegram.

## Testing

- **Unit:** shim profile-resolution + local-form handling; `/cli` argv
  list-passing (injection-safe); captcha endpoint profile→port resolution.
- **Integration:** from inside the persona container, `bsession session list`
  reaches agent-browser; run `uscis-check` end-to-end through the shim; force a
  text-captcha path and verify the round-trip (photo sent → reply consumed →
  field filled).

## Out of scope (YAGNI)

- Shared-token auth on `/cli` (note only).
- Generic channel hook in the CLI; non-Telegram channels (the contract supports
  them, but only Telegram is implemented now).
- Solving click-grid captchas over Telegram (those go to VNC).
