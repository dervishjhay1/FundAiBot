# FundzAiBot — Deployment Report

**Generated:** 2026-06-13  
**Version:** 2.5.1  
**Target Platform:** Railway (sole deployment environment)

---

## ✅ Railway Deployment Readiness

| Check | Status | Notes |
|---|---|---|
| `railway.json` present | ✅ | Build: NIXPACKS, start: `python main.py` |
| `nixpacks.toml` present | ✅ | Python 3.11, pip install from `requirements.txt` |
| Health check endpoint | ✅ | `GET /health` → always 200 while process alive |
| Readiness endpoint | ✅ | `GET /ready` → 200 after full bot init, 503 before |
| Single replica | ✅ | `numReplicas: 1` prevents duplicate polling |
| Restart policy | ✅ | `ON_FAILURE`, max 5 retries |
| Health check timeout | ✅ | 30s — sufficient for startup |
| Python version | ✅ | 3.11 (supports all type hints used) |
| PORT env var | ✅ | Read from `os.getenv("PORT", "5000")` |
| Polling guard | ✅ | Only starts when `RAILWAY_ENVIRONMENT` / `RAILWAY_SERVICE_NAME` / `RAILWAY_PROJECT_ID` / `RAILWAY_SERVICE_ID` detected |

---

## Required Environment Variables (set in Railway)

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ Required | From @BotFather |
| `ADMIN_USER_ID` | ✅ Required | Your Telegram numeric user ID |
| `SUPABASE_URL` | ✅ Required | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | ✅ Required | `service_role` key from Supabase |
| `OPENROUTER_API_KEY` | ⚠️ At least one AI key required | Primary AI provider |
| `GEMINI_API_KEY` | ⚠️ At least one AI key required | Fallback AI provider |
| `HUGGINGFACE_API_KEY` | Optional | Fallback AI + image generation |

---

## Optional Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_MODEL` | `google/gemma-3-27b-it:free` | OpenRouter model ID |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model |
| `HF_CHAT_MODEL` | `mistralai/Mistral-7B-Instruct-v0.2` | HuggingFace chat model |
| `TELEGRAM_CHANNEL_ID` | _(empty)_ | Channel ID for membership/announcements |
| `TELEGRAM_CHANNEL_URL` | `https://t.me/FundzAiChannel` | Channel invite link |
| `TELEGRAM_CHANNEL_NAME` | `FundzAi Channel` | Display name in buttons |
| `TELEGRAM_GROUP_ID` | _(empty)_ | Group ID for membership/announcements |
| `TELEGRAM_GROUP_URL` | `https://t.me/FundzAiGroup` | Group invite link |
| `TELEGRAM_GROUP_NAME` | `FundzAi Community` | Display name in buttons |
| `ONBOARDING_REQUIRED` | `false` | Force users to join before using bot |
| `ONBOARDING_CHANNEL_REWARD_CHAT` | `5` | Credits for joining channel |
| `ONBOARDING_CHANNEL_REWARD_IMAGE` | `1` | Image credits for joining channel |
| `ONBOARDING_GROUP_REWARD_CHAT` | `5` | Credits for joining group |
| `ONBOARDING_GROUP_REWARD_IMAGE` | `1` | Image credits for joining group |
| `BOT_WEB_URL` | _(empty)_ | Railway public URL for Web App buttons |

---

## Supabase Schema

Run the following SQL files **once** in Supabase SQL Editor before deploying:

1. `supabase_schema.sql` — core tables: `users`, `user_credits`, `conversations`, `image_history`, `referrals`, `error_logs`
2. `supabase_admin_schema.sql` — multi-admin support: `admin_accounts` / `admins`
3. `supabase_announcements_schema.sql` — announcement system: `announcements`
4. `supabase_language_schema.sql` — language preferences
5. `supabase_onboarding_schema.sql` — onboarding + community join tracking

The bot logs a clear warning for any missing table at startup (non-fatal — bot continues).

---

## Deployment Workflow

```
1. Edit code in Replit (or locally)
2. git add -A && git commit -m "your message"
3. git push origin main           (or use push_to_github.sh)
4. Railway auto-deploys from main branch
5. Check Railway logs for startup banner:
   ======================================================================
     FundzAiBot  v2.5.1
     Environment : 🚂 RAILWAY (production)
     Polling     : ✅ YES — Telegram polling active
   ======================================================================
```

---

## Bug Fixes Applied (v2.5.1)

| # | File | Bug | Severity | Fix |
|---|---|---|---|---|
| 1 | `config/settings.py` | `OWNER_USER_ID` missing — `ImportError` on startup | 🔴 Critical | Added `OWNER_USER_ID = ADMIN_USER_ID` alias |
| 2 | `services/audit_service.py` | `BOT_TOKEN` import — should be `TELEGRAM_BOT_TOKEN` | 🔴 Critical | Renamed to `TELEGRAM_BOT_TOKEN` |
| 3 | `handlers/announcements.py` | `send_sticky_announcement` missing — `/start` breaks when announcement active | 🔴 Critical | Added function |
| 4 | `.env.example` | `ALLOW_POLLING=true` documented but permanently removed | 🟡 Warning | Updated documentation |
| 5 | `.env.example` | Default `OPENROUTER_MODEL` was `openai/gpt-3.5-turbo` (causes 404) | 🟡 Warning | Changed to `google/gemma-3-27b-it:free` |

---

## Remaining Notes

- **`fundzaibot/` subdirectory**: Contains an older copy of the bot source. It is not referenced by `main.py` and does not affect runtime. It can safely be deleted to reduce repo clutter, but has been left intact to preserve history.
- **`push_to_github.py` / `push_to_github.sh`**: Helper scripts for syncing from Replit to GitHub. Railway does not execute these — they are dev tooling only.
- **`data/` and `logs/`**: Created at runtime by `main.py` (`os.makedirs`). Railway's filesystem is ephemeral; logs are printed to stdout (captured by Railway log aggregator). The `data/` directory is empty on Railway — all persistent state is in Supabase.

---

## ✅ Deployment Status: READY
