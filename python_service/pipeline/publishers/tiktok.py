"""
TikTok Content Posting API v2 publisher — Direct Post (FILE_UPLOAD).

Requirements:
  1. TikTok for Developers account: https://developers.tiktok.com/
  2. App with "Content Posting API" product added and approved
  3. Scopes requested: video.publish, video.upload, user.info.basic
  4. Run scripts/auth_tiktok.py ONCE on the host to get OAuth tokens
  5. Token is auto-refreshed (stored in data/credentials/tiktok_token.json)

Env vars:
  TIKTOK_ENABLED=true
  TIKTOK_CLIENT_KEY=<your app client key>
  TIKTOK_CLIENT_SECRET=<your app client secret>
  TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE  (PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | SELF_ONLY)

Notes:
  - No PUBLIC_BASE_URL needed — file is uploaded directly to TikTok servers.
  - Videos are uploaded in chunks (5 MB–64 MB per chunk).
  - Max video size: 4 GB.
  - Duration: 3 seconds to 10 minutes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TIKTOK_API_BASE  = "https://open.tiktokapis.com/v2"
CREDENTIALS_DIR  = Path(os.environ.get("DATA_ROOT", "/data")) / "credentials"
TOKEN_FILE       = CREDENTIALS_DIR / "tiktok_token.json"

CLIENT_KEY       = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET    = os.getenv("TIKTOK_CLIENT_SECRET", "")
PRIVACY_LEVEL    = os.getenv("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE")

CHUNK_MIN_BYTES  = 5  * 1024 * 1024   # 5  MB (TikTok minimum)
CHUNK_MAX_BYTES  = 64 * 1024 * 1024   # 64 MB (TikTok maximum)


class TikTokPublisher:
    platform_name = "tiktok"

    # ── Token management ──────────────────────────────────────────────────────

    def _load_token(self) -> dict:
        if not TOKEN_FILE.exists():
            raise FileNotFoundError(
                f"TikTok token not found at {TOKEN_FILE}. "
                "Run 'python scripts/auth_tiktok.py' on the host first."
            )
        return json.loads(TOKEN_FILE.read_text())

    def _save_token(self, data: dict) -> None:
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data, indent=2))

    def _refresh_if_needed(self, token_data: dict) -> dict:
        expires_at = token_data.get("expires_at", 0)
        if time.time() < expires_at - 300:   # 5 min buffer
            return token_data

        if not CLIENT_KEY or not CLIENT_SECRET:
            raise RuntimeError(
                "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET not set. "
                "Cannot refresh token automatically."
            )

        logger.info("Refreshing TikTok access token…")
        resp = requests.post(
            f"{TIKTOK_API_BASE}/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key":    CLIENT_KEY,
                "client_secret": CLIENT_SECRET,
                "grant_type":    "refresh_token",
                "refresh_token": token_data["refresh_token"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        new_data = resp.json().get("data", resp.json())
        new_data["expires_at"] = time.time() + new_data.get("expires_in", 86400)
        # Preserve refresh_token if not returned
        new_data.setdefault("refresh_token", token_data.get("refresh_token"))
        self._save_token(new_data)
        logger.info("TikTok token refreshed.")
        return new_data

    def _get_access_token(self) -> str:
        token_data = self._load_token()
        token_data = self._refresh_if_needed(token_data)
        return token_data["access_token"]

    # ── Upload helpers ────────────────────────────────────────────────────────

    def _auth_headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json; charset=UTF-8",
        }

    def _init_upload(self, access_token: str, video_size: int, title: str) -> tuple[str, str, int]:
        """
        Initialize direct post.
        Returns (publish_id, upload_url, chunk_size).
        """
        chunk_size = min(max(video_size, CHUNK_MIN_BYTES), CHUNK_MAX_BYTES)
        total_chunks = math.ceil(video_size / chunk_size)

        payload = {
            "post_info": {
                "title":                   title[:150],
                "privacy_level":           PRIVACY_LEVEL,
                "disable_duet":            False,
                "disable_comment":         False,
                "disable_stitch":          False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source":            "FILE_UPLOAD",
                "video_size":        video_size,
                "chunk_size":        chunk_size,
                "total_chunk_count": total_chunks,
            },
        }
        resp = requests.post(
            f"{TIKTOK_API_BASE}/post/publish/video/init/",
            headers=self._auth_headers(access_token),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise RuntimeError(f"TikTok init failed: {resp.json()}")

        logger.info("TikTok upload initiated: publish_id=%s, chunks=%d", publish_id, total_chunks)
        return publish_id, upload_url, chunk_size

    def _upload_chunks(self, upload_url: str, video_path: Path, video_size: int, chunk_size: int) -> None:
        """Upload video in chunks using PUT requests."""
        with open(video_path, "rb") as f:
            chunk_index = 0
            offset = 0
            while offset < video_size:
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                end_byte = offset + len(chunk_data) - 1

                resp = requests.put(
                    upload_url,
                    data=chunk_data,
                    headers={
                        "Content-Range":  f"bytes {offset}-{end_byte}/{video_size}",
                        "Content-Length": str(len(chunk_data)),
                        "Content-Type":   "video/mp4",
                    },
                    timeout=300,
                )
                if resp.status_code not in (200, 201, 206):
                    raise RuntimeError(
                        f"TikTok chunk {chunk_index} upload failed: "
                        f"HTTP {resp.status_code} — {resp.text[:300]}"
                    )

                offset += len(chunk_data)
                chunk_index += 1
                pct = int(offset / video_size * 100)
                logger.info("TikTok upload: %d%% (%d / %d bytes)", pct, offset, video_size)

    def _wait_for_publish(self, access_token: str, publish_id: str) -> None:
        """Poll publish status until PUBLISH_COMPLETE or failure."""
        for attempt in range(24):   # 24 × 5s = 2 minutes
            time.sleep(5)
            resp = requests.post(
                f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
                headers=self._auth_headers(access_token),
                json={"publish_id": publish_id},
                timeout=30,
            )
            resp.raise_for_status()
            data   = resp.json().get("data", {})
            status = data.get("status", "PROCESSING_UPLOAD")
            logger.info("TikTok publish status: %s (attempt %d/24)", status, attempt + 1)

            if status == "PUBLISH_COMPLETE":
                return
            if status in ("FAILED", "CANCELLED"):
                fail_code = data.get("fail_reason", "unknown")
                raise RuntimeError(f"TikTok publish failed: {fail_code}")

        raise TimeoutError("TikTok publish timed out after 2 minutes")

    # ── Public interface ──────────────────────────────────────────────────────

    def publish(
        self,
        video_path: Path,
        title: str,
        caption: str,
        hashtags: list[str],
        job: dict,
    ) -> dict:
        if not CLIENT_KEY or not CLIENT_SECRET:
            raise ValueError(
                "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set."
            )

        access_token = self._get_access_token()
        video_size   = video_path.stat().st_size

        # TikTok title = title + hashtags (max 150 chars)
        ht_str = " ".join(hashtags[:5])
        tt_title = f"{title} {ht_str}"[:150]

        # Step 1: Init
        publish_id, upload_url, chunk_size = self._init_upload(
            access_token, video_size, tt_title
        )

        # Step 2: Upload chunks
        logger.info("Uploading %s to TikTok (%.1f MB)…",
                    video_path.name, video_size / 1024 / 1024)
        self._upload_chunks(upload_url, video_path, video_size, chunk_size)

        # Step 3: Wait for publish
        self._wait_for_publish(access_token, publish_id)

        logger.info("TikTok publish complete: publish_id=%s", publish_id)
        return {
            "platform":         "tiktok",
            "status":           "SUCCESS",
            "platform_post_id": publish_id,
            # TikTok API doesn't return the post URL directly
            "platform_url":     "https://www.tiktok.com/",
        }
