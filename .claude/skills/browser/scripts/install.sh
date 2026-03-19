#!/usr/bin/env bash
# Install and configure bsession browser automation environment.
#
# What this script does:
#   1. Ensures Docker is available
#   2. Installs uv (Python runtime manager) if missing
#   3. Installs Python 3.12 via uv
#   4. Builds the agent-browser Docker image
#   5. Starts the container
#   6. Sets up workspace directories
#   7. Makes bsession CLI available on PATH
#   8. Verifies the full stack
#
# Usage:
#   bash install.sh [--workspace <path>]
#
# Options:
#   --workspace <path>   Custom workspace directory (default: ./workspace)
#   --vnc-password <pw>  Set a VNC password (default: none)
#   --no-start           Build only, don't start the container

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
WORKSPACE_DIR=""
VNC_PASSWORD=""
NO_START=false

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workspace)   WORKSPACE_DIR="$2"; shift 2 ;;
        --vnc-password) VNC_PASSWORD="$2"; shift 2 ;;
        --no-start)    NO_START=true; shift ;;
        *)             echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────
info()  { printf "\033[32m[+]\033[0m %s\n" "$*"; }
warn()  { printf "\033[33m[!]\033[0m %s\n" "$*"; }
fail()  { printf "\033[31m[x]\033[0m %s\n" "$*"; exit 1; }
check() { command -v "$1" &>/dev/null; }

# ── Step 1: Check Docker ─────────────────────────────────────────────
info "Checking Docker..."
if ! check docker; then
    fail "Docker not found. Install Docker Desktop (macOS) or docker-ce (Linux) first."
fi
if ! docker info &>/dev/null; then
    fail "Docker daemon not running. Start Docker Desktop or the docker service."
fi
info "Docker OK."

# ── Step 2: Install uv + Python ──────────────────────────────────────
info "Checking uv..."
if ! check uv; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$PATH"
    if ! check uv; then
        fail "uv installed but not on PATH. Add ~/.local/bin to your PATH and rerun."
    fi
fi
info "uv OK: $(uv --version)"

info "Ensuring Python 3.12 via uv..."
uv python install 3.12 2>/dev/null || true
PYTHON_BIN="$(uv python find 3.12 2>/dev/null || echo "")"
if [[ -z "$PYTHON_BIN" ]]; then
    warn "Could not find Python 3.12 via uv. Host Python not required for Docker mode, continuing."
else
    info "Python OK: $PYTHON_BIN"
fi

# ── Step 3: Verify project files ─────────────────────────────────────
info "Checking project files in $PROJECT_DIR..."
cd "$PROJECT_DIR"

for f in Dockerfile docker-compose.yml session.py entrypoint.sh bsession lib/browser.py; do
    [[ -f "$f" ]] || fail "Missing required file: $f (are you in the bsession repo root?)"
done
info "Project files OK."

# ── Step 4: Configure workspace ──────────────────────────────────────
if [[ -n "$WORKSPACE_DIR" ]]; then
    info "Using custom workspace: $WORKSPACE_DIR"
    mkdir -p "$WORKSPACE_DIR"/{conf,data,scripts}

    # Update docker-compose to use custom workspace path
    if grep -q './workspace:/workspace' docker-compose.yml; then
        # Create a docker-compose.override.yml for custom workspace
        cat > docker-compose.override.yml <<YAML
services:
  agent-browser:
    volumes:
      - ${WORKSPACE_DIR}:/workspace
YAML
        info "Created docker-compose.override.yml for custom workspace."
    fi
else
    WORKSPACE_DIR="$PROJECT_DIR/workspace"
    mkdir -p "$WORKSPACE_DIR"/{conf,data,scripts}
fi
info "Workspace directories ready: $WORKSPACE_DIR"

# ── Step 5: Configure .env ───────────────────────────────────────────
if [[ ! -f .env ]]; then
    touch .env
fi

if [[ -n "$VNC_PASSWORD" ]]; then
    # Replace or add VNC_PASSWORD
    if grep -q '^VNC_PASSWORD=' .env; then
        sed -i.bak "s/^VNC_PASSWORD=.*/VNC_PASSWORD=$VNC_PASSWORD/" .env && rm -f .env.bak
    else
        echo "VNC_PASSWORD=$VNC_PASSWORD" >> .env
    fi
    info "VNC password configured."
else
    info "No VNC password set (open access)."
fi

# ── Step 6: Build Docker image ───────────────────────────────────────
info "Building agent-browser Docker image..."
docker compose build
info "Image built."

# ── Step 7: Start container ──────────────────────────────────────────
if [[ "$NO_START" == true ]]; then
    info "Skipping container start (--no-start)."
else
    # Stop any existing agent-browser container (may be from a different project dir)
    if docker inspect agent-browser &>/dev/null 2>&1; then
        info "Stopping existing agent-browser container..."
        docker rm -f agent-browser &>/dev/null || true
        docker compose down --remove-orphans &>/dev/null 2>&1 || true
    fi

    info "Starting container..."
    docker compose up -d
    info "Container started."

    # Wait for container to be ready
    info "Waiting for container to be ready..."
    for i in $(seq 1 15); do
        if docker exec agent-browser echo ok &>/dev/null; then
            break
        fi
        sleep 1
    done

    if ! docker exec agent-browser echo ok &>/dev/null; then
        fail "Container not responding after 15s. Check: docker compose logs"
    fi
fi

# ── Step 8: Set up bsession CLI ──────────────────────────────────────
chmod +x "$PROJECT_DIR/bsession"

# Determine a good bin directory
BIN_DIR=""
if [[ -d "$HOME/.local/bin" ]]; then
    BIN_DIR="$HOME/.local/bin"
elif [[ -d "/usr/local/bin" && -w "/usr/local/bin" ]]; then
    BIN_DIR="/usr/local/bin"
else
    mkdir -p "$HOME/.local/bin"
    BIN_DIR="$HOME/.local/bin"
fi

LINK_PATH="$BIN_DIR/bsession"
if [[ -L "$LINK_PATH" || -f "$LINK_PATH" ]]; then
    rm -f "$LINK_PATH"
fi
ln -sf "$PROJECT_DIR/bsession" "$LINK_PATH"
info "bsession linked: $LINK_PATH → $PROJECT_DIR/bsession"

# Check if BIN_DIR is on PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    warn "$BIN_DIR is not on your PATH. Add it:"
    warn "  export PATH=\"$BIN_DIR:\$PATH\""
    warn "Or add that line to your shell profile (~/.bashrc, ~/.zshrc, ~/.config/fish/config.fish)."
fi

# ── Step 9: Verify stack ─────────────────────────────────────────────
if [[ "$NO_START" == false ]]; then
    info "Verifying stack..."
    ERRORS=0

    # Check Xvfb
    if docker exec agent-browser pgrep Xvfb &>/dev/null; then
        info "  Xvfb display: OK"
    else
        warn "  Xvfb display: NOT RUNNING"
        ((ERRORS++))
    fi

    # Check agent-browser CLI
    if docker exec agent-browser which agent-browser &>/dev/null; then
        info "  agent-browser CLI: OK"
    else
        warn "  agent-browser CLI: NOT FOUND"
        ((ERRORS++))
    fi

    # Check browser.py
    if docker exec agent-browser python3 -c "import sys; sys.path.insert(0, '/app'); from lib.browser import ab; print('ok')" 2>/dev/null | grep -q ok; then
        info "  browser.py: OK"
    else
        warn "  browser.py: IMPORT FAILED"
        ((ERRORS++))
    fi

    # Check workspace mount
    if docker exec agent-browser ls /workspace/conf /workspace/data /workspace/scripts &>/dev/null; then
        info "  workspace mount: OK"
    else
        warn "  workspace mount: NOT MOUNTED"
        ((ERRORS++))
    fi

    if [[ $ERRORS -gt 0 ]]; then
        warn "$ERRORS verification(s) failed. Check: docker compose logs"
    else
        info "All checks passed!"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
info "=========================================="
info "  bsession is ready!"
info "=========================================="
info ""
info "  VNC web access:  http://localhost:6080/vnc.html"
info "  Workspace:       $WORKSPACE_DIR"
info ""
info "  Quick start:"
info "    bsession list              # list sessions"
info "    bsession run <name>        # start a session"
info ""
info "  Claude Code skills:"
info "    /browser fetch <url>       # grab data from a URL"
info "    /browser new <name>        # create a new automation"
info "    /browser <session-id>      # debug a session"
info ""
