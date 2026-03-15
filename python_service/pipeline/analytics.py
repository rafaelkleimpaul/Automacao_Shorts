"""
YouTube performance analytics.

Fetches weekly video statistics (views, likes, comments) for all videos
published via the pipeline and sends a summary report via Telegram.

Requirements:
  - YOUTUBE_ENABLED=true and a valid youtube_token.json with youtube.readonly scope
  - Run 'python scripts/auth_youtube.py' again if token was generated before this update
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_ROOT       = Path(os.environ.get("DATA_ROOT", "/data"))
CREDENTIALS_DIR = DATA_ROOT / "credentials"
TOKEN_FILE      = CREDENTIALS_DIR / "youtube_token.json"
SECRETS_FILE    = CREDENTIALS_DIR / "youtube_client_secrets.json"

YOUTUBE_SCOPES  = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOP_N = 5   # number of top videos to highlight in the report


# ─────────────────────────────────────────────────────────────────────────────
# YouTube API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"YouTube token not found at {TOKEN_FILE}. "
            "Run 'python scripts/auth_youtube.py' on the host first."
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), YOUTUBE_SCOPES)

    if creds.expired and creds.refresh_token:
        logger.info("Refreshing YouTube OAuth token…")
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())

    if not creds.valid:
        raise RuntimeError(
            "YouTube credentials invalid. Run 'python scripts/auth_youtube.py' again."
        )
    return creds


def _extract_video_id(url: str) -> str | None:
    """Extract video ID from a YouTube URL."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _fetch_stats(video_ids: list[str]) -> dict[str, dict]:
    """
    Fetch statistics for up to 50 video IDs from YouTube Data API.
    Returns a dict keyed by video_id with keys: title, views, likes, comments.
    """
    if not video_ids:
        return {}

    from googleapiclient.discovery import build
    creds   = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    result = {}
    # API allows up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp  = youtube.videos().list(
            part="statistics,snippet",
            id=",".join(chunk),
        ).execute()

        for item in resp.get("items", []):
            vid_id = item["id"]
            stats  = item.get("statistics", {})
            result[vid_id] = {
                "title":    item["snippet"]["title"],
                "views":    int(stats.get("viewCount",    0)),
                "likes":    int(stats.get("likeCount",    0)),
                "comments": int(stats.get("commentCount", 0)),
            }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_published_videos(days: int = 7) -> list[dict]:
    """Fetch YouTube videos published in the last `days` days from the DB."""
    try:
        import psycopg2.extras
        from utils.db import get_conn
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT r.platform_post_id, r.platform_url, r.published_at,
                           j.main_subject, j.niche
                    FROM   publish_results r
                    JOIN   video_jobs j ON j.id = r.job_id
                    WHERE  r.platform = 'youtube'
                    AND    r.status   = 'SUCCESS'
                    AND    r.published_at >= %s
                    ORDER  BY r.published_at DESC
                    """,
                    (cutoff,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("Could not fetch published videos from DB: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_weekly_report(days: int = 7) -> str:
    """Build a formatted weekly performance report for all YouTube videos."""
    published = _get_published_videos(days)

    if not published:
        return (
            f"📈 <b>Relatório semanal — YouTube</b>\n\n"
            f"Nenhum vídeo publicado nos últimos {days} dias."
        )

    # Extract video IDs
    video_ids = []
    id_to_meta: dict[str, dict] = {}
    for row in published:
        vid_id = row.get("platform_post_id") or _extract_video_id(row.get("platform_url", ""))
        if vid_id:
            video_ids.append(vid_id)
            id_to_meta[vid_id] = row

    # Fetch stats from YouTube API
    try:
        stats = _fetch_stats(video_ids)
    except Exception as exc:
        logger.warning("YouTube stats fetch failed: %s", exc)
        stats = {}

    # Merge stats with metadata
    videos: list[dict] = []
    for vid_id, meta in id_to_meta.items():
        s = stats.get(vid_id, {})
        videos.append({
            "id":       vid_id,
            "title":    s.get("title", meta.get("main_subject", "N/A")),
            "niche":    meta.get("niche", "N/A"),
            "views":    s.get("views",    0),
            "likes":    s.get("likes",    0),
            "comments": s.get("comments", 0),
            "url":      meta.get("platform_url", f"https://youtube.com/watch?v={vid_id}"),
        })

    # Sort by views
    videos.sort(key=lambda v: v["views"], reverse=True)

    # Totals
    total_views    = sum(v["views"]    for v in videos)
    total_likes    = sum(v["likes"]    for v in videos)
    total_comments = sum(v["comments"] for v in videos)

    # Date range
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    date_range = f"{start.strftime('%d/%m')} – {now.strftime('%d/%m/%Y')}"

    lines = [
        f"📈 <b>Relatório semanal — YouTube</b>",
        f"📅 {date_range} | {len(videos)} vídeo(s)\n",
        f"📊 <b>Totais:</b>",
        f"  👁 {total_views:,} views",
        f"  👍 {total_likes:,} likes",
        f"  💬 {total_comments:,} comentários\n",
    ]

    # Top videos
    top = videos[:TOP_N]
    lines.append(f"🏆 <b>Top {min(TOP_N, len(top))} vídeos:</b>")
    for i, v in enumerate(top, 1):
        lines.append(
            f"\n{i}. <b>{v['title'][:60]}</b>\n"
            f"   👁 {v['views']:,} | 👍 {v['likes']:,} | 💬 {v['comments']:,}\n"
            f"   🔗 {v['url']}"
        )

    # Worst performer (if more than TOP_N videos)
    if len(videos) > TOP_N:
        worst = videos[-1]
        lines.append(
            f"\n📉 <b>Menor alcance:</b>\n"
            f"  \"{worst['title'][:60]}\"\n"
            f"  👁 {worst['views']:,} views"
        )

    return "\n".join(lines)


def send_weekly_report(days: int = 7) -> None:
    """Fetch stats and send weekly performance report via Telegram."""
    from utils.telegram import send_message
    logger.info("Building weekly YouTube performance report")
    report = build_weekly_report(days)
    send_message(report)
    logger.info("Weekly report sent")
