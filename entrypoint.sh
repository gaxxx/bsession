#!/bin/bash
set -e

echo "========================================="
echo " Agent Browser Container"
echo "========================================="
echo ""

# Ensure workspace directories exist
mkdir -p /workspace/conf /workspace/data /workspace/scripts

# Start virtual display
export DISPLAY=:99
Xvfb :99 -screen 0 1280x900x24 &
sleep 1

# Start window manager
fluxbox &
sleep 1

# Start VNC server
if [ -n "$VNC_PASSWORD" ]; then
    mkdir -p ~/.vnc
    x11vnc -storepasswd "$VNC_PASSWORD" ~/.vnc/passwd
    x11vnc -display :99 -forever -rfbauth ~/.vnc/passwd -rfbport 5900 &
else
    x11vnc -display :99 -forever -nopw -rfbport 5900 &
fi
sleep 1

# Start noVNC (web-based VNC client)
websockify --web=/usr/share/novnc 6080 localhost:5900 &
sleep 1

echo "========================================="
echo " VNC server running on port 5900"
echo " noVNC web access: http://localhost:6080/vnc.html"
echo "========================================="
echo ""
echo "Use bsession to manage monitors:"
echo "  python3 /app/session.py run <id>"
echo "  python3 /app/session.py list"
echo ""

# Start bsession HTTP API (for container-to-container access)
python3 /app/lib/api.py &
echo " API server running on port 8080"
echo ""

# Keep container alive
exec tail -f /dev/null
