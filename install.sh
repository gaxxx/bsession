#!/usr/bin/env bash
# One-line installer for bsession browser automation + Claude Code skill.
#
# Usage:
#   curl -fsSL https://gaxxx.me/bsession.sh | bash
#   curl -fsSL https://gaxxx.me/bsession.sh | bash -s -- --vnc-password secret
#   curl -fsSL https://gaxxx.me/bsession.sh | bash -s -- --workspace ~/my-workspace

set -euo pipefail

REPO="https://github.com/gaxxx/bsession.git"
BSESSION_HOME="$HOME/.bsession"
TMPDIR="$(mktemp -d)"

info()  { printf "\033[32m[+]\033[0m %s\n" "$*"; }
warn()  { printf "\033[33m[!]\033[0m %s\n" "$*"; }
fail()  { printf "\033[31m[x]\033[0m %s\n" "$*"; exit 1; }
check() { command -v "$1" &>/dev/null; }

cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

# ── Check prerequisites ──────────────────────────────────────────────
check docker || fail "Docker not found. Install Docker first: https://docs.docker.com/get-docker/"
docker info &>/dev/null || fail "Docker daemon not running. Start Docker and try again."
check git || fail "git not found. Install git first."

# ── Clone repo to temp dir ───────────────────────────────────────────
info "Downloading bsession..."
git clone --depth 1 --quiet "$REPO" "$TMPDIR/bsession"

# ── Run the full installer ───────────────────────────────────────────
info "Running installer..."
bash "$TMPDIR/bsession/.claude/skills/browser/scripts/install.sh" "$@"
