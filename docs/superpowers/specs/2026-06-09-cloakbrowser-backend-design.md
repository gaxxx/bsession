# cloakbrowser as a bsession browser backend

**Date:** 2026-06-09
**Status:** Approved (design)

## Goal

Make bsession use [cloakbrowser](https://github.com/CloakHQ/cloakbrowser) — a
source-patched stealth Chromium — as its browser, so bot-protected sites
(Cloudflare Turnstile, fingerprint walls) are handled by C++-level stealth
instead of bsession's weak JS stealth extension. Do this **without** giving up
bsession's core guarantee: per-profile cookies persist on disk across container
restarts.

## Background

bsession launches a Chromium binary with `--remote-debugging-port` and drives it
over CDP via `agent-browser` (Playwright). The single integration point is
`start_chrome()` in `lib/chrome.py`, plus the stealth extension it injects.

cloakbrowser is a drop-in Chromium binary (58 source patches on Linux:
canvas/WebGL/audio/fonts/GPU/automation-signal removal) that speaks the same CDP
and accepts `--remote-debugging-port`. It auto-downloads (~200 MB, cached) and
can be launched as a raw binary or via its own Playwright wrapper.

## Decision: drop-in binary, not `cloakserve`

cloakbrowser can run as `cloakserve`, a standalone multi-session CDP server. We
**reject** that route. `cloakserve` has **no per-connection on-disk
`user-data-dir`**: sessions are routed by fingerprint seed (`?fingerprint=N`),
same seed reuses the same Chrome *process*, and cookies/localStorage live only
in RAM for that process's lifetime. `launch_persistent_context()` is documented
as client-side only and does not work in server mode.

That breaks bsession's core guarantee — *"Persistent profile per skill —
Cloudflare cookies survive container restarts"* (CLAUDE.md). Restart the
container and every profile's hard-won Cloudflare clearance is gone.

The drop-in-binary route preserves it: launching the raw cloak binary with
`--user-data-dir=profiles/<profile>/` gives cloak's stealth **plus** real
on-disk persistence — which is exactly the "direct binary access to a profile
directory" cloak's own persistent-context path requires.

| | Drop-in binary | cloakserve |
|---|---|---|
| Cloak C++ stealth | yes | yes |
| Per-profile on-disk cookies | yes (`--user-data-dir`) | no (RAM-only) |
| Survives container restart | yes | no |
| Stable fingerprint per profile | yes (`--fingerprint`) | yes (query seed) |
| Keeps bsession lifecycle/LRU | yes | no (rewrite) |

## Design

### 1. Backend resolution (`lib/chrome.py`)

A resolver returns `(binary_path, backend_kind)` where
`backend_kind ∈ {"cloak", "chrome"}`. Selection order:

1. `CHROME_BIN` explicitly set → use it as-is. Backend is `cloak` only if the
   path resolves to the cloak binary, else `chrome`.
2. `BSESSION_BROWSER` env: `auto` (default) | `cloak` | `chrome`.
3. `auto` → `cloak` if the cloak binary is resolvable, else `chrome`.

Cloak availability is detected by importing the `cloakbrowser` package and
asking it for the cached binary path (fallback: parse `python -m cloakbrowser
info`). Import failure ⇒ not available ⇒ plain Chromium.

Net effect:
- **Container:** cloak is pre-installed (see §5), so `auto` picks cloak.
- **Native macOS (`BSESSION_LOCAL=1`):** cloak absent by default → falls back to
  system Chrome. `pip install cloakbrowser` locally opts in (26 patches on mac).

### 2. Backend-specific launch flags

`start_chrome()` builds a common flag set, then branches:

- **Common (both backends):** `--remote-debugging-port`, `--user-data-dir`,
  `--remote-allow-origins=*`, `--no-first-run`, `--no-default-browser-check`,
  `--disable-background-networking`, `--disable-sync`, `--window-size`,
  `--disable-infobars`. In Docker, add `--no-sandbox`, `--test-type`,
  `--disable-dev-shm-usage`.
- **`chrome` backend only:** `--disable-blink-features=AutomationControlled` and
  `--load-extension=<stealth-ext>` (unchanged from today).
- **`cloak` backend:** **neither** of those — cloak owns stealth at the C++
  level, and loading an unpacked extension is itself an automation tell. Skip
  `ensure_stealth_ext()`.

`--disable-gpu` in Docker is retained but flagged for verification: under Xvfb
with software GL (SwiftShader), cloak's WebGL patches still apply. Confirm the
GPU fingerprint stays believable; drop the flag if cloak prefers it present.

### 3. Stable fingerprint per profile

cloak randomizes its device fingerprint on every launch by default. bsession's
profiles are persistent, so the same profile presenting a different device each
restart is itself suspicious. On the `cloak` backend, derive a deterministic
seed from the profile name and pass `--fingerprint=<seed>`:

```python
seed = int(hashlib.sha256(profile.encode()).hexdigest()[:8], 16)
```

Same profile ⇒ same fingerprint, forever — matching the persistent-cookie model.
**Verify** the installed cloak binary accepts `--fingerprint=` as a raw launch
arg (cloak docs show it passed via Playwright `args`, which forwards to the
binary).

### 4. Cloudflare bypass — no change required

The existing `cmd_bypass` (in `lib/cli.py`) already (a) detects Cloudflare, (b)
nudges + waits 8 s + re-checks, (c) polls until resolved or hands off to VNC.
cloak's **non-interactive auto-resolve happens inside those wait/poll windows**,
so the logic degrades gracefully into "wait for cloak to auto-resolve, else hand
a human the VNC link."

Keep the detection and the VNC handoff: **visual CAPTCHAs (image grids,
distorted text) still require a human — nothing solves those automatically**, and
saved guidance already routes them straight to VNC. Optional: skip the Tier-1
iframe click on the cloak backend since it's redundant, but it is already
functionally correct as written.

### 5. Dockerfile

Add `python3-pip`, then `pip install cloakbrowser` and `python -m cloakbrowser
install` to pre-bake the ~200 MB patched binary at build time (cloak ships Linux
x86_64 + arm64 — both bsession build targets). Keep the apt `chromium` package
as the fallback path.

### 6. Out of scope (YAGNI)

Proxy, `geoip`, and `humanize` are additional cloak launch flags, trivial to
expose later as `_bsession_proxy` / `_bsession_geoip` / `_bsession_humanize`
form keys. Not in this change.

## Files touched

- `lib/chrome.py` — resolver, branched flags, per-profile fingerprint seed, skip
  stealth-ext on cloak.
- `Dockerfile` — install + pre-download cloakbrowser.
- `CLAUDE.md` — update Stack / Anti-detection sections.
- `README` — mention the cloak backend and `BSESSION_BROWSER`.
- *(optional)* `lib/cli.py` `cmd_bypass` — skip redundant iframe click on cloak.

## Testing

- **Unit (pure functions):** backend resolver across env permutations
  (`CHROME_BIN` set / `BSESSION_BROWSER` ∈ {auto,cloak,chrome} / cloak
  available-or-not); flag-builder produces the expected set per backend;
  fingerprint-seed is deterministic per profile.
- **Integration:** build the container, launch a profile, and via VNC against a
  fingerprint-detection page confirm `navigator.webdriver` is clean and the
  device fingerprint is **stable across a restart** of the same profile.

## Verification items (resolved during implementation)

1. **`--fingerprint=<seed>` confirmed.** cloak's `config.get_default_stealth_args()`
   passes `--fingerprint=<random>` + `--fingerprint-platform=windows|macos` as raw
   args, so the binary accepts them. We pass a *deterministic* per-profile seed
   instead of cloak's random one.
2. **`--disable-gpu` dropped on cloak.** cloak auto-generates the GPU
   vendor/renderer from the seed and warns that forced software GL
   (`--enable-unsafe-swiftshader`) yields a detectable renderer, so the cloak
   branch omits `--disable-gpu` and `--window-size` (screen/window also seed-derived).
3. **Binary path:** `cloakbrowser.download.ensure_binary()` (downloads if needed;
   cache at `~/.cloakbrowser/chromium-<ver>/chrome`). Pre-baked in the image.

Verified end to end: container build bakes the arm64 binary, `resolve_browser()`
returns the cloak backend, and a Reddit login completed with no Cloudflare/CAPTCHA
challenge (`/api/me.json` → the expected account).
