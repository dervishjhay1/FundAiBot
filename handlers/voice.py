"""
FundzAiBot — Voice & audio message handler.

Workflow:
  1. User sends voice note or audio file
  2. Bot downloads the audio bytes from Telegram
  3. Gemini 1.5 Flash transcribes the audio
  4. Transcription is shown to user
  5. Transcription is fed into the AI chat pipeline for a full response
  6. Credit usage counted (same as /chat)

Requires: GEMINI_API_KEY in Railway env vars (for transcription).
Falls back gracefully with a clear error if Gemini key is missing.

Only active in PRIVATE chats — group voice messages are ignored
(groups use /ai command or @mention for AI interaction).
"""

import asyncio
import html
import io

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.voice_service import transcribe_voice, mime_for_extension
from services.ai_service import get_ai_response
from services.database import (
    get_or_create_user, can_use_chat, increment_chat,
    save_message, get_conversation, check_and_fix_vip_expiry,
    log_error,
)
from utils.helpers import chunk_text
from utils.keyboards import main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)

_MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MB safety cap


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle voice notes and audio files in private chats.
    Transcribes with Gemini → feeds into AI chat → returns full response.
    """
    user    = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    uid   = user.id
    admin = is_admin(uid)

    # Feature flag check
    if not FEATURE_FLAGS.get("voice_enabled", True):
        await message.reply_text(
            "🎙️ Voice transcription is currently disabled. Use text messages instead.",
        )
        return

    # Maintenance mode
    if FEATURE_FLAGS["maintenance_mode"] and not admin:
        await message.reply_text("🚧 Bot is under maintenance. Please try again shortly.")
        return

    # Determine audio source
    voice = message.voice
    audio = message.audio
    file_obj = voice or audio
    if not file_obj:
        return

    # Initial status
    status_msg = await message.reply_text(
        "🎙️ <i>Downloading voice message…</i>",
        parse_mode="HTML",
    )

    loop = asyncio.get_running_loop()

    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        # ── Download audio from Telegram ─────────────────────────────────────
        tg_file  = await context.bot.get_file(file_obj.file_id)
        buf      = io.BytesIO()
        await tg_file.download_to_memory(buf)
        audio_bytes = buf.getvalue()

        if len(audio_bytes) > _MAX_AUDIO_BYTES:
            await status_msg.edit_text(
                "⚠️ Audio file too large (max 20 MB). Please send a shorter voice message."
            )
            return

        # Determine MIME type
        if voice:
            mime = "audio/ogg"
        else:
            ext  = (getattr(file_obj, "file_name", None) or "").rsplit(".", 1)[-1]
            mime = mime_for_extension(ext) if ext else (file_obj.mime_type or "audio/mpeg")

        await status_msg.edit_text(
            "🎙️ <i>Transcribing your voice message…</i>",
            parse_mode="HTML",
        )

        # ── Transcribe ────────────────────────────────────────────────────────
        transcription = await loop.run_in_executor(
            None, transcribe_voice, audio_bytes, mime
        )

        if transcription.startswith("⚠️"):
            await status_msg.edit_text(transcription, parse_mode="HTML")
            return

        # ── Load user from DB ─────────────────────────────────────────────────
        db_user = await loop.run_in_executor(
            None,
            lambda: get_or_create_user(
                uid,
                first_name=user.first_name or "",
                last_name=user.last_name   or "",
                username=user.username     or "",
            ),
        )

        if db_user.get("is_banned"):
            await status_msg.edit_text("🚫 You have been banned from using FundzAiBot.")
            return

        # ── Credit check ──────────────────────────────────────────────────────
        is_vip = True if admin else await loop.run_in_executor(
            None, check_and_fix_vip_expiry, db_user
        )
        allowed, reason = await loop.run_in_executor(None, can_use_chat, uid, is_vip)

        if not allowed:
            await status_msg.edit_text(
                f"🎙️ <b>Transcription:</b>\n<i>{html.escape(transcription[:300])}</i>\n\n"
                f"❌ <b>{html.escape(reason)}</b>\n\n"
                "💡 Earn credits: /referral  •  Upgrade: /subscribe",
                parse_mode="HTML",
            )
            return

        # ── Show transcription + thinking indicator ───────────────────────────
        await status_msg.edit_text(
            f"🎙️ <b>You said:</b>\n<i>{html.escape(transcription[:300])}</i>"
            + ("…" if len(transcription) > 300 else "")
            + "\n\n💭 <i>Thinking…</i>",
            parse_mode="HTML",
        )
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        # ── Load conversation history ─────────────────────────────────────────
        history = await loop.run_in_executor(None, get_conversation, uid, 15)
        messages_for_ai = history + [{"role": "user", "content": transcription}]

        # ── AI response ───────────────────────────────────────────────────────
        response, provider = await loop.run_in_executor(
            None, get_ai_response, messages_for_ai
        )

        if not response or not response.strip():
            response = "⚠️ AI returned an empty response. Please try again."

        # ── Persist ───────────────────────────────────────────────────────────
        await loop.run_in_executor(None, save_message, uid, "user",      transcription)
        await loop.run_in_executor(None, save_message, uid, "assistant", response)
        await loop.run_in_executor(None, increment_chat, uid)

        # ── Send response ─────────────────────────────────────────────────────
        try:
            await status_msg.delete()
        except Exception:
            pass

        reply_markup = admin_main_menu() if admin else main_menu()
        header       = f"🎙️ <b>You said:</b> <i>{html.escape(transcription[:200])}</i>"
        if len(transcription) > 200:
            header += "…"
        full_response = f"{header}\n\n{response}"
        chunks        = chunk_text(full_response, size=4000)

        for i, chunk in enumerate(chunks):
            await message.reply_text(
                chunk,
                parse_mode="HTML",
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )

        log.info("[VOICE] Done: user=%s provider=%s chars=%d", uid, provider, len(response))

    except Exception as exc:
        log.error("[VOICE] Unhandled error: user=%s %s", uid, exc, exc_info=True)
        try:
            await loop.run_in_executor(
                None, lambda: log_error("voice_handler", str(exc)[:500], user_id=uid)
            )
        except Exception:
            pass
        try:
            await status_msg.edit_text(
                "⚠️ <b>Voice processing failed.</b> Please try again or send a text message.",
                parse_mode="HTML",
            )
        except Exception:
            pass
