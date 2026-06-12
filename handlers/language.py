"""
FundzAiBot — /language command handler.
Lets users choose their preferred language.
Free users: English, Spanish, French.
VIP users: All 10 languages (de, pt, ar, ru, tr, hi, zh, yo).
Admins: All languages automatically (no restriction).

First-start language detection:
  show_language_detection_prompt() — called from start.py for new users.
  Detects Telegram language_code and prompts user to confirm or choose another.
"""

import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import is_admin
from services.database import get_or_create_user
from services.language import (
    FREE_LANGUAGES, VIP_LANGUAGES, ALL_LANGUAGES,
    get_string, get_user_language, save_user_language, can_use_language,
    detect_language,
)
from utils.keyboards import back_to_menu, main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)


def _language_keyboard(user: dict, user_id: int) -> InlineKeyboardMarkup:
    """Build 10-language selection grid — 2 columns.
    VIP-only languages are locked (🔒) for free users.
    """
    admin  = is_admin(user_id)
    is_vip = admin or bool((user or {}).get("is_vip"))

    rows = []

    # ── Free languages (always available) ────────────────────────────────────
    free_items = list(FREE_LANGUAGES.items())
    for i in range(0, len(free_items), 2):
        row = [InlineKeyboardButton(free_items[i][1], callback_data=f"lang:{free_items[i][0]}")]
        if i + 1 < len(free_items):
            row.append(InlineKeyboardButton(free_items[i + 1][1], callback_data=f"lang:{free_items[i + 1][0]}"))
        rows.append(row)

    # ── VIP separator ─────────────────────────────────────────────────────────
    if is_vip:
        rows.append([InlineKeyboardButton("────── 💎 VIP Languages ──────", callback_data="noop")])
    else:
        rows.append([InlineKeyboardButton("────── 🔒 VIP Required ──────", callback_data="noop")])

    # ── VIP languages (2-column grid) ─────────────────────────────────────────
    vip_items = list(VIP_LANGUAGES.items())
    for i in range(0, len(vip_items), 2):
        if is_vip:
            row = [InlineKeyboardButton(vip_items[i][1], callback_data=f"lang:{vip_items[i][0]}")]
            if i + 1 < len(vip_items):
                row.append(InlineKeyboardButton(vip_items[i + 1][1], callback_data=f"lang:{vip_items[i + 1][0]}"))
        else:
            row = [InlineKeyboardButton(f"🔒 {vip_items[i][1]}", callback_data="lang:vip_locked")]
            if i + 1 < len(vip_items):
                row.append(InlineKeyboardButton(f"🔒 {vip_items[i + 1][1]}", callback_data="lang:vip_locked"))
        rows.append(row)

    rows.append([InlineKeyboardButton("« Back to Menu", callback_data="menu:back")])
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
    current_name = ALL_LANGUAGES.get(lang, "English")

    text = (
        f"🌍 <b>Choose Your Language</b>\n\n"
        f"Current: {current_name}\n\n"
        f"Free users: English, Spanish, French\n"
        f"💎 VIP: All {len(ALL_LANGUAGES)} languages"
    )

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

    if code == "noop" or not code:
        await query.answer()
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


async def show_language_detection_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tg_lang_code: str | None,
    db_user: dict,
) -> bool:
    """Show language auto-detection prompt for new users.

    Returns True if the prompt was shown (caller should return early),
    False if detection produced English (skip the prompt — just continue).
    """
    detected = detect_language(tg_lang_code)

    # No prompt needed if we'd default to English anyway
    if detected == "en":
        return False

    # Only show if the user can actually use the detected language
    if not can_use_language(detected, db_user, update.effective_user.id):
        return False

    lang_name = ALL_LANGUAGES.get(detected, detected.upper())

    text = (
        f"🌍 <b>Language detected: {lang_name}</b>\n\n"
        f"Would you like to continue in <b>{lang_name}</b>?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Continue in {lang_name}", callback_data=f"lang_detect:confirm:{detected}"),
            InlineKeyboardButton("🌍 Choose Another", callback_data="lang_detect:choose"),
        ]
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    log.info("Language detection prompt: user=%s detected=%s", update.effective_user.id, detected)
    return True


async def handle_lang_detect_callback(
    query,
    user_id: int,
    action: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle lang_detect:confirm:<code> and lang_detect:choose callbacks."""
    loop = asyncio.get_running_loop()
    admin = is_admin(user_id)

    if action.startswith("confirm:"):
        code = action.split(":", 1)[1]
        if code in ALL_LANGUAGES:
            await loop.run_in_executor(None, lambda: save_user_language(user_id, code))
            lang_name = ALL_LANGUAGES[code]
            await query.answer(f"✅ Language set to {lang_name}!")
            try:
                confirmation = get_string(code, "language_set", lang=lang_name)
                await query.edit_message_text(
                    confirmation,
                    parse_mode="HTML",
                    reply_markup=admin_main_menu() if admin else main_menu(),
                )
            except Exception:
                pass
            log.info("Lang detect confirmed: user=%s lang=%s", user_id, code)
        else:
            await query.answer("Unknown language.", show_alert=True)

    elif action == "choose":
        await query.answer()
        db_user = await loop.run_in_executor(None, lambda: get_or_create_user(user_id))
        lang = get_user_language(db_user, user_id)
        current_name = ALL_LANGUAGES.get(lang, "English")
        text = (
            f"🌍 <b>Choose Your Language</b>\n\n"
            f"Current: {current_name}\n\n"
            f"Free users: English, Spanish, French\n"
            f"💎 VIP: All {len(ALL_LANGUAGES)} languages"
        )
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=_language_keyboard(db_user, user_id),
            )
        except Exception:
            pass
        log.info("Lang detect → choose language: user=%s", user_id)

    else:
        await query.answer()
