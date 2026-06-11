"""
FundAiBot — Admin permission decorators and middleware.

Usage in handlers:
    from utils.admin_guard import admin_only, owner_only

    @admin_only          # any admin (owner or promoted admin)
    async def my_cmd(update, context): ...

    @owner_only          # permanent owner only
    async def sensitive_cmd(update, context): ...

Design principles:
  - Non-admin requests are silently ignored (no error message exposed).
  - Callback queries from non-admins get a private alert instead of a public reply.
  - All blocked attempts are logged at WARNING level with user_id and function name.
"""

import functools
from telegram import Update
from telegram.ext import ContextTypes
from utils.logger import get_logger

log = get_logger(__name__)


def admin_only(func):
    """
    Decorator: drop the request silently if the caller is not an admin.
    Works for both command handlers and callback query handlers.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from services.admin_manager import is_admin
        user = update.effective_user
        if not user:
            return
        if not is_admin(user.id):
            log.warning(
                "ADMIN ACCESS DENIED — user=%s tried %s", user.id, func.__name__
            )
            if update.callback_query:
                await update.callback_query.answer("⛔ Access denied.", show_alert=True)
            # No message sent — do not reveal admin feature existence to regular users
            return
        return await func(update, context)
    return wrapper


def owner_only(func):
    """
    Decorator: drop the request if the caller is not the permanent owner.
    Replies with a brief owner-only notice (only visible to the caller).
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from services.admin_manager import is_owner
        user = update.effective_user
        if not user:
            return
        if not is_owner(user.id):
            log.warning(
                "OWNER ACCESS DENIED — user=%s tried %s", user.id, func.__name__
            )
            if update.callback_query:
                await update.callback_query.answer(
                    "⛔ Owner-only action.", show_alert=True
                )
            elif update.effective_message:
                await update.effective_message.reply_text("⛔ Owner-only command.")
            return
        return await func(update, context)
    return wrapper
