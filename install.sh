#!/usr/bin/env bash
# One-line installer for bsession.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/gaxxx/bsession/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --vnc-password secret
#   curl -fsSL .../install.sh | bash -s -- --workspace ~/work/bsession
#
# Local equivalent (from a clone):
#   bash .claude/skills/bsession/scripts/install.sh
set -euo pipefail

REPO="${BSESSION_REPO:-https://github.com/gaxxx/bsession.git}"
SOURCE_DIR="${BSESSION_SOURCE:-$HOME/.bsession/source}"

info()  { printf "\033[32m[+]\033[0m %s\n" "$*"; }
fail()  { printf "\033[31m[x]\033[0m %s\n" "$*" >&2; exit 1; }

command -v docker >/dev/null  || fail "docker not found"
command -v git    >/dev/null  || fail "git not found"

# ── Clone or update source ──────────────────────────────────────────
if [[ -d "$SOURCE_DIR/.git" ]]; then
    info "Updating $SOURCE_DIR"
    git -C "$SOURCE_DIR" pull --quiet --ff-only || fail "git pull failed; resolve manually"
else
    info "Cloning $REPO → $SOURCE_DIR"
    mkdir -p "$(dirname "$SOURCE_DIR")"
    git clone --depth 1 --quiet "$REPO" "$SOURCE_DIR"
fi

# ── Run the real installer ──────────────────────────────────────────
exec bash "$SOURCE_DIR/.claude/skills/bsession/scripts/install.sh" "$@"
