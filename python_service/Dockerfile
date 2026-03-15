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

# ── Libre Baskerville font (baked into image so libass always finds it) ───────
RUN mkdir -p /usr/local/share/fonts/librebaskerville \
    && curl -fL "https://github.com/google/fonts/raw/main/ofl/librebaskerville/LibreBaskerville-Regular.ttf" \
            -o /usr/local/share/fonts/librebaskerville/LibreBaskerville-Regular.ttf \
    && curl -fL "https://github.com/google/fonts/raw/main/ofl/librebaskerville/LibreBaskerville-Bold.ttf" \
            -o /usr/local/share/fonts/librebaskerville/LibreBaskerville-Bold.ttf \
    && fc-cache -f /usr/local/share/fonts/

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . .

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
