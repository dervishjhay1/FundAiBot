"""
FundzAiBot — Product Metadata

Exposes structured product metadata consumed by Fundz Company Headquarters.
Official referral links are sourced from HQ Product Registry — never hardcoded.
"""

from __future__ import annotations

import requests
from datetime import datetime, timezone

from config.settings import (
    BOT_NAME, BOT_VERSION, BOT_DESCRIPTION,
    GITHUB_REPO, HQ_API_URL, HQ_API_KEY, HQ_SYNC_ENABLED,
    TELEGRAM_BOT_USERNAME, REFERRAL_LINK, BOT_DEEP_LINK,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Cached HQ-sourced referral link ──────────────────────────────────────────

_cached_referral_link: str = REFERRAL_LINK
_cached_deep_link: str = BOT_DEEP_LINK
_hq_registry_loaded: bool = False


def _fetch_hq_registry() -> dict | None:
    """Fetch this product's registry entry from HQ Product Registry."""
    if not HQ_SYNC_ENABLED:
        return None
    try:
        resp = requests.get(
            f"{HQ_API_URL}/api/products/fundzaibot",
            headers={
                "Authorization": f"Bearer {HQ_API_KEY}",
                "X-Source":      "fundzaibot",
            },
            timeout=(5, 10),
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.debug("Could not fetch HQ registry: %s", exc)
    return None


def refresh_from_hq() -> bool:
    """
    Refresh referral link and deep link from HQ Product Registry.
    Called once on startup and can be refreshed at runtime.
    Returns True if updated from HQ.
    """
    global _cached_referral_link, _cached_deep_link, _hq_registry_loaded

    data = _fetch_hq_registry()
    if not data:
        log.info("Product metadata: HQ registry not available — using local defaults")
        return False

    updated = False
    new_referral = data.get("referral_link") or data.get("official_referral_link", "")
    new_deep     = data.get("deep_link") or data.get("official_deep_link", "")

    if new_referral and new_referral != _cached_referral_link:
        _cached_referral_link = new_referral
        updated = True
    if new_deep and new_deep != _cached_deep_link:
        _cached_deep_link = new_deep
        updated = True

    _hq_registry_loaded = True
    if updated:
        log.info("Product metadata: referral/deep links refreshed from HQ")
    return updated


def get_referral_link() -> str:
    """Return the official referral link (always from HQ if available)."""
    return _cached_referral_link or f"https://t.me/{TELEGRAM_BOT_USERNAME}"


def get_deep_link() -> str:
    """Return the official deep link."""
    return _cached_deep_link or f"https://t.me/{TELEGRAM_BOT_USERNAME}"


def get_metadata() -> dict:
    """
    Return the full structured product metadata dict.
    This is the payload Headquarters consumes.
    """
    return {
        "product_id":        "fundzaibot",
        "product_name":      BOT_NAME,
        "version":           BOT_VERSION,
        "description":       BOT_DESCRIPTION,
        "tagline":           "Your Intelligent AI Assistant",
        "status":            "active",
        "github_repository": GITHUB_REPO,
        "telegram_username": TELEGRAM_BOT_USERNAME,
        "official_referral_link": get_referral_link(),
        "official_deep_link":     get_deep_link(),
        "deployment_platform":    "Railway",
        "source_of_truth":        "GitHub",
        "hq_registry_loaded":     _hq_registry_loaded,
        "capabilities": [
            "AI Chat with memory",
            "Writing assistance",
            "Code generation and debugging",
            "Business assistance",
            "Education and tutoring",
            "Prompt engineering",
            "Translation",
            "Summarization",
            "Image generation",
            "Image analysis (vision)",
            "Voice transcription",
            "Web search",
            "Productivity tools",
            "Multi-language support",
            "VIP subscription plans",
            "Referral reward system",
        ],
        "ai_providers": ["OpenAI", "OpenRouter", "Gemini", "HuggingFace"],
        "metadata_fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def push_metadata_to_hq() -> bool:
    """Push current product metadata to HQ Product Registry."""
    if not HQ_SYNC_ENABLED:
        return False
    try:
        resp = requests.put(
            f"{HQ_API_URL}/api/products/fundzaibot/metadata",
            json=get_metadata(),
            headers={
                "Authorization": f"Bearer {HQ_API_KEY}",
                "Content-Type":  "application/json",
                "X-Source":      "fundzaibot",
            },
            timeout=(5, 10),
        )
        if resp.status_code in (200, 201, 204):
            log.info("Product metadata pushed to HQ successfully")
            return True
        log.warning("HQ metadata push: HTTP %s", resp.status_code)
        return False
    except Exception as exc:
        log.warning("Could not push metadata to HQ: %s", exc)
        return False
