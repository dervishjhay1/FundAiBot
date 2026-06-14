# FundzAiBot — Project Structure

**Version:** 2.5.1  
**Language:** Python 3.11  
**Framework:** python-telegram-bot 21.6  
**Database:** Supabase (PostgreSQL via REST API)  
**Deployment:** Railway (sole environment)

---

## Directory Layout

```
FundAiBot/
│
├── main.py                        # Entry point — builds PTB Application, registers all handlers
├── requirements.txt               # Python dependencies
├── railway.json                   # Railway deploy config (start command, health check, replicas)
├── nixpacks.toml                  # Railway build config (Python 3.11, pip install)
├── .env.example                   # Template for required environment variables
│
├── config/
│   └── settings.py                # ⭐ Single source of truth for ALL env vars and constants
│                                  #    Exports: TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, OWNER_USER_ID,
│                                  #    SUPABASE_*, AI keys/models, VIP_PLANS, feature flags,
│                                  #    IS_RAILWAY, ALLOW_POLLING, is_admin(), is_owner()
│
├── handlers/                      # Telegram update handlers (one file per feature domain)
│   ├── __init__.py
│   ├── admin.py                   # /admin, /admin_*, broadcast, bot settings, feature flags
│   ├── ai_commands.py             # /ask, /code, /summarize, /translate, /analyze, /model
│   ├── announcements.py           # /pin, /unpin, /announce_*, send_sticky_announcement()
│   ├── audit.py                   # /testaudit, /status — enterprise diagnostic center
│   ├── callbacks.py               # Master inline-keyboard callback dispatcher
│   ├── chat.py                    # Free-text AI chat (/chat, /clear)
│   ├── extras.py                  # /feedback, /leaderboard, /streak
│   ├── group.py                   # Group: welcome, /ai command, @mention, anti-spam
│   ├── help.py                    # /help, /about
│   ├── image.py                   # /image — AI image generation flow
│   ├── language.py                # /language — multi-language selection
│   ├── membership.py              # Force-join membership verification + caching
│   ├── onboarding.py              # Onboarding flow, community join callbacks
│   ├── payment.py                 # /subscribe, Telegram Stars invoice, VIP activation
│   ├── profile.py                 # /profile, /stats, /referral, /history
│   ├── retouch.py                 # Photo retouching — enhance/beautify/upscale/artistic/brighten
│   ├── start.py                   # /start — registration, referral deep-links, onboarding gate
│   └── style.py                   # /style — AI personality selector
│
├── services/                      # Business logic (synchronous; called via run_in_executor)
│   ├── __init__.py
│   ├── admin_manager.py           # Multi-admin CRUD with Supabase + 60s in-memory cache
│   ├── ai_service.py              # Multi-provider AI: OpenRouter → Gemini → HuggingFace
│   ├── announcements.py           # Announcement CRUD (separate from handlers/announcements.py)
│   ├── audit_service.py           # Full diagnostic checks for /testaudit
│   ├── database.py                # ⭐ All Supabase REST operations (users, credits, convos, etc.)
│   ├── image_service.py           # Image generation: Pollinations.ai → HuggingFace
│   ├── keepalive.py               # Flask keep-alive server + /health /ready /status endpoints
│   ├── language.py                # Translation strings, language detection, VIP language gate
│   ├── onboarding.py              # Onboarding DB ops (channel/group join tracking, rewards)
│   ├── queue_manager.py           # Async task queue — prevents concurrent AI overload
│   ├── retouch_service.py         # Image retouching logic
│   └── vip_scheduler.py           # Background daemon: VIP expiry check + downgrade + notify
│
├── utils/                         # Shared utilities
│   ├── __init__.py
│   ├── admin_guard.py             # @admin_only, @owner_only decorators
│   ├── helpers.py                 # chunk_text, sanitise_prompt, time_ago, format_number, etc.
│   ├── keyboards.py               # All InlineKeyboardMarkup builders
│   ├── logger.py                  # Centralised logging (console on Railway, file in dev)
│   └── rate_limiter.py            # Sliding-window rate limiter (in-memory)
│
├── locales/                       # Translation JSON files
│   ├── en.json, es.json, fr.json  # Free tier languages
│   └── de.json, pt.json, ar.json, ru.json, tr.json, hi.json, zh.json, yo.json  # VIP languages
│
├── supabase_schema.sql            # Core tables: users, credits, conversations, image_history, referrals, error_logs
├── supabase_admin_schema.sql      # Multi-admin: admins / admin_accounts table
├── supabase_announcements_schema.sql  # Announcements table
├── supabase_language_schema.sql   # Language preference storage
├── supabase_onboarding_schema.sql # Onboarding state tracking
│
├── push_to_github.sh              # Dev helper: git commit + push to GitHub (Replit → Railway)
├── push_to_github.py              # Dev helper: GitHub REST API pusher (no git required)
├── test_services.py               # Basic service sanity checks
│
├── CHANGELOG.md                   # Version history and bug fixes
├── DEPLOYMENT_REPORT.md           # Railway deployment readiness + env var reference
└── PROJECT_STRUCTURE.md           # This file
```

> **Note:** The `fundzaibot/` subdirectory is a legacy copy of the bot source from an earlier
> version. It is **not imported or used** by `main.py` — all active code lives at the root
> level. It can be deleted without affecting the running bot.

---

## Architecture Overview

```
Telegram API
    │
    ▼
python-telegram-bot (PTB 21.6)
    │  ApplicationBuilder → polling (Railway only)
    │
    ├── CommandHandlers ──→ handlers/*.py
    │                         (each handler: auth check → DB → AI/image → reply)
    │
    ├── CallbackQueryHandler ──→ handlers/callbacks.py
    │                              (master dispatcher for all inline buttons)
    │
    ├── MessageHandlers ──→ chat_handler / photo_handler / smart_text_handler
    │
    └── Error Handler ──→ tiered: suppress harmless / warn transient / log+notify real errors
    
Services layer (synchronous, called via asyncio.run_in_executor):
    ├── services/database.py     ←→  Supabase REST API (PostgreSQL)
    ├── services/ai_service.py   ←→  OpenRouter / Gemini / HuggingFace
    ├── services/image_service.py ←→ Pollinations.ai / HuggingFace
    └── services/queue_manager.py    (in-process async queue, max 5 concurrent tasks)

Background threads (daemon):
    ├── services/keepalive.py    — Flask on PORT (Railway health checks)
    └── services/vip_scheduler.py — VIP expiry check every 60 minutes
```

---

## Key Design Decisions

1. **Railway-only polling guard** — `IS_RAILWAY` is derived from Railway's auto-injected
   env vars. Any other environment (Replit, local, CI) only starts the Flask keep-alive,
   never Telegram polling. Prevents 409 Conflict errors from duplicate instances.

2. **Synchronous services + run_in_executor** — All Supabase and AI calls use `requests`
   (synchronous). Handlers call them via `asyncio.get_running_loop().run_in_executor(None, fn)`.
   This keeps the PTB event loop unblocked while IO runs in a thread pool.

3. **Multi-provider AI fallback** — OpenRouter → Gemini → HuggingFace. Each provider
   fails gracefully (4xx is not retried; 5xx retried once) before falling through.

4. **Credit system** — Free users get 30 chat + 5 image per day. VIP tiers extend limits.
   Referral bonuses and onboarding rewards add to a separate `bonus_chat`/`bonus_image` pool.

5. **Single `config/settings.py`** — All env vars loaded once at import time. All modules
   import from `config.settings`. No scattered `os.getenv()` calls in handlers.
