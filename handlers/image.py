"""
FundzAiBot — AI image generation handler.
/image <prompt> or via inline menu → style selection → prompt.
Admin bypasses all limits. Feature flags respected.
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.database import (
    get_or_create_user, can_use_image, increment_image,
    save_image, log_error, check_and_fix_vip_expiry, get_credits,
)
from services.image_service import generate_image
from services.ai_service import enhance_prompt
from utils.helpers import sanitise_prompt
from utils.keyboards import image_styles_menu, main_menu, back_to_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)

# Temporary per-user state: {user_id: {"style": ...}}
_pending: dict[int, dict] = {}


async def image_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/image [prompt] — If prompt given, use default style; else show style picker."""
    user = update.effective_user
    if not user:
        return

    uid   = user.id
    admin = is_admin(uid)

    log.info("[IMAGE] Command received: user=%s args=%s", uid, context.args)

    # ── Maintenance mode ───────────────────────────────────────────────────────
    if FEATURE_FLAGS["maintenance_mode"] and not admin:
        await update.effective_message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
            parse_mode="HTML",
        )
        return

    # ── Feature flag: images disabled ─────────────────────────────────────────
    if not FEATURE_FLAGS["image_enabled"] and not admin:
        await update.effective_message.reply_text(
            "🎨 <b>Image generation is temporarily disabled.</b>\n\nCheck back soon!",
            parse_mode="HTML",
        )
        return

    try:
        loop = asyncio.get_running_loop()
        log.info("[IMAGE] STAGE 1 — loading user: user=%s", uid)
        db_user = await loop.run_in_executor(
            None,
            lambda: get_or_create_user(uid, first_name=user.first_name or ""),
        )

        if db_user.get("is_banned"):
            await update.effective_message.reply_text("🚫 You have been banned.")
            return

        log.info("[IMAGE] STAGE 2 — checking credits: user=%s", uid)
        is_vip = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)

        allowed, reason = await loop.run_in_executor(None, can_use_image, uid, is_vip)
        if not allowed:
            await update.effective_message.reply_text(
                f"❌ <b>{reason}</b>\n\n"
                "💡 Earn more:\n• /referral — invite friends (+2 images each)\n• Upgrade to 💎 VIP",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
            return

        if context.args:
            prompt = sanitise_prompt(" ".join(context.args))
            await _run_generation(update, uid, prompt, "realistic", db_user, is_vip, admin)
        else:
            await update.effective_message.reply_text(
                "🎨 <b>Image Generation</b>\n\nFirst, choose a style:",
                parse_mode="HTML",
                reply_markup=image_styles_menu(),
            )

    except Exception as exc:
        log.error("[IMAGE] image_command_handler crash: user=%s error=%s", uid, exc, exc_info=True)
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>Image generation failed to start.</b>\n\n"
                "Please try again in a moment.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception:
            pass


async def _run_generation(
    update: Update,
    user_id: int,
    prompt: str,
    style: str,
    db_user: dict,
    is_vip: bool = False,
    admin: bool = False,
) -> None:
    """Internal: enhance prompt, call image service, send result."""
    from config.settings import FREE_DAILY_IMAGE, VIP_DAILY_IMAGE
    msg  = update.effective_message
    loop = asyncio.get_running_loop()

    log.info("[IMAGE] STAGE 3 — starting generation: user=%s style=%s prompt=%.60s", user_id, style, prompt)

    loading = None
    try:
        loading = await msg.reply_text(
            f"🎨 <i>Generating your <b>{style}</b> image…</i>\n"
            "⏳ This takes 20–40 seconds. Hang tight!",
            parse_mode="HTML",
        )
        log.info("[IMAGE] STAGE 3 — loading message sent: user=%s", user_id)

        # ── STAGE 4: Enhance prompt with AI ───────────────────────────────────
        log.info("[IMAGE] STAGE 4 — enhancing prompt: user=%s", user_id)
        enhanced = prompt
        try:
            enhanced = await loop.run_in_executor(None, enhance_prompt, prompt)
            log.info("[IMAGE] STAGE 4 — prompt enhanced: user=%s", user_id)
        except Exception as exc:
            log.warning("[IMAGE] STAGE 4 — prompt enhancement failed (using original): %s", exc)

        # ── STAGE 5: Generate image ────────────────────────────────────────────
        log.info("[IMAGE] STAGE 5 — calling image provider: user=%s", user_id)
        image_buf = await loop.run_in_executor(None, generate_image, enhanced, style)
        log.info("[IMAGE] STAGE 5 — provider returned: user=%s result=%s",
                 user_id, "bytes" if image_buf else "None")

        try:
            await loading.delete()
        except Exception:
            pass
        loading = None

        if image_buf:
            # ── STAGE 6: Save usage record ─────────────────────────────────────
            log.info("[IMAGE] STAGE 6 — saving usage: user=%s", user_id)
            await loop.run_in_executor(None, increment_image, user_id)
            await loop.run_in_executor(
                None,
                lambda: save_image(user_id, prompt, style, "stabilityai/stable-diffusion-xl-base-1.0"),
            )

            credits = await loop.run_in_executor(None, get_credits, user_id)
            used  = credits.get("image_today", 0)
            limit = 999999 if admin else (VIP_DAILY_IMAGE if is_vip else FREE_DAILY_IMAGE)
            bonus = 0 if admin else credits.get("bonus_image", 0)

            caption = (
                f"🎨 <b>Your Image</b>\n\n"
                f"📝 <b>Prompt:</b> {prompt[:200]}\n"
                f"🎭 <b>Style:</b> {style.capitalize()}\n"
                + (f"📊 <b>Used:</b> {used}/{limit + bonus} today" if not admin else "📊 <b>Admin — unlimited</b>")
            )
            reply_markup = admin_main_menu() if admin else main_menu()

            # ── STAGE 7: Send photo ────────────────────────────────────────────
            log.info("[IMAGE] STAGE 7 — sending photo: user=%s", user_id)
            try:
                await msg.reply_photo(
                    photo=image_buf,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                log.info("[IMAGE] DONE — sent: user=%s admin=%s style=%s", user_id, admin, style)
            except Exception as exc:
                log.error("[IMAGE] STAGE 7 — reply_photo failed: user=%s error=%s", user_id, exc, exc_info=True)
                await loop.run_in_executor(
                    None,
                    lambda: log_error("image_send_failed", str(exc)[:500], user_id=user_id),
                )
                try:
                    await msg.reply_text(
                        "⚠️ <b>Image was generated but failed to send.</b>\n\n"
                        "This can happen due to a temporary Telegram issue. "
                        "Please try generating your image again.\n\n"
                        "💡 If it keeps failing, try a different style.",
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                except Exception as fallback_exc:
                    log.error("[IMAGE] STAGE 7 — fallback text also failed: %s", fallback_exc)
        else:
            log.error("[IMAGE] STAGE 5 — all image providers returned None: user=%s", user_id)
            await loop.run_in_executor(
                None,
                lambda: log_error("image_generation_failed", "All image providers returned None", user_id=user_id),
            )
            await msg.reply_text(
                "❌ <b>Image generation failed.</b>\n\n"
                "Our image server is temporarily busy. Please try again in a moment.\n\n"
                "💡 Tips:\n"
                "• Try a simpler or shorter prompt\n"
                "• Try a different style\n"
                "• Wait 30 seconds and retry",
                parse_mode="HTML",
                reply_markup=admin_main_menu() if admin else main_menu(),
            )

    except Exception as exc:
        log.error("[IMAGE] _run_generation crash: user=%s error=%s", user_id, exc, exc_info=True)
        if loading:
            try:
                await loading.delete()
            except Exception:
                pass
        try:
            await loop.run_in_executor(
                None,
                lambda: log_error("image_run_generation_crash", str(exc)[:500], user_id=user_id),
            )
        except Exception:
            pass
        try:
            await msg.reply_text(
                "❌ <b>Image generation encountered an unexpected error.</b>\n\n"
                "Please try again in a moment.",
                parse_mode="HTML",
                reply_markup=admin_main_menu() if admin else main_menu(),
            )
        except Exception as final_exc:
            log.error("[IMAGE] Could not send error fallback: %s", final_exc)


async def handle_image_style_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, style: str
) -> None:
    """Called from callback handler when user picks an image style."""
    user  = update.effective_user
    query = update.callback_query
    await query.answer()
    _pending[user.id] = {"style": style}
    await query.edit_message_text(
        f"✅ <b>Style:</b> {style.capitalize()}\n\n"
        "Now send me your image description as a text message:",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )


async def handle_image_prompt_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Called when a user sends text while in image-prompt-waiting state."""
    user = update.effective_user
    if not user:
        return
    pending = _pending.pop(user.id, None)
    if not pending:
        return
    prompt = sanitise_prompt(update.effective_message.text or "")
    if not prompt:
        return
    loop    = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(None, get_or_create_user, user.id)
    admin   = is_admin(user.id)
    is_vip  = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)
    await _run_generation(update, user.id, prompt, pending["style"], db_user, is_vip, admin)
