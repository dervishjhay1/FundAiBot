"""
FundzAiBot — Extended AI command handlers.

/ask <question>             — One-shot Q&A (no memory, stateless, fast)
/code <request>             — Code generation & explanation (Replit AI / Copilot mode)
/summarize [text]           — Summarize text or a replied-to message
/translate <lang> <text>    — Translate to any language
/analyze [question]         — Analyze a photo with Gemini Vision (reply to image)
/model                      — View and switch the active AI model
/testbroadcast              — Admin: preview the active announcement in your DM
"""

import asyncio
import html
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS, GEMINI_API_KEY
from services.ai_service import get_ai_response
from services.database import (
    get_or_create_user, can_use_chat, increment_chat,
    check_and_fix_vip_expiry,
)
from utils.helpers import chunk_text, sanitise_prompt
from utils.keyboards import main_menu, admin_main_menu, back_to_menu
from utils.logger import get_logger

log = get_logger(__name__)


# ── Model catalog ──────────────────────────────────────────────────────────────
# (model_id, display_label, description)
# All model_ids are OpenRouter paths — so they fall through to OpenRouter provider.
# Gemini/HuggingFace remain the automatic fallback when OpenRouter fails.

MODEL_CATALOG = [
    ("openai/gpt-4o-mini",                   "⚡ GPT-4o Mini",        "Fast & smart (default)"),
    ("openai/gpt-4o",                         "🧠 GPT-4o",             "Most capable GPT-4"),
    ("anthropic/claude-3-haiku",              "🟣 Claude 3 Haiku",     "Fast Claude model"),
    ("anthropic/claude-3.5-sonnet",           "💜 Claude 3.5 Sonnet",  "Best Claude — great reasoning"),
    ("google/gemini-flash-1.5",               "🔵 Gemini Flash 1.5",   "Google's fast model"),
    ("mistralai/mistral-7b-instruct:free",    "🔴 Mistral 7B",         "Free — good quality"),
    ("meta-llama/llama-3.1-8b-instruct:free", "🦙 Llama 3.1 8B",      "Free Meta model"),
    ("deepseek/deepseek-chat",                "🌊 DeepSeek Chat",      "Cost-efficient alternative"),
]

_MODEL_KEY_SET: set[str] = {m[0] for m in MODEL_CATALOG}

# In-memory model preference per user (session-level; resets on bot restart)
_USER_MODELS: dict[int, str] = {}


# ── System prompts ─────────────────────────────────────────────────────────────

_CODE_SYSTEM = (
    "You are an expert programming assistant — think Replit AI, GitHub Copilot, and "
    "ChatGPT Code Interpreter combined.\n\n"
    "Rules:\n"
    "1. Always wrap code in triple-backtick blocks with the language tag (```python, ```js, etc.)\n"
    "2. Explain what the code does and why\n"
    "3. Follow best practices and idiomatic style for the language\n"
    "4. Mention edge cases or potential bugs\n"
    "5. Offer one actionable improvement tip when relevant\n\n"
    "Be concise but thorough. Prefer correct, working code over lengthy theory."
)

_SUMMARIZE_SYSTEM = (
    "You are an expert summarizer. Given any text, respond with exactly this structure:\n\n"
    "🔹 TL;DR: One clear sentence.\n"
    "🔹 Key Points:\n  • Bullet 1\n  • Bullet 2\n  • Bullet 3 (etc.)\n"
    "🔹 Notable Details: Any critical nuances worth preserving.\n\n"
    "Be accurate, concise, and preserve the original meaning. No padding."
)

_TRANSLATE_SYSTEM = (
    "You are a professional translator. "
    "Translate the given text to the requested language, preserving tone, "
    "formatting, and meaning exactly. "
    "Output ONLY the translated text — no commentary, no explanation."
)


# ── Shared access guard ────────────────────────────────────────────────────────

async def _check_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[bool, dict, bool]:
    """
    Validate feature flags, ban status, and daily chat credit.
    Returns (access_granted, db_user, is_vip).
    Sends the appropriate error message to the user if blocked.
    """
    user    = update.effective_user
    message = update.effective_message
    if not user or not message:
        return False, {}, False

    uid   = user.id
    admin = is_admin(uid)

    if FEATURE_FLAGS.get("maintenance_mode") and not admin:
        await message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
            parse_mode="HTML",
        )
        return False, {}, False

    if not FEATURE_FLAGS.get("chat_enabled", True) and not admin:
        await message.reply_text(
            "💬 <b>AI Chat is temporarily disabled.</b>\n\nCheck back soon!",
            parse_mode="HTML",
        )
        return False, {}, False

    loop    = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(
        None,
        lambda: get_or_create_user(
            uid,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            username=user.username or "",
        ),
    )

    if db_user.get("is_banned"):
        await message.reply_text("🚫 You have been banned from using FundzAiBot.")
        return False, db_user, False

    is_vip = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)

    allowed, reason = await loop.run_in_executor(None, can_use_chat, uid, is_vip)
    if not allowed:
        await message.reply_text(
            f"❌ <b>{html.escape(reason)}</b>\n\n"
            "💡 Earn more credits:\n"
            "• Invite friends with /referral (+10 chats)\n"
            "• Upgrade to 💎 VIP with /subscribe",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return False, db_user, is_vip

    return True, db_user, is_vip


# ── Helper: send chunked AI response ──────────────────────────────────────────

async def _send_response(
    message,
    response: str,
    admin: bool,
    thinking=None,
) -> None:
    if thinking:
        try:
            await thinking.delete()
        except Exception:
            pass
    reply_markup = admin_main_menu() if admin else main_menu()
    chunks = chunk_text(response, 4000)
    for i, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            reply_markup=reply_markup if i == len(chunks) - 1 else None,
        )


# ── /ask ──────────────────────────────────────────────────────────────────────

async def ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ask <question> — One-shot Q&A, no conversation memory."""
    user    = update.effective_user
    message = update.effective_message

    if not context.args:
        await message.reply_text(
            "❓ <b>Quick Question Mode</b>\n\n"
            "Usage: <code>/ask &lt;your question&gt;</code>\n\n"
            "Examples:\n"
            "<code>/ask What is quantum computing?</code>\n"
            "<code>/ask Explain recursion in simple terms</code>\n"
            "<code>/ask Who wrote Pride and Prejudice?</code>\n\n"
            "<i>Unlike /chat, each /ask is independent — no memory between questions.</i>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    allowed, _, is_vip = await _check_access(update, context)
    if not allowed:
        return

    uid      = user.id
    admin    = is_admin(uid)
    question = sanitise_prompt(" ".join(context.args))
    model    = _USER_MODELS.get(uid, "")
    loop     = asyncio.get_running_loop()

    log.info("[ASK] STAGE 3 — sending thinking indicator: user=%s", uid)
    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    thinking = await message.reply_text("💭 <i>Thinking…</i>", parse_mode="HTML")

    msgs = [
        {"role": "system", "content": "You are FundzAiBot, a helpful, accurate, and concise AI assistant."},
        {"role": "user",   "content": question},
    ]
    log.info("[ASK] STAGE 4 — calling AI provider: user=%s model=%s", uid, model or "auto")
    response, provider = await loop.run_in_executor(
        None, lambda: get_ai_response(msgs, model=model)
    )
    log.info("[ASK] STAGE 5 — AI response: user=%s provider=%s len=%d", uid, provider, len(response))
    if not response or not response.strip():
        log.error("[ASK] STAGE 5 — empty AI response: user=%s", uid)
        response = "⚠️ AI returned an empty response. Please try again."
    await loop.run_in_executor(None, increment_chat, uid)
    log.info("[ASK] STAGE 6 — sending reply: user=%s", uid)
    await _send_response(message, response, admin, thinking)
    log.info("[ASK] DONE: user=%s admin=%s provider=%s len=%d", uid, admin, provider, len(response))


# ── /code ─────────────────────────────────────────────────────────────────────

async def code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/code <request> — Code generation, debugging, and explanation (Replit AI / Copilot mode)."""
    user    = update.effective_user
    message = update.effective_message

    if not context.args:
        await message.reply_text(
            "🧑‍💻 <b>Code Mode — Replit AI Style</b>\n\n"
            "Usage: <code>/code &lt;what you need&gt;</code>\n\n"
            "Examples:\n"
            "<code>/code Python function to reverse a linked list</code>\n"
            "<code>/code Explain this SQL: SELECT * FROM users WHERE age &gt; 18</code>\n"
            "<code>/code Debug this JS: if (x = 5) { console.log('yes') }</code>\n"
            "<code>/code FastAPI endpoint that accepts a JSON body and returns a UUID</code>\n"
            "<code>/code React hook for debounced search input</code>\n\n"
            "<i>Expert code assistant — explains, debugs, and builds code for you.</i>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    allowed, _, is_vip = await _check_access(update, context)
    if not allowed:
        return

    uid     = user.id
    admin   = is_admin(uid)
    request = sanitise_prompt(" ".join(context.args))
    # Code mode defaults to GPT-4o-mini for precision; overridden by user's /model selection
    model   = _USER_MODELS.get(uid, "openai/gpt-4o-mini")
    loop    = asyncio.get_running_loop()

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    thinking = await message.reply_text("🧑‍💻 <i>Writing code…</i>", parse_mode="HTML")

    msgs = [
        {"role": "system", "content": _CODE_SYSTEM},
        {"role": "user",   "content": request},
    ]
    response, provider = await loop.run_in_executor(
        None, lambda: get_ai_response(msgs, model=model)
    )
    await loop.run_in_executor(None, increment_chat, uid)
    await _send_response(message, response, admin, thinking)
    log.info("/code user=%s provider=%s len=%d", uid, provider, len(response))


# ── /summarize ────────────────────────────────────────────────────────────────

async def summarize_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/summarize [text] or reply to a message — produces TL;DR + key points."""
    user    = update.effective_user
    message = update.effective_message

    text_to_summarize = ""
    if context.args:
        text_to_summarize = " ".join(context.args)
    elif message.reply_to_message:
        replied = message.reply_to_message
        text_to_summarize = (replied.text or replied.caption or "").strip()

    if len(text_to_summarize.strip()) < 30:
        await message.reply_text(
            "📝 <b>Summarize Mode</b>\n\n"
            "Two ways to use it:\n"
            "1. <code>/summarize &lt;paste text here&gt;</code>\n"
            "2. <b>Reply</b> to any long message with <code>/summarize</code>\n\n"
            "<i>Great for articles, long explanations, or meeting notes!</i>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    allowed, _, is_vip = await _check_access(update, context)
    if not allowed:
        return

    uid   = user.id
    admin = is_admin(uid)
    model = _USER_MODELS.get(uid, "")
    loop  = asyncio.get_running_loop()

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    thinking = await message.reply_text("📝 <i>Summarizing…</i>", parse_mode="HTML")

    msgs = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {"role": "user",   "content": f"Summarize this:\n\n{text_to_summarize[:5000]}"},
    ]
    response, provider = await loop.run_in_executor(
        None, lambda: get_ai_response(msgs, model=model)
    )
    await loop.run_in_executor(None, increment_chat, uid)
    await _send_response(message, response, admin, thinking)
    log.info("/summarize user=%s provider=%s source_len=%d", uid, provider, len(text_to_summarize))


# ── /translate ────────────────────────────────────────────────────────────────

async def translate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/translate <language> <text>  or reply to a message with /translate <language>."""
    user    = update.effective_user
    message = update.effective_message

    args            = context.args or []
    target_lang     = args[0] if args else ""
    text_to_translate = ""

    if len(args) > 1:
        text_to_translate = " ".join(args[1:])
    elif message.reply_to_message:
        replied = message.reply_to_message
        text_to_translate = (replied.text or replied.caption or "").strip()

    if not target_lang or not text_to_translate:
        await message.reply_text(
            "🌐 <b>Translate Mode</b>\n\n"
            "Usage:\n"
            "• <code>/translate Spanish Hello, how are you?</code>\n"
            "• <code>/translate French</code>  (reply to a message)\n"
            "• <code>/translate Arabic Thank you very much</code>\n"
            "• <code>/translate Chinese Good morning!</code>\n\n"
            "Supports any language — just type its name in English.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    allowed, _, is_vip = await _check_access(update, context)
    if not allowed:
        return

    uid   = user.id
    admin = is_admin(uid)
    model = _USER_MODELS.get(uid, "")
    loop  = asyncio.get_running_loop()

    log.info("[TRANSLATE] STAGE 3 — sending indicator: user=%s target=%s", uid, target_lang)
    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    thinking = await message.reply_text(
        f"🌐 <i>Translating to {html.escape(target_lang)}…</i>", parse_mode="HTML"
    )

    msgs = [
        {"role": "system", "content": _TRANSLATE_SYSTEM},
        {"role": "user",   "content": f"Translate to {target_lang}:\n\n{text_to_translate[:4000]}"},
    ]
    log.info("[TRANSLATE] STAGE 4 — calling AI provider: user=%s model=%s", uid, model or "auto")
    response, provider = await loop.run_in_executor(
        None, lambda: get_ai_response(msgs, model=model)
    )
    log.info("[TRANSLATE] STAGE 5 — AI response: user=%s provider=%s len=%d", uid, provider, len(response))
    if not response or not response.strip():
        log.error("[TRANSLATE] STAGE 5 — empty AI response: user=%s", uid)
        response = "⚠️ Translation failed — AI returned an empty response. Please try again."
    await loop.run_in_executor(None, increment_chat, uid)
    log.info("[TRANSLATE] STAGE 6 — sending reply: user=%s", uid)
    await _send_response(message, response, admin, thinking)
    log.info("[TRANSLATE] DONE: user=%s target=%s provider=%s len=%d", uid, target_lang, provider, len(response))


# ── /analyze (Gemini Vision) ──────────────────────────────────────────────────

async def analyze_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /analyze [question] — analyze a photo using Gemini Vision.
    Works when user replies to a photo, or sends a photo with /analyze as caption.
    """
    user    = update.effective_user
    message = update.effective_message

    # Locate the photo: current message or replied-to message
    photo_msg = None
    if message.photo:
        photo_msg = message
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_msg = message.reply_to_message

    if not photo_msg:
        await message.reply_text(
            "🔍 <b>Image Analysis — Gemini Vision</b>\n\n"
            "Two ways to use it:\n"
            "1️⃣ Send a photo with <code>/analyze</code> as the caption\n"
            "2️⃣ Reply to any photo with <code>/analyze [question]</code>\n\n"
            "Examples:\n"
            "<code>/analyze What brand is this?</code>\n"
            "<code>/analyze What does the text say?</code>\n"
            "<code>/analyze Describe this image in detail</code>\n"
            "<code>/analyze What emotion does this convey?</code>\n\n"
            "<i>Powered by Google Gemini Vision.</i>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    if not GEMINI_API_KEY:
        await message.reply_text(
            "⚠️ <b>Image analysis requires GEMINI_API_KEY.</b>\n\n"
            "Add <code>GEMINI_API_KEY</code> to your Railway environment variables.",
            parse_mode="HTML",
        )
        return

    allowed, _, is_vip = await _check_access(update, context)
    if not allowed:
        return

    uid      = user.id
    admin    = is_admin(uid)
    question = " ".join(context.args).strip() if context.args else "Describe this image in detail."
    loop     = asyncio.get_running_loop()

    await context.bot.send_chat_action(chat_id=message.chat_id, action="upload_photo")
    thinking = await message.reply_text("🔍 <i>Analysing image with Gemini Vision…</i>", parse_mode="HTML")

    # Download the highest-resolution photo available
    try:
        photo     = photo_msg.photo[-1]
        tg_file   = await context.bot.get_file(photo.file_id)
        buf       = io.BytesIO()
        await tg_file.download_to_memory(buf)
        image_bytes = buf.getvalue()
    except Exception as exc:
        try:
            await thinking.delete()
        except Exception:
            pass
        await message.reply_text(
            f"❌ Could not download the image: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )
        log.error("/analyze download failed: %s", exc)
        return

    from services.ai_service import analyze_image_gemini
    response = await loop.run_in_executor(
        None, lambda: analyze_image_gemini(image_bytes, "image/jpeg", question)
    )
    await loop.run_in_executor(None, increment_chat, uid)
    await _send_response(message, response, admin, thinking)
    log.info("/analyze user=%s question=%s", uid, question[:60])


# ── /model ────────────────────────────────────────────────────────────────────

def _model_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{label} — {desc}", callback_data=f"setmodel:{mid}")]
        for mid, label, desc in MODEL_CATALOG
    ]
    rows.append([InlineKeyboardButton("« Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


async def model_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/model — View the current AI model and switch to any other model."""
    user = update.effective_user
    if not user:
        return

    uid     = user.id
    current = _USER_MODELS.get(uid, "")
    label   = next((m[1] for m in MODEL_CATALOG if m[0] == current), "Auto (default)")

    await update.effective_message.reply_text(
        f"🤖 <b>AI Model Selection</b>\n\n"
        f"Active model: <b>{html.escape(label)}</b>\n\n"
        "Choose a model — takes effect immediately for all commands:\n"
        "/chat · /ask · /code · /summarize · /translate\n\n"
        "<i>💡 GPT-4o Mini is the best balance of speed + quality.\n"
        "Claude 3.5 Sonnet excels at reasoning and nuanced writing.</i>",
        parse_mode="HTML",
        reply_markup=_model_keyboard(),
    )


async def handle_setmodel_callback(query, uid: int) -> None:
    """Handle setmodel:<model_id> inline callback from /model keyboard."""
    model_id = (query.data or "").split(":", 1)[1]
    if model_id not in _MODEL_KEY_SET:
        await query.answer("❌ Unknown model.", show_alert=True)
        return

    _USER_MODELS[uid] = model_id
    label = next((m[1] for m in MODEL_CATALOG if m[0] == model_id), model_id)

    await query.answer(f"✅ Switched to {label}")
    await query.edit_message_text(
        f"✅ <b>Model updated!</b>\n\n"
        f"Now using: <b>{html.escape(label)}</b>\n\n"
        "All AI commands (/chat, /ask, /code, /summarize, /translate) will use this model.\n\n"
        "Send /model to switch again.",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    log.info("Model set: user=%s model=%s", uid, model_id)


# ── /testbroadcast (admin only) ───────────────────────────────────────────────

async def testbroadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /testbroadcast — Preview the active announcement in the admin's own DM.
    Shows exactly what users will see before you push it live.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    from services.database import get_active_announcement
    from handlers.announcements import format_announcement_card, SUPPORT_URL
    from utils.keyboards import announcement_keyboard

    loop = asyncio.get_running_loop()
    ann  = await loop.run_in_executor(None, get_active_announcement)

    if not ann:
        await update.effective_message.reply_text(
            "📭 <b>No active announcement to preview.</b>\n\n"
            "Create one with <code>/pin &lt;message&gt;</code> first.",
            parse_mode="HTML",
        )
        return

    card      = format_announcement_card(ann.get("message", ""))
    photo_url = ann.get("photo_url") or ""

    await update.effective_message.reply_text(
        "👁️ <b>Announcement Preview</b>\n"
        "<i>This is exactly how it appears to users:</i>",
        parse_mode="HTML",
    )

    try:
        if photo_url:
            await context.bot.send_photo(
                chat_id=user.id,
                photo=photo_url,
                caption=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        else:
            await context.bot.send_message(
                chat_id=user.id,
                text=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        await update.effective_message.reply_text(
            "✅ <b>Preview sent!</b>\n\n"
            "Happy with it? Push live with:\n"
            "• /announce_channel — channel only\n"
            "• /announce_group — group only\n"
            "• /announce_both — both at once",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Preview failed: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )
    log.info("/testbroadcast admin=%s", user.id)
