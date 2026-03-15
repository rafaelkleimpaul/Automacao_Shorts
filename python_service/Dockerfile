FROM python:3.12-slim-bookworm

# ── System packages (FFmpeg + audio libs) ────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libsndfile1-dev \
        sox \
        curl \
        fontconfig \
        && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . .

# ── Bake fonts from build context into image ─────────────────────────────────
RUN if ls fonts/*.ttf 2>/dev/null; then \
        cp fonts/*.ttf /usr/local/share/fonts/ && fc-cache -f /usr/local/share/fonts/; \
    fi

# ── Data volume (matched with n8n) ────────────────────────────────────────────
RUN mkdir -p /data/videos /data/assets/broll/finance /data/assets/music /data/assets/fonts \
    && mkdir -p /usr/local/share/fonts

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
