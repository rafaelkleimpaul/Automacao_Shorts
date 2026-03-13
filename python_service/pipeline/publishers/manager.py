"""
Publishing manager – orchestrates posting to all enabled platforms.

Each platform is enabled via env vars:
  YOUTUBE_ENABLED=true
  INSTAGRAM_ENABLED=true
  TIKTOK_ENABLED=true

All three are disabled by default. Enable only the ones you have credentials for.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

YOUTUBE_ENABLED   = os.getenv("YOUTUBE_ENABLED", "false").lower() == "true"
INSTAGRAM_ENABLED = os.getenv("INSTAGRAM_ENABLED", "false").lower() == "true"
TIKTOK_ENABLED    = os.getenv("TIKTOK_ENABLED", "false").lower() == "true"


def any_enabled() -> bool:
    return YOUTUBE_ENABLED or INSTAGRAM_ENABLED or TIKTOK_ENABLED


def publish_all(
    job: dict[str, Any],
    final_video_path: Path,
    title: str,
    caption: str,
    hashtags: "list[str] | dict[str, list[str]]",
) -> list[dict[str, Any]]:
    """
    Publish to all enabled platforms.
    Returns a list of result dicts, one per platform attempted.
    Never raises — failures are captured in the result dict.
    """
    results: list[dict[str, Any]] = []

    platforms = []
    if YOUTUBE_ENABLED:
        from pipeline.publishers.youtube import YouTubePublisher
        platforms.append(YouTubePublisher())
    if INSTAGRAM_ENABLED:
        from pipeline.publishers.instagram import InstagramPublisher
        platforms.append(InstagramPublisher())
    if TIKTOK_ENABLED:
        from pipeline.publishers.tiktok import TikTokPublisher
        platforms.append(TikTokPublisher())

    from utils.db import log_publish as _log_publish
    job_id = str(job.get("id", ""))

    # Support both flat list and per-platform dict (from hashtag_booster)
    def _hashtags_for(platform_name: str) -> list:
        if isinstance(hashtags, dict):
            return hashtags.get(platform_name, hashtags.get("default", []))
        return hashtags  # type: ignore[return-value]

    for publisher in platforms:
        name = publisher.platform_name
        logger.info("[%s] Starting upload…", name.upper())
        _log_publish(job_id, name, "STARTED", "Upload started",
                     details={"title": title, "video": str(final_video_path)})
        try:
            result = publisher.publish(
                video_path = final_video_path,
                title      = title,
                caption    = caption,
                hashtags   = _hashtags_for(name),
                job        = job,
            )
            result.setdefault("platform", name)
            result.setdefault("status", "SUCCESS")
            logger.info("[%s] Upload complete → %s", name.upper(), result.get("platform_url"))
        except Exception as exc:
            import traceback
            error_detail = traceback.format_exc()
            logger.error("[%s] Upload FAILED: %s", name.upper(), exc)
            logger.debug("[%s] Full traceback:\n%s", name.upper(), error_detail)
            result = {
                "platform":      name,
                "status":        "FAILED",
                "error_message": f"{type(exc).__name__}: {exc}",
                "error_detail":  error_detail,
            }
        results.append(result)

    return results
