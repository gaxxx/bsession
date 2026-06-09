# bsession ↔ persona integration (plugin + Telegram CAPTCHA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let persona (and any Claude Code instance) drive bsession over HTTP via a Claude Code plugin, with a Telegram round-trip for text-entry CAPTCHAs.

**Architecture:** bsession gains an HTTP `/cli` endpoint (runs the existing per-profile CLI) and profile-aware captcha endpoints, served on port 18000. The consumer side ships as a Claude Code plugin: a `bin/bsession` shim (pure HTTP transport, on PATH when the plugin is enabled) plus the bsession skill spec documenting a channel-agnostic CAPTCHA hand-off. persona implements the Telegram channel using its existing `tg-send-photo`/`tg-pull` tools — no bespoke persona skill.

**Tech Stack:** Python 3 (stdlib `http.server`), bash + curl (shim), Docker Compose, Claude Code plugin manifest, Bun (persona Telegram tools).

**Spec:** `docs/superpowers/specs/2026-06-09-persona-bsession-integration-design.md`

**Branch:** base this work on `cloakbrowser-backend` (so the integration runs on the cloak-backed browser):
```bash
git checkout cloakbrowser-backend
git checkout -b persona-integration-impl
```

---

## File Structure

bsession repo:
- `lib/api.py` — add `BSESSION_API_PORT`, `POST /cli`, `?profile=` resolution (modify).
- `docker-compose.yml` — publish `18000:18000` (modify).
- `entrypoint.sh` — cosmetic port message (modify).
- `tests/test_api_cli.py` — unit tests for port + profile resolution + cli helper (create).
- `.claude/skills/bsession/SKILL.md` — document the CAPTCHA hand-off contract (modify).

Plugin (new top-level dir `plugin/`, the publishable Claude Code plugin):
- `plugin/.claude-plugin/plugin.json` — manifest (create).
- `plugin/bin/bsession` — HTTP shim (create).
- `plugin/skills/bsession/SKILL.md` — copy/symlink of the skill spec (create).
- `tests/test_shim.sh` — shim skill_id derivation + transport against a mock (create).

persona repo:
- `.claude/settings.json` or `--plugin-dir` wiring — enable the plugin (modify/document).

---

## Task 1: API port → 18000 (configurable)

**Files:**
- Modify: `lib/api.py:199-203` (`main`)
- Test: `tests/test_api_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_cli.py
import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_api_port_defaults_to_18000(monkeypatch=None):
    os.environ.pop("BSESSION_API_PORT", None)
    from lib import api
    assert api._api_port() == 18000


def test_api_port_reads_env():
    os.environ["BSESSION_API_PORT"] = "9999"
    from lib import api
    assert api._api_port() == 9999
    os.environ.pop("BSESSION_API_PORT", None)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try:
            f(); print(f"  PASS {f.__name__}")
        except Exception as e:
            bad += 1; print(f"  FAIL {f.__name__}: {e}")
    print(f"{len(fns)-bad}/{len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_api_cli.py`
Expected: FAIL — `module 'lib.api' has no attribute '_api_port'`

- [ ] **Step 3: Implement `_api_port()` and use it in `main`**

In `lib/api.py`, add near the top (after imports):

```python
import os
```

Replace `main()`:

```python
def _api_port():
    return int(os.environ.get("BSESSION_API_PORT", "18000"))


def main():
    port = _api_port()
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"bsession API listening on port {port}")
    server.serve_forever()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_api_cli.py`
Expected: both port tests PASS.

- [ ] **Step 5: Update compose + entrypoint message**

`docker-compose.yml` — change the API port line:

```yaml
    ports:
      - "5900:5900"   # VNC
      - "6080:6080"   # noVNC web
      - "18000:18000" # bsession API
```

`entrypoint.sh` — update the echo (cosmetic):

```bash
python3 /app/lib/api.py &
echo " API server running on port 18000"
```

- [ ] **Step 6: Commit**

```bash
git add lib/api.py docker-compose.yml entrypoint.sh tests/test_api_cli.py
git commit -m "feat(api): serve on port 18000 (BSESSION_API_PORT)"
```

---

## Task 2: Profile-aware GET resolution for captcha/screenshot endpoints

**Files:**
- Modify: `lib/api.py:95-100` (`_resolve_screenshot_port`)
- Test: `tests/test_api_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_cli.py`:

```python
def test_resolve_port_prefers_explicit_port():
    from urllib.parse import urlparse
    from lib import api
    h = api.Handler.__new__(api.Handler)
    assert h._resolve_screenshot_port(urlparse("/screenshot?port=9300")) == 9300


def test_resolve_port_from_profile(tmp_state="/tmp/bs-test-state"):
    import shutil
    os.environ["BSESSION_STATE_DIR"] = tmp_state
    shutil.rmtree(tmp_state, ignore_errors=True)
    import importlib
    from lib import state as _state
    importlib.reload(_state)
    _state.insert_chrome("demo", 9333, 4242)
    from urllib.parse import urlparse
    from lib import api
    importlib.reload(api)
    h = api.Handler.__new__(api.Handler)
    assert h._resolve_screenshot_port(urlparse("/captcha/screenshot?profile=demo")) == 9333
    shutil.rmtree(tmp_state, ignore_errors=True)
    os.environ.pop("BSESSION_STATE_DIR", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_api_cli.py`
Expected: `test_resolve_port_from_profile` FAILS (returns None for `?profile=`).

- [ ] **Step 3: Implement profile resolution**

In `lib/api.py`, add `from lib import state` to the imports block, then replace `_resolve_screenshot_port`:

```python
    def _resolve_screenshot_port(self, parsed) -> int | None:
        """Resolve CDP port from ?port= or ?profile= query param. None if neither."""
        qs = parse_qs(parsed.query)
        if "port" in qs:
            return int(qs["port"][0])
        if "profile" in qs:
            row = state.get_chrome(qs["profile"][0])  # (port, pid) or None
            return int(row[0]) if row else None
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_api_cli.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/api.py tests/test_api_cli.py
git commit -m "feat(api): resolve captcha/screenshot endpoints by ?profile="
```

---

## Task 3: `POST /cli` endpoint (run the per-profile CLI, with form staging)

**Files:**
- Modify: `lib/api.py` (`do_POST`, add helper)
- Test: `tests/test_api_cli.py`

The endpoint mirrors the host wrapper: optional `form` payload is staged under
`/workspace/.bsession-staging/<skill_id>/<rel>` and exposed via `BSESSION_FORM`;
`profile` sets `BSESSION_PROFILE`; `argv` is passed as a list (no shell).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_cli.py`:

```python
def test_stage_form_writes_file_and_returns_env():
    import shutil
    base = "/tmp/bs-stage-test"
    os.environ["BSESSION_STAGING_DIR"] = base
    shutil.rmtree(base, ignore_errors=True)
    import importlib
    from lib import api
    importlib.reload(api)
    env_form = api._stage_form({
        "skill_id": "uscis-check",
        "rel": "forms/wang-jue-ead.toml",
        "content": 'person = "x"\nreceipt_number = "WAC1"\n',
    })
    assert env_form == f"{base}/uscis-check/forms/wang-jue-ead.toml"
    with open(env_form) as f:
        assert "WAC1" in f.read()
    shutil.rmtree(base, ignore_errors=True)
    os.environ.pop("BSESSION_STAGING_DIR", None)


def test_cli_argv_is_list_not_shell():
    # The constructed argv must be a list (injection-safe), never a shell string.
    from lib import api
    argv = api._cli_argv(["nav", "https://x;rm -rf /", "--wait", "1"])
    assert argv[0:3] == ["python3", "-m", "lib.cli"]
    assert "https://x;rm -rf /" in argv  # passed as one element, not split
    assert all(isinstance(a, str) for a in argv)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_api_cli.py`
Expected: FAIL — `_stage_form` / `_cli_argv` not defined.

- [ ] **Step 3: Implement the helpers + endpoint**

In `lib/api.py`, add module-level helpers:

```python
STAGING_DIR = os.environ.get("BSESSION_STAGING_DIR", "/workspace/.bsession-staging")


def _cli_argv(argv):
    return ["python3", "-m", "lib.cli", *[str(a) for a in argv]]


def _stage_form(form):
    """Write a posted form to the staging dir; return its absolute path."""
    skill_id = form["skill_id"]
    rel = form["rel"].lstrip("/")
    dest = os.path.join(STAGING_DIR, skill_id, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        f.write(form["content"])
    return dest
```

Read the staging dir lazily inside `_stage_form` so tests that set
`BSESSION_STAGING_DIR` before reload work — replace the body's first line:

```python
    base = os.environ.get("BSESSION_STAGING_DIR", "/workspace/.bsession-staging")
    skill_id = form["skill_id"]
    rel = form["rel"].lstrip("/")
    dest = os.path.join(base, skill_id, rel)
```

In `do_POST`, add a branch (before the final `else`):

```python
            elif self.path == "/cli":
                argv = body.get("argv", [])
                env = os.environ.copy()
                if body.get("profile"):
                    env["BSESSION_PROFILE"] = body["profile"]
                if body.get("form"):
                    env["BSESSION_FORM"] = _stage_form(body["form"])
                result = subprocess.run(
                    _cli_argv(argv), cwd="/app", env=env,
                    capture_output=True, text=True, timeout=300,
                )
                self._json_response(200, {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "code": result.returncode,
                })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_api_cli.py`
Expected: all PASS.

- [ ] **Step 5: Integration smoke (manual, needs running container)**

Run:
```bash
docker compose up -d --build
curl -s -X POST http://localhost:18000/cli \
  -H 'Content-Type: application/json' \
  -d '{"profile":"smoke","argv":["session","list","--json"]}'
```
Expected: JSON `{"stdout": "...", "stderr": "...", "code": 0}` with `stdout` an empty/`[]` session list.

- [ ] **Step 6: Commit**

```bash
git add lib/api.py tests/test_api_cli.py
git commit -m "feat(api): POST /cli runs per-profile CLI with optional form staging"
```

---

## Task 4: Plugin scaffold (manifest + dirs)

**Files:**
- Create: `plugin/.claude-plugin/plugin.json`
- Create: `plugin/skills/bsession/SKILL.md` (filled in Task 6)
- Create: `plugin/bin/` (shim added in Task 5)

- [ ] **Step 1: Create the manifest**

`plugin/.claude-plugin/plugin.json`:

```json
{
  "name": "bsession",
  "version": "0.1.0",
  "description": "Drive the bsession headed-browser engine over HTTP; build Cloudflare/CAPTCHA-aware browser skills.",
  "author": "gaxxx"
}
```

- [ ] **Step 2: Create the skill placeholder**

`plugin/skills/bsession/SKILL.md`:

```markdown
---
name: bsession
description: Build headed browser automation skills over a remote bsession engine. Use for Cloudflare/Turnstile/CAPTCHA, JS-heavy logins, persistent cookies. (filled in Task 6)
---
```

- [ ] **Step 3: Verify the plugin loads**

Run: `claude --plugin-dir ./plugin -p "run: command -v bsession || echo NO_SHIM_YET"`
Expected: prints `NO_SHIM_YET` (manifest valid, plugin enabled, shim not added yet).

- [ ] **Step 4: Commit**

```bash
git add plugin/.claude-plugin/plugin.json plugin/skills/bsession/SKILL.md
git commit -m "feat(plugin): scaffold bsession Claude Code plugin"
```

---

## Task 5: `bin/bsession` HTTP shim

**Files:**
- Create: `plugin/bin/bsession`
- Test: `tests/test_shim.sh`

The shim is pure transport: derive `skill_id`/`rel` from `BSESSION_FORM` (same
rule as the host wrapper), POST `{argv, profile?, form?}` to `$BSESSION_API_URL/cli`,
print stdout, echo stderr, exit with the returned code.

- [ ] **Step 1: Write the failing test (skill_id derivation + transport against a mock)**

`tests/test_shim.sh`:

```bash
#!/usr/bin/env bash
# Tests the bsession shim's path derivation and JSON transport against a mock.
set -uo pipefail
SHIM="$(cd "$(dirname "$0")/.." && pwd)/plugin/bin/bsession"
fail=0

# Mock API: a netcat-free python server that echoes the posted body to a file.
PORT=18099
REQ=/tmp/bs-shim-req.json
python3 - "$PORT" "$REQ" <<'PY' &
import sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
port, reqfile = int(sys.argv[1]), sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        open(reqfile, "wb").write(self.rfile.read(n))
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(b'{"stdout":"OK","stderr":"","code":0}')
    def log_message(self,*a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
MOCK_PID=$!
sleep 1
export BSESSION_API_URL="http://127.0.0.1:$PORT"

# Case A: plain command, no form
out=$("$SHIM" --profile demo session list --json)
[ "$out" = "OK" ] || { echo "FAIL: stdout not forwarded (got '$out')"; fail=1; }
grep -q '"argv"' "$REQ" && grep -q '"session"' "$REQ" || { echo "FAIL: argv missing"; fail=1; }
grep -q '"profile":"demo"' "$REQ" || { echo "FAIL: profile override missing"; fail=1; }

# Case B: form path → skill_id + rel derived
FORMDIR=/tmp/bs-shim/uscis-check/forms
mkdir -p "$FORMDIR"; printf 'person = "x"\n' > "$FORMDIR/wang.toml"
BSESSION_FORM="$FORMDIR/wang.toml" "$SHIM" nav https://x >/dev/null
grep -q '"skill_id":"uscis-check"' "$REQ" || { echo "FAIL: skill_id derivation"; fail=1; }
grep -q '"rel":"forms/wang.toml"' "$REQ" || { echo "FAIL: rel derivation"; fail=1; }

kill "$MOCK_PID" 2>/dev/null
[ "$fail" = 0 ] && echo "PASS shim tests" || echo "shim tests FAILED"
exit $fail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash tests/test_shim.sh`
Expected: FAIL — shim file does not exist yet.

- [ ] **Step 3: Implement the shim**

`plugin/bin/bsession`:

```bash
#!/usr/bin/env bash
# bsession HTTP shim — forwards CLI commands to the bsession API over HTTP.
# Mirrors the host wrapper's form staging, but transports content (no docker exec).
set -euo pipefail

API="${BSESSION_API_URL:-http://host.docker.internal:18000}"

# Build the argv JSON array from "$@".
argv_json=$(printf '%s\n' "$@" | python3 -c 'import json,sys; print(json.dumps([l.rstrip("\n") for l in sys.stdin]))')

profile_json=null
[ -n "${BSESSION_PROFILE:-}" ] && profile_json=$(printf '%s' "$BSESSION_PROFILE" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')

form_json=null
if [ -n "${BSESSION_FORM:-}" ] && [ -f "${BSESSION_FORM}" ]; then
    abs=$(cd "$(dirname "$BSESSION_FORM")" && pwd)/$(basename "$BSESSION_FORM")
    parent=$(dirname "$abs")
    if [ "$(basename "$parent")" = "forms" ]; then
        skill_dir=$(dirname "$parent"); rel="forms/$(basename "$abs")"
    else
        skill_dir="$parent"; rel="$(basename "$abs")"
    fi
    skill_id=$(basename "$skill_dir")
    form_json=$(BS_SKILL="$skill_id" BS_REL="$rel" BS_FILE="$abs" python3 -c '
import json,os
print(json.dumps({"skill_id":os.environ["BS_SKILL"],"rel":os.environ["BS_REL"],
                  "content":open(os.environ["BS_FILE"]).read()}))')
fi

payload=$(BS_ARGV="$argv_json" BS_PROFILE="$profile_json" BS_FORM="$form_json" python3 -c '
import json,os
print(json.dumps({"argv":json.loads(os.environ["BS_ARGV"]),
                  "profile":json.loads(os.environ["BS_PROFILE"]),
                  "form":json.loads(os.environ["BS_FORM"])}))')

resp=$(curl -s -X POST "$API/cli" -H 'Content-Type: application/json' -d "$payload")
[ -z "$resp" ] && { echo "bsession: API unreachable at $API" >&2; exit 3; }

BS_RESP="$resp" python3 -c '
import json,os,sys
r=json.loads(os.environ["BS_RESP"])
sys.stdout.write(r.get("stdout",""))
sys.stderr.write(r.get("stderr",""))
sys.exit(int(r.get("code",1)))
'
```

Make it executable:
```bash
chmod +x plugin/bin/bsession
```

(Note: the shim uses `python3` only for JSON marshalling — already present in
persona's container; if a target lacks it, swap to `jq`. Document this.)

- [ ] **Step 4: Run test to verify it passes**

Run: `bash tests/test_shim.sh`
Expected: `PASS shim tests`.

- [ ] **Step 5: Commit**

```bash
git add plugin/bin/bsession tests/test_shim.sh
git commit -m "feat(plugin): bin/bsession HTTP shim with host-wrapper-compatible form staging"
```

---

## Task 6: Document the CAPTCHA hand-off contract in the skill spec

**Files:**
- Modify: `.claude/skills/bsession/SKILL.md` (bsession repo)
- Mirror: `plugin/skills/bsession/SKILL.md` (keep identical; symlink or copy)

- [ ] **Step 1: Add the hand-off section**

Append to `.claude/skills/bsession/SKILL.md` (after the Cloudflare/CAPTCHA section):

````markdown
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
````

- [ ] **Step 2: Mirror into the plugin**

```bash
cp .claude/skills/bsession/SKILL.md plugin/skills/bsession/SKILL.md
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/bsession/SKILL.md plugin/skills/bsession/SKILL.md
git commit -m "docs(skill): channel-agnostic CAPTCHA hand-off + Telegram reference"
```

---

## Task 7: Enable the plugin in persona + end-to-end smoke

**Files:**
- Modify: persona repo — plugin enablement (`.claude/settings.json` `enabledPlugins`, or document `--plugin-dir`).

- [ ] **Step 1: Make the plugin reachable from persona**

Either mount/copy the `plugin/` dir into persona and enable via settings, or for
dev, point the persona Claude invocation at it. Document in persona's SETUP:

```bash
# In persona's container/launch, add to its claude flags:
claude --plugin-dir /path/to/bsession/plugin ...
# or enable a marketplace-installed plugin in .claude/settings.json:
# { "enabledPlugins": ["bsession@<marketplace>"] }
```

Set `BSESSION_API_URL` only if not the default (`http://host.docker.internal:18000`).

- [ ] **Step 2: Smoke test from inside the persona container**

Run:
```bash
docker exec persona bash -lc 'BSESSION_API_URL=http://host.docker.internal:18000 \
  curl -s -X POST http://host.docker.internal:18000/cli \
  -H "Content-Type: application/json" -d "{\"profile\":\"smoke\",\"argv\":[\"session\",\"list\",\"--json\"]}"'
```
Expected: JSON with `"code": 0`.

- [ ] **Step 3: End-to-end through the shim (uscis-check, placeholder receipt)**

With the plugin enabled in a persona Claude session, run:
```bash
bash .claude/skills/uscis-check/run.sh .claude/skills/uscis-check/forms/<a-case>.toml
```
Expected: JSON line with `status`/`detail` (real USCIS response; placeholder/invalid receipts yield a validation message — still proves the path).

- [ ] **Step 4: Commit (persona repo)**

```bash
cd <persona-repo>
git add .claude/settings.json SETUP.md
git commit -m "feat: enable bsession plugin (HTTP drive of agent-browser)"
```

---

## Task 8: CAPTCHA round-trip — manual verification

The Telegram round-trip is emergent (contract + persona's tools); full automation
needs a live text captcha and a human, so verification is a scripted manual check.

- [ ] **Step 1: Force the captcha path on a test profile**

Navigate the profile to a page known to present a text captcha (or a local test
page). Then:
```bash
docker exec agent-browser python3 -m lib.cli --profile cap-test captcha bounds
```
Expected: prints a bounds JSON (exit 0), confirming detection.

- [ ] **Step 2: Verify image bytes over HTTP by profile**

```bash
curl -s "http://localhost:18000/captcha/screenshot?profile=cap-test" -o /tmp/cap.png
file /tmp/cap.png
```
Expected: `/tmp/cap.png: PNG image data`.

- [ ] **Step 3: Verify the Telegram leg**

```bash
docker exec persona bash -lc 'bun run bin/tg-send-photo.ts "$TELEGRAM_CHAT_ID" /tmp/cap.png "test captcha"'
```
Expected: the photo arrives in Telegram; `{"ok":true}` printed. (Copy `/tmp/cap.png`
into the persona container first, or fetch it there via the API URL.)

- [ ] **Step 4: Verify fill-back**

Reply to the bot, then:
```bash
docker exec persona bash -lc 'bun run bin/tg-pull.ts'   # confirm reply text is returned
docker exec agent-browser python3 -m lib.cli --profile cap-test fill <ref> "<reply>"
```
Expected: the captcha field is filled with the reply.

- [ ] **Step 5: Document the verification result** in the PR description (no code change).

---

## Self-Review notes

- **Spec coverage:** transport/port (Task 1), captcha-by-profile (Task 2), `/cli` (Task 3), plugin packaging (Tasks 4–5), hand-off contract doc (Task 6), persona enablement (Task 7), Telegram round-trip (Task 6 recipe + Task 8 verification). The spec's "forms handled locally" intent is met via server-side staging (shim sends content; `/cli` stages it) — a refinement that avoids TOML parsing in the shim while keeping forms in persona's repo.
- **Types/signatures:** `_api_port()`, `_resolve_screenshot_port()`, `_stage_form(form)`, `_cli_argv(argv)`, `state.get_chrome()→(port,pid)` are used consistently across tasks.
- **Out of scope (per spec):** shared-token auth on `/cli`; non-Telegram channels; solving click-grid captchas over Telegram (→ VNC).
```
