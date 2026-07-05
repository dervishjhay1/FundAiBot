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

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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
)
from handlers.announcements import (
    pin_handler, unpin_handler, updateannouncement_handler,
    pinphoto_handler, listannouncements_handler,
    announce_channel_handler, announce_group_handler, announce_both_handler,
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
from handlers.language import language_handler
from handlers.onboarding import admin_onboarding_handler
from handlers.audit import testaudit_handler, status_handler
from handlers.ceo_office import ceo_office_command_handler, schedule_meeting_command_handler
from handlers.group import (
    new_member_handler,
    testaudit_mention_handler,
    spam_filter,
    smart_community_handler,
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
from utils.logger import get_logger

log = get_logger(__name__)

_SEPARATOR = "=" * 70


def _print_startup_banner() -> None:
    env_label  = "🚂 RAILWAY (production)" if IS_RAILWAY else "🚫 NON-RAILWAY (polling BLOCKED)"
    poll_label = "✅ YES — Telegram polling active" if IS_RAILWAY else "❌ NO — Railway env vars not detected"
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
    """Register bot commands and start background services after bot connects."""
    from config.settings import ADMIN_USER_ID

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
    ]

    # ── Admin commands — visible ONLY in the admin's chat ─────────────────────
    admin_commands = public_commands + [
        BotCommand("status",            "📊 Live bot status"),
        BotCommand("testaudit",         "🔬 Enterprise audit center"),
        BotCommand("broadcast",         "📢 Broadcast message to all users"),
        BotCommand("admin",             "👑 Admin dashboard"),
        BotCommand("admin_stats",       "📊 Platform statistics"),
        BotCommand("admin_users",       "👥 User management"),
        BotCommand("admin_ban",         "🚫 Ban a user"),
        BotCommand("admin_setvip",      "💎 Set VIP status"),
        BotCommand("admin_addcredits",  "➕ Add credits"),
        BotCommand("admin_logs",        "📋 Recent error logs"),
        BotCommand("admin_clearlogs",   "🗑️ Clear error logs"),
        BotCommand("admin_health",      "🩺 AI health check"),
        BotCommand("testbroadcast",     "👁️ Preview active announcement"),
        BotCommand("pin",               "📌 Create announcement"),
        BotCommand("announce_both",     "📣 Push to channel + group"),
        BotCommand("ceo_office",        "🏢 Open CEO Office (TestAudit)"),
        BotCommand("schedule_meeting",  "📅 Schedule a meeting with TestAudit"),
    ]

    # Set public list for everyone
    await application.bot.set_my_commands(
        public_commands,
        scope=BotCommandScopeDefault(),
    )

    # Set full admin list — only shows up in admin's private chat
    if ADMIN_USER_ID:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=ADMIN_USER_ID),
            )
        except Exception as exc:
            log.warning("Could not set admin-scoped commands: %s", exc)

    log.info("Bot commands registered (public=%d, admin=%d).",
             len(public_commands), len(admin_commands))
    await queue_manager.start()

    # ── Background services ───────────────────────────────────────────────────
    # Each import is isolated in its own try/except so a single broken service
    # can NEVER crash post_init and take the entire bot down.
    try:
        from services.channel_publisher import run_channel_publisher
        asyncio.create_task(run_channel_publisher(application.bot))
        log.info("Channel publisher background task started.")
    except Exception as exc:
        log.error("Channel publisher failed to start (bot continues): %s", exc)

    try:
        from services.dm_operations import run_group_engagement_scheduler
        asyncio.create_task(run_group_engagement_scheduler(application.bot))
        log.info("Group engagement scheduler background task started.")
    except Exception as exc:
        log.error("Group engagement scheduler failed to start (bot continues): %s", exc)

    # ── Phase 2: Enterprise Intelligence Services ─────────────────────────────
    try:
        from services.ceo_office import initialize as ceo_office_initialize
        ceo_office_initialize()
        log.info("CEO Office initialized (memory + history restored).")
    except Exception as exc:
        log.warning("CEO Office init warning: %s", exc)

    try:
        from services.testaudit_core import start_testaudit_core
        start_testaudit_core()
        log.info("TestAudit intelligence core started.")
    except Exception as exc:
        log.warning("TestAudit core start warning: %s", exc)

    try:
        from services.executive_assistant import start_executive_assistant
        start_executive_assistant()
        log.info("Executive Assistant scheduler started.")
    except Exception as exc:
        log.warning("Executive Assistant start warning: %s", exc)

    try:
        from services.autonomous_mode import start_autonomous_mode_monitor
        start_autonomous_mode_monitor()
        log.info("Autonomous Operations Mode monitor started.")
    except Exception as exc:
        log.warning("Autonomous Mode start warning: %s", exc)

    try:
        from services.meeting_manager import start_meeting_manager
        start_meeting_manager()
        log.info("Meeting Manager reminder loop started.")
    except Exception as exc:
        log.warning("Meeting Manager start warning: %s", exc)

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
        # Still inform the user — never leave them in silence
        if update and update.effective_message and not (update.callback_query):
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

    # Only try to reply when we have an actual user message (not a callback ghost)
    if update and update.effective_message and not (update.callback_query):
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
    2. If user is in CEO Office session → CEO Office handler
    3. Otherwise → AI chat handler
    """
    user = update.effective_user
    if user and user.id in _pending:
        await handle_image_prompt_message(update, context)
        return
    from handlers.ceo_office import handle_ceo_message
    handled = await handle_ceo_message(update, context)
    if not handled:
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
    app.add_handler(CommandHandler("status",             status_handler))
    app.add_handler(CommandHandler("testaudit",          testaudit_handler))

    # ── CEO Office (Phase 2) ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("ceo_office",         ceo_office_command_handler))
    app.add_handler(CommandHandler("schedule_meeting",   schedule_meeting_command_handler))

    # ── Announcements ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("pin",                pin_handler))
    app.add_handler(CommandHandler("unpin",              unpin_handler))
    app.add_handler(CommandHandler("updateannouncement", updateannouncement_handler))
    app.add_handler(CommandHandler("pinphoto",           pinphoto_handler))
    app.add_handler(CommandHandler("listannouncements",  listannouncements_handler))
    app.add_handler(CommandHandler("announce_channel",   announce_channel_handler))
    app.add_handler(CommandHandler("announce_group",     announce_group_handler))
    app.add_handler(CommandHandler("announce_both",      announce_both_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Photo messages — AI retouching ───────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, photo_handler))

    # ── Group integration — TESTAUDIT ONLY — main bot is completely silent ───────
    #
    # Architecture: Only TestAudit (Operations Manager) speaks inside groups.
    # No /ai command. No generic AI replies. No mention-as-AI-chatbot.
    # All group behaviour goes through the TestAudit community manager persona.
    #
    # Handler group order:
    #   group=1  testaudit_mention_handler  — @mention → TestAudit responds conversationally
    #   group=2  spam_filter               — anti-spam enforcement
    #   group=3  smart_community_handler   — unanswered question detection (2.5 min wait)

    # Welcome new group members (TestAudit warm welcome persona)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    # Membership monitoring (requires "Track all member changes" in Bot Settings)
    app.add_handler(ChatMemberHandler(membership_change_handler))
    # @mention → TestAudit community manager persona (NOT generic AI)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            testaudit_mention_handler,
        ),
        group=1,
    )
    # Anti-spam filter
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            spam_filter,
        ),
        group=2,
    )
    # Smart community manager — monitors unanswered questions,
    # waits 2.5 min before stepping in (humans always respond first)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            smart_community_handler,
        ),
        group=3,
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
    Non-Railway mode: Flask keep-alive only — Telegram polling is PERMANENTLY BLOCKED.

    This is not configurable. There is no override. Only Railway can run the bot.
    Replit, local machines, CI, VPS, Docker — all blocked by design.
    This prevents duplicate bot instances and Telegram 409 Conflict errors.
    """
    log.info(_SEPARATOR)
    log.info("  🚫 RAILWAY-ONLY DEPLOYMENT POLICY ENFORCED")
    log.info("  Telegram polling is BLOCKED — Railway env vars not detected.")
    log.info("  This environment is: Replit / Local / CI / Other (not Railway).")
    log.info("  Flask keep-alive will run on port %s (health checks only).", os.getenv("PORT", "5000"))
    log.info("  To deploy the bot: push to GitHub → Railway auto-deploys.")
    log.info("  DO NOT attempt to bypass this guard — it protects production.")
    log.info(_SEPARATOR)
    start_keepalive()
    mark_ready()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("Non-Railway mode stopped.")


def main() -> None:
    _print_startup_banner()

    require_config()

    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # ── Railway-only guard — no override exists ───────────────────────────────
    # ALLOW_POLLING == IS_RAILWAY (override permanently removed).
    # Non-Railway environments run Flask keep-alive only and stop here.
    if not IS_RAILWAY:
        _run_dev_mode()
        return

    # ── Production startup sequence (Railway) ─────────────────────────────────
    log.info("Starting production boot sequence…")

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

    start_keepalive()
    start_vip_scheduler()

    # ── Explicitly delete any active Telegram webhook before polling ──────────
    # If a webhook is set (from any previous deployment or misconfiguration),
    # Telegram sends updates to that URL and getUpdates returns nothing.
    # We force-delete it here so polling always wins, regardless of prior state.
    try:
        import requests as _req
        _wh_resp = _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        _wh_data = _wh_resp.json()
        if _wh_data.get("result"):
            log.info("✅ Webhook deleted — Telegram polling mode confirmed.")
        else:
            log.warning("deleteWebhook response: %s", _wh_data)
    except Exception as _exc:
        log.warning("Could not delete Telegram webhook (non-fatal): %s", _exc)

    app = build_app()

    # ── Global asyncio exception handler ─────────────────────────────────────
    # Catches unhandled exceptions in background asyncio tasks so they are
    # logged instead of silently killing the task or spamming stderr.
    import asyncio as _asyncio

    def _task_exception_handler(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "unknown asyncio error")
        if exc is not None:
            log.error(
                "Unhandled asyncio task exception: %s — %s",
                type(exc).__name__, exc,
                exc_info=exc,
            )
        else:
            log.error("Asyncio error context: %s", msg)

    try:
        _loop = _asyncio.get_event_loop()
        _loop.set_exception_handler(_task_exception_handler)
        log.info("✅ Global asyncio exception handler registered.")
    except Exception as _exc:
        log.warning("Could not set asyncio exception handler: %s", _exc)

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
