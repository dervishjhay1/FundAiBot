"""
FundzAiBot — Central configuration.
All environment variables and constants live here.
Every other module imports from this file.

VERSION 5.0.1 — Ecosystem Restructuring
  FundzAiBot is now a dedicated AI assistant product.
  Executive governance has moved to Fundz Company Headquarters.
  This bot never makes executive decisions.

DEPLOYMENT POLICY:
  Telegram polling starts ONLY when Railway environment variables are detected (IS_RAILWAY=True).
  Any non-Railway environment runs Flask keep-alive only.
  This is a hard architectural boundary — do not bypass it.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_ID: int = int(os.getenv("ADMIN_USER_ID", "0"))

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── AI Providers ──────────────────────────────────────────────────────────────
# Provider priority: OpenAI → OpenRouter → Gemini → HuggingFace
# Set at least ONE key in Railway for AI to work.
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY: str = os.getenv("HUGGINGFACE_API_KEY", "")

# ── AI model names ────────────────────────────────────────────────────────────
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
HF_CHAT_MODEL: str = os.getenv("HF_CHAT_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")

# ── Flask keep-alive ──────────────────────────────────────────────────────────
FLASK_PORT: int = int(os.getenv("PORT", "5000"))
FLASK_HOST: str = "0.0.0.0"

# ── Bot identity ──────────────────────────────────────────────────────────────
BOT_NAME: str = "FundzAiBot"
BOT_VERSION: str = "5.0.1"
BOT_TAGLINE: str = "Your Intelligent AI Assistant"
BOT_DESCRIPTION: str = "An AI Assistant developed by Fundz Company Ltd."

# ── AI defaults ───────────────────────────────────────────────────────────────
DEFAULT_AI_MODEL: str = OPENROUTER_MODEL
DEFAULT_IMAGE_MODEL: str = "stabilityai/stable-diffusion-xl-base-1.0"
MAX_CONTEXT_MESSAGES: int = 20
AI_TIMEOUT: int = 45
IMAGE_TIMEOUT: int = 90

# ── Credit system ─────────────────────────────────────────────────────────────
FREE_DAILY_CHAT: int = 30
FREE_DAILY_IMAGE: int = 5
VIP_DAILY_CHAT: int = 500
VIP_DAILY_IMAGE: int = 50
REFERRAL_CHAT_BONUS: int = 10
REFERRAL_IMAGE_BONUS: int = 2

# ── VIP Plans (Telegram Stars pricing) ────────────────────────────────────────
VIP_PLANS: dict = {
    "basic": {
        "stars": 250,
        "chat_limit": 500,
        "image_limit": 50,
        "label": "⭐ Basic VIP",
    },
    "pro": {
        "stars": 500,
        "chat_limit": 2000,
        "image_limit": 100,
        "label": "💎 Pro VIP",
    },
    "elite": {
        "stars": 1000,
        "chat_limit": 999999,
        "image_limit": 200,
        "label": "🚀 Elite VIP",
    },
}

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_MESSAGES: int = 5
RATE_LIMIT_WINDOW: int = 60

# ── Queue ─────────────────────────────────────────────────────────────────────
MAX_QUEUE_SIZE: int = 50
QUEUE_TIMEOUT: int = 120

# ── Headquarters integration ──────────────────────────────────────────────────
# FundzAiBot reports all significant events to Fundz Company Headquarters.
# Headquarters governs. FundzAiBot never makes executive decisions.
HQ_API_URL: str = os.getenv("HQ_API_URL", "").rstrip("/")
HQ_API_KEY: str = os.getenv("HQ_API_KEY", "")
HQ_SYNC_ENABLED: bool = bool(HQ_API_URL and HQ_API_KEY)
# Retry policy for offline sync
HQ_SYNC_MAX_RETRIES: int = int(os.getenv("HQ_SYNC_MAX_RETRIES", "10"))
HQ_SYNC_RETRY_INTERVAL: int = int(os.getenv("HQ_SYNC_RETRY_INTERVAL", "60"))  # seconds

# ── Referral links (sourced from HQ Product Registry) ─────────────────────────
# FundzAiBot never hardcodes referral links.
# Official links are fetched from HQ and cached here at runtime.
# Default values are placeholders — HQ overrides these on startup.
REFERRAL_LINK: str = os.getenv("REFERRAL_LINK", "")  # set by HQ registry or env
BOT_DEEP_LINK: str = os.getenv("BOT_DEEP_LINK", "")

# ── Telegram community (optional) ─────────────────────────────────────────────
TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_CHANNEL_URL: str = os.getenv("TELEGRAM_CHANNEL_URL", "")
TELEGRAM_CHANNEL_NAME: str = os.getenv("TELEGRAM_CHANNEL_NAME", "Fundz Channel")
TELEGRAM_GROUP_ID: str = os.getenv("TELEGRAM_GROUP_ID", "")
TELEGRAM_GROUP_URL: str = os.getenv("TELEGRAM_GROUP_URL", "")
TELEGRAM_GROUP_NAME: str = os.getenv("TELEGRAM_GROUP_NAME", "Fundz Community")
BOT_WEB_URL: str = os.getenv("BOT_WEB_URL", "")
TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "FundzAiBot")

# ── Onboarding ────────────────────────────────────────────────────────────────
ONBOARDING_REQUIRED: bool = os.getenv("ONBOARDING_REQUIRED", "false").lower() == "true"
ONBOARDING_CHANNEL_REWARD_CHAT: int = int(os.getenv("ONBOARDING_CHANNEL_REWARD_CHAT", "5"))
ONBOARDING_CHANNEL_REWARD_IMAGE: int = int(os.getenv("ONBOARDING_CHANNEL_REWARD_IMAGE", "1"))
ONBOARDING_GROUP_REWARD_CHAT: int = int(os.getenv("ONBOARDING_GROUP_REWARD_CHAT", "5"))
ONBOARDING_GROUP_REWARD_IMAGE: int = int(os.getenv("ONBOARDING_GROUP_REWARD_IMAGE", "1"))

# ── Railway detection ─────────────────────────────────────────────────────────
IS_RAILWAY: bool = (
    os.getenv("RAILWAY_ENVIRONMENT") is not None
    or os.getenv("RAILWAY_SERVICE_ID") is not None
    or os.getenv("IS_RAILWAY", "").lower() == "true"
)

# Legacy compat shim — kept for any remaining references
ALLOW_POLLING: bool = IS_RAILWAY

# ── Web search ────────────────────────────────────────────────────────────────
WEB_SEARCH_ENABLED: bool = os.getenv("WEB_SEARCH_ENABLED", "true").lower() == "true"
WEB_SEARCH_MAX_RESULTS: int = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "3"))

# ── Voice transcription ────────────────────────────────────────────────────────
VOICE_ENABLED: bool = os.getenv("VOICE_ENABLED", "true").lower() == "true"

# ── Session secret ─────────────────────────────────────────────────────────────
SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change-me-in-railway")

# ── Runtime feature flags ─────────────────────────────────────────────────────
FEATURE_FLAGS: dict[str, bool] = {
    "chat_enabled":       True,
    "image_enabled":      True,
    "new_users_enabled":  True,
    "maintenance_mode":   False,
    "web_search_enabled": WEB_SEARCH_ENABLED,
    "voice_enabled":      VOICE_ENABLED,
}

# ── GitHub repository (for metadata) ─────────────────────────────────────────
GITHUB_REPO: str = "https://github.com/dervishjhay1/FundAiBot"


def validate_config() -> list[str]:
    """Return a list of missing/invalid critical env vars."""
    missing: list[str] = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not ADMIN_USER_ID:
        missing.append("ADMIN_USER_ID")
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_KEY:
        missing.append("SUPABASE_SERVICE_KEY")
    if not any([OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY]):
        missing.append("at least one AI key (OPENROUTER_API_KEY / GEMINI_API_KEY / HUGGINGFACE_API_KEY)")
    return missing


def require_config() -> None:
    """Raise EnvironmentError if critical variables are missing or invalid."""
    missing = validate_config()
    if missing:
        print(f"[FundzAiBot] FATAL — Missing/invalid environment variables:\n  " + "\n  ".join(missing), file=sys.stderr)
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")


# ── Multi-admin system ────────────────────────────────────────────────────────
# Owner has full access. Secondary admins are persisted in Supabase.
SECONDARY_ADMINS: set[int] = set()
OWNER_USER_ID: int = ADMIN_USER_ID  # alias


def is_owner(user_id: int) -> bool:
    return bool(ADMIN_USER_ID) and int(user_id) == int(ADMIN_USER_ID)


def is_admin(user_id: int) -> bool:
    uid = int(user_id)
    return (bool(ADMIN_USER_ID) and uid == int(ADMIN_USER_ID)) or uid in SECONDARY_ADMINS
