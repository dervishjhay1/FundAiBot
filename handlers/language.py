"""
FundzAiBot — /language command handler.
Lets users choose their preferred language.
Free users: English, Spanish, French.
VIP users: All 8 languages.
Admins: All languages automatically (no restriction).
"""

import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import is_admin
from services.database import get_or_create_user
from services.language import (
    FREE_LANGUAGES, VIP_LANGUAGES, ALL_LANGUAGES,
    get_string, get_user_language, save_user_language, can_use_language,
)
from utils.keyboards import back_to_menu, main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)


def _language_keyboard(user: dict, user_id: int) -> InlineKeyboardMarkup:
    """Build language selection keyboard. VIP-only languages are locked for free users."""
    admin = is_admin(user_id)
    is_vip = admin or bool((user or {}).get("is_vip"))

    rows = []

    # Free languages — always available
    for code, label in FREE_LANGUAGES.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"lang:{code}")])

    # Separator label
    if is_vip:
        rows.append([InlineKeyboardButton("─── 💎 VIP Languages ───", callback_data="noop")])
    else:
        rows.append([InlineKeyboardButton("─── 💎 VIP Only ─────────", callback_data="noop")])

    # VIP languages
    for code, label in VIP_LANGUAGES.items():
        if is_vip:
            rows.append([InlineKeyboardButton(label, callback_data=f"lang:{code}")])
        else:
            rows.append([InlineKeyboardButton(f"🔒 {label}", callback_data="lang:vip_locked")])

    rows.append([InlineKeyboardButton("« Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


async def language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/language — Show language selection menu."""
    user = update.effective_user
    if not user:
        return

    loop = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(
        None, lambda: get_or_create_user(user.id, first_name=user.first_name or "", username=user.username or "")
    )

    lang = get_user_language(db_user, user.id)
    text = get_string(lang, "choose_language")

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_language_keyboard(db_user, user.id),
    )
    log.info("/language user=%s current=%s", user.id, lang)


async def handle_language_callback(query, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle lang:XX callback from inline keyboard."""
    code = (query.data or "").split(":", 1)[1]

    if code == "vip_locked":
        await query.answer(
            "💎 This language requires VIP. Use /subscribe to upgrade!",
            show_alert=True,
        )
        return

    if code not in ALL_LANGUAGES:
        await query.answer("Unknown language.", show_alert=True)
        return

    loop = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(None, lambda: get_or_create_user(user_id))

    if not can_use_language(code, db_user, user_id):
        await query.answer(
            "💎 VIP required for this language. Upgrade with /subscribe!",
            show_alert=True,
        )
        return

    # Save to DB
    await loop.run_in_executor(None, lambda: save_user_language(user_id, code))

    lang_name = ALL_LANGUAGES[code]
    confirmation = get_string(code, "language_set", lang=lang_name)

    await query.answer(f"✅ Language set to {lang_name}!")
    try:
        admin = is_admin(user_id)
        await query.edit_message_text(
            confirmation,
            parse_mode="HTML",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )
    except Exception:
        pass

    log.info("Language set: user=%s lang=%s", user_id, code)
