"""
FundzAiBot — Document & file analysis handler.

Supported formats (plain text extraction):
  Text:  .txt .md .py .js .ts .json .xml .yaml .yml .csv .html .css .sh .sql
  Code:  Any text-based source file

The user sends a file (Document) in a private chat. The bot:
  1. Validates file type and size
  2. Downloads and decodes the content
  3. Feeds it to the AI with a smart analysis prompt
  4. Returns a structured analysis with credits deducted

PDF support: handled via plain text extraction if the PDF contains extractable text;
skipped gracefully if it's image-based.
"""

import asyncio
import html
import io
import mimetypes

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
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

_MAX_FILE_BYTES = 4 * 1024 * 1024  # 4 MB
_MAX_CONTENT_CHARS = 12_000         # chars sent to AI

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".xml", ".yaml", ".yml", ".csv",
    ".html", ".htm", ".css", ".sh", ".bash",
    ".sql", ".rs", ".go", ".rb", ".php",
    ".c", ".cpp", ".h", ".java", ".kt",
    ".swift", ".r", ".m", ".tex", ".log",
    ".env", ".toml", ".ini", ".cfg", ".conf",
}

_ANALYSIS_SYSTEM = """You are an expert document analyst and code reviewer.
When given file content, you MUST:
1. Identify the document/file type and purpose
2. Provide a clear, structured summary
3. For CODE: highlight logic, potential bugs, improvements, security issues
4. For DATA (JSON/CSV/XML): describe the schema and key statistics
5. For TEXT/MD: extract key points, themes, and actionable insights
6. End with 2-3 specific recommendations

Format your response with clear sections using emoji headers.
Be concise but thorough. Use code blocks for code snippets."""


def _extract_text_from_pdf(content: bytes) -> str | None:
    """Try to extract text from PDF. Returns None if not possible."""
    try:
        import io as _io
        # Try pypdf / PyPDF2 if installed
        try:
            from pypdf import PdfReader
            reader = PdfReader(_io.BytesIO(content))
            pages = []
            for page in reader.pages[:15]:  # cap at 15 pages
                text = page.extract_text() or ""
                pages.append(text)
            return "\n".join(pages)
        except ImportError:
            pass
        # Try PyPDF2
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(_io.BytesIO(content))
            pages = []
            for page in reader.pages[:15]:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except ImportError:
            pass
        return None
    except Exception as exc:
        log.debug("PDF extraction failed: %s", exc)
        return None


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document/file messages in private chats — extract and analyse with AI."""
    user    = update.effective_user
    message = update.effective_message
    if not user or not message or not message.document:
        return

    uid   = user.id
    admin = is_admin(uid)

    if FEATURE_FLAGS["maintenance_mode"] and not admin:
        await message.reply_text("🚧 Bot under maintenance. Try again shortly.")
        return

    doc      = message.document
    filename = doc.file_name or "unknown"
    ext      = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    is_pdf   = ext == ".pdf" or (doc.mime_type or "").lower() == "application/pdf"
    is_text  = ext in _TEXT_EXTENSIONS

    if not is_text and not is_pdf:
        await message.reply_text(
            "📄 <b>Unsupported file type.</b>\n\n"
            "I can analyse:\n"
            "• Text files: .txt .md .csv .json .xml .yaml\n"
            "• Code: .py .js .ts .html .css .sql .go .rs + more\n"
            "• Documents: .pdf (text-based)\n\n"
            "Send one of these and I'll give you a full AI analysis.",
            parse_mode="HTML",
        )
        return

    if doc.file_size and doc.file_size > _MAX_FILE_BYTES:
        await message.reply_text(
            f"❌ File too large ({doc.file_size // 1024} KB). Maximum is 4 MB."
        )
        return

    status = await message.reply_text(
        f"📄 <i>Reading {html.escape(filename)}…</i>",
        parse_mode="HTML",
    )

    loop = asyncio.get_running_loop()

    try:
        # ── Download ──────────────────────────────────────────────────────────
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        tg_file  = await context.bot.get_file(doc.file_id)
        buf      = io.BytesIO()
        await tg_file.download_to_memory(buf)
        raw_bytes = buf.getvalue()

        # ── Extract text ──────────────────────────────────────────────────────
        file_text: str | None = None

        if is_pdf:
            file_text = await loop.run_in_executor(None, _extract_text_from_pdf, raw_bytes)
            if not file_text or not file_text.strip():
                await status.edit_text(
                    "❌ This PDF appears to be image-based and cannot be read as text.\n\n"
                    "Try sending a text-based PDF or copy-paste the content directly."
                )
                return
        else:
            for encoding in ("utf-8", "latin-1", "cp1252"):
                try:
                    file_text = raw_bytes.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if file_text is None:
                await status.edit_text(
                    "❌ Could not read this file — it may be binary or use an unsupported encoding."
                )
                return

        file_text = file_text.strip()
        if not file_text:
            await status.edit_text("❌ The file appears to be empty.")
            return

        char_count = len(file_text)
        truncated  = char_count > _MAX_CONTENT_CHARS
        snippet    = file_text[:_MAX_CONTENT_CHARS]

        # ── Credit check ──────────────────────────────────────────────────────
        db_user = await loop.run_in_executor(
            None, lambda: get_or_create_user(
                uid, first_name=user.first_name or "", username=user.username or ""
            ),
        )
        if db_user.get("is_banned"):
            await status.edit_text("🚫 You are banned.")
            return

        is_vip  = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)
        allowed, reason = await loop.run_in_executor(None, can_use_chat, uid, is_vip)
        if not allowed:
            await status.edit_text(
                f"❌ <b>{html.escape(reason)}</b>\n\nUpgrade to 💎 VIP for unlimited analysis.",
                parse_mode="HTML",
            )
            return

        # ── Build AI prompt ───────────────────────────────────────────────────
        trunc_note = f"\n\n[File truncated to {_MAX_CONTENT_CHARS} chars — original: {char_count} chars]" if truncated else ""
        user_extra = message.caption or ""
        if user_extra:
            user_extra = f"\n\nUser instruction: {user_extra}"

        prompt = (
            f"File: {filename}\n"
            f"Size: {char_count} characters\n"
            f"---\n{snippet}{trunc_note}\n---"
            f"{user_extra}"
        )

        await status.edit_text(
            f"📄 <b>{html.escape(filename)}</b> ({char_count:,} chars)"
            + (" ⚠️ <i>truncated to 12k for analysis</i>" if truncated else "")
            + "\n\n🤖 <i>Analysing with AI…</i>",
            parse_mode="HTML",
        )
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

        # ── AI call ───────────────────────────────────────────────────────────
        history  = await loop.run_in_executor(None, get_conversation, uid, 5)
        messages = (
            [{"role": "system", "content": _ANALYSIS_SYSTEM}]
            + history
            + [{"role": "user", "content": prompt}]
        )
        response, provider = await loop.run_in_executor(None, get_ai_response, messages)

        if not response or not response.strip():
            response = "⚠️ AI returned an empty response. Please try again."

        # ── Save & send ───────────────────────────────────────────────────────
        await loop.run_in_executor(None, save_message, uid, "user",
                                   f"[Document: {filename}]")
        await loop.run_in_executor(None, save_message, uid, "assistant", response)
        await loop.run_in_executor(None, increment_chat, uid)

        try:
            await status.delete()
        except Exception:
            pass

        reply_markup = admin_main_menu() if admin else main_menu()
        header       = f"📄 <b>Analysis: {html.escape(filename)}</b>\n\n"
        chunks       = chunk_text(header + response, size=4000)
        for i, chunk in enumerate(chunks):
            await message.reply_text(
                chunk,
                parse_mode="HTML",
                reply_markup=reply_markup if i == len(chunks) - 1 else None,
            )

        log.info("[DOC] Done: user=%s file=%s chars=%d provider=%s", uid, filename, char_count, provider)

    except Exception as exc:
        log.error("[DOC] Error: user=%s %s", uid, exc, exc_info=True)
        try:
            await loop.run_in_executor(None, lambda: log_error("document_handler", str(exc)[:500], user_id=uid))
        except Exception:
            pass
        try:
            await status.edit_text("⚠️ Document analysis failed. Please try again.")
        except Exception:
            pass
