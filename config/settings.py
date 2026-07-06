"""
FundAiBot — Central configuration.
All environment variables and constants live here.
Every other module imports from this file.

DEPLOYMENT POLICY:
  Telegram polling starts when IS_RAILWAY is True (Railway auto-injects its env vars)
  OR when ALLOW_POLLING=true is explicitly set in environment variables.

  ALLOW_POLLING=true is the safe override for Replit testing. It is intentional —
  use it only when Railway is NOT also running the same token, or you will get
  Telegram 409 Conflict errors (two instances polling the same bot).
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
FUNDZMARKET_BOT_TOKEN: str = os.getenv("FUNDZMARKET_BOT_TOKEN", "")  # FundzMarket Telegram bot token — for sending user notifications
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
# OpenAI: gpt-4o-mini is the recommended default (cheap, fast, capable).
# Override with OPENAI_MODEL env var in Railway.
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# OpenRouter: free models — no credits required. Override with OPENROUTER_MODEL.
# WARNING: "openai/gpt-3.5-turbo" causes HTTP 404 on OpenRouter — do NOT use it.
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
HF_CHAT_MODEL: str = os.getenv("HF_CHAT_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")

# ── Flask keep-alive ──────────────────────────────────────────────────────────
FLASK_PORT: int = int(os.getenv("PORT", "5000"))
FLASK_HOST: str = "0.0.0.0"

# ── Bot identity ──────────────────────────────────────────────────────────────
BOT_NAME: str = "FundzAiBot"
BOT_VERSION: str = "5.0.0"
BOT_TAGLINE: str = "Your Intelligent AI Assistant"

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
MAX_CONCURRENT_TASKS: int = 5

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.dirname(__file__))
DATA_DIR: str = os.path.join(BASE_DIR, "data")
LOGS_DIR: str = os.path.join(BASE_DIR, "logs")

# ── Onboarding / Community ────────────────────────────────────────────────────
TELEGRAM_CHANNEL_ID: str   = os.getenv("TELEGRAM_CHANNEL_ID", "-1003695220825")
TELEGRAM_CHANNEL_URL: str  = os.getenv("TELEGRAM_CHANNEL_URL", "https://t.me/FundzAiChannel")
TELEGRAM_CHANNEL_NAME: str = os.getenv("TELEGRAM_CHANNEL_NAME", "FundzAi Channel")

TELEGRAM_GROUP_ID: str     = os.getenv("TELEGRAM_GROUP_ID", "-1004297201446")
TELEGRAM_GROUP_URL: str    = os.getenv("TELEGRAM_GROUP_URL", "https://t.me/FundzAiGroup")
TELEGRAM_GROUP_NAME: str   = os.getenv("TELEGRAM_GROUP_NAME", "FundzAi Community")

ONBOARDING_CHANNEL_REWARD_CHAT: int  = int(os.getenv("ONBOARDING_CHANNEL_REWARD_CHAT", "5"))
ONBOARDING_CHANNEL_REWARD_IMAGE: int = int(os.getenv("ONBOARDING_CHANNEL_REWARD_IMAGE", "1"))
ONBOARDING_GROUP_REWARD_CHAT: int    = int(os.getenv("ONBOARDING_GROUP_REWARD_CHAT", "5"))
ONBOARDING_GROUP_REWARD_IMAGE: int   = int(os.getenv("ONBOARDING_GROUP_REWARD_IMAGE", "1"))
ONBOARDING_REQUIRED: bool = os.getenv("ONBOARDING_REQUIRED", "false").lower() == "true"

# ── Membership gate ───────────────────────────────────────────────────────────
# When True, ALL premium commands (not just /start) require channel+group membership.
# Set via MEMBERSHIP_GATE_ENABLED=true in Railway env vars.
MEMBERSHIP_GATE_ENABLED: bool = os.getenv("MEMBERSHIP_GATE_ENABLED", "false").lower() == "true"

# ── Web App / Mini-App ────────────────────────────────────────────────────────
# Public HTTPS base URL where Flask is accessible (e.g. Railway service URL).
# Leave empty in Replit — mini-app WebApp button is only shown when set.
# Example: https://fundzaibot.up.railway.app
BOT_WEB_URL: str = os.getenv("BOT_WEB_URL", "").rstrip("/")

# ── Deployment environment detection ─────────────────────────────────────────
#
# Railway automatically injects these environment variables into every service.
# None of them will be present in Replit, local dev, or any other platform.
#
IS_RAILWAY: bool = bool(
    os.getenv("RAILWAY_ENVIRONMENT") or      # e.g. "production"
    os.getenv("RAILWAY_SERVICE_NAME") or     # e.g. "FundzAiBot"
    os.getenv("RAILWAY_PROJECT_ID") or       # UUID
    os.getenv("RAILWAY_SERVICE_ID")          # UUID
)

# ALLOW_POLLING: True on Railway automatically.
# Set ALLOW_POLLING=true in env vars to enable polling in Replit/local testing.
# WARNING: Never set this while Railway is also polling the same token —
# two simultaneous pollers cause Telegram 409 Conflict errors.
ALLOW_POLLING: bool = IS_RAILWAY or os.getenv("ALLOW_POLLING", "").lower() == "true"

# ── Backward-compatibility alias ──────────────────────────────────────────────
# services/admin_manager.py and any future modules may reference OWNER_USER_ID.
# It is identical to ADMIN_USER_ID — the permanent super-admin.
OWNER_USER_ID: int = ADMIN_USER_ID

# ── Validation ────────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    """Return a list of missing or invalid critical environment variables."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not ADMIN_USER_ID or ADMIN_USER_ID == 0:
        missing.append("ADMIN_USER_ID (must be a non-zero Telegram user ID)")
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
SECONDARY_ADMINS: set[int] = set()


def is_owner(user_id: int) -> bool:
    return bool(ADMIN_USER_ID) and int(user_id) == int(ADMIN_USER_ID)


def is_admin(user_id: int) -> bool:
    uid = int(user_id)
    return (bool(ADMIN_USER_ID) and uid == int(ADMIN_USER_ID)) or uid in SECONDARY_ADMINS


# ── Web search ────────────────────────────────────────────────────────────────
# DuckDuckGo — no API key required. Set WEB_SEARCH_ENABLED=false to disable.
WEB_SEARCH_ENABLED:     bool = os.getenv("WEB_SEARCH_ENABLED", "true").lower() == "true"
WEB_SEARCH_MAX_RESULTS: int  = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "3"))

# ── Voice transcription ────────────────────────────────────────────────────────
# Requires GEMINI_API_KEY. Set VOICE_ENABLED=false to disable.
VOICE_ENABLED: bool = os.getenv("VOICE_ENABLED", "true").lower() == "true"

# ── Session secret (Flask sessions / future use) ──────────────────────────────
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
