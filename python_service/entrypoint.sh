#!/bin/sh
# Register any fonts placed in /data/assets/fonts/ at startup
if [ -d /data/assets/fonts ] && [ "$(ls /data/assets/fonts/*.ttf 2>/dev/null | wc -l)" -gt 0 ]; then
    cp /data/assets/fonts/*.ttf /usr/local/share/fonts/
    fc-cache -f /usr/local/share/fonts/
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
