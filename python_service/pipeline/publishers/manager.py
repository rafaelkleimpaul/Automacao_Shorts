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
    hashtags: list[str],
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

    for publisher in platforms:
        name = publisher.platform_name
        logger.info("Publishing to %s…", name)
        try:
            result = publisher.publish(
                video_path = final_video_path,
                title      = title,
                caption    = caption,
                hashtags   = hashtags,
                job        = job,
            )
            result.setdefault("platform", name)
            result.setdefault("status", "SUCCESS")
            logger.info("Published to %s: %s", name, result.get("platform_url"))
        except Exception as exc:
            logger.error("Failed to publish to %s: %s", name, exc, exc_info=True)
            result = {
                "platform":      name,
                "status":        "FAILED",
                "error_message": str(exc),
            }
        results.append(result)

    return results
