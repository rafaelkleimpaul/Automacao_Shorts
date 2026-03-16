"""
Telegram notification client.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
All functions silently no-op if the vars are not set, so the pipeline
never breaks when Telegram is not configured.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _is_configured() -> bool:
    return bool(_TOKEN and _CHAT_ID)


def send_message(text: str) -> None:
    """Send a plain or HTML-formatted message to the configured Telegram chat."""
    if not _is_configured():
        logger.debug("Telegram not configured — skipping notification")
        return
    url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug("Telegram message sent (%d chars)", len(text))
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)


def send_message_with_buttons(text: str, buttons: list[list[dict]]) -> None:
    """
    Send an HTML message with an inline keyboard.

    Args:
        text: HTML-formatted message body.
        buttons: List of rows, each row is a list of button dicts.
                 Each button must have 'text' and either 'url' or 'callback_data'.

    Example:
        buttons = [[
            {"text": "✅ Aprovar", "url": "https://..."},
            {"text": "❌ Cancelar", "url": "https://..."},
        ]]
    """
    if not _is_configured():
        logger.debug("Telegram not configured — skipping notification")
        return
    url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id":    _CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug("Telegram message with buttons sent (%d chars)", len(text))
    except Exception as exc:
        logger.warning("Telegram notification with buttons failed: %s", exc)
