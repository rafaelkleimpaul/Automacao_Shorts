"""
Instagram Graph API publisher — posts as a Reel.

Requirements:
  1. Instagram Business or Creator account connected to a Facebook Page
  2. Meta Developer App with instagram_content_publish permission approved
  3. Long-lived User Access Token (60-day, renewable)
  4. PUBLIC_BASE_URL must be set so Instagram's servers can fetch the video file
     → Use ngrok, Cloudflare Tunnel, or a public-facing server

Env vars:
  INSTAGRAM_ENABLED=true
  INSTAGRAM_USER_ID=<numeric IG Business Account ID>
  INSTAGRAM_ACCESS_TOKEN=<long-lived token>
  PUBLIC_BASE_URL=https://your-public-url.example.com   ← ngrok or real domain

Limitations:
  - Video must be publicly accessible via HTTP/S (no localhost).
  - Token expires every 60 days — run scripts/auth_instagram.py to renew.
  - API: Meta Graph API v21.0

Video specs (already matched by our FFmpeg output):
  - Format: MP4, H.264 + AAC
  - Aspect ratio: 9:16 vertical
  - Duration: 3–90 seconds
  - Max size: 100 MB
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE          = "https://graph.facebook.com/v21.0"
INSTAGRAM_USER_ID   = os.getenv("INSTAGRAM_USER_ID", "")
INSTAGRAM_TOKEN     = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
PUBLIC_BASE_URL     = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

MAX_POLL_ATTEMPTS   = 30    # 30 × 10s = 5 minutes max wait
POLL_INTERVAL_S     = 10


class InstagramPublisher:
    platform_name = "instagram"

    def _validate_config(self) -> None:
        if not INSTAGRAM_USER_ID:
            raise ValueError("INSTAGRAM_USER_ID env var is not set.")
        if not INSTAGRAM_TOKEN:
            raise ValueError("INSTAGRAM_ACCESS_TOKEN env var is not set.")
        if not PUBLIC_BASE_URL:
            raise ValueError(
                "PUBLIC_BASE_URL env var is not set. "
                "Instagram requires the video to be publicly accessible. "
                "Set PUBLIC_BASE_URL to your ngrok URL or public domain."
            )

    def _get_public_video_url(self, job_id: str, video_path: Path) -> str:
        """
        Build the public URL for the video file.
        The FastAPI worker serves files via GET /serve/{job_id}/{filename}.
        """
        return f"{PUBLIC_BASE_URL}/serve/{job_id}/{video_path.name}"

    def _create_container(self, video_url: str, caption: str) -> str:
        """POST to /{ig-user-id}/media → returns container_id."""
        resp = requests.post(
            f"{GRAPH_BASE}/{INSTAGRAM_USER_ID}/media",
            data={
                "media_type":    "REELS",
                "video_url":     video_url,
                "caption":       caption[:2200],   # Instagram cap
                "share_to_feed": "true",
                "access_token":  INSTAGRAM_TOKEN,
            },
            timeout=30,
        )
        resp.raise_for_status()
        container_id = resp.json().get("id")
        if not container_id:
            raise RuntimeError(f"No container ID in response: {resp.json()}")
        logger.info("Instagram container created: %s", container_id)
        return container_id

    def _wait_for_processing(self, container_id: str) -> None:
        """Poll until status_code=FINISHED or raise on ERROR/timeout."""
        for attempt in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL_S)
            resp = requests.get(
                f"{GRAPH_BASE}/{container_id}",
                params={
                    "fields":       "status_code,status",
                    "access_token": INSTAGRAM_TOKEN,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data        = resp.json()
            status_code = data.get("status_code", "IN_PROGRESS")
            logger.info("Instagram container %s status: %s (attempt %d/%d)",
                        container_id, status_code, attempt + 1, MAX_POLL_ATTEMPTS)

            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                raise RuntimeError(
                    f"Instagram video processing failed: {data.get('status')}"
                )
        raise TimeoutError(
            f"Instagram video still processing after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_S}s"
        )

    def _publish_container(self, container_id: str) -> str:
        """POST to /{ig-user-id}/media_publish → returns post_id."""
        resp = requests.post(
            f"{GRAPH_BASE}/{INSTAGRAM_USER_ID}/media_publish",
            data={
                "creation_id":  container_id,
                "access_token": INSTAGRAM_TOKEN,
            },
            timeout=30,
        )
        resp.raise_for_status()
        post_id = resp.json().get("id")
        if not post_id:
            raise RuntimeError(f"No post ID in publish response: {resp.json()}")
        return post_id

    def publish(
        self,
        video_path: Path,
        title: str,
        caption: str,
        hashtags: list[str],
        job: dict,
    ) -> dict:
        self._validate_config()

        full_caption = (
            f"{caption}\n\n"
            f"{' '.join(hashtags[:30])}"
        )
        job_id    = str(job["id"])
        video_url = self._get_public_video_url(job_id, video_path)
        logger.info("Instagram video URL: %s", video_url)

        # 3-step Instagram Reels flow
        container_id = self._create_container(video_url, full_caption)
        self._wait_for_processing(container_id)
        post_id = self._publish_container(container_id)

        post_url = f"https://www.instagram.com/reel/{post_id}/"
        logger.info("Instagram Reel published: %s", post_url)

        return {
            "platform":         "instagram",
            "status":           "SUCCESS",
            "platform_post_id": post_id,
            "platform_url":     post_url,
        }
