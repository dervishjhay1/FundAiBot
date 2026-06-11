"""
FundzAiBot — AI image retouching handler.
Users send a photo → bot shows retouch options → user picks mode → bot returns enhanced image.

Modes: enhance, beautify, upscale, artistic, brighten.
Credits: consumes 1 image credit per retouch (same as image generation).
Admins: unlimited, no credit check.
"""

import asyncio
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS, FREE_DAILY_IMAGE, VIP_DAILY_IMAGE
from services.database import (
    get_or_create_user, can_use_image, increment_image, log_error,
    check_and_fix_vip_expiry, get_credits,
)
from utils.keyboards import main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)

# Store pending retouch jobs per user: {user_id: file_id}
_pending_retouch: dict[int, str] = {}


def retouch_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Enhance",  callback_data="retouch:enhance"),
            InlineKeyboardButton("💄 Beautify", callback_data="retouch:beautify"),
        ],
        [
            InlineKeyboardButton("🔍 Upscale",  callback_data="retouch:upscale"),
            InlineKeyboardButton("🎨 Artistic", callback_data="retouch:artistic"),
        ],
        [
            InlineKeyboardButton("🔆 Brighten", callback_data="retouch:brighten"),
        ],
        [
            InlineKeyboardButton("❌ Cancel",   callback_data="retouch:cancel"),
        ],
    ])


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when a user sends a photo.
    Saves the best-quality file_id and shows retouch options.
    """
    user    = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    uid   = user.id
    admin = is_admin(uid)

    if FEATURE_FLAGS.get("maintenance_mode") and not admin:
        await message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
            parse_mode="HTML",
        )
        return

    if not FEATURE_FLAGS.get("image_enabled") and not admin:
        await message.reply_text(
            "🎨 <b>Image features are temporarily disabled.</b>\n\nCheck back soon!",
            parse_mode="HTML",
        )
        return

    loop    = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(
        None, lambda: get_or_create_user(uid, first_name=user.first_name or "")
    )
    if db_user.get("is_banned"):
        await message.reply_text("🚫 You have been banned.")
        return

    is_vip = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)
    allowed, reason = await loop.run_in_executor(None, can_use_image, uid, is_vip)
    if not allowed:
        await message.reply_text(
            f"❌ <b>{reason}</b>\n\n"
            "💡 Earn more credits:\n• /referral — invite friends\n• 💎 Upgrade to VIP",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    # Pick the highest-resolution version
    photos  = message.photo
    best    = max(photos, key=lambda p: p.file_size) if photos else None
    if not best:
        return

    _pending_retouch[uid] = best.file_id

    credits  = await loop.run_in_executor(None, get_credits, uid)
    used     = credits.get("image_today", 0)
    limit    = 999999 if admin else (VIP_DAILY_IMAGE if is_vip else FREE_DAILY_IMAGE)
    bonus    = 0 if admin else credits.get("bonus_image", 0)

    await message.reply_text(
        "🖼️ <b>AI Photo Retouching</b>\n\n"
        "Your photo is ready. Choose a retouching style:\n\n"
        "✨ <b>Enhance</b> — sharpen &amp; improve quality\n"
        "💄 <b>Beautify</b> — portrait enhancement\n"
        "🔍 <b>Upscale</b> — super-resolution detail boost\n"
        "🎨 <b>Artistic</b> — convert to painting style\n"
        "🔆 <b>Brighten</b> — fix exposure &amp; colours\n\n"
        + (f"📊 Credits used today: <b>{used}/{limit + bonus}</b>" if not admin else "📊 <b>Admin — unlimited</b>"),
        parse_mode="HTML",
        reply_markup=retouch_mode_keyboard(),
    )
    log.info("Retouch options shown: user=%s file_id=%s", uid, best.file_id)


async def handle_retouch_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str
) -> None:
    """
    Called from callback_handler when user picks a retouch mode.
    Downloads the photo, retouches it, sends the result.
    """
    query = update.callback_query
    user  = query.from_user
    uid   = user.id
    await query.answer()

    if mode == "cancel":
        _pending_retouch.pop(uid, None)
        try:
            await query.edit_message_text(
                "❌ Retouching cancelled.",
                reply_markup=main_menu() if not is_admin(uid) else admin_main_menu(),
            )
        except Exception:
            pass
        return

    file_id = _pending_retouch.pop(uid, None)
    if not file_id:
        await query.edit_message_text(
            "⚠️ No photo found. Please send your photo again.",
            reply_markup=main_menu(),
        )
        return

    mode_labels = {
        "enhance":  "✨ Enhance",
        "beautify": "💄 Beautify",
        "upscale":  "🔍 Upscale",
        "artistic": "🎨 Artistic",
        "brighten": "🔆 Brighten",
    }
    label = mode_labels.get(mode, mode.capitalize())

    try:
        await query.edit_message_text(
            f"🎨 <i>Retouching your photo with <b>{label}</b> mode…</i>\n"
            "⏳ This takes 20–40 seconds. Please wait!",
            parse_mode="HTML",
        )
    except Exception:
        pass

    loop    = asyncio.get_running_loop()
    admin   = is_admin(uid)
    db_user = await loop.run_in_executor(None, lambda: get_or_create_user(uid))
    is_vip  = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)

    allowed, reason = await loop.run_in_executor(None, can_use_image, uid, is_vip)
    if not allowed:
        await context.bot.send_message(
            uid,
            f"❌ <b>{reason}</b>\n\nUpgrade to 💎 VIP for more credits.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    # Download the Telegram file
    try:
        tg_file   = await context.bot.get_file(file_id)
        buf       = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        img_bytes = buf.read()
    except Exception as exc:
        log.error("Could not download Telegram photo for retouch: %s", exc)
        await context.bot.send_message(
            uid,
            "❌ Could not download your photo. Please try again.",
            reply_markup=main_menu(),
        )
        return

    # Retouch via HuggingFace
    from services.retouch_service import retouch_image
    result_buf = await loop.run_in_executor(None, retouch_image, img_bytes, mode)

    if result_buf:
        await loop.run_in_executor(None, increment_image, uid)
        credits  = await loop.run_in_executor(None, get_credits, uid)
        used     = credits.get("image_today", 0)
        limit    = 999999 if admin else (VIP_DAILY_IMAGE if is_vip else FREE_DAILY_IMAGE)
        bonus    = 0 if admin else credits.get("bonus_image", 0)

        caption = (
            f"🖼️ <b>Retouched Photo</b>\n\n"
            f"🎨 <b>Mode:</b> {label}\n"
            + (f"📊 <b>Used:</b> {used}/{limit + bonus} today" if not admin else "📊 <b>Admin — unlimited</b>")
        )
        await context.bot.send_photo(
            uid,
            photo=result_buf,
            caption=caption,
            parse_mode="HTML",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )
        log.info("Retouch sent: user=%s mode=%s", uid, mode)
    else:
        await loop.run_in_executor(
            None,
            lambda: log_error("retouch_failed", f"mode={mode} file_id={file_id}", user_id=uid),
        )
        await context.bot.send_message(
            uid,
            "❌ <b>Retouching failed.</b>\n\n"
            "The AI model may be loading (cold start).\n"
            "Please try again in ~60 seconds, or try a different mode.\n\n"
            "💡 Make sure <code>HUGGINGFACE_API_KEY</code> is set and valid.",
            parse_mode="HTML",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )
