"""
Automated topic generator: fetches trending news via RSS and uses the local LLM
to suggest video topic ideas ready for job creation.

Extensible via NICHE_CONFIGS — add a new key to support new niches without
touching any other file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LLM_ENDPOINT: str = os.environ.get("LLM_ENDPOINT", "http://host.docker.internal:11434/api/generate")
LLM_MODEL:    str = os.environ.get("LLM_MODEL", "llama3")
LLM_API_KEY:  str = os.environ.get("LLM_API_KEY", "")
LLM_TIMEOUT:  int = int(os.environ.get("LLM_TIMEOUT", "120"))

# Topic deduplication settings
DEDUP_LOOKBACK_DAYS: int   = int(os.environ.get("TOPIC_DEDUP_DAYS",      "30"))
DEDUP_THRESHOLD:     float = float(os.environ.get("TOPIC_DEDUP_THRESHOLD", "0.45"))


# ─────────────────────────────────────────────────────────────────────────────
# Niche configurations
# To add a new niche (e.g. "crypto", "real_estate"), add a new key below.
# ─────────────────────────────────────────────────────────────────────────────

NICHE_CONFIGS: dict[str, dict[str, Any]] = {
    "finance": {
        "rss_feeds": [
            "https://finance.yahoo.com/news/rssindex",
            "https://feeds.marketwatch.com/marketwatch/topstories/",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
            "https://feeds.bloomberg.com/markets/news.rss",
        ],
        "language":                 "en_US",
        "style":                    "commentary",
        "duration_target_seconds":  30,
        "assets_profile":           "finance",
        "priority":                 7,
        "extra_params":             {"tone": "friendly", "complexity": "intermediate"},
        "llm_system": (
            "You are a finance content strategist specializing in short-form educational videos. "
            "Suggest engaging video topics that are educational only — never personalized financial advice. "
            "Topics should be specific, timely, and suitable for a 30-second short."
        ),
    },
    "lifestyle": {
        "rss_feeds": [],
        "language":                 "en_US",
        "style":                    "commentary",
        "duration_target_seconds":  30,
        "assets_profile":           "lifestyle",
        "priority":                 7,
        "extra_params":             {"tone": "aspirational", "complexity": "beginner"},
        "llm_system": (
            "You are a premium short-form video content strategist for luxury lifestyle brands. "
            "Your videos are hypnotic, aspirational, and impossible to scroll past. "
            "They capture attention instantly, keep viewers hooked until the last second, "
            "and trigger an emotional response of desire, status, and aspiration. "
            "Never suggest generic, weak, or common motivational content. "
            "Every topic must feel premium, rare, and viral."
        ),
        "topic_user_prompt": (
            "Generate exactly {{count}} viral short-video topic ideas for a premium luxury lifestyle channel.\n\n"
            "Each topic must:\n"
            "- Lead with a brutally strong hook that stops the scroll\n"
            "- Feel like rare, high-value content — not generic\n"
            "- Revolve around luxury, power, money, achievement, or status\n"
            "- Be visually intense and suitable for fast-cut short-form video\n"
            "- Use few but extremely powerful words\n"
            "- Make viewers want to share, save, and follow\n\n"
            "Respond ONLY with a JSON array of {{count}} strings — no explanation, no markdown:\n"
            '[\"Topic 1\", \"Topic 2\"]'
        ),
    },
    "mindset": {
        "rss_feeds": [],
        "language":                 "en_US",
        "style":                    "quote",
        "duration_target_seconds":  15,
        "assets_profile":           "mindset",
        "priority":                 8,
        "extra_params": {
            "silent":        True,
            "music_profile": "mindset",
            "tone":          "powerful",
        },
        "llm_system": (
            "You are a motivational content creator for viral short-form videos. "
            "You draw inspiration from books like Rich Dad Poor Dad, The Richest Man in Babylon, "
            "Think and Grow Rich, The 48 Laws of Power, and Atomic Habits. "
            "Your phrases are about not giving up, focus, winning, discipline, and conquest. "
            "Never generate generic or weak motivational clichés."
        ),
        "topic_user_prompt": (
            "Generate exactly {{count}} short powerful phrases for motivational short videos.\n\n"
            "Rules:\n"
            "- Maximum 8 words per phrase\n"
            "- Direct, powerful, impossible to ignore\n"
            "- About: not failing, focus, winning, studying, discipline, conquest\n"
            "- Can be inspired by books or original\n"
            "- All in English\n"
            "- Examples: 'I will not fail.', 'Focus or fall behind.', 'Winners study. Losers scroll.'\n\n"
            "Respond ONLY with a JSON array of {{count}} strings — no explanation, no markdown:\n"
            '[\"Phrase 1\", \"Phrase 2\"]'
        ),
    },
    # ── Future niches ─────────────────────────────────────────────────────────
    # "crypto": {
    #     "rss_feeds": ["https://cointelegraph.com/rss", "https://coindesk.com/arc/outboundfeeds/rss/"],
    #     "language": "en_US", "style": "commentary", "duration_target_seconds": 30,
    #     "assets_profile": "finance", "priority": 7,
    #     "extra_params": {"tone": "energetic", "complexity": "intermediate"},
    #     "llm_system": "You are a crypto content strategist ...",
    # },
    # "real_estate": { ... },
    # "entrepreneurship": { ... },
}


# ─────────────────────────────────────────────────────────────────────────────
# RSS fetching
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_headlines(rss_urls: list[str], max_per_feed: int = 6) -> list[str]:
    """Fetch multiple RSS feeds and return a deduplicated list of headlines."""
    seen: set[str] = set()
    headlines: list[str] = []

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for url in rss_urls:
            try:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                root = ET.fromstring(resp.text)

                # RSS 2.0: //item/title  |  Atom: //{atom}title
                items = (
                    root.findall(".//item/title")
                    or root.findall(".//{http://www.w3.org/2005/Atom}title")
                )
                count = 0
                for item in items:
                    text = (item.text or "").strip()
                    if text and text not in seen:
                        seen.add(text)
                        headlines.append(text)
                        count += 1
                        if count >= max_per_feed:
                            break

                logger.info("RSS %s → %d headlines", url, count)
            except Exception as exc:
                logger.warning("Failed to fetch RSS %s: %s", url, exc)

    return headlines


# ─────────────────────────────────────────────────────────────────────────────
# LLM topic generation
# ─────────────────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, system: str) -> str:
    """Call the configured LLM endpoint and return raw text response."""
    endpoint = LLM_ENDPOINT.lower()

    if "/api/generate" in endpoint:
        payload = {
            "model":   LLM_MODEL,
            "prompt":  f"{system}\n\n{prompt}",
            "stream":  False,
            "options": {"temperature": 0.85, "num_predict": 600},
        }
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            resp = client.post(LLM_ENDPOINT, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    if "chat/completions" in endpoint:
        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens":  600,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            resp = client.post(LLM_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    # Custom endpoint fallback
    payload = {"prompt": f"{system}\n\n{prompt}", "model": LLM_MODEL}
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        resp = client.post(LLM_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response") or data.get("text") or data.get("output") or ""


def _generate_topics_via_llm(
    headlines: list[str],
    niche: str,
    count: int,
    system_prompt: str,
    topic_user_prompt: str | None = None,
) -> list[str]:
    """Ask the LLM to generate video topic ideas based on trending headlines."""
    if topic_user_prompt:
        prompt = topic_user_prompt.replace("{{count}}", str(count))
    else:
        headlines_block = "\n".join(f"- {h}" for h in headlines[:20]) if headlines else "(no trend data available)"
        prompt = f"""Trending {niche} news headlines right now:
{headlines_block}

Based on these trends, generate exactly {count} short-video topic ideas for a 30-second educational video.

Requirements for each topic:
- Specific and engaging (not generic like "money tips")
- Inspired by the trends above but rephrased as an educational angle
- Suitable for a general audience

Respond ONLY with a JSON array of {count} strings — no explanation, no markdown:
["Topic 1", "Topic 2", "Topic 3"]"""

    raw = _call_llm(prompt, system_prompt)
    raw = raw.strip()

    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if match:
        topics = json.loads(match.group())
        return [str(t).strip() for t in topics[:count] if str(t).strip()]

    raise ValueError(f"LLM did not return a valid JSON array. Raw:\n{raw[:400]}")


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _filter_duplicates(topics: list[str], niche: str) -> list[str]:
    """
    Remove topics that are too similar to recently generated jobs in the DB.
    Uses pg_trgm similarity (threshold=DEDUP_THRESHOLD, window=DEDUP_LOOKBACK_DAYS).
    Falls back gracefully if the DB is unavailable.
    """
    try:
        from utils.db import find_similar_topics
    except ImportError:
        return topics

    kept:    list[str] = []
    skipped: list[str] = []

    for topic in topics:
        try:
            matches = find_similar_topics(
                subject   = topic,
                niche     = niche,
                days      = DEDUP_LOOKBACK_DAYS,
                threshold = DEDUP_THRESHOLD,
            )
        except Exception as exc:
            logger.warning("Dedup DB check failed for '%s': %s", topic, exc)
            kept.append(topic)
            continue

        if matches:
            best = matches[0]
            logger.warning(
                "Dedup: skipping '%s' — %.0f%% similar to recent job '%s' (%s ago)",
                topic,
                best["sim"] * 100,
                best["main_subject"],
                _days_ago(best["created_at"]),
            )
            skipped.append((topic, best))
        else:
            kept.append(topic)

    if skipped:
        logger.info(
            "Dedup summary: kept %d/%d topics, skipped %d (lookback=%dd, threshold=%.0f%%)",
            len(kept), len(topics), len(skipped),
            DEDUP_LOOKBACK_DAYS, DEDUP_THRESHOLD * 100,
        )
        # Telegram alert for blocked topics
        try:
            from utils.telegram import send_message
            lines = [f"⛔ <b>Tópicos bloqueados por duplicidade — {niche}</b>"]
            for topic, best in skipped:
                sim_pct = int(best["sim"] * 100)
                lines.append(
                    f"❌ \"{topic}\"\n"
                    f"   ↔️ Similar a: \"{best['main_subject']}\" "
                    f"({sim_pct}% — {_days_ago(best['created_at'])} atrás)"
                )
            send_message("\n".join(lines))
        except Exception as exc:
            logger.debug("Telegram dedup alert failed: %s", exc)

    return kept


def _days_ago(ts: Any) -> str:
    """Return a human-readable string like '3d' or '2h' from a datetime."""
    try:
        from datetime import datetime, timezone
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        days  = delta.days
        hours = delta.seconds // 3600
        return f"{days}d" if days else f"{hours}h"
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_topics(niche: str = "finance", count: int = 3) -> list[dict[str, Any]]:
    """
    Fetch trending news for the given niche, use the local LLM to suggest
    video topic ideas, and return a list of job parameter dicts ready for
    insertion into video_jobs.

    Args:
        niche:  Key from NICHE_CONFIGS (e.g. "finance").
        count:  Number of topic ideas to generate.

    Returns:
        List of dicts with keys: main_subject, niche, language, style,
        duration_target_seconds, assets_profile, priority, extra_params.
    """
    config = NICHE_CONFIGS.get(niche)
    if not config:
        available = list(NICHE_CONFIGS.keys())
        raise ValueError(f"Unknown niche '{niche}'. Available niches: {available}")

    logger.info("Fetching trending headlines for niche=%s", niche)
    headlines = _fetch_headlines(config["rss_feeds"])

    if not headlines:
        logger.warning("No headlines fetched — LLM will generate topics without trend context")

    logger.info("Generating %d topic ideas via LLM (model=%s)", count, LLM_MODEL)
    topics = _generate_topics_via_llm(
        headlines=headlines,
        niche=niche,
        count=count,
        system_prompt=config["llm_system"],
        topic_user_prompt=config.get("topic_user_prompt"),
    )

    # ── Deduplication: skip topics too similar to recent ones ─────────────────
    topics = _filter_duplicates(topics, niche)

    return [
        {
            "main_subject":             topic,
            "niche":                    niche,
            "language":                 config["language"],
            "style":                    config["style"],
            "duration_target_seconds":  config["duration_target_seconds"],
            "assets_profile":           config["assets_profile"],
            "priority":                 config["priority"],
            "extra_params":             config["extra_params"],
        }
        for topic in topics
    ]


def list_available_niches() -> list[str]:
    """Return the list of configured niche keys."""
    return list(NICHE_CONFIGS.keys())
