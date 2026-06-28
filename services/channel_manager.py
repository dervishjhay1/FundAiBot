"""
FundzAiBot — Channel Manager (TestAudit role)

Manages the official Telegram Channel on behalf of the company.

Publishing cadence: approximately one high-quality post every 2–3 hours,
distributed naturally across the active day (07:00–22:00 UTC) — targeting
5–7 posts per day.

Every draft is scored for quality before publishing. Posts below the
quality threshold are skipped. Category rotation is enforced so the feed
stays varied.

Content categories:
  • AI education            • FundzAiBot tutorials
  • Productivity insights   • Security awareness
  • Industry inspiration    • Feature highlights
  • Community highlights    • Telegram tips

Posts are logged to Supabase to avoid repetition.
All posting goes through the Decision Engine — confidence 0.91 (operational).
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_POSTS_TARGET_MIN    = 5      # minimum posts per day
_POSTS_TARGET_MAX    = 7      # maximum posts per day (1 post per ~2h over 15h window)
_MIN_POST_GAP_MINS   = 120    # at least 2 hours between posts
_ACTIVE_HOURS        = (7, 22) # only post between 07:00 and 22:00 UTC
_CHECK_INTERVAL      = 1800   # check every 30 min
_QUALITY_THRESHOLD   = 50     # minimum quality score (0-100) — below this, skip the post

_running: bool = False
_thread:  threading.Thread | None = None

_last_post_time:      float = 0.0
_posts_today:         int   = 0
_posts_today_date:    str   = ""
_last_category_posted: str  = ""   # track last category to enforce rotation

# ── Content library ───────────────────────────────────────────────────────────

_CONTENT_LIBRARY: list[dict] = [
    # AI Education
    {
        "category": "ai_education",
        "title": "What is a Token?",
        "text": (
            "🧠 <b>AI Basics: What is a Token?</b>\n\n"
            "When you type a message to an AI, it doesn't read your words the way you do. "
            "It breaks them into <b>tokens</b> — chunks of text that might be a word, part of a word, "
            "or even a single character.\n\n"
            "• 'Hello' = 1 token\n"
            "• 'Extraordinary' = 4 tokens\n"
            "• A typical paragraph = ~100 tokens\n\n"
            "AI models have a <b>context window</b> — the maximum tokens they can 'see' at once. "
            "GPT-4o handles ~128k tokens. Gemini 1.5 Pro handles up to 1 million tokens.\n\n"
            "This is why long conversations can sometimes lose earlier context — the window fills up!\n\n"
            f"📌 <i>Powered by {BOT_NAME} — Your AI Intelligence Platform</i>"
        ),
    },
    {
        "category": "ai_education",
        "title": "How AI Hallucinations Happen",
        "text": (
            "⚠️ <b>Why AI Sometimes Makes Things Up</b>\n\n"
            "AI 'hallucinations' happen because language models are trained to predict the most "
            "statistically likely text — not to verify facts.\n\n"
            "If the model has seen many articles where 'Einstein won the Nobel Prize for relativity,' "
            "it might generate that confidently — even though it's wrong (he won it for the "
            "photoelectric effect).\n\n"
            "<b>How to reduce hallucinations:</b>\n"
            "✅ Ask the AI to cite sources\n"
            "✅ Use 'I don't know' prompts: 'If you're not sure, say so'\n"
            "✅ Cross-reference important facts\n"
            "✅ Prefer AI with web search enabled\n\n"
            f"📌 <i>{BOT_NAME} — Helping you use AI smarter every day</i>"
        ),
    },
    {
        "category": "ai_education",
        "title": "The Difference Between AI Models",
        "text": (
            "🤖 <b>GPT-4o vs Gemini vs Claude — What's the Difference?</b>\n\n"
            "All three are large language models, but each has strengths:\n\n"
            "🟢 <b>GPT-4o (OpenAI)</b>\n"
            "Best at: Coding, instructions, structured output\n"
            "Strength: Extremely versatile, widely tested\n\n"
            "🔵 <b>Gemini 1.5 (Google)</b>\n"
            "Best at: Huge context windows, multimodal (image + text)\n"
            "Strength: Can process entire books in one pass\n\n"
            "🟣 <b>Claude 3.5 (Anthropic)</b>\n"
            "Best at: Long-form writing, nuanced analysis\n"
            "Strength: Follows complex instructions very precisely\n\n"
            f"With {BOT_NAME}, you can switch between models anytime using /model 🔄\n\n"
            f"📌 <i>{BOT_NAME} — Multi-model AI at your fingertips</i>"
        ),
    },
    # FundzAiBot Tutorials
    {
        "category": "tutorial",
        "title": "How to use /chat vs /ask",
        "text": (
            f"📚 <b>{BOT_NAME} Tutorial: /chat vs /ask</b>\n\n"
            "Two of the most-used commands — but they work differently:\n\n"
            "💬 <b>/chat</b>\n"
            "• Keeps conversation memory\n"
            "• Remembers what you said earlier\n"
            "• Best for: ongoing projects, multi-step tasks\n"
            "• Clear memory anytime with /clear\n\n"
            "⚡ <b>/ask</b>\n"
            "• One-shot question, no memory\n"
            "• Faster and lighter on credits\n"
            "• Best for: quick lookups, one-off questions\n\n"
            "<b>Pro tip:</b> Use /ask for research questions and /chat when you're working through "
            "a complex problem that needs follow-up questions.\n\n"
            f"📌 <i>{BOT_NAME} — Every command has a purpose</i>"
        ),
    },
    {
        "category": "tutorial",
        "title": "Generate AI Images",
        "text": (
            f"🎨 <b>{BOT_NAME} Tutorial: Generate AI Images</b>\n\n"
            "Creating AI images is easier than you think:\n\n"
            "1️⃣ Send <code>/image</code>\n"
            "2️⃣ Describe what you want\n"
            "3️⃣ Get your image in seconds\n\n"
            "<b>Tips for better images:</b>\n"
            "• Be specific: 'a sunset over mountains, photorealistic, golden hour lighting'\n"
            "• Include style: 'oil painting', 'digital art', 'minimalist'\n"
            "• Add quality markers: 'highly detailed', '4K', 'professional'\n\n"
            "<b>Bad prompt:</b> 'a dog'\n"
            "<b>Good prompt:</b> 'a golden retriever sitting in autumn leaves, warm light, "
            "professional photography, shallow depth of field'\n\n"
            f"📌 <i>{BOT_NAME} — Powered by Stable Diffusion XL</i>"
        ),
    },
    {
        "category": "tutorial",
        "title": "AI Styles Guide",
        "text": (
            f"🎭 <b>{BOT_NAME} AI Styles — Full Guide</b>\n\n"
            "Change how the AI thinks and responds with /style:\n\n"
            "🎓 <b>Teacher</b> — Clear explanations, examples, step-by-step\n"
            "💼 <b>Professional</b> — Formal, business-ready, concise\n"
            "😎 <b>Friend</b> — Casual, warm, conversational\n"
            "🔬 <b>Scientist</b> — Data-driven, analytical, precise\n"
            "✍️ <b>Writer</b> — Creative, literary, expressive\n"
            "🧘 <b>Coach</b> — Motivational, supportive, action-focused\n"
            "😄 <b>Comedian</b> — Light, witty, entertaining\n"
            "🤔 <b>Philosopher</b> — Deep thinking, questioning, reflective\n\n"
            "The same question gives completely different answers in each style. Try it!\n\n"
            f"📌 <i>{BOT_NAME} — 8 personalities, infinite possibilities</i>"
        ),
    },
    # Productivity
    {
        "category": "productivity",
        "title": "The 5-Minute AI Workflow",
        "text": (
            "⚡ <b>The 5-Minute AI Morning Routine</b>\n\n"
            "Start every workday sharper with this 5-minute AI routine:\n\n"
            "1️⃣ <b>Minute 1</b>: Ask AI to summarize your top 3 priorities for today\n"
            "2️⃣ <b>Minute 2</b>: Get AI to draft your first important email\n"
            "3️⃣ <b>Minute 3</b>: Ask for 2 creative approaches to your biggest challenge\n"
            "4️⃣ <b>Minute 4</b>: Request a quick research summary on a relevant topic\n"
            "5️⃣ <b>Minute 5</b>: Have AI identify potential risks in your plan\n\n"
            "5 minutes → clearer thinking, better communication, smarter decisions.\n\n"
            f"Try it with {BOT_NAME} today. Start with /chat 👇\n\n"
            f"📌 <i>{BOT_NAME} — Your AI productivity partner</i>"
        ),
    },
    {
        "category": "productivity",
        "title": "Chain Prompting Technique",
        "text": (
            "🔗 <b>Chain Prompting — Get 10x Better AI Results</b>\n\n"
            "Instead of one big prompt, break complex tasks into a chain:\n\n"
            "<b>Step 1:</b> 'Analyze this problem: [problem]'\n"
            "<b>Step 2:</b> 'Based on that analysis, what are 3 solutions?'\n"
            "<b>Step 3:</b> 'For solution #2, create a detailed action plan'\n"
            "<b>Step 4:</b> 'Write a 1-page executive summary of this plan'\n\n"
            "Each step builds on the last. The AI stays focused and the quality "
            "compounds with each iteration.\n\n"
            "This is how professionals use AI — not one-shot prompts, but structured chains.\n\n"
            f"📌 <i>{BOT_NAME} — /chat keeps context for the entire chain</i>"
        ),
    },
    # Security
    {
        "category": "security",
        "title": "AI Phishing Awareness",
        "text": (
            "🔐 <b>AI-Powered Scams Are Getting Smarter</b>\n\n"
            "Scammers now use AI to:\n"
            "• Clone voices from 3-second audio clips\n"
            "• Generate perfectly written phishing emails with no typos\n"
            "• Create realistic fake images and videos (deepfakes)\n"
            "• Impersonate customer support agents in real-time chat\n\n"
            "<b>Your defense:</b>\n"
            "✅ Always verify urgent requests through a second channel\n"
            "✅ Be suspicious of unexpected urgency ('act NOW')\n"
            "✅ Voice ≠ identity — call back on the official number\n"
            "✅ Check URLs carefully — AI can spoof branding perfectly\n\n"
            "The best security is a healthy skepticism, even with 'obvious' messages.\n\n"
            f"📌 <i>{BOT_NAME} — Stay safe, stay smart</i>"
        ),
    },
    # Telegram Tips
    {
        "category": "telegram_tip",
        "title": "Telegram Power User Tips",
        "text": (
            "📱 <b>Telegram Power User Tips Most People Don't Know</b>\n\n"
            "🔹 <b>Saved Messages</b> — use it as a personal note-taking cloud\n"
            "🔹 <b>Schedule messages</b> — long-press the send button\n"
            "🔹 <b>Silent messages</b> — 🔕 icon to notify without sound\n"
            "🔹 <b>Quick reactions</b> — double-tap any message\n"
            "🔹 <b>Spoiler text</b> — wrap text in ||spoiler|| syntax\n"
            "🔹 <b>Message search</b> — search inside any chat instantly\n"
            "🔹 <b>Folders</b> — organize chats by topic or priority\n"
            "🔹 <b>Voice-to-text</b> — hold the mic icon and slide to record\n\n"
            "Telegram is more powerful than most people realize!\n\n"
            f"📌 <i>{BOT_NAME} — Built for Telegram power users</i>"
        ),
    },
    # Quotes
    {
        "category": "inspiration",
        "title": "AI Quote of the Day",
        "text": (
            "💬 <b>Quote of the Day</b>\n\n"
            "<i>'The question is not whether machines can think. "
            "The question is whether humans can.'</i>\n"
            "— B.F. Skinner\n\n"
            "In 2026, the most valuable skill isn't coding or design — it's knowing "
            "how to ask the right questions, whether to a human or an AI.\n\n"
            "The best prompt engineers aren't the ones who know the most syntax. "
            "They're the ones who can think most clearly about what they actually need.\n\n"
            f"📌 <i>{BOT_NAME} — Intelligence, amplified</i>"
        ),
    },
    {
        "category": "inspiration",
        "title": "What's Next in AI",
        "text": (
            "🔮 <b>What's Coming in AI — 2026 Outlook</b>\n\n"
            "The AI landscape is moving faster than ever:\n\n"
            "🟢 <b>Real-time voice AI</b> — conversations indistinguishable from humans\n"
            "🟢 <b>AI agents</b> — systems that browse, code, and act autonomously\n"
            "🟢 <b>Personalized models</b> — AI that learns your specific style\n"
            "🟡 <b>Multimodal reasoning</b> — seeing, hearing, and thinking together\n"
            "🟡 <b>On-device AI</b> — powerful models running entirely on your phone\n\n"
            "The gap between 'AI user' and 'AI power user' is widening every month.\n\n"
            "Which of these are you most excited about? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Staying ahead of the curve</i>"
        ),
    },
    {
        "category": "feature",
        "title": "VIP Benefits",
        "text": (
            f"⭐ <b>{BOT_NAME} VIP Plans — Is It Worth It?</b>\n\n"
            "Here's what VIP unlocks:\n\n"
            "🔹 <b>Basic VIP</b> (250 ⭐)\n"
            "  500 AI chats/day · 50 images/day\n\n"
            "🔹 <b>Pro VIP</b> (500 ⭐)\n"
            "  2,000 AI chats/day · 100 images/day\n\n"
            "🔹 <b>Elite VIP</b> (1,000 ⭐)\n"
            "  Unlimited AI chats · 200 images/day\n\n"
            "Free users get 30 chats + 5 images per day — enough to explore.\n"
            "Power users upgrading to Pro see a 67x daily capacity increase.\n\n"
            "Payment is via Telegram Stars — instant, secure, no credit card needed.\n\n"
            f"Upgrade with /subscribe · Questions? Just ask! 👇"
        ),
    },
    # ── FAQ ────────────────────────────────────────────────────────────────────
    {
        "category": "faq",
        "title": "FAQ: How many messages per day?",
        "text": (
            "❓ <b>FAQ: How many messages do I get per day?</b>\n\n"
            f"Your daily allowance on {BOT_NAME}:\n\n"
            "🆓 <b>Free:</b> 30 AI chats · 5 image generations\n"
            "⭐ <b>Basic VIP:</b> 500 chats · 50 images\n"
            "💎 <b>Pro VIP:</b> 2,000 chats · 100 images\n"
            "🚀 <b>Elite VIP:</b> Unlimited chats · 200 images\n\n"
            "All limits reset every day at midnight UTC.\n"
            "Upgrade anytime with <code>/subscribe</code> in the private bot chat.\n\n"
            "Referrals earn bonus credits — share your link with <code>/refer</code>.\n\n"
            f"📌 <i>{BOT_NAME} — Questions? Ask us in the community group</i>"
        ),
    },
    {
        "category": "faq",
        "title": "FAQ: Which AI model does FundzAiBot use?",
        "text": (
            "❓ <b>FAQ: Which AI model powers the bot?</b>\n\n"
            f"{BOT_NAME} uses a multi-model architecture:\n\n"
            "🟢 <b>Default:</b> Google Gemma 3 27B\n"
            "   Fast, free, and highly capable for everyday tasks\n\n"
            "🔵 <b>Vision:</b> Gemini 1.5 Flash\n"
            "   Powers image analysis, photo recognition, visual Q&A\n\n"
            "🟣 <b>Fallback chain:</b> Llama 3.3 70B → Mistral Small 24B\n"
            "   Switches automatically if the primary model is busy\n\n"
            "All models are available at no extra cost to free users.\n"
            "Use <code>/model</code> in private chat to see your current model.\n\n"
            f"📌 <i>{BOT_NAME} — Always using the best available model</i>"
        ),
    },
    {
        "category": "faq",
        "title": "FAQ: Is my data private?",
        "text": (
            "❓ <b>FAQ: Is my data private and secure?</b>\n\n"
            f"{BOT_NAME} is designed with your privacy in mind:\n\n"
            "✅ Conversations are NOT permanently stored\n"
            "✅ Chat history clears with <code>/clear</code>\n"
            "✅ We never sell your data to third parties\n"
            "✅ Your Telegram ID is used only to manage your account\n"
            "✅ All AI API calls are made server-side — your tokens stay private\n\n"
            "For maximum privacy during sensitive sessions, use <code>/clear</code> "
            "after each conversation.\n\n"
            f"📌 <i>{BOT_NAME} — Your privacy matters to us</i>"
        ),
    },
    # ── Ecosystem Updates ──────────────────────────────────────────────────────
    {
        "category": "ecosystem_update",
        "title": "AI Ecosystem 2026 Update",
        "text": (
            "🌐 <b>AI Ecosystem Update — 2026</b>\n\n"
            "The AI landscape is evolving faster than ever:\n\n"
            "📌 <b>OpenAI</b> — GPT-4o processes video and audio natively\n"
            "📌 <b>Google</b> — Gemini 1.5 handles multi-million token contexts\n"
            "📌 <b>Anthropic</b> — Claude 3.5 Sonnet leads coding benchmarks\n"
            "📌 <b>Meta</b> — Llama 3.3 is the strongest open-source model to date\n"
            "📌 <b>Mistral</b> — Compact models are closing the gap fast\n\n"
            "The trend: models are faster, cheaper, and more capable every quarter. "
            "Open-source is rapidly matching proprietary systems.\n\n"
            "What AI development has surprised you most this year? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Keeping you ahead of the curve</i>"
        ),
    },
    {
        "category": "ecosystem_update",
        "title": "AI Agents Are Here",
        "text": (
            "🤖 <b>The Age of AI Agents Has Arrived</b>\n\n"
            "2026 is the year AI stopped just talking and started <b>doing</b>.\n\n"
            "AI agents can now:\n"
            "🔹 Browse the web and gather real-time information\n"
            "🔹 Write, test, and deploy code autonomously\n"
            "🔹 Schedule meetings and send emails on your behalf\n"
            "🔹 Coordinate with other AI agents on complex multi-step tasks\n"
            "🔹 Learn your preferences and adapt over time\n\n"
            "The question isn't whether AI agents will change how we work — "
            "it's whether you'll be the one directing them.\n\n"
            "Which tasks would you most want an AI agent to handle? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Your AI operations partner</i>"
        ),
    },
    # ── Community Highlights ───────────────────────────────────────────────────
    {
        "category": "community_highlight",
        "title": "Community Spotlight",
        "text": (
            "🎉 <b>Community Spotlight</b>\n\n"
            f"The {BOT_NAME} community continues to grow — and the quality of "
            "questions, ideas, and conversations here is genuinely impressive.\n\n"
            "What's happening in the community:\n\n"
            "💬 Members exploring AI for content creation, automation, and research\n"
            "🚀 Feature requests being reviewed and added to our development roadmap\n"
            "🤝 Experienced members helping newcomers — exactly what this space is for\n\n"
            "A reminder: there are no bad questions here. If you're wondering "
            "about something, ask — someone will always be ready to help.\n\n"
            "What would you like to see more of in this community? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Built with this community, for this community</i>"
        ),
    },
    {
        "category": "community_highlight",
        "title": "Weekly Community Digest",
        "text": (
            "📊 <b>This Week in the Community</b>\n\n"
            f"A quick digest of what's been happening in the {BOT_NAME} ecosystem:\n\n"
            "🔸 <b>Top discussions:</b> Prompt engineering, AI model comparisons, automation\n"
            "🔸 <b>Most-used commands:</b> /chat, /image, /style — thousands of sessions daily\n"
            "🔸 <b>Trending topic:</b> Using AI agents for productivity and workflow automation\n"
            "🔸 <b>Coming up:</b> New features being built based on your feedback\n\n"
            "Every conversation in this group shapes what we build next. "
            "Your feedback directly influences the roadmap.\n\n"
            "What's the #1 improvement you'd love to see? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Community-driven AI platform</i>"
        ),
    },
    # ── Weekly Summary ─────────────────────────────────────────────────────────
    {
        "category": "weekly_summary",
        "title": "Weekly AI Roundup",
        "text": (
            "📅 <b>Weekly AI Roundup</b>\n\n"
            "Quick summary of what's been happening in the AI world:\n\n"
            "⚡ Model capabilities continue improving across all major providers\n"
            "⚡ AI image quality has reached near-photorealistic levels\n"
            "⚡ Voice AI is increasingly indistinguishable from human speech\n"
            "⚡ More businesses are deploying AI agents for routine workflows\n"
            "⚡ Open-source models are now competitive with proprietary systems\n\n"
            "The pace of progress is extraordinary. The best strategy: stay curious, "
            "keep experimenting, and use communities like this to stay sharp.\n\n"
            "What was your biggest AI-related win this week? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Your AI intelligence hub</i>"
        ),
    },
    # ── Announcements / Platform Updates ──────────────────────────────────────
    {
        "category": "announcement",
        "title": "Platform Status and Updates",
        "text": (
            f"📣 <b>{BOT_NAME} — Platform Update</b>\n\n"
            "Our systems are operating at full capacity. "
            "Here's what's been improved recently:\n\n"
            "✅ Response speed improvements across all AI models\n"
            "✅ Image generation queue optimised for faster delivery\n"
            "✅ Community Manager (TestAudit) upgraded with smarter conversation handling\n"
            "✅ Channel content quality gate improved — only high-value posts are published\n"
            "✅ Active discussion detection added — bot stays silent during live conversations\n\n"
            "Your experience and reliability are our top priorities. "
            "Report any issues directly in the community group.\n\n"
            "What improvements would you like to see next? 👇\n\n"
            f"📌 <i>{BOT_NAME} — Always improving for you</i>"
        ),
    },
]


# ── Content quality gate ──────────────────────────────────────────────────────

def _content_quality_score(text: str) -> int:
    """
    Score a draft post 0-100 before publishing.
    Checks length, structure, formatting, and engagement signals.
    Posts below _QUALITY_THRESHOLD are skipped.
    """
    score = 0
    word_count = len(text.split())

    # Length: 80-350 words ideal
    if 80 <= word_count <= 350:
        score += 30
    elif 50 <= word_count < 80 or 350 < word_count <= 500:
        score += 15

    # Has bold formatting (structured, not wall of text)
    if "<b>" in text:
        score += 20

    # Has a footer / brand tagline
    if "📌" in text or "— TestAudit" in text:
        score += 10

    # Has numbered steps or bullet points (educational structure)
    if any(s in text for s in ["1️⃣", "✅", "•\n", "• ", "1.\n", "2.\n"]):
        score += 15

    # Minimum content length beyond empty
    if len(text) > 200:
        score += 15

    # Has at least one emoji (engagement signal)
    if any(ord(c) > 0x1F300 for c in text):
        score += 10

    return min(score, 100)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick_content() -> dict | None:
    """Pick content that hasn't been posted recently (check DB log)."""
    used_titles = _get_recent_titles()
    available = [c for c in _CONTENT_LIBRARY if c["title"] not in used_titles]
    if not available:
        available = _CONTENT_LIBRARY  # all used — full reset
    return random.choice(available) if available else None


def _get_recent_titles(hours: int = 72) -> set[str]:
    """Fetch recently posted titles from Supabase to avoid repetition."""
    from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return set()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/channel_posts_log",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            },
            params={"posted_at": f"gte.{cutoff}", "select": "topic", "limit": "100"},
            timeout=(5, 10),
        )
        if r.status_code == 200:
            return {row.get("topic", "") for row in r.json()}
    except Exception as exc:
        log.debug("channel_manager._get_recent_titles: %s", exc)
    return set()


def _log_post(category: str, title: str, message_id: int | None = None) -> None:
    """Log a posted item to Supabase."""
    from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/channel_posts_log",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={"category": category, "topic": title, "message_id": message_id},
            timeout=(5, 10),
        )
    except Exception as exc:
        log.debug("channel_manager._log_post: %s", exc)


def _try_ai_content(category: str) -> dict | None:
    """Generate fresh content via AI for variety."""
    if not OPENROUTER_API_KEY:
        return None
    try:
        category_prompts = {
            "ai_education":       "Write an educational post explaining one specific AI concept in simple terms. Include a practical example the reader can try immediately.",
            "productivity":       "Write a practical, actionable productivity tip about using AI tools effectively in daily work. Be specific — generic advice is not useful.",
            "tutorial":           f"Write a short tutorial tip for {BOT_NAME} Telegram bot users. Be specific about which command to use and what to expect.",
            "security":           "Write a concise cybersecurity awareness tip relevant to AI and Telegram users. Give at least 2 specific, actionable steps the reader can take today.",
            "inspiration":        "Write an inspiring, thought-provoking observation about human-AI collaboration and the future of work. Avoid clichés — say something genuinely insightful.",
            "faq":                f"Write a clear, friendly answer to a common question about {BOT_NAME} or AI assistants. Format it as 'FAQ: [question]' with a direct, helpful answer.",
            "ecosystem_update":   "Write a brief, accurate update about a recent development in the AI ecosystem (models, tools, or industry trends). Be specific and factual.",
            "community_highlight": f"Write a warm community spotlight post celebrating the {BOT_NAME} community and encouraging participation. End with a genuine question.",
            "weekly_summary":     "Write a weekly roundup of notable AI trends and developments relevant to Telegram bot users and AI enthusiasts. Keep it concise and genuinely informative.",
            "announcement":       f"Write a professional platform status or improvement announcement for {BOT_NAME}. Focus on what benefits the user, not internal details.",
        }
        prompt = category_prompts.get(category, "Write an educational AI tip for a Telegram channel.")
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are TestAudit, the Content Manager for {BOT_NAME}, an AI-powered Telegram platform. "
                            "Your job: publish high-quality, genuinely useful content to the official channel.\n\n"
                            "Every post must meet these standards:\n"
                            "• 100-220 words — substantial but not bloated\n"
                            "• Clear headline and logical structure\n"
                            "• Accurate — never fabricate facts or statistics\n"
                            "• Professional but approachable — not corporate or cold\n"
                            "• Educational — the reader must learn something genuinely useful\n"
                            "• Ends with a clear CTA or engaging question\n"
                            "• Uses HTML: <b>bold</b> for key points, <code>code</code> for commands\n"
                            "• Starts with a relevant emoji and bold title\n"
                            f"• Always ends with: 📌 <i>{BOT_NAME} — [specific, relevant tagline]</i>\n\n"
                            "Never repeat generic AI platitudes. Make every post genuinely valuable."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 400,
                "temperature": 0.8,
            },
            timeout=25,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content and len(content) > 80:
                return {"category": category, "title": f"AI-{category}-{int(time.time())}", "text": content}
    except Exception as exc:
        log.debug("channel_manager._try_ai_content: %s", exc)
    return None


def _post_to_channel(text: str) -> int | None:
    """
    Send post EXCLUSIVELY to the channel.
    Uses services.messaging.send_channel_post() — routed to CHANNEL only.
    This function MUST NEVER send to the group or any private chat.
    """
    if not TELEGRAM_CHANNEL_ID:
        return None
    try:
        from services.messaging import send_channel_post
        result = send_channel_post(text)
        if result:
            msg_id = result.get("message_id")
            log.info("Channel Manager posted content (msg_id=%s)", msg_id)
            return msg_id
    except Exception as exc:
        log.warning("channel_manager._post_to_channel: %s", exc)
    return None


# ── Scheduling logic ──────────────────────────────────────────────────────────

def _should_post_now() -> bool:
    """Return True if it's time to post another channel item."""
    global _posts_today, _posts_today_date

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    if _posts_today_date != today:
        _posts_today      = 0
        _posts_today_date = today

    if _posts_today >= _POSTS_TARGET_MAX:
        return False

    hour = now.hour
    if hour < _ACTIVE_HOURS[0] or hour >= _ACTIVE_HOURS[1]:
        return False

    # Spread posts evenly across active hours
    active_hours = _ACTIVE_HOURS[1] - _ACTIVE_HOURS[0]  # 15 hours
    posts_per_hour = _POSTS_TARGET_MAX / active_hours    # ~1 post/hour

    elapsed_since_last = time.time() - _last_post_time
    min_gap = (60 / max(posts_per_hour, 1)) * 60  # seconds
    min_gap = max(min_gap, _MIN_POST_GAP_MINS * 60)

    return elapsed_since_last >= min_gap


def _do_post() -> None:
    """
    Execute one channel post with quality gate and category rotation.

    Flow:
    1. Decision engine approval
    2. Category rotation — avoid repeating the last category
    3. Content selection (AI-generated or static library)
    4. Quality scoring — skip posts below _QUALITY_THRESHOLD
    5. Publish + log
    """
    global _last_post_time, _posts_today, _last_category_posted

    from services.decision_engine import evaluate
    decision = evaluate(
        action_type="send_channel_post",
        title="Post educational content to channel",
        description=(
            "Scheduled channel content post — 1 post per 2-3 hours, "
            f"targeting {_POSTS_TARGET_MIN}-{_POSTS_TARGET_MAX} posts/day."
        ),
        payload={"channel_id": TELEGRAM_CHANNEL_ID},
        confidence=0.91,
        business_risk=False,
        irreversible=False,
    )

    if decision["decision"] != "auto":
        log.info("channel_manager: blocked by decision engine — %s", decision["reason"])
        return

    # ── Category rotation: pick a category different from the last one ────────
    all_categories = [
        "ai_education", "productivity", "tutorial",
        "security", "inspiration", "feature", "telegram_tip",
        "faq", "ecosystem_update", "community_highlight",
        "weekly_summary", "announcement",
    ]
    available_cats = [c for c in all_categories if c != _last_category_posted]
    if not available_cats:
        available_cats = all_categories
    cat = random.choice(available_cats)

    # ── Content selection: prefer AI-generated (40% chance), fall back static ─
    content: dict | None = None
    if OPENROUTER_API_KEY and random.random() < 0.4:
        content = _try_ai_content(cat)

    if not content:
        content = _pick_content()

    if not content:
        return

    # ── Quality gate: score the draft before publishing ───────────────────────
    score = _content_quality_score(content["text"])
    if score < _QUALITY_THRESHOLD:
        log.info(
            "channel_manager: skipped '%s' — quality score %d < %d threshold",
            content["title"], score, _QUALITY_THRESHOLD,
        )
        return

    # ── Publish ───────────────────────────────────────────────────────────────
    msg_id = _post_to_channel(content["text"])
    if msg_id is not None:
        _last_post_time      = time.time()
        _posts_today        += 1
        _last_category_posted = content["category"]
        _log_post(content["category"], content["title"], msg_id)

        log.info(
            "Channel Manager posted '%s' (cat=%s score=%d posts_today=%d)",
            content["title"], content["category"], score, _posts_today,
        )

        try:
            from services.testaudit_core import log_memory
            log_memory(
                "action_taken",
                f"Channel Manager posted: {content['title']}",
                detail={
                    "category":   content["category"],
                    "quality":    score,
                    "posts_today": _posts_today,
                },
                category="channel",
                confidence=0.91,
                outcome="resolved",
            )
        except Exception:
            pass


# ── Background loop ───────────────────────────────────────────────────────────

def _channel_loop() -> None:
    log.info("📢 Channel Manager started — targeting %d-%d posts/day",
             _POSTS_TARGET_MIN, _POSTS_TARGET_MAX)
    time.sleep(150)  # give bot time to start

    while _running:
        try:
            if TELEGRAM_CHANNEL_ID and _should_post_now():
                _do_post()
        except Exception as exc:
            log.error("channel_manager loop error: %s", exc)

        for _ in range(_CHECK_INTERVAL):
            if not _running:
                break
            time.sleep(1)


def start_channel_manager() -> None:
    global _running, _thread
    if _running:
        return
    if not TELEGRAM_CHANNEL_ID:
        log.warning("Channel Manager: TELEGRAM_CHANNEL_ID not set — skipping start")
        return
    _running = True
    _thread  = threading.Thread(target=_channel_loop, daemon=True, name="channel-mgr")
    _thread.start()
    log.info("✅ Channel Manager started (channel: %s)", TELEGRAM_CHANNEL_ID)


def stop_channel_manager() -> None:
    global _running
    _running = False
