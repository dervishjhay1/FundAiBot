"""
FundAiBot — /style handler.
"""

from telegram import Update
from telegram.ext import ContextTypes

from utils.keyboards import ai_styles_menu
from utils.logger import get_logger

log = get_logger(__name__)


async def style_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🎭 <b>Choose an AI Style</b>\n\n"
        "Pick the personality for your AI assistant:\n\n"
        "🧠 <b>Default</b> — Balanced, helpful, friendly\n"
        "📚 <b>Teacher</b> — Step-by-step with examples\n"
        "😂 <b>Comedian</b> — Witty and entertaining\n"
        "🔬 <b>Scientist</b> — Precise and evidence-based\n"
        "📝 <b>Writer</b> — Creative and eloquent\n"
        "💼 <b>Business</b> — Concise and professional\n"
        "🧑‍💻 <b>Coder</b> — Code-focused with explanations\n"
        "🎭 <b>Creative</b> — Imaginative and inspiring",
        parse_mode="HTML",
        reply_markup=ai_styles_menu(),
    )
