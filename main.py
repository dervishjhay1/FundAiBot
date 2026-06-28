"""
FundAiBot — Main entry point.

Architecture:
  Replit  →  GitHub  →  Railway (LIVE BOT)
  Replit is for code editing + GitHub sync ONLY.
  Railway is the SOLE environment where Telegram polling runs.

Deployment policy — RAILWAY ONLY:
  Polling starts ONLY when Railway environment variables are detected (IS_RAILWAY=True).
  The ALLOW_POLLING override has been permanently removed.
  Any non-Railway environment (Replit, local, CI, Docker, VPS) runs Flask keep-alive
  only and exits without starting any Telegram connection.
  This is a hard architectural boundary — do not bypass it.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL: Flask starts HERE — at module level, BEFORE every other import.
#
# Python executes ALL top-level imports before main() is ever called.
# If any import (handlers, ai_service, queue_manager…) blocks for >N seconds,
# main() is never reached and Flask never starts — healthcheck fails.
#
# By starting Flask here:
#   • keepalive.py only needs config.settings (env-var reads, zero network I/O)
#   • /health responds in < 2 seconds, regardless of what happens next
#   • Railway's 120-second healthcheckTimeout gives 118 s of startup budget
#   • start_keepalive() is idempotent — the call inside main() is a safe no-op
# ─────────────────────────────────────────────────────────────────────────────
from services.keepalive import start_keepalive as _early_keepalive, mark_ready
_early_keepalive()

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from config.settings import (
    TELEGRAM_BOT_TOKEN, BOT_NAME, BOT_VERSION,
    IS_RAILWAY, ALLOW_POLLING,
    require_config,
)
from handlers.admin import (
    admin_handler, admin_users_handler, admin_ban_handler,
    admin_unban_handler, admin_setvip_handler, admin_addcredits_handler,
    admin_broadcast_handler, admin_logs_handler, admin_stats_handler,
    admin_images_handler, admin_userinfo_handler, admin_health_handler,
    admin_resetlimit_handler,
    admin_config_handler, admin_setcredits_handler, admin_resetuser_handler,
    admin_clearlogs_handler, admin_addadmin_handler, admin_removeadmin_handler,
    admin_listadmins_handler, admin_dm_handler,
    admin_help_handler, admin_clearchat_handler,
)
from handlers.announcements import (
    pin_handler, unpin_handler, updateannouncement_handler,
    pinphoto_handler, listannouncements_handler,
    announce_channel_handler, announce_group_handler, announce_both_handler,
    pin_priority_handler, schedule_announcement_handler,
)
from handlers.ai_commands import (
    ask_handler, code_handler, summarize_handler, translate_handler,
    analyze_handler, model_handler, testbroadcast_handler,
)
from handlers.callbacks import callback_handler
from handlers.chat import chat_handler, clear_handler
from handlers.extras import feedback_handler, leaderboard_handler, streak_handler
from handlers.help import help_handler, about_handler
from handlers.image import image_command_handler, _pending, handle_image_prompt_message
from handlers.retouch import photo_handler
from handlers.voice import voice_handler
from handlers.tools import (
    weather_handler, calc_handler, qr_handler, crypto_handler,
    wiki_handler, news_handler, currency_handler, quote_handler,
)
from handlers.document import document_handler
from handlers.language import language_handler
from handlers.onboarding import admin_onboarding_handler
from handlers.audit import testaudit_handler, status_handler
from handlers.group import (
    new_member_handler, group_ai_handler, mention_handler,
    spam_filter, group_command_blocker,
)
from handlers.membership import membership_change_handler
from handlers.profile import profile_handler, referral_handler, history_handler, stats_handler
from handlers.payment import subscribe_handler, precheckout_handler, successful_payment_handler
from handlers.start import start_handler
from handlers.style import style_handler
from services.keepalive import start_keepalive, mark_ready
from services.queue_manager import queue_manager
from services.database import bootstrap_schema, load_secondary_admins
from services.vip_scheduler import start_vip_scheduler
from services.department_registry import bootstrap_departments
from utils.logger import get_logger

log = get_logger(__name__)

_SEPARATOR = "=" * 70


def _print_startup_banner() -> None:
    if IS_RAILWAY:
        env_label  = "🚂 RAILWAY (production)"
    elif ALLOW_POLLING:
        env_label  = "🧪 REPLIT / LOCAL (ALLOW_POLLING=true)"
    else:
        env_label  = "🚫 NON-RAILWAY (polling BLOCKED)"
    poll_label = "✅ YES — Telegram polling active" if ALLOW_POLLING else "❌ NO — set ALLOW_POLLING=true to enable"
    log.info(_SEPARATOR)
    log.info("  %s  v%s", BOT_NAME, BOT_VERSION)
    log.info("  Environment : %s", env_label)
    log.info("  Polling     : %s", poll_label)
    log.info(_SEPARATOR)


def _seed_default_announcement() -> None:
    from services.database import get_active_announcement, create_announcement
    from handlers.announcements import DEFAULT_ANNOUNCEMENT
    if not get_active_announcement():
        create_announcement(DEFAULT_ANNOUNCEMENT)
        log.info("Default announcement seeded into DB.")


def _bootstrap_onboarding_schema() -> None:
    from services.database import _safe_get, _headers, _url
    try:
        r = _safe_get(f"{_url('onboarding')}?limit=1", headers=_headers())
        if r.status_code == 200:
            log.info("✅ Onboarding table verified.")
        else:
            log.warning(
                "⚠️  'onboarding' table missing — run supabase_onboarding_schema.sql "
                "in Supabase SQL Editor to enable the onboarding system."
            )
    except Exception as exc:
        log.warning("Could not verify onboarding table: %s", exc)


async def post_init(application: Application) -> None:
    """Register bot commands, log startup info, and start background services after bot connects."""
    from config.settings import ADMIN_USER_ID

    bot_info = await application.bot.get_me()
    total_handlers = sum(len(h) for h in application.handlers.values())
    log.info(_SEPARATOR)
    log.info("  ✅ BOT STARTED SUCCESSFULLY")
    log.info("  Bot username : @%s (ID: %s)", bot_info.username, bot_info.id)
    log.info("  Version      : %s", BOT_VERSION)
    log.info("  Handlers     : %d registered across %d group(s)",
             total_handlers, len(application.handlers))
    log.info(_SEPARATOR)

    # ── Public commands — visible to ALL users ────────────────────────────────
    public_commands = [
        BotCommand("start",       "Open the main menu"),
        BotCommand("help",        "Full help guide"),
        BotCommand("about",       "About FundzAiBot"),
        BotCommand("chat",        "AI conversation (with memory)"),
        BotCommand("ask",         "Quick one-shot question (no memory)"),
        BotCommand("code",        "Code generation & debugging"),
        BotCommand("summarize",   "Summarize text or a replied message"),
        BotCommand("translate",   "Translate to any language"),
        BotCommand("analyze",     "Analyze a photo with Gemini Vision"),
        BotCommand("image",       "Generate an AI image"),
        BotCommand("model",       "Switch AI model: GPT-4o, Claude, Gemini…"),
        BotCommand("style",       "Change AI personality (8 modes)"),
        BotCommand("clear",       "Clear conversation memory"),
        BotCommand("language",    "Change bot language 🌍"),
        BotCommand("subscribe",   "⭐ VIP plans & Telegram Stars"),
        BotCommand("profile",     "Your profile & credits"),
        BotCommand("stats",       "Your usage statistics"),
        BotCommand("referral",    "Referral link & rewards"),
        BotCommand("history",     "Image generation history"),
        BotCommand("feedback",    "Send feedback or report a bug"),
        BotCommand("leaderboard", "Top referrers leaderboard"),
        BotCommand("streak",      "Your daily chat streak"),
        BotCommand("weather",     "🌤️ Live weather forecast"),
        BotCommand("calc",        "🧮 Smart calculator"),
        BotCommand("qr",          "📱 Generate QR code"),
        BotCommand("crypto",      "₿ Live crypto prices"),
        BotCommand("wiki",        "📖 Wikipedia lookup"),
        BotCommand("news",        "📰 Latest news headlines"),
        BotCommand("currency",    "💱 Currency converter"),
        BotCommand("quote",       "💬 Daily inspiration"),
    ]

    # ── Admin commands — visible ONLY in the admin's chat ─────────────────────
    admin_commands = public_commands + [
        BotCommand("health",               "🩺 Live health dashboard"),
        BotCommand("status",               "📊 Live bot status"),
        BotCommand("testaudit",            "🔬 Enterprise audit center"),
        BotCommand("broadcast",            "📢 Broadcast message to all users"),
        BotCommand("admin",                "👑 Admin dashboard"),
        BotCommand("admin_help",           "📖 Admin command reference (grouped)"),
        BotCommand("admin_stats",          "📊 Platform statistics"),
        BotCommand("admin_users",          "👥 User management"),
        BotCommand("admin_user",           "🔍 View user profile"),
        BotCommand("admin_ban",            "🚫 Ban a user"),
        BotCommand("admin_unban",          "✅ Unban a user"),
        BotCommand("admin_setvip",         "💎 Set VIP status"),
        BotCommand("admin_addcredits",     "➕ Add bonus credits"),
        BotCommand("admin_setcredits",     "🔢 Set credits to exact amount"),
        BotCommand("admin_resetlimit",     "⏱️ Clear rate limit"),
        BotCommand("admin_resetuser",      "🔄 Full user reset"),
        BotCommand("admin_clearchat",      "🧹 Clear user chat history"),
        BotCommand("admin_dm",             "📬 DM any user"),
        BotCommand("admin_logs",           "📋 Recent error logs"),
        BotCommand("admin_clearlogs",      "🗑️ Clear error logs"),
        BotCommand("admin_health",         "🩺 AI health check"),
        BotCommand("admin_config",         "⚙️ Full configuration view"),
        BotCommand("admin_images",         "🖼️ Recent image generations"),
        BotCommand("admin_addadmin",       "👑 Promote to admin (owner only)"),
        BotCommand("admin_removeadmin",    "🗑️ Remove admin (owner only)"),
        BotCommand("admin_listadmins",     "📋 List all admins"),
        BotCommand("admin_onboarding",     "🚀 Onboarding stats & config"),
        BotCommand("testbroadcast",        "👁️ Preview active announcement"),
        BotCommand("pin",                  "📌 Create announcement"),
        BotCommand("pin_priority",         "⚡ High-priority announcement"),
        BotCommand("schedule_announcement","🗓️ Schedule a future announcement"),
        BotCommand("unpin",                "🗑️ Remove announcement"),
        BotCommand("updateannouncement",   "✏️ Edit current announcement"),
        BotCommand("pinphoto",             "🖼️ Add/remove banner image"),
        BotCommand("listannouncements",    "📜 Announcement history"),
        BotCommand("announce_channel",     "📢 Push to channel"),
        BotCommand("announce_group",       "👥 Push to group"),
        BotCommand("announce_both",        "📣 Push to channel + group"),
    ]

    # Set public list for everyone (private chats + any chat without an explicit scope override)
    await application.bot.set_my_commands(
        public_commands,
        scope=BotCommandScopeDefault(),
    )

    # ── CRITICAL: clear ALL commands from every group and supergroup ───────────
    # BotCommandScopeAllGroupChats overrides the Default scope for groups, so
    # users never see slash-command suggestions or the command menu inside groups.
    # This makes the group feel like a human community rather than a bot interface.
    try:
        await application.bot.set_my_commands(
            [],                                 # empty — no commands in any group
            scope=BotCommandScopeAllGroupChats(),
        )
        log.info("  Group menu   : ✅ cleared for ALL groups (no command suggestions)")
    except Exception as exc:
        log.warning("Could not clear group command menu: %s", exc)

    # Set full admin list — only shows up in admin's private chat
    if ADMIN_USER_ID:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=ADMIN_USER_ID),
            )
        except Exception as exc:
            log.warning("Could not set admin-scoped commands: %s", exc)

    log.info("  Commands     : public=%d, admin=%d",
             len(public_commands), len(admin_commands))
    await queue_manager.start()

    # ── Start all AI departments (TestAudit, Executive Assistant, etc.) ────────
    try:
        bootstrap_departments()
        log.info("✅ AI departments bootstrapped successfully")
    except Exception as exc:
        log.error("Failed to bootstrap departments: %s", exc)

    mark_ready()
    log.info("Bot fully initialised — polling active.")


async def error_handler(update, context) -> None:
    """
    Global error handler — three tiers:

    1. 'Message is not modified' (BadRequest) → silently ignored.
       This is a harmless Telegram quirk when a callback button is tapped twice.

    2. Transient infrastructure errors (Bad Gateway, NetworkError, TimedOut,
       httpx.ReadError, connection resets) → logged at WARNING.
       A user-friendly retry message IS sent so users are never left in silence.

    3. Everything else → logged as ERROR, written to Supabase error log,
       user-friendly error message sent.
    """
    from telegram.error import BadRequest, NetworkError, TimedOut
    from services.database import log_error

    exc = context.error
    err = str(exc)

    # ── Tier 1: harmless "Message is not modified" ────────────────────────────
    if isinstance(exc, BadRequest) and "message is not modified" in err.lower():
        uid = update.effective_user.id if update and update.effective_user else "?"
        log.debug("Suppressed 'Message is not modified' for user %s", uid)
        return

    # ── Tier 2: transient infrastructure / network errors ────────────────────
    _transient_strings = (
        "bad gateway",
        "httpx.readerror",
        "read error",
        "connection reset by peer",
        "connection aborted",
        "remotedisconnected",
        "connection refused",
    )
    is_transient = (
        isinstance(exc, (NetworkError, TimedOut))
        or any(s in err.lower() for s in _transient_strings)
    )
    if is_transient:
        log.warning("Transient infra error: %.120s", err)
        # Only inform the user in PRIVATE chats — stay completely silent in groups
        _chat = update.effective_chat if update else None
        _is_private = _chat and _chat.type == "private"
        if _is_private and update.effective_message and not update.callback_query:
            try:
                await update.effective_message.reply_text(
                    "⚠️ A temporary network issue occurred. Please try again in a moment."
                )
            except Exception:
                pass
        return

    # ── Tier 3: real application errors ───────────────────────────────────────
    user_id = None
    if update and update.effective_user:
        user_id = update.effective_user.id

    log.error("Unhandled error — user=%s: %s", user_id, err, exc_info=exc)

    try:
        log_error("unhandled_exception", err[:500], user_id=user_id)
    except Exception:
        pass

    # Only reply in PRIVATE chats — never post error messages into groups
    _chat = update.effective_chat if update else None
    _is_private = _chat and _chat.type == "private"
    if _is_private and update.effective_message and not update.callback_query:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again in a moment."
            )
        except Exception:
            pass


async def _smart_text_handler(update, context) -> None:
    """
    Route plain-text messages:
    1. If user is in image-prompt flow → image handler
    2. Otherwise → AI chat handler
    """
    user = update.effective_user
    if user and user.id in _pending:
        await handle_image_prompt_message(update, context)
    else:
        await chat_handler(update, context)


def build_app() -> Application:
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # ── Core commands ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       start_handler))
    app.add_handler(CommandHandler("help",        help_handler))
    app.add_handler(CommandHandler("about",       about_handler))
    app.add_handler(CommandHandler("chat",        chat_handler))
    app.add_handler(CommandHandler("image",       image_command_handler))
    app.add_handler(CommandHandler("style",       style_handler))
    app.add_handler(CommandHandler("language",    language_handler))
    app.add_handler(CommandHandler("model",       model_handler))
    app.add_handler(CommandHandler("subscribe",   subscribe_handler))

    # ── Extended AI commands ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("ask",         ask_handler))
    app.add_handler(CommandHandler("code",        code_handler))
    app.add_handler(CommandHandler("summarize",   summarize_handler))
    app.add_handler(CommandHandler("translate",   translate_handler))
    app.add_handler(CommandHandler("analyze",     analyze_handler))
    app.add_handler(CommandHandler("profile",     profile_handler))
    app.add_handler(CommandHandler("stats",       stats_handler))
    app.add_handler(CommandHandler("referral",    referral_handler))
    app.add_handler(CommandHandler("history",     history_handler))
    app.add_handler(CommandHandler("clear",       clear_handler))
    app.add_handler(CommandHandler("feedback",    feedback_handler))
    app.add_handler(CommandHandler("leaderboard", leaderboard_handler))
    app.add_handler(CommandHandler("streak",      streak_handler))

    # ── Telegram Stars payment handlers ───────────────────────────────────────
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # ── Admin — dashboard & monitoring ───────────────────────────────────────
    app.add_handler(CommandHandler("admin",              admin_handler))
    app.add_handler(CommandHandler("admin_help",         admin_help_handler))
    app.add_handler(CommandHandler("admin_stats",        admin_stats_handler))
    app.add_handler(CommandHandler("admin_health",       admin_health_handler))
    app.add_handler(CommandHandler("admin_config",       admin_config_handler))
    app.add_handler(CommandHandler("admin_logs",         admin_logs_handler))
    app.add_handler(CommandHandler("admin_images",       admin_images_handler))
    app.add_handler(CommandHandler("admin_clearlogs",    admin_clearlogs_handler))

    # ── Admin — user management ───────────────────────────────────────────────
    app.add_handler(CommandHandler("admin_users",        admin_users_handler))
    app.add_handler(CommandHandler("admin_user",         admin_userinfo_handler))
    app.add_handler(CommandHandler("admin_ban",          admin_ban_handler))
    app.add_handler(CommandHandler("admin_unban",        admin_unban_handler))
    app.add_handler(CommandHandler("admin_setvip",       admin_setvip_handler))
    app.add_handler(CommandHandler("admin_addcredits",   admin_addcredits_handler))
    app.add_handler(CommandHandler("admin_setcredits",   admin_setcredits_handler))
    app.add_handler(CommandHandler("admin_resetlimit",   admin_resetlimit_handler))
    app.add_handler(CommandHandler("admin_resetuser",    admin_resetuser_handler))
    app.add_handler(CommandHandler("admin_clearchat",    admin_clearchat_handler))

    # ── Admin — communication ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("admin_broadcast",    admin_broadcast_handler))
    app.add_handler(CommandHandler("broadcast",          admin_broadcast_handler))
    app.add_handler(CommandHandler("testbroadcast",      testbroadcast_handler))
    app.add_handler(CommandHandler("admin_dm",           admin_dm_handler))

    # ── Admin — multi-admin (owner only) ─────────────────────────────────────
    app.add_handler(CommandHandler("admin_addadmin",     admin_addadmin_handler))
    app.add_handler(CommandHandler("admin_removeadmin",  admin_removeadmin_handler))
    app.add_handler(CommandHandler("admin_listadmins",   admin_listadmins_handler))

    # ── Admin — onboarding system ─────────────────────────────────────────────
    app.add_handler(CommandHandler("admin_onboarding",   admin_onboarding_handler))

    # ── Enterprise audit center (admin only) ──────────────────────────────────
    app.add_handler(CommandHandler("health",             status_handler))
    app.add_handler(CommandHandler("status",             status_handler))
    app.add_handler(CommandHandler("testaudit",          testaudit_handler))

    # ── Announcements ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("pin",                      pin_handler))
    app.add_handler(CommandHandler("pin_priority",             pin_priority_handler))
    app.add_handler(CommandHandler("schedule_announcement",    schedule_announcement_handler))
    app.add_handler(CommandHandler("unpin",                    unpin_handler))
    app.add_handler(CommandHandler("updateannouncement",       updateannouncement_handler))
    app.add_handler(CommandHandler("pinphoto",                 pinphoto_handler))
    app.add_handler(CommandHandler("listannouncements",        listannouncements_handler))
    app.add_handler(CommandHandler("announce_channel",         announce_channel_handler))
    app.add_handler(CommandHandler("announce_group",           announce_group_handler))
    app.add_handler(CommandHandler("announce_both",            announce_both_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Photo messages — AI retouching ───────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, photo_handler))

    # ── Voice & audio messages — transcription + AI response ──────────────────
    # Private chats only — groups use /ai or @mention for AI interaction
    app.add_handler(MessageHandler(
        (filters.VOICE | filters.AUDIO) & ~filters.COMMAND & filters.ChatType.PRIVATE,
        voice_handler,
    ))

    # ── Document / file analysis ───────────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND & filters.ChatType.PRIVATE,
        document_handler,
    ))

    # ── Utility tool commands ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("weather",  weather_handler))
    app.add_handler(CommandHandler("calc",     calc_handler))
    app.add_handler(CommandHandler("qr",       qr_handler))
    app.add_handler(CommandHandler("crypto",   crypto_handler))
    app.add_handler(CommandHandler("wiki",     wiki_handler))
    app.add_handler(CommandHandler("news",     news_handler))
    app.add_handler(CommandHandler("currency", currency_handler))
    app.add_handler(CommandHandler("quote",    quote_handler))

    # ── Group integration ─────────────────────────────────────────────────────
    # /ai command only in groups — private chats use chat_handler (with full guardrails)
    app.add_handler(CommandHandler("ai", group_ai_handler, filters=filters.ChatType.GROUPS))

    # Welcome new group members
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))

    # Block non-admin commands in groups (except /ai and explicit group commands above)
    # group=0 catches all group commands; runs AFTER specific group commands registered above
    app.add_handler(
        MessageHandler(
            filters.COMMAND & filters.ChatType.GROUPS,
            group_command_blocker,
        ),
        group=3,
    )

    # Membership monitoring — detects when users leave the channel or group
    # Requires "Track all member changes" enabled in Bot Settings on Telegram
    app.add_handler(ChatMemberHandler(membership_change_handler))

    # @mention reply — group 1 so it runs alongside spam_filter
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            mention_handler,
        ),
        group=1,
    )
    # Anti-spam filter — group 2 runs on every group text message
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            spam_filter,
        ),
        group=2,
    )

    # ── Free-text messages (PRIVATE only — groups handled above) ──────────────
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        _smart_text_handler,
    ))

    # ── Global error handler ──────────────────────────────────────────────────
    app.add_error_handler(error_handler)

    return app


def _run_dev_mode() -> None:
    """
    Non-polling mode: Flask keep-alive only — Telegram polling is blocked.

    To enable polling in Replit or locally, set ALLOW_POLLING=true in env vars.
    WARNING: Do NOT set ALLOW_POLLING=true while Railway is also running the same
    token — two pollers will cause Telegram 409 Conflict errors.
    """
    log.info(_SEPARATOR)
    log.info("  🚫 POLLING BLOCKED — ALLOW_POLLING is not set.")
    log.info("  This environment is: Replit / Local / CI / Other (not Railway).")
    log.info("  Flask keep-alive will run on port %s (health checks only).", os.getenv("PORT", "5000"))
    log.info("  To run the bot here: set ALLOW_POLLING=true in Replit Secrets.")
    log.info("  WARNING: Stop Railway first to avoid 409 Conflict errors.")
    log.info(_SEPARATOR)
    # Flask is already running (started at top of main())
    mark_ready()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("Non-Railway mode stopped.")


def main() -> None:
    # ── Flask starts FIRST — /health must respond during entire boot sequence ──
    # All init errors below are caught. The process never exits, so the health
    # endpoint always has a live Python process behind it on Railway.
    start_keepalive()

    _print_startup_banner()

    try:
        require_config()
    except EnvironmentError as exc:
        log.critical("FATAL — missing Railway env vars: %s", exc)
        log.critical("Bot polling disabled. Fix variables in Railway → Variables tab, then redeploy.")
        mark_ready()   # health endpoint returns 200 so Railway doesn't restart-loop
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            pass
        return

    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # ── Polling guard ─────────────────────────────────────────────────────────
    # ALLOW_POLLING is True when IS_RAILWAY is detected, or ALLOW_POLLING=true
    # is set explicitly (e.g. Replit testing). Without it, only Flask runs.
    if not ALLOW_POLLING:
        _run_dev_mode()
        return

    # ── Startup sequence ──────────────────────────────────────────────────────
    env_name = "Railway (production)" if IS_RAILWAY else "Replit/local (ALLOW_POLLING=true)"
    log.info("Starting bot in %s…", env_name)

    try:
        bootstrap_schema()
    except Exception as exc:
        log.warning("Schema bootstrap warning: %s", exc)

    try:
        _bootstrap_onboarding_schema()
    except Exception as exc:
        log.warning("Onboarding schema check warning: %s", exc)

    try:
        load_secondary_admins()
    except Exception as exc:
        log.warning("Could not load secondary admins: %s", exc)

    try:
        _seed_default_announcement()
    except Exception as exc:
        log.warning("Could not seed default announcement: %s", exc)

    # Flask is already running (started at top of main())
    start_vip_scheduler()

    app = build_app()
    log.info("Starting Telegram polling — Railway-only production instance.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
            "pre_checkout_query",
            "chat_member",
            "my_chat_member",
        ],
    )


if __name__ == "__main__":
    main()
