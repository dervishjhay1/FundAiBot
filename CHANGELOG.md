# FundzAiBot — Changelog

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
