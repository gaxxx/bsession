#!/usr/bin/env bash
# bsession installer — sets up the Docker container, host CLI, and
# Claude Code skill registration.
#
# Usage:
#   bash install.sh [--workspace DIR] [--vnc-password PWD]
#                   [--no-container] [--no-skill] [--uninstall]
#
# Idempotent: re-running updates symlinks, leaves data untouched.
# Run from inside a clone of the bsession repo.
set -euo pipefail

# ── defaults ─────────────────────────────────────────────────────────
WORKSPACE="${BSESSION_WORKSPACE:-$HOME/.bsession/workspace}"
VNC_PASSWORD=""
DO_CONTAINER=1
DO_SKILL=1
UNINSTALL=0
BIN_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workspace)      WORKSPACE="$2"; shift 2 ;;
        --vnc-password)   VNC_PASSWORD="$2"; shift 2 ;;
        --no-container)   DO_CONTAINER=0; shift ;;
        --no-skill)       DO_SKILL=0; shift ;;
        --uninstall)      UNINSTALL=1; shift ;;
        --bin-dir)        BIN_DIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,11p' "$0"; exit 0 ;;
        *)  echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

info()  { printf "\033[32m[+]\033[0m %s\n" "$*"; }
warn()  { printf "\033[33m[!]\033[0m %s\n" "$*"; }
fail()  { printf "\033[31m[x]\033[0m %s\n" "$*" >&2; exit 1; }

# ── locate repo source ──────────────────────────────────────────────
SOURCE="$(cd "$(dirname "$0")/../../../.." && pwd)"
[[ -f "$SOURCE/.claude/skills/bsession/bsession" ]] \
    || fail "expected to be inside a bsession repo clone (no .claude/skills/bsession/bsession at $SOURCE)"

# ── pick bin dir ────────────────────────────────────────────────────
if [[ -z "$BIN_DIR" ]]; then
    if [[ -d "$HOME/.local/bin" ]]; then
        BIN_DIR="$HOME/.local/bin"
    elif [[ -w "/usr/local/bin" ]]; then
        BIN_DIR="/usr/local/bin"
    else
        BIN_DIR="$HOME/.local/bin"
        mkdir -p "$BIN_DIR"
    fi
fi

# ── uninstall ───────────────────────────────────────────────────────
if [[ "$UNINSTALL" == "1" ]]; then
    info "Stopping container"
    (cd "$SOURCE" && docker compose down 2>/dev/null) || true
    info "Removing $BIN_DIR/bsession"
    rm -f "$BIN_DIR/bsession"
    info "Removing $HOME/.claude/skills/bsession (if symlinked)"
    [[ -L "$HOME/.claude/skills/bsession" ]] && rm -f "$HOME/.claude/skills/bsession"
    info "Done. Workspace at $WORKSPACE preserved."
    exit 0
fi

# ── prereqs ─────────────────────────────────────────────────────────
command -v docker >/dev/null   || fail "docker not found. Install Docker first."
docker info >/dev/null 2>&1    || fail "Docker daemon not running."

# ── workspace ───────────────────────────────────────────────────────
info "Workspace: $WORKSPACE"
mkdir -p "$WORKSPACE"

# ── .env (vnc password) ─────────────────────────────────────────────
if [[ -n "$VNC_PASSWORD" ]]; then
    grep -q '^VNC_PASSWORD=' "$SOURCE/.env" 2>/dev/null \
        && sed -i.bak "s|^VNC_PASSWORD=.*|VNC_PASSWORD=$VNC_PASSWORD|" "$SOURCE/.env" \
        || echo "VNC_PASSWORD=$VNC_PASSWORD" >> "$SOURCE/.env"
    info "Set VNC_PASSWORD"
fi

# ── bsession command on PATH ────────────────────────────────────────
info "Symlinking bsession → $BIN_DIR/bsession"
ln -sf "$SOURCE/.claude/skills/bsession/bsession" "$BIN_DIR/bsession"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on \$PATH. Add it (e.g. echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc)" ;;
esac

# ── Claude Code skill registration ─────────────────────────────────
if [[ "$DO_SKILL" == "1" ]]; then
    SKILL_DST="$HOME/.claude/skills/bsession"
    mkdir -p "$HOME/.claude/skills"
    if [[ -e "$SKILL_DST" && ! -L "$SKILL_DST" ]]; then
        warn "$SKILL_DST exists and is not a symlink; not overwriting"
    else
        ln -sfn "$SOURCE/.claude/skills/bsession" "$SKILL_DST"
        info "Linked Claude Code skill: $SKILL_DST"
    fi

    if [[ -e "$HOME/.claude/skills/browser" ]]; then
        warn "Old 'browser' skill exists at ~/.claude/skills/browser — remove manually if no longer used"
    fi
fi

# ── start container ─────────────────────────────────────────────────
if [[ "$DO_CONTAINER" == "1" ]]; then
    info "Starting container (BSESSION_WORKSPACE=$WORKSPACE)"
    BSESSION_WORKSPACE="$WORKSPACE" docker compose -f "$SOURCE/docker-compose.yml" up -d
fi

# ── verify ──────────────────────────────────────────────────────────
info "Verifying"
"$BIN_DIR/bsession" session list >/dev/null 2>&1 \
    && info "bsession command works" \
    || warn "bsession command failed — try restarting your shell to pick up PATH"

cat <<EOF

  Setup complete.

  Try it:
    bsession session list
    BSESSION_FORM=$SOURCE/.claude/skills/uscis-check/forms/example.toml \\
      bash $SOURCE/.claude/skills/uscis-check/run.sh \\
        $SOURCE/.claude/skills/uscis-check/forms/example.toml

  VNC: http://localhost:6080/vnc.html
  Container logs: docker logs -f agent-browser

EOF
