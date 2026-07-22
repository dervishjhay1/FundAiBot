"""
FundzAiBot — Main entry point.
Version 5.0.1 — Ecosystem Restructuring

Architecture:
  Replit  →  GitHub  →  Railway (LIVE BOT)
  Replit is for code editing + GitHub sync ONLY.
  Railway is the SOLE environment where Telegram polling runs.

Product identity:
  FundzAiBot is an AI Assistant developed by Fundz Company Ltd.
  It serves users. It does not govern.
  Executive authority belongs to Fundz Company Headquarters.

Deployment policy — RAILWAY ONLY:
  Polling starts ONLY when Railway environment variables are detected (IS_RAILWAY=True).
  Any non-Railway environment runs Flask keep-alive only.
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
from handlers.ai_commands import (
    ask_handler, code_handler, summarize_handler, translate_handler,
    analyze_handler, model_handler,
)
from handlers.callbacks import callback_handler
from handlers.chat import chat_handler, clear_handler
from handlers.extras import feedback_handler, leaderboard_handler, streak_handler
from handlers.help import help_handler, about_handler
from handlers.image import image_command_handler, _pending, handle_image_prompt_message
from handlers.retouch import photo_handler
from handlers.language import language_handler
from handlers.onboarding import admin_onboarding_handler
from handlers.group import (
    new_member_handler,
    spam_filter,
)
from handlers.membership import membership_change_handler
from handlers.profile import profile_handler, referral_handler, history_handler, stats_handler
from handlers.payment import subscribe_handler, precheckout_handler, successful_payment_handler
from handlers.start import start_handler
from handlers.style import style_handler
from handlers.tools import tools_handler
from services.keepalive import start_keepalive, mark_ready
from services.queue_manager import queue_manager
from services.database import bootstrap_schema, load_secondary_admins
from services.vip_scheduler import start_vip_scheduler
from services.hq_sync import start_hq_sync, event_system_restart
from services.product_metadata import refresh_from_hq, push_metadata_to_hq
from utils.logger import get_logger

log = get_logger(__name__)

_SEPARATOR = "=" * 70


def _print_startup_banner() -> None:
    env_label  = "🚂 RAILWAY (production)" if IS_RAILWAY else "🚫 NON-RAILWAY (polling BLOCKED)"
    poll_label = "✅ YES — Telegram polling active" if IS_RAILWAY else "❌ NO — Railway env vars not detected"
    log.info(_SEPARATOR)
    log.info("  %s  v%s", BOT_NAME, BOT_VERSION)
    log.info("  %s", "AI Assistant by Fundz Company Ltd.")
    log.info("  Environment : %s", env_label)
    log.info("  Polling     : %s", poll_label)
    log.info(_SEPARATOR)


# ── Bot commands ──────────────────────────────────────────────────────────────

_USER_COMMANDS = [
    BotCommand("start",      "Start FundzAiBot"),
    BotCommand("ask",        "Quick AI question"),
    BotCommand("code",       "Code generation & debugging"),
    BotCommand("summarize",  "Summarize any text"),
    BotCommand("translate",  "Translate to any language"),
    BotCommand("analyze",    "Analyze an image with AI"),
    BotCommand("image",      "Generate an image"),
    BotCommand("model",      "Switch AI model"),
    BotCommand("style",      "Change AI personality style"),
    BotCommand("profile",    "View your profile"),
    BotCommand("stats",      "View your usage stats"),
    BotCommand("referral",   "Your referral link & rewards"),
    BotCommand("subscribe",  "VIP plans & upgrade"),
    BotCommand("language",   "Change language"),
    BotCommand("tools",      "Useful tools (weather, crypto, news...)"),
    BotCommand("history",    "Recent conversation history"),
    BotCommand("feedback",   "Send feedback"),
    BotCommand("streak",     "View your activity streak"),
    BotCommand("leaderboard","Top users this week"),
    BotCommand("clear",      "Clear conversation history"),
    BotCommand("help",       "Help & features"),
    BotCommand("about",      "About FundzAiBot"),
]

_ADMIN_EXTRA_COMMANDS = [
    BotCommand("onboarding", "Configure onboarding (admin)"),
]


async def _set_commands(app: Application) -> None:
    try:
        await app.bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())
        log.info("✅ Bot commands registered (%d commands)", len(_USER_COMMANDS))
    except Exception as exc:
        log.warning("Could not set bot commands: %s", exc)


# ── Application builder ───────────────────────────────────────────────────────

def build_app() -> Application:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # ── User AI commands ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       start_handler))
    app.add_handler(CommandHandler("help",        help_handler))
    app.add_handler(CommandHandler("about",       about_handler))
    app.add_handler(CommandHandler("ask",         ask_handler))
    app.add_handler(CommandHandler("code",        code_handler))
    app.add_handler(CommandHandler("summarize",   summarize_handler))
    app.add_handler(CommandHandler("translate",   translate_handler))
    app.add_handler(CommandHandler("analyze",     analyze_handler))
    app.add_handler(CommandHandler("model",       model_handler))
    app.add_handler(CommandHandler("style",       style_handler))
    app.add_handler(CommandHandler("image",       image_command_handler))
    app.add_handler(CommandHandler("profile",     profile_handler))
    app.add_handler(CommandHandler("stats",       stats_handler))
    app.add_handler(CommandHandler("referral",    referral_handler))
    app.add_handler(CommandHandler("history",     history_handler))
    app.add_handler(CommandHandler("subscribe",   subscribe_handler))
    app.add_handler(CommandHandler("language",    language_handler))
    app.add_handler(CommandHandler("tools",       tools_handler))
    app.add_handler(CommandHandler("feedback",    feedback_handler))
    app.add_handler(CommandHandler("streak",      streak_handler))
    app.add_handler(CommandHandler("leaderboard", leaderboard_handler))
    app.add_handler(CommandHandler("clear",       clear_handler))

    # ── Admin operational commands ─────────────────────────────────────────────
    app.add_handler(CommandHandler("onboarding",  admin_onboarding_handler))

    # ── Callbacks & payments ──────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))

    # ── Photo / retouch ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler), group=0)

    # ── Group: new member welcome and spam filter ─────────────────────────────
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS,
        new_member_handler,
    ), group=0)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS,
        spam_filter,
    ), group=2)

    # ── Membership change (chat_member events) ────────────────────────────────
    app.add_handler(ChatMemberHandler(membership_change_handler))

    # ── Payment: successful payment ───────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT,
        successful_payment_handler,
    ))

    # ── Image prompt input (pending image generation) ─────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_image_prompt_message,
    ), group=5)

    # ── Main AI chat handler (catch-all, lowest priority) ─────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        chat_handler,
    ), group=10)

    return app


# ── Post-init: run once after bot connects ────────────────────────────────────

async def post_init(app: Application) -> None:
    """Run after Telegram connection is established."""
    # 1. Register bot commands
    await _set_commands(app)

    # 2. Bootstrap database schema
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, bootstrap_schema)
        log.info("✅ Database schema bootstrapped")
    except Exception as exc:
        log.warning("DB bootstrap: %s (non-fatal)", exc)

    # 3. Load secondary admins
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, load_secondary_admins)
        log.info("✅ Secondary admins loaded")
    except Exception as exc:
        log.warning("Secondary admins load: %s (non-fatal)", exc)

    # 4. Start HQ synchronization daemon
    start_hq_sync()
    log.info("✅ HQ sync daemon started")

    # 5. Refresh product metadata from HQ (non-blocking)
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, refresh_from_hq)
        await loop.run_in_executor(None, push_metadata_to_hq)
    except Exception as exc:
        log.debug("HQ metadata refresh: %s (non-fatal)", exc)

    # 6. Start VIP expiry scheduler
    try:
        start_vip_scheduler(app.bot)
        log.info("✅ VIP scheduler started")
    except Exception as exc:
        log.warning("VIP scheduler: %s (non-fatal)", exc)

    # 7. Report system restart to HQ
    event_system_restart({
        "environment": "railway" if IS_RAILWAY else "development",
        "version":     BOT_VERSION,
    })

    mark_ready()
    log.info("✅ FundzAiBot %s is ready", BOT_VERSION)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _print_startup_banner()
    require_config()

    # Always start Flask keep-alive (for Railway's health check)
    start_keepalive()

    if not IS_RAILWAY:
        log.warning("Non-Railway environment — Telegram polling is BLOCKED.")
        log.info("Flask keep-alive is running. Polling starts only on Railway.")
        # Block forever so keep-alive stays up
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        return

    # ── Railway: delete any stale webhook, then start polling ─────────────────
    import httpx
    try:
        _wh_resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
            timeout=10.0,
        )
        _wh_data = _wh_resp.json()
        if _wh_data.get("result"):
            log.info("✅ Webhook deleted — Telegram polling mode confirmed.")
        else:
            log.warning("deleteWebhook response: %s", _wh_data)
    except Exception as _exc:
        log.warning("Could not delete Telegram webhook (non-fatal): %s", _exc)

    app = build_app()
    app.post_init = post_init

    # Global asyncio exception handler
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
