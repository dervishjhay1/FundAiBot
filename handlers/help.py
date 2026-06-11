"""
FundAiBot — /help and /about handlers.
"""

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import BOT_NAME, BOT_VERSION
from utils.keyboards import back_to_menu
from utils.logger import get_logger

log = get_logger(__name__)

HELP_TEXT = """
<b>🤖 {bot} — Help Guide</b>

<b>Core Commands:</b>
/start    — Main menu
/chat     — AI conversation
/image    — Generate an image
/style    — Change AI personality
/profile  — Your stats & credits
/referral — Referral link & rewards
/history  — Image generation history
/clear    — Clear chat memory
/help     — This help guide
/about    — About {bot}

<b>💬 AI Chat:</b>
Just send any message — I'll reply intelligently!
Or tap 🤖 AI Chat from the menu.

<b>🎨 Image Generation:</b>
<code>/image a sunset over the ocean, dramatic lighting</code>
Or tap 🎨 Image Gen and choose a style first.

<b>🎭 AI Styles (8 modes):</b>
🧠 Default · 📚 Teacher · 😂 Comedian · 🔬 Scientist
📝 Writer · 💼 Business · 🧑‍💻 Coder · 🎭 Creative

<b>💳 Daily Limits (Free):</b>
• 30 AI chat messages / day
• 5 image generations / day
• Resets at midnight UTC

<b>💎 VIP Benefits:</b>
• 500 chats + 50 images / day (Basic)
• Unlimited + priority (Pro / Elite)

<b>🔗 Earn Bonus Credits:</b>
Invite friends with /referral → +10 chats & +2 images per referral!
"""

ABOUT_TEXT = """
<b>ℹ️ About {bot}</b>  <code>v{version}</code>

<b>FundAiBot</b> is a premium AI assistant platform built inside Telegram, powered by the world's most advanced language and image models.

<b>🧠 AI Chat Providers:</b>
• <b>OpenRouter</b> — GPT-4, Claude, Mixtral & more
• <b>Google Gemini</b> — Gemini Pro
• <b>HuggingFace</b> — Mistral 7B (fallback)

<b>🎨 Image Generation:</b>
• <b>Stable Diffusion XL</b> via HuggingFace
• 6 artistic styles supported

<b>✅ Platform Features:</b>
• Smart conversation memory
• 8 AI personality modes
• Prompt enhancement system
• Daily credit wallet
• Referral reward system
• VIP subscription tiers
• Full admin dashboard
• Supabase-powered storage
• Railway-deployed, GitHub-synced

<b>Built for scale. Designed for you.</b>
"""


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    await msg.reply_text(HELP_TEXT.format(bot=BOT_NAME), parse_mode="HTML", reply_markup=back_to_menu())
    log.info("/help user=%s", update.effective_user.id if update.effective_user else "?")


async def about_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    await msg.reply_text(ABOUT_TEXT.format(bot=BOT_NAME, version=BOT_VERSION), parse_mode="HTML", reply_markup=back_to_menu())
