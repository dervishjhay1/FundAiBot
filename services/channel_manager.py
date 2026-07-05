"""
FundzAiBot — Channel Manager (TestAudit role)

Manages the official Telegram Channel on behalf of the company.

Publishing cadence: one high-quality post every 30 minutes during active hours
(07:00–22:00 UTC). Targeting 10–20 posts per day. Posts slow naturally at night
(active-hours gate still applies).

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

# ── Channel Guard — phrases that must NEVER appear in published content ───────
_CHANNEL_GUARD_PHRASES: tuple[str, ...] = (
    "service interruption",
    "can't process that request",
    "cannot process that request",
    "experiencing a service",
    "restore full capability",
    "ai unavailable",
    "provider unavailable",
    "system is working to restore",
    "try again in a moment",
    "temporarily unavailable",
    "i'm currently experiencing",
    "i am currently experiencing",
    "api error",
    "connection failed",
    "request failed",
    "internal server error",
)


def _is_channel_safe(text: str) -> bool:
    """Return True only if the text is safe to publish to the public channel."""
    lower = text.lower()
    for phrase in _CHANNEL_GUARD_PHRASES:
        if phrase in lower:
            log.warning("Channel guard blocked post containing forbidden phrase: %r", phrase)
            return False
    return True


# ── Configuration ─────────────────────────────────────────────────────────────

_POSTS_TARGET_MIN    = 10      # minimum posts per day
_POSTS_TARGET_MAX    = 20      # maximum posts per day (1 post per ~2h over 15h window)
_MIN_POST_GAP_MINS   = 30    # at least 2 hours between posts
_ACTIVE_HOURS        = (7, 22) # only post between 07:00 and 22:00 UTC
_CHECK_INTERVAL      = 1800   # check every 30 min
_QUALITY_THRESHOLD   = 50     # minimum quality score (0-100) — below this, skip the post

_running: bool = False
_thread:  threading.Thread | None = None

_last_post_time:      float = 0.0
_posts_today:         int   = 0
_posts_today_date:    str   = ""
_last_category_posted: str  = ""   # track last category to enforce rotation
_post_counter:        int  = 0    # total posts sent this session (for product rotation)

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

    # Responsible AI
    {
        "category": "responsible_ai",
        "title": "Using AI Ethically",
        "text": (
            "🤝 <b>Using AI Responsibly — A Practical Guide</b>\n\n"
            "AI tools are powerful. With that power comes responsibility:\n\n"
            "✅ <b>Fact-check AI outputs</b> — especially for medical, legal, or financial decisions\n"
            "✅ <b>Don't share sensitive data</b> — no passwords, personal IDs, or private keys\n"
            "✅ <b>Attribute AI work</b> — be transparent when AI helped you create something\n"
            "✅ <b>Avoid bias amplification</b> — AI reflects training data, not truth\n"
            "✅ <b>Respect others</b> — don't use AI to create harmful or deceptive content\n\n"
            "Responsible AI use protects you, your work, and your community.\n\n"
            f"📌 <i>{BOT_NAME} — AI you can trust</i>"
        ),
    },
    # Bot guide
    {
        "category": "bot_guide",
        "title": "Getting Started Guide",
        "text": (
            f"🚀 <b>New to {BOT_NAME}? Start Here</b>\n\n"
            "Everything you need in 60 seconds:\n\n"
            "1️⃣ <b>/start</b> — activate the bot and see your dashboard\n"
            "2️⃣ <b>/chat</b> — start a conversation with memory\n"
            "3️⃣ <b>/ask</b> — one-shot question, no memory needed\n"
            "4️⃣ <b>/image</b> — generate AI images from text\n"
            "5️⃣ <b>/style</b> — switch between 8 AI personalities\n"
            "6️⃣ <b>/model</b> — choose GPT-4o, Gemini, or Claude\n"
            "7️⃣ <b>/vip</b> — upgrade for higher limits\n"
            "8️⃣ <b>/help</b> — full command reference\n\n"
            f"That's it. {BOT_NAME} is designed to be simple and powerful.\n\n"
            f"📌 <i>{BOT_NAME} — Start smart</i>"
        ),
    },
    # Success story
    {
        "category": "success_story",
        "title": "Community Win",
        "text": (
            "🏆 <b>Community Spotlight</b>\n\n"
            "This week a community member shared how they used AI to cut their "
            "weekly report writing from 3 hours to 20 minutes.\n\n"
            "The approach: feed the AI your raw notes, ask it to structure them "
            "as a professional report, then edit the output rather than writing from scratch.\n\n"
            "The key insight: <b>AI as editor, human as director.</b>\n\n"
            "What's a workflow you've transformed with AI? Share it — "
            "your experience might be the tip someone else needs today. 👇\n\n"
            f"📌 <i>{BOT_NAME} — Powered by the community</i>"
        ),
    },
    # Release notes
    {
        "category": "release_notes",
        "title": "Platform Updates",
        "text": (
            "📦 <b>Platform Updates</b>\n\n"
            f"{BOT_NAME} is continuously improving based on your feedback.\n\n"
            "Recent focus areas:\n"
            "• ⚡ Faster response times across all AI models\n"
            "• 🧠 Improved memory and context retention in /chat\n"
            "• 🎨 Enhanced image generation quality\n"
            "• 🔐 Stronger privacy controls for conversation data\n"
            "• 📊 More detailed usage stats in /credits\n\n"
            "Your feedback directly shapes what gets built. "
            "Drop feature requests or bug reports in this group.\n\n"
            f"📌 <i>{BOT_NAME} — Built by the community, for the community</i>"
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


def _try_product_ai_content(product: dict) -> dict | None:
    """
    Generate a product-spotlight post for a specific Fundz product.
    Called every 3rd channel post to ensure multi-product representation.

    The post introduces or highlights the product's value for the audience,
    potentially cross-promoting it with related Fundz products.
    """
    if not OPENROUTER_API_KEY:
        return None
    try:
        name        = product.get("name", "Fundz Product")
        description = product.get("description", "")
        features    = ", ".join(product.get("features", [])[:4]) or "AI tools"
        audience    = product.get("target_audience", "")
        status      = product.get("status", "planned")

        status_note = {
            "active":     "is now live and available",
            "beta":       "is currently in beta — early access available",
            "planned":    "is coming soon to the Fundz ecosystem",
            "deprecated": "has been retired",
        }.get(status, "is part of the Fundz ecosystem")

        prompt = (
            f"Write a Telegram channel post highlighting {name}.\n"
            f"{name} {status_note}.\n"
            f"Description: {description}\n"
            f"Key features: {features}\n"
            f"Target audience: {audience}\n\n"
            f"Requirements: 100-180 words, HTML bold for key terms, "
            f"1-2 relevant emojis, engaging tone, end with a CTA. "
            f"Include: 📌 <i>Fundz Ecosystem — {name}</i> as the footer."
        )
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model":    OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are the content manager for the Fundz digital ecosystem. "
                            "Write high-quality product spotlight posts for the Telegram channel. "
                            "Each post should be informative, exciting, and professional. "
                            "Use HTML formatting: <b>bold</b> for key terms."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens":  400,
                "temperature": 0.75,
            },
            timeout=25,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content and len(content) > 80 and _is_channel_safe(content):
                return {
                    "category": "ecosystem_update",
                    "title":    f"Product-{product['product_id']}-{int(time.time())}",
                    "text":     content,
                }
    except Exception as exc:
        log.debug("channel_manager._try_product_ai_content: %s", exc)
    return None


def _try_ai_content(category: str) -> dict | None:
    """Generate fresh content via AI for variety."""
    if not OPENROUTER_API_KEY:
        return None
    try:
        category_prompts = {
            "ai_education": "Write an educational post explaining one specific AI concept in simple terms.",
            "productivity":  "Write a practical productivity tip about using AI tools effectively.",
            "tutorial":      f"Write a short tutorial tip for {BOT_NAME} Telegram bot users.",
            "security":      "Write a concise cybersecurity awareness tip relevant to AI and Telegram users.",
            "inspiration":   "Write an inspiring thought about human-AI collaboration.",
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
                            f"You are the content manager for {BOT_NAME}, an AI-powered Telegram platform. "
                            "Write high-quality, educational Telegram channel posts. "
                            "Posts should be 100-200 words, informative, well-structured, and end with a CTA. "
                            "Use HTML formatting (bold with <b>, code with <code>). "
                            f"Always end with: 📌 <i>{BOT_NAME} — [relevant tagline]</i>"
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
            if content and len(content) > 80 and _is_channel_safe(content):
                return {"category": category, "title": f"AI-{category}-{int(time.time())}", "text": content}
    except Exception as exc:
        log.debug("channel_manager._try_ai_content: %s", exc)
    return None


def _post_to_channel(text: str) -> int | None:
    """Send post to channel. Returns message_id on success.
    Final safety gate: never publishes error/fallback text to the public channel.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return None
    # Absolute last-resort guard before any text hits the wire
    if not _is_channel_safe(text):
        log.error(
            "BLOCKED: _post_to_channel() received unsafe text — "
            "NOT sending to channel. Internal logging only."
        )
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHANNEL_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if r.status_code == 200:
            msg_id = r.json().get("result", {}).get("message_id")
            log.info("Channel Manager posted content (msg_id=%s)", msg_id)
            return msg_id
        log.warning("channel_manager post HTTP %d: %s", r.status_code, r.text[:80])
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
            "Scheduled channel content post — 1 post per 30 minutes, "
            f"targeting {_POSTS_TARGET_MIN}–{_POSTS_TARGET_MAX} posts/day."
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
        "responsible_ai", "bot_guide", "success_story", "release_notes",
    ]
    available_cats = [c for c in all_categories if c != _last_category_posted]
    if not available_cats:
        available_cats = all_categories
    cat = random.choice(available_cats)

    # ── Content selection ─────────────────────────────────────────────────────
    # Every 3rd post: spotlight a specific Fundz product (multi-product rotation)
    # Other posts: general AI/productivity education (existing category logic)
    global _post_counter
    _post_counter += 1

    content: dict | None = None

    if _post_counter % 3 == 0 and OPENROUTER_API_KEY:
        try:
            from services.product_registry import get_next_product_to_feature
            product = get_next_product_to_feature()
            if product:
                content = _try_product_ai_content(product)
                if content:
                    log.info(
                        "channel_manager: featuring product '%s' (post #%d)",
                        product.get("name"), _post_counter,
                    )
        except Exception as exc:
            log.debug("channel_manager: product post skipped — %s", exc)

    # Standard path: AI-generated general content (40% chance) or static library
    if not content and OPENROUTER_API_KEY and random.random() < 0.4:
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
