"""
Script generation via a pluggable local LLM endpoint.

Supported endpoint types:
  - Ollama  (default):  POST /api/generate  → streams JSON lines
  - OpenAI-compatible:  POST /v1/chat/completions
  - Custom REST:        POST /generate  (see README for contract)

The endpoint is selected automatically based on the LLM_ENDPOINT URL path.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

LLM_ENDPOINT: str = os.environ.get("LLM_ENDPOINT", "http://host.docker.internal:11434/api/generate")
LLM_MODEL:    str = os.environ.get("LLM_MODEL", "llama3")
TIMEOUT:      int = int(os.environ.get("LLM_TIMEOUT", "120"))


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(
    topic: str,
    niche: str,
    language: str,
    duration_target: int,
    style: str,
    extra_params: dict[str, Any],
) -> str:
    tone = extra_params.get("tone", "friendly")
    complexity = extra_params.get("complexity", "beginner")

    return f"""You are a professional short-video scriptwriter specializing in {niche} content.

Write a complete script for a {duration_target}-second vertical short video (9:16 format) about:
TOPIC: {topic}

REQUIREMENTS:
- Language: American English (en_US), natural spoken style
- Style: {style} (voice-over with on-screen text)
- Tone: {tone}
- Complexity level: {complexity} (for general audience)
- Total estimated speaking duration: {duration_target} seconds (~{duration_target * 2.5:.0f} words)
- IMPORTANT: This is EDUCATIONAL content only. Never give personalized financial advice.

OUTPUT FORMAT — respond ONLY with a valid JSON object (no markdown, no explanation):
{{
  "title": "<short catchy title, max 60 chars>",
  "hook": "<attention-grabbing opening line, 1-2 sentences>",
  "voiceover": "<full narration text, approx {duration_target * 2.5:.0f} words, spoken naturally>",
  "scenes": [
    {{
      "index": 1,
      "description": "<visual description for b-roll selection>",
      "duration_s": <seconds for this scene>,
      "keywords": ["<keyword1>", "<keyword2>"]
    }}
  ],
  "on_screen_captions": [
    "<short phrase 1 (max 6 words)>",
    "<short phrase 2>",
    "<short phrase 3>"
  ],
  "hashtags": ["#PersonalFinance", "#MoneyTips", "#Finance101"],
  "keywords": ["<keyword for asset selection>", "<keyword2>"],
  "disclaimer": "This video is for educational purposes only and does not constitute financial advice. Always consult a qualified financial professional."
}}

RULES:
- voiceover must be approx {duration_target * 2.5:.0f} words (±25%)
- 3 to 6 scenes, each 5–12 seconds
- 10–20 hashtags relevant to {niche}
- keywords should map to generic stock visuals (e.g., "money", "charts", "coins", "hands counting money")
- disclaimer must say "educational" and disclaim financial advice
- Do NOT include markdown, code blocks, or any text outside the JSON
"""


# ─────────────────────────────────────────────────────────────────────────────
# LLM callers
# ─────────────────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    """POST to Ollama /api/generate endpoint (non-streaming)."""
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.7, "num_predict": 1500},
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(LLM_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")


def _call_openai_compat(prompt: str) -> str:
    """POST to any OpenAI-compatible /chat/completions endpoint."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional scriptwriter. Respond only with valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1500,
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(LLM_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _call_custom(prompt: str) -> str:
    """POST to a custom /generate endpoint. See README for contract."""
    payload = {"prompt": prompt, "model": LLM_MODEL}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(LLM_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response") or data.get("text") or data.get("output") or ""


def _select_caller():
    endpoint = LLM_ENDPOINT.lower()
    if "/api/generate" in endpoint:
        return _call_ollama
    if "chat/completions" in endpoint:
        return _call_openai_compat
    return _call_custom


def _extract_json(raw: str) -> dict:
    """Extract the first JSON object from an LLM response string."""
    raw = raw.strip()
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try to find JSON block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No valid JSON found in LLM response. Raw:\n{raw[:500]}")


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def generate_script(
    topic: str,
    niche: str,
    language: str,
    duration_target: int,
    style: str,
    extra_params: dict[str, Any],
) -> dict:
    """
    Call the local LLM and return a parsed script dict.
    Retries up to 3 times with exponential back-off.
    """
    prompt = build_prompt(topic, niche, language, duration_target, style, extra_params)
    caller = _select_caller()

    logger.info("Calling LLM at %s (model=%s, topic=%s)", LLM_ENDPOINT, LLM_MODEL, topic)
    raw_response = caller(prompt)
    logger.debug("LLM raw response length: %d chars", len(raw_response))

    script = _extract_json(raw_response)
    return script
