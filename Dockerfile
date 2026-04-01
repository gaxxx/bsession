FROM node:22-slim

# Install dependencies for Chromium + VNC + noVNC
RUN apt-get update && apt-get install -y \
    chromium \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    fonts-liberation libappindicator3-1 xdg-utils \
    xvfb x11vnc fluxbox xdotool \
    novnc websockify curl \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Install agent-browser globally
RUN npm install -g agent-browser

WORKDIR /app

# Install PyYAML for skill definitions
RUN python3 -c "import ensurepip; ensurepip.bootstrap()" 2>/dev/null; \
    python3 -m pip install --no-cache-dir pyyaml || true

# Copy session manager + skill runner (baked into image)
COPY session.py /app/session.py
COPY run_skill.py /app/run_skill.py
COPY lib/ /app/lib/

# Copy entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Volume mount point
VOLUME ["/workspace"]

# VNC port + noVNC web port
EXPOSE 5900 6080

LABEL org.opencontainers.image.source="https://github.com/gaxxx/bsession"

ENTRYPOINT ["/app/entrypoint.sh"]
