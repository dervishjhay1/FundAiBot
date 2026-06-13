"""
FundAiBot — Central configuration.
All environment variables and constants live here.
Every other module imports from this file.

DEPLOYMENT POLICY — RAILWAY ONLY:
  Telegram polling ONLY starts when Railway environment variables are detected.
  There is NO override mechanism. ALLOW_POLLING is permanently removed.
  Any attempt to run the bot outside Railway will start Flask keep-alive only
  and log a clear rejection. This prevents duplicate polling, 409 Conflicts,
  and accidental production incidents from dev/Replit environments.
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
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY: str = os.getenv("HUGGINGFACE_API_KEY", "")

# ── AI model names ────────────────────────────────────────────────────────────
# Default: google/gemma-3-27b-it:free — free, stable, no credits required on OpenRouter.
# Fallback chain: google/gemma-3-27b-it:free → meta-llama/llama-3.3-70b-instruct:free
#                 → mistralai/mistral-small-3.1-24b-instruct:free
# Override with OPENROUTER_MODEL env var in Railway if you want a different model.
# WARNING: "openai/gpt-3.5-turbo" causes HTTP 404 on OpenRouter — do NOT use it.
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemma-3-27b-it:free")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
HF_CHAT_MODEL: str = os.getenv("HF_CHAT_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")

# ── Flask keep-alive ──────────────────────────────────────────────────────────
FLASK_PORT: int = int(os.getenv("PORT", "5000"))
FLASK_HOST: str = "0.0.0.0"

# ── Bot identity ──────────────────────────────────────────────────────────────
BOT_NAME: str = "FundzAiBot"
BOT_VERSION: str = "2.5.0"
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
TELEGRAM_CHANNEL_ID: str   = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_CHANNEL_URL: str  = os.getenv("TELEGRAM_CHANNEL_URL", "https://t.me/FundzAiChannel")
TELEGRAM_CHANNEL_NAME: str = os.getenv("TELEGRAM_CHANNEL_NAME", "FundzAi Channel")

TELEGRAM_GROUP_ID: str     = os.getenv("TELEGRAM_GROUP_ID", "")
TELEGRAM_GROUP_URL: str    = os.getenv("TELEGRAM_GROUP_URL", "https://t.me/FundzAiGroup")
TELEGRAM_GROUP_NAME: str   = os.getenv("TELEGRAM_GROUP_NAME", "FundzAi Community")

ONBOARDING_CHANNEL_REWARD_CHAT: int  = int(os.getenv("ONBOARDING_CHANNEL_REWARD_CHAT", "5"))
ONBOARDING_CHANNEL_REWARD_IMAGE: int = int(os.getenv("ONBOARDING_CHANNEL_REWARD_IMAGE", "1"))
ONBOARDING_GROUP_REWARD_CHAT: int    = int(os.getenv("ONBOARDING_GROUP_REWARD_CHAT", "5"))
ONBOARDING_GROUP_REWARD_IMAGE: int   = int(os.getenv("ONBOARDING_GROUP_REWARD_IMAGE", "1"))
ONBOARDING_REQUIRED: bool = os.getenv("ONBOARDING_REQUIRED", "false").lower() == "true"

# ── Web App / Mini-App ────────────────────────────────────────────────────────
# Public HTTPS base URL where Flask is accessible (e.g. Railway service URL).
# Leave empty in Replit — mini-app WebApp button is only shown when set.
# Example: https://fundzaibot.up.railway.app
BOT_WEB_URL: str = os.getenv("BOT_WEB_URL", "").rstrip("/")

# ── Deployment environment detection — RAILWAY ONLY ──────────────────────────
#
# Railway automatically injects these environment variables into every service.
# None of them will be present in Replit, local dev, or any other platform.
#
# ALLOW_POLLING override has been permanently removed.
# Telegram polling starts ONLY when IS_RAILWAY is True.
# There is NO escape hatch — this is by design to prevent duplicate instances,
# Telegram 409 Conflict errors, and dropped messages.
#
# If you need to test locally, point your local environment at a TEST bot token
# and manually deploy to Railway. Do NOT attempt to bypass this guard.
#
IS_RAILWAY: bool = bool(
    os.getenv("RAILWAY_ENVIRONMENT") or      # e.g. "production"
    os.getenv("RAILWAY_SERVICE_NAME") or     # e.g. "FundzAiBot"
    os.getenv("RAILWAY_PROJECT_ID") or       # UUID
    os.getenv("RAILWAY_SERVICE_ID")          # UUID
)

# ALLOW_POLLING override is permanently disabled — Railway detection only.
# (Kept as a constant for audit/display purposes — always False outside Railway.)
ALLOW_POLLING: bool = IS_RAILWAY

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


# ── Runtime feature flags ─────────────────────────────────────────────────────
FEATURE_FLAGS: dict[str, bool] = {
    "chat_enabled":      True,
    "image_enabled":     True,
    "new_users_enabled": True,
    "maintenance_mode":  False,
}
