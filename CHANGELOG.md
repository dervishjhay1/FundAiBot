# FundzAiBot — Changelog

## [3.0.0] — 2026-06-30  Phase 2 — Enterprise Operating System (EOS)

### Critical Bug Fix

#### `handlers/ceo_office.py` (NEW FILE — was missing)
- **Created the missing `handlers/ceo_office.py` handler.** `main.py` line 291 imported
  `from handlers.ceo_office import handle_ceo_message` but this file did not exist.
  This caused a fatal `ImportError` at Railway startup, meaning the bot never ran.
  Fix: Created the full handler with session management, exit commands, and async routing
  to `services/ceo_office.chat_with_ceo_office()`.

### New: CEO Office (Phase 2 EOS)

#### `handlers/ceo_office.py`
- **`handle_ceo_message(update, context) → bool`** — called by `_smart_text_handler`
  for every private admin text message. Returns `True` if handled (CEO Office session
  active), `False` to fall through to regular AI chat. Admin-only.
- **`start_ceo_session(user_id)` / `end_ceo_session(user_id)`** — activate/deactivate
  the CEO Office session. Session TTL: 30 min idle (mirrors service layer).
- **`ceo_office_command_handler`** — new `/ceo_office` command opens the CEO Office with
  a welcome message and inline Exit / Dashboard buttons.
- Exit words (`exit`, `quit`, `bye`, `/exit`, etc.) close the session gracefully.

#### `handlers/callbacks.py`
- **`ceo:open`** callback — opens CEO Office session from any inline button.
- **`ceo:exit`** callback — closes CEO Office session, sends confirmation, removes button.
- Both callbacks are admin-only with `show_alert` denial for non-admins.

### New: Background Services Wired (Phase 2 EOS)

#### `main.py` — `post_init()`
All four Phase 2 background services now start automatically when Railway boots the bot:
- **CEO Office** — `ceo_office.initialize()` restores persistent memory + conversation
  history from Supabase on every Railway restart.
- **TestAudit Intelligence Core** — `start_testaudit_core()` launches the 10-minute
  health monitoring daemon thread (was defined but never started).
- **Executive Assistant** — `start_executive_assistant()` launches the daily briefing
  scheduler (morning/evening briefs, weekly/monthly reports, critical alerts).
- **Autonomous Operations Mode** — `start_autonomous_mode_monitor()` launches the CEO
  inactivity monitor (7-day threshold → AOM activates). Includes Return Report on
  CEO comeback.
- All four wrapped in individual try/except so a single service failure can never
  block bot startup.

### New: `/ceo_office` Command

#### `main.py` — `build_app()`
- Registered `CommandHandler("ceo_office", ceo_office_command_handler)`.
- Added `BotCommand("ceo_office", "🏢 Open CEO Office (TestAudit)")` to admin-scoped
  command list (only visible in admin's private chat).

### Deployment

- **Railway ONLY.** `IS_RAILWAY` guard is permanent and unchanged.
  Replit runs Flask keep-alive only — no Telegram polling.
- Push to GitHub → Railway auto-deploys.

---

## [2.5.1] — 2026-06-13

### Bug Fixes (Critical)

#### `config/settings.py`
- **Added `OWNER_USER_ID` alias** → `services/admin_manager.py` imports `OWNER_USER_ID`
  but the settings module only exported `ADMIN_USER_ID`. This caused an `ImportError`
  at bot startup, preventing the bot from ever running.  
  Fix: Added `OWNER_USER_ID: int = ADMIN_USER_ID` backward-compatibility alias.

#### `services/audit_service.py`
- **Fixed `BOT_TOKEN` → `TELEGRAM_BOT_TOKEN`** in the Telegram API reachability check
  (line ~910). The import `from config.settings import BOT_TOKEN` raised `ImportError`
  because the setting is named `TELEGRAM_BOT_TOKEN`. The `/testaudit` command would
  crash whenever it ran the bot-core section.  
  Fix: Updated import and all three references on lines 910–924.

#### `handlers/announcements.py`
- **Added missing `send_sticky_announcement` function**.  
  `handlers/start.py` lazy-imports `send_sticky_announcement` from `handlers.announcements`
  on every `/start` call when an active announcement exists. The function was only present
  in the legacy `fundzaibot/` subdirectory copy, not in the root-level module that `main.py`
  uses. This caused an `ImportError` for every user whose `/start` triggered an announcement
  display.  
  Fix: Added the complete `send_sticky_announcement` async function to
  `handlers/announcements.py`.

### Documentation / Config Fixes

#### `.env.example`
- Removed misleading `ALLOW_POLLING=true` comment. `ALLOW_POLLING` was permanently
  removed as an override mechanism in v2.5.0. The comment implied it still worked,
  which could confuse operators trying to debug a non-starting bot.
- Changed `OPENROUTER_MODEL` default from `openai/gpt-3.5-turbo` to
  `google/gemma-3-27b-it:free` to match the actual default in `config/settings.py`
  (the old value causes HTTP 404 on OpenRouter).
- Added `BOT_WEB_URL` entry with documentation.

### Added Files
- `CHANGELOG.md` — this file
- `DEPLOYMENT_REPORT.md` — Railway deployment readiness report
- `PROJECT_STRUCTURE.md` — annotated project structure

---

## [2.5.0] — Previous release

- Multi-announcement navigator (◀ Prev / Next ▶)
- Premium sticky announcement overlay (Telegram Web App)
- AI image retouching system (`/retouch`)
- Multi-admin system with Supabase persistence
- Enterprise audit center (`/testaudit`, `/status`)
- Onboarding system with channel/group rewards
- VIP expiry scheduler
- Anti-spam filter with auto-mute
- Telegram Stars payment integration
- 10-language support (free: EN/ES/FR; VIP: DE/PT/AR/RU/TR/HI/ZH/YO)
- 8 AI personality styles
- Referral system with credit rewards
- Daily streak tracker
- Leaderboard
