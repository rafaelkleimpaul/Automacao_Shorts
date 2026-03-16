"""
Hashtag Manager — discovers and scores trending hashtags per niche.

Strategy:
  1. Search YouTube for top-performing Shorts in each niche
  2. Extract hashtags from video tags + titles + descriptions
  3. Score by: frequency × average view count of videos that use the tag
  4. Cache results to /data/assets/hashtags/<niche>.json
  5. hashtag_booster.py loads the cache to enrich every video's hashtag set
  6. Sends a Telegram summary with top discovered tags

Requires YOUTUBE_ENABLED=true and a valid youtube_token.json with
youtube.readonly scope (run scripts/auth_youtube.py to generate).

Can also run in LLM-only mode (no YouTube token needed) — less data-driven
but still useful for generating niche-relevant hashtag suggestions.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_ROOT     = Path(os.environ.get("DATA_ROOT", "/data"))
HASHTAG_DIR   = DATA_ROOT / "assets" / "hashtags"
CREDENTIALS_DIR = DATA_ROOT / "credentials"
TOKEN_FILE    = CREDENTIALS_DIR / "youtube_token.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# How many YouTube search results to analyse per niche
SEARCH_RESULTS = 50

# Maximum tags to keep in the cache per niche
MAX_CACHED_TAGS = 60

# Minimum times a tag must appear across videos to be included
MIN_FREQUENCY = 2

# Niche → search queries used to find relevant Shorts
NICHE_SEARCH_QUERIES: dict[str, list[str]] = {
    "finance": [
        "personal finance tips shorts",
        "money tips 2025 shorts",
        "how to save money shorts",
        "investing for beginners shorts",
        "financial freedom shorts",
    ],
    "mindset": [
        "mindset motivation shorts",
        "success mindset shorts",
        "discipline motivation shorts",
        "self improvement shorts",
        "daily motivation quotes shorts",
    ],
    "lifestyle": [
        "luxury lifestyle shorts",
        "lifestyle motivation shorts",
        "glow up lifestyle shorts",
        "aesthetic lifestyle shorts",
        "life goals motivation shorts",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# YouTube API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_youtube_client():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"YouTube token not found at {TOKEN_FILE}. "
            "Run 'python scripts/auth_youtube.py' first."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError("YouTube credentials invalid. Re-run auth_youtube.py.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _extract_hashtags_from_text(text: str) -> list[str]:
    """Extract #Hashtag tokens from any text."""
    return re.findall(r"#[A-Za-z][A-Za-z0-9_]+", text)


def _search_top_shorts(youtube, query: str, max_results: int = 50) -> list[str]:
    """Search YouTube for Shorts matching `query`, return list of video IDs."""
    try:
        resp = youtube.search().list(
            part="id",
            q=query,
            type="video",
            videoDuration="short",
            order="viewCount",
            maxResults=min(max_results, 50),
        ).execute()
        return [item["id"]["videoId"] for item in resp.get("items", []) if "videoId" in item["id"]]
    except Exception as exc:
        logger.warning("YouTube search failed for '%s': %s", query, exc)
        return []


def _fetch_video_details(youtube, video_ids: list[str]) -> list[dict]:
    """Fetch snippet + statistics for a list of video IDs (max 50 per call)."""
    results = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        try:
            resp = youtube.videos().list(
                part="snippet,statistics",
                id=",".join(chunk),
            ).execute()
            results.extend(resp.get("items", []))
        except Exception as exc:
            logger.warning("Video details fetch failed: %s", exc)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

def _score_tags(videos: list[dict]) -> list[dict]:
    """
    Score hashtags by: frequency × avg_views of videos that use them.
    Returns list of dicts sorted by score desc.
    """
    tag_freq:  dict[str, int]   = defaultdict(int)
    tag_views: dict[str, list]  = defaultdict(list)
    tag_norm:  dict[str, str]   = {}  # lowercase → original casing

    for video in videos:
        snippet = video.get("snippet", {})
        stats   = video.get("statistics", {})
        views   = int(stats.get("viewCount", 0))

        # Collect tags from: explicit tags, title, description
        raw_tags: list[str] = list(snippet.get("tags") or [])
        raw_tags += _extract_hashtags_from_text(snippet.get("title", ""))
        raw_tags += _extract_hashtags_from_text(snippet.get("description", ""))

        seen_in_video: set[str] = set()
        for raw in raw_tags:
            tag = raw if raw.startswith("#") else f"#{raw}"
            key = tag.lower()

            # Skip overly generic or very short tags
            if len(tag) < 4 or key in {"#fy", "#fyp", "#foryou", "#shorts", "#reels", "#viral"}:
                continue

            if key not in seen_in_video:
                tag_freq[key]  += 1
                tag_views[key].append(views)
                tag_norm[key]   = tag
                seen_in_video.add(key)

    if not tag_freq:
        return []

    # Compute score = frequency × log(avg_views + 1)
    import math
    scored = []
    max_freq  = max(tag_freq.values())
    max_views = max((sum(v) / len(v) for v in tag_views.values()), default=1)

    for key, freq in tag_freq.items():
        if freq < MIN_FREQUENCY:
            continue
        avg_views  = sum(tag_views[key]) / len(tag_views[key])
        raw_score  = (freq / max_freq) * 0.4 + (math.log(avg_views + 1) / math.log(max_views + 1)) * 0.6
        scored.append({
            "tag":       tag_norm[key],
            "score":     round(raw_score * 100, 1),
            "frequency": freq,
            "avg_views": int(avg_views),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ─────────────────────────────────────────────────────────────────────────────
# LLM fallback
# ─────────────────────────────────────────────────────────────────────────────

def _llm_suggest_hashtags(niche: str, count: int = 30) -> list[dict]:
    """
    Ask the LLM to suggest currently relevant hashtags for a niche.
    Used as fallback when YouTube API is unavailable.
    """
    try:
        from pipeline.script_gen import _call_llm  # type: ignore
        prompt = (
            f"You are a social media expert. List the {count} most effective and currently "
            f"trending hashtags for {niche} content on YouTube Shorts and Instagram Reels in 2025. "
            f"Focus on hashtags that drive real reach and engagement. "
            f"Return ONLY a JSON array of hashtag strings, e.g. [\"#Finance\", \"#MoneyTips\"]. "
            f"No explanation, no markdown, just the JSON array."
        )
        raw = _call_llm(prompt)
        # Extract JSON array from response
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            tags = json.loads(match.group())
            return [
                {"tag": t if t.startswith("#") else f"#{t}", "score": 50.0, "frequency": 0, "avg_views": 0}
                for t in tags if isinstance(t, str)
            ]
    except Exception as exc:
        logger.warning("LLM hashtag suggestion failed: %s", exc)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(niche: str) -> Path:
    HASHTAG_DIR.mkdir(parents=True, exist_ok=True)
    return HASHTAG_DIR / f"{niche}.json"


def load_cached_hashtags(niche: str) -> list[str]:
    """
    Load cached hashtags for a niche.
    Returns a list of hashtag strings sorted by score, or [] if no cache.
    """
    path = _cache_path(niche)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [item["tag"] for item in data.get("hashtags", [])]
    except Exception as exc:
        logger.warning("Could not load hashtag cache for '%s': %s", niche, exc)
        return []


def _save_cache(niche: str, scored: list[dict]) -> None:
    path = _cache_path(niche)
    data = {
        "niche":      niche,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hashtags":   scored[:MAX_CACHED_TAGS],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Hashtag cache saved: %s (%d tags)", path, len(data["hashtags"]))


# ─────────────────────────────────────────────────────────────────────────────
# Main refresh logic
# ─────────────────────────────────────────────────────────────────────────────

def refresh_hashtags_for_niche(niche: str) -> list[dict]:
    """
    Discover and score trending hashtags for one niche.
    Tries YouTube API first, falls back to LLM if unavailable.
    Saves results to cache and returns the scored list.
    """
    queries  = NICHE_SEARCH_QUERIES.get(niche, [f"{niche} tips shorts"])
    all_vids: list[dict] = []

    # Try YouTube API
    try:
        youtube    = _get_youtube_client()
        video_ids  = []
        for q in queries:
            ids = _search_top_shorts(youtube, q, max_results=SEARCH_RESULTS // len(queries))
            video_ids.extend(ids)
        video_ids = list(dict.fromkeys(video_ids))  # deduplicate, preserve order
        logger.info("Fetching details for %d videos (niche: %s)", len(video_ids), niche)
        all_vids  = _fetch_video_details(youtube, video_ids)
        scored    = _score_tags(all_vids)
        source    = "youtube"
    except Exception as exc:
        logger.warning("YouTube discovery failed for '%s', falling back to LLM: %s", niche, exc)
        scored = _llm_suggest_hashtags(niche)
        source = "llm"

    if not scored:
        logger.warning("No hashtags discovered for niche '%s'", niche)
        return []

    # Add source metadata
    for item in scored:
        item["source"] = source

    _save_cache(niche, scored)
    logger.info(
        "Hashtag refresh done for '%s': %d tags (source: %s, videos analysed: %d)",
        niche, len(scored), source, len(all_vids),
    )
    return scored


def refresh_all_niches() -> dict[str, list[dict]]:
    """Refresh hashtag cache for all configured niches."""
    results = {}
    for niche in NICHE_SEARCH_QUERIES:
        logger.info("Refreshing hashtags for niche: %s", niche)
        results[niche] = refresh_hashtags_for_niche(niche)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Telegram report
# ─────────────────────────────────────────────────────────────────────────────

def build_hashtag_report(results: dict[str, list[dict]]) -> str:
    lines = ["🏷️ <b>Hashtag Trends — atualizado</b>\n"]
    for niche, scored in results.items():
        if not scored:
            lines.append(f"<b>{niche}:</b> nenhuma hashtag encontrada\n")
            continue
        top = scored[:10]
        lines.append(f"📌 <b>{niche.capitalize()} — Top 10:</b>")
        for i, item in enumerate(top, 1):
            score = item["score"]
            freq  = item.get("frequency", 0)
            views = item.get("avg_views", 0)
            tag   = item["tag"]
            detail = f"freq:{freq} avg:{views:,}v" if freq else "via LLM"
            lines.append(f"  {i:2}. {tag:<28} {score:.0f}pts  ({detail})")
        lines.append("")
    return "\n".join(lines)


def refresh_and_notify(niches: list[str] | None = None) -> dict[str, list[dict]]:
    """Refresh hashtag cache and send Telegram report."""
    from utils.telegram import send_message
    targets = niches or list(NICHE_SEARCH_QUERIES.keys())
    results = {}
    for niche in targets:
        results[niche] = refresh_hashtags_for_niche(niche)
    report = build_hashtag_report(results)
    send_message(report)
    return results
