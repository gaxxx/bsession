#!/usr/bin/env bash
# SessionStart hook: if the bsession engine (agent-browser container) isn't
# reachable, emit a setup hint. On SessionStart, stdout is injected into the
# session context, so Claude sees the hint and can relay/act on it (works in
# both interactive and headless `claude -p` runs). Silent when the engine is up.
#
# Health-check via the HTTP API (not `docker compose`) so it works from any
# consumer — including containers without Docker access (e.g. persona).

API="${BSESSION_API_URL:-http://host.docker.internal:18000}"

if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 2 "$API/health" >/dev/null 2>&1 \
     || curl -fsS --max-time 2 "http://localhost:18000/health" >/dev/null 2>&1; then
    exit 0   # engine is up — stay silent
  fi
fi

cat <<EOF
[bsession] The browser engine isn't reachable ($API, or http://localhost:18000).
bsession skills (nav/find/fill/captcha/…) need it running. Start it once on a
host with Docker:
  curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash
  # or from a clone:  docker compose up -d --build
After it's up, the API answers on port 18000 and VNC is at
http://localhost:6080/vnc.html. A containerized consumer without Docker access
(e.g. persona) can't start it itself — run the above on the host.
EOF
exit 0
