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

<b>💬 AI Chat & Q&A:</b>
/chat      — Persistent AI conversation (with memory)
/ask       — Quick one-shot question (no memory)
/clear     — Clear your conversation history

<b>🧑‍💻 Code Assistant (Replit AI mode):</b>
/code      — Generate, explain, or debug code
<code>/code Python function to sort a dict by value</code>
<code>/code Debug: why does this crash?</code>

<b>📝 Text Tools:</b>
/summarize — Summarize text or a replied-to message
/translate — Translate to any language
<code>/translate Spanish Good morning, how are you?</code>
<code>/translate French</code>  (reply to a message)

<b>🔍 Vision (Gemini):</b>
/analyze   — Analyze a photo (reply or send with caption)
<code>/analyze What does the text say?</code>
<code>/analyze Describe this image in detail</code>

<b>🎨 Image Generation:</b>
/image     — Generate an AI image from a prompt
<code>/image a cyberpunk city at night, neon lights</code>

<b>🤖 AI Models:</b>
/model     — Switch between GPT-4o, Claude, Gemini & more
/style     — Change AI personality (8 modes)

<b>🎭 8 AI Styles:</b>
🧠 Default · 📚 Teacher · 😂 Comedian · 🔬 Scientist
📝 Writer · 💼 Business · 🧑‍💻 Coder · 🎭 Creative

<b>👤 Your Account:</b>
/start     — Main menu
/profile   — Your stats &amp; credits
/stats     — Usage statistics
/referral  — Referral link &amp; rewards
/history   — Image generation history
/language  — Change bot language
/subscribe — ⭐ VIP plans
/streak    — Daily chat streak

<b>💳 Free Daily Limits:</b>
• 30 AI chat messages / day
• 5 image generations / day
• Resets at midnight UTC

<b>💎 VIP Benefits:</b>
• 500 chats + 50 images / day (Basic)
• 2000 chats + priority (Pro)
• Unlimited + custom AI (Elite)

<b>🔗 Earn Bonus Credits:</b>
/referral → +10 chats &amp; +2 images per friend invited!
"""

ABOUT_TEXT = """
<b>ℹ️ About {bot}</b>  <code>v{version}</code>

<b>{bot}</b> is a premium all-in-one AI platform inside Telegram — the power of ChatGPT, Gemini, Replit AI, and Claude, combined in one bot.

<b>🧠 AI Language Models:</b>
• <b>GPT-4o / GPT-4o Mini</b> — OpenAI via OpenRouter
• <b>Claude 3.5 Sonnet / Haiku</b> — Anthropic via OpenRouter
• <b>Gemini Flash 1.5</b> — Google via OpenRouter
• <b>Mistral 7B / Llama 3.1</b> — Free models via OpenRouter
• <b>DeepSeek Chat</b> — Cost-efficient via OpenRouter
• <b>Gemini Pro</b> — Google (direct fallback)
• <b>Mistral 7B</b> — HuggingFace (final fallback)

<b>👁️ Vision (Image Understanding):</b>
• <b>Gemini Vision</b> — analyze any photo with /analyze

<b>🎨 Image Generation:</b>
• <b>Stable Diffusion XL</b> via HuggingFace
• 6 artistic styles supported

<b>✅ Platform Features:</b>
• Multi-model AI chat with memory
• One-shot Q&amp;A (/ask)
• Code generation &amp; debugging (/code)
• Text summarization (/summarize)
• Real-time translation (/translate)
• Image analysis — Gemini Vision (/analyze)
• AI model switcher (/model)
• 8 AI personality modes (/style)
• Prompt enhancement system
• Daily credit wallet
• Referral reward system
• VIP subscription tiers (Telegram Stars)
• Full admin dashboard
• Multi-language support (8 languages)
• Supabase-powered persistent storage
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
