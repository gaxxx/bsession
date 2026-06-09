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
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install agent-browser globally
RUN npm install -g agent-browser

# Install cloakbrowser (source-patched stealth Chromium) and pre-bake its
# binary into the image so the cloak backend launches instantly at runtime.
# Auto-update is disabled so launches never make network calls.
ENV CLOAKBROWSER_AUTO_UPDATE=false
RUN pip3 install --no-cache-dir --break-system-packages cloakbrowser \
    && python3 -m cloakbrowser install

WORKDIR /app

# Copy bsession primitive CLI (baked into image)
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
