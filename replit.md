# FundzAiBot

AI-powered Telegram bot — multi-model chat, image generation, VIP subscriptions, referral rewards, and a full autonomous operations suite (TestAudit) that manages the official channel, group community, and CEO reporting on behalf of the Fundz company.

---

## Run & Operate

```bash
# Run locally (Flask keep-alive only — Telegram polling is Railway-only)
python main.py

# Trigger production deployment
git push origin main   # → Railway auto-deploys on push
```

> **Hard rule**: Telegram polling only starts on Railway (`IS_RAILWAY=true`).
> Replit, local, CI, Docker — all are blocked by design to prevent Telegram 409 Conflict errors.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Telegram SDK | python-telegram-bot 21.6 (ext, asyncio) |
| Database | Supabase (PostgreSQL via REST API — no ORM) |
| AI providers | OpenRouter (chat), Google Gemini (vision), HuggingFace (image) |
| Keep-alive | Flask 3.0 + Gunicorn |
| Deployment | Railway (polls), Replit (edits), GitHub (bridge) |

---

## Where Things Live

```
fundzaibot/                 ← project root
├── main.py                 ← entry point, handler registration, build_app()
├── config/settings.py      ← ALL env vars and constants (source of truth)
├── handlers/               ← Telegram command & event handlers (thin layer)
│   ├── announcements.py    ← /pin /unpin /announce_* — sticky announcement system
│   ├── audit.py            ← /testaudit /status — enterprise audit center
│   ├── callbacks.py        ← all InlineKeyboardButton callback routing
│   ├── chat.py             ← /chat /clear — AI conversation with memory
│   ├── group.py            ← all group behaviour (TestAudit persona only)
│   ├── admin.py            ← /admin dashboard + all admin_* commands
│   └── ...                 ← start, help, image, profile, payment, etc.
├── services/               ← business logic, background daemons, integrations
│   ├── database.py         ← ALL Supabase REST calls (source of truth for DB)
│   ├── testaudit_core.py   ← background health monitor daemon (10 min loop)
│   ├── channel_publisher.py← asyncio channel posting (15-25 posts/day, 06-23 UTC)
│   ├── channel_manager.py  ← channel content engine (used by publisher)
│   ├── community_manager.py← group engagement AI (TestAudit persona)
│   ├── ceo_office.py       ← CEO private command center + intent classifier
│   ├── autonomous_mode.py  ← AOM daemon — CEO inactive 7d → auto-operations
│   ├── executive_assistant.py← morning/evening briefs for CEO
│   ├── executive_chat.py   ← CEO Office conversation engine
│   ├── department_registry.py← plugin framework for AI departments
│   ├── product_registry.py ← Fundz product catalog (multi-product aware)
│   ├── ai_service.py       ← AI provider routing (OpenRouter/Gemini/HF)
│   ├── image_service.py    ← HuggingFace SDXL image generation
│   ├── onboarding.py       ← user onboarding flow service
│   ├── keepalive.py        ← Flask /health endpoint
│   └── ...
├── utils/                  ← shared helpers
│   ├── logger.py           ← structured logging (always use req.log or logger)
│   ├── admin_guard.py      ← @admin_only decorator
│   ├── keyboards.py        ← InlineKeyboardMarkup builders
│   ├── helpers.py          ← time_ago(), format utilities
│   └── rate_limiter.py     ← per-user rate limiting
├── supabase_*.sql          ← schema SQL files (run in Supabase SQL Editor)
├── railway.json            ← Railway deploy config (start command, health check)
├── nixpacks.toml           ← Nixpacks build config (python311, no install override)
├── requirements.txt        ← Python dependencies
└── test_services.py        ← live API connectivity test script
```

---

## Architecture Decisions

- **Railway-only polling**: `IS_RAILWAY` flag is the sole gate. Non-Railway runs Flask keep-alive only. Hard boundary — no override. Prevents 409 Conflict on multi-instance setups.
- **Supabase via raw REST API**: No Supabase Python client, no ORM. All DB calls go through `services/database.py` using `requests`. Keeps dependencies minimal and makes the REST contract explicit.
- **Main bot is SILENT in groups**: Only TestAudit (via `community_manager` persona) speaks in groups. No `/ai` command in groups. Handler groups enforce this: group=1 (mention), group=2 (spam filter), group=3 (smart community).
- **Single config file**: `config/settings.py` is the ONLY place env vars are read. All other modules import constants from there — never call `os.getenv()` directly in handlers or services.
- **Background services via asyncio tasks**: Channel publisher and group engagement scheduler are launched as `asyncio.create_task()` in `post_init`. TestAudit core and AOM run as `daemon=True` threads (threading-based, not asyncio).

---

## Product — User-Facing Capabilities

- **AI Chat** — multi-turn conversation with memory, 8 personality styles, model switching (GPT-4o / Gemini / Claude / Llama / Mistral / Gemma)
- **AI Image Generation** — SDXL via HuggingFace; prompt-based and AI-retouching of uploaded photos
- **Tools** — weather, crypto prices, news, QR code generation, Wikipedia lookup
- **VIP Subscriptions** — Telegram Stars payment; Basic/Pro/Elite tiers with daily credit limits
- **Referral System** — per-user referral links, credit rewards, leaderboard
- **TestAudit Operations Suite** — autonomous channel posting (15-25/day), group community management, CEO briefings, autonomous operations mode on CEO absence, enterprise health audit (/testaudit command)
- **Announcements** — sticky announcement cards with smart show logic (seen tracking, priority override, scheduling)

---

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ADMIN_USER_ID` | Telegram numeric user ID of the CEO/admin |
| `SUPABASE_URL` | Supabase project REST URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `OPENROUTER_API_KEY` | OpenRouter API key for chat AI |
| `GEMINI_API_KEY` | Google Gemini API key (vision/fallback) |
| `HUGGINGFACE_API_KEY` | HuggingFace API key (image generation) |
| `TELEGRAM_CHANNEL_ID` | Channel ID for autonomous publishing |
| `TELEGRAM_GROUP_ID` | Group chat ID for community management |

Optional:
- `OPENROUTER_MODEL` — override default model (default: `google/gemma-3-27b-it:free`)
- `GEMINI_MODEL` — override Gemini model (default: `gemini-1.5-flash`)

---

## Supabase Schema Setup

Run these SQL files in order in Supabase SQL Editor:

1. `supabase_schema.sql` — core tables (users, messages, images, error_log)
2. `supabase_admin_schema.sql` — admin/secondary admin tables
3. `supabase_announcements_schema.sql` — announcements table
4. `supabase_language_schema.sql` — i18n/language preferences
5. `supabase_onboarding_schema.sql` — onboarding flow table
6. `supabase_ceo_office_schema.sql` — CEO memory + AOM log tables
7. `supabase_products_schema.sql` — Fundz product registry
8. `supabase_testaudit_schema.sql` — TestAudit health + backlog tables

---

## Gotchas

- **Never `os.getenv()` outside `config/settings.py`** — import the constant instead.
- **`python3` is not available** on Nixpacks — Railway uses `python` (Python 3.11 from `nixpacks.toml`).
- **Do NOT add a `[start]` phase in `nixpacks.toml`** — it conflicts with `railway.json`'s `startCommand`.
- **Orval / codegen is NOT used** — this is a pure Python project, no TypeScript codegen.
- **`push_to_github.py` / `push_to_github.sh`** — helper scripts for syncing; use `git push origin main` instead.
- **`data/` and `logs/` dirs** — created at startup by `main()`. Gitignored. Do not commit logs.
- **Two-draft channel posts** — channel publisher generates 2 AI drafts and picks the higher-quality one before posting. Quality threshold: 50/100.

---

## User Preferences

- Deployment target is Railway only; Replit is the code editor.
- Keep the Railway-only polling guard — do not add any override mechanism.
- All DB access must go through `services/database.py` (never raw Supabase client elsewhere).
- Never use `console.log` (N/A for Python) — use structured logger from `utils/logger.py`.
