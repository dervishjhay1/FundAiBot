"""
FundzAiBot — TestAudit Community Manager Service

Environment 1: Telegram Group (Community Manager role)
  - Smart response system: monitors messages, waits 2-3 min before assisting
  - Natural engagement: greetings, discussions, polls, keepalive
  - Never dominates; humans respond first

Environment 2: Telegram Channel (Communications Manager role)
  - Daily content strategy: 15-30 quality posts/day
  - Variety: tutorials, tips, features, news, highlights, polls
  - Content quality review before every post

This service is background-safe: designed to run alongside CEO Office.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# ── Pending message tracking for smart response ────────────────────────────────
# { chat_id: { msg_id: { "text": str, "user": str, "ts": float, "replied": bool } } }
_PENDING_MESSAGES: dict[int, dict] = {}

# Track when TestAudit last posted in each chat to avoid flooding
_LAST_POSTED: dict[int, float] = {}

# Minimum quiet time before TestAudit initiates engagement (seconds)
_MIN_QUIET_SECONDS = 180  # 3 minutes

# Smart response delay: wait this long for humans to reply first
_HUMAN_REPLY_WAIT = 150  # 2.5 minutes

# Channel post tracking
_CHANNEL_POST_COUNT: dict[str, int] = {}  # date_str -> count
_LAST_CHANNEL_POST: float = 0.0


# ── Group engagement content ───────────────────────────────────────────────────

_MORNING_GREETINGS = [
    "Good morning everyone 👋\n\nWhat's everyone building today?",
    "Morning! ☀️\n\nHope everyone's having a productive start. What are you working on this week?",
    "Good morning, community! 🌅\n\nAnyone trying out any new AI tools lately?",
    "Rise and shine! ☕\n\nWhat's on your AI agenda today?",
]

_AFTERNOON_GREETINGS = [
    "Good afternoon everyone! 🌤️\n\nHow's the day going so far?",
    "Afternoon! Hope the day's been productive 💪\n\nAnything interesting happening?",
    "Hey everyone, good afternoon! ☀️\n\nWhat are you all up to?",
]

_EVENING_GREETINGS = [
    "Good evening! 🌙\n\nWhat did everyone accomplish today?",
    "Evening everyone! Hope it's been a great day 🌆\n\nAny wins to share?",
    "Good evening community! 🌇\n\nWinding down or still grinding?",
]

_ENGAGEMENT_PROMPTS = [
    "The group has been quiet 😄\n\nHas anyone tried any interesting AI tools this week?",
    "Quick question for the community:\n\nWhat's the #1 AI feature you wish existed but doesn't yet?",
    "Productivity tip of the day:\n\nTry using AI to draft your emails first, then edit. You'll be surprised how much time it saves! ⏰\n\nWho's already doing this?",
    "Quick poll 🗳️\n\nWhat do you use AI for most?\n\n💬 Chat & writing\n🎨 Image generation\n💻 Coding help\n📊 Data analysis\n\nComment below!",
    "AI thought of the day 🤔\n\nThe most powerful use of AI isn't replacing what you do — it's handling the parts that slow you down.\n\nWhat's slowing YOU down that AI could fix?",
    "Community challenge:\n\nShare one task you automated with AI this week 🚀\n\nLet's inspire each other!",
    "What would you like us to build next? 🛠️\n\nYour feedback shapes FundzAiBot's roadmap!",
    "Fun fact: The average person saves 2+ hours per week using AI assistants.\n\nHow much time does FundzAiBot save you? ⏱️",
    "Reminder: You can use /ai in this group to ask questions directly! 🤖\n\nWhat would you like to ask?",
    "Who here is using AI for business? 💼\n\nWould love to hear your use case!",
]

_WELCOME_TEMPLATES = [
    "Welcome to FundzAiBot, {name}! Great to have you with us. 👋",
    "Hey {name}! Welcome to the community 🎉 Feel free to ask any AI questions here!",
    "Welcome {name}! 🌟 Glad you joined. We have a great community here — don't be shy!",
    "Great to have you, {name}! Welcome to FundzAiBot community 🚀",
    "Welcome aboard, {name}! 👋 This is a great place to learn and discuss AI together.",
]

_UNANSWERED_RESPONSES = [
    "Happy to help with that! Let me think...\n\n",
    "Great question! Here's what I know:\n\n",
    "I got you! Here's my take:\n\n",
]


def get_time_greeting() -> Optional[str]:
    """Return a time-appropriate group greeting or None if recently posted."""
    hour = datetime.now(timezone.utc).hour
    if 5 <= hour < 12:
        return random.choice(_MORNING_GREETINGS)
    elif 12 <= hour < 17:
        return random.choice(_AFTERNOON_GREETINGS)
    elif 17 <= hour < 22:
        return random.choice(_EVENING_GREETINGS)
    return None


def get_engagement_prompt() -> str:
    return random.choice(_ENGAGEMENT_PROMPTS)


def get_welcome_message(first_name: str) -> str:
    template = random.choice(_WELCOME_TEMPLATES)
    return template.format(name=first_name)


def can_post_in_group(chat_id: int, min_gap: int = _MIN_QUIET_SECONDS) -> bool:
    """Return True if enough time has passed since last TestAudit post."""
    last = _LAST_POSTED.get(chat_id, 0)
    return (time.time() - last) >= min_gap


def record_group_post(chat_id: int) -> None:
    _LAST_POSTED[chat_id] = time.time()


def register_message(chat_id: int, msg_id: int, text: str, user_name: str) -> None:
    """Register a new group message for smart-response monitoring."""
    if chat_id not in _PENDING_MESSAGES:
        _PENDING_MESSAGES[chat_id] = {}
    _PENDING_MESSAGES[chat_id][msg_id] = {
        "text": text,
        "user": user_name,
        "ts": time.time(),
        "replied": False,
    }
    # Clean old messages (older than 10 minutes)
    cutoff = time.time() - 600
    _PENDING_MESSAGES[chat_id] = {
        mid: m for mid, m in _PENDING_MESSAGES[chat_id].items()
        if m["ts"] > cutoff
    }


def mark_replied(chat_id: int, reply_to_id: Optional[int] = None) -> None:
    """Mark a message as having received a human reply."""
    if chat_id not in _PENDING_MESSAGES:
        return
    if reply_to_id and reply_to_id in _PENDING_MESSAGES[chat_id]:
        _PENDING_MESSAGES[chat_id][reply_to_id]["replied"] = True
    else:
        # Mark all recent messages as replied (conversation is active)
        cutoff = time.time() - 300
        for mid, m in _PENDING_MESSAGES[chat_id].items():
            if m["ts"] > cutoff:
                m["replied"] = True


def get_unanswered_messages(chat_id: int) -> list[dict]:
    """Return messages older than the wait threshold with no human reply."""
    if chat_id not in _PENDING_MESSAGES:
        return []
    threshold = time.time() - _HUMAN_REPLY_WAIT
    return [
        {"id": mid, **m}
        for mid, m in _PENDING_MESSAGES[chat_id].items()
        if not m["replied"] and m["ts"] < threshold
    ]


# ── Channel content strategy ───────────────────────────────────────────────────

_CHANNEL_CONTENT_TYPES = [
    "ai_tutorial",
    "productivity_tip",
    "feature_spotlight",
    "security_tip",
    "community_highlight",
    "faq",
    "ai_news",
    "success_story",
    "weekly_summary",
    "poll",
    "fun_fact",
    "quick_tip",
    "use_case",
    "announcement",
]

_CONTENT_TEMPLATES: dict[str, list[str]] = {
    "productivity_tip": [
        "⚡ <b>Productivity Tip #{n}</b>\n\nUse AI to handle your repetitive tasks first thing in the morning. By the time others are just starting, you're already ahead.\n\n🤖 Try it now with FundzAiBot!",
        "💡 <b>AI Tip of the Day</b>\n\nInstead of staring at a blank page, tell FundzAiBot: \"Help me draft a rough outline for...\" — then refine from there. First drafts are the hardest part. AI handles it in seconds.\n\n✅ Start today!",
        "🚀 <b>Work Smarter</b>\n\nDid you know you can use FundzAiBot to:\n\n• Summarize long documents\n• Draft professional emails\n• Translate content instantly\n• Explain complex topics simply\n\nAll in one chat. Try it now!",
        "⏰ <b>Time-Saving Hack</b>\n\nStop re-writing the same types of content. Use FundzAiBot to create templates, then customize them.\n\nSave hours every week. 🎯",
    ],
    "ai_tutorial": [
        "📚 <b>AI Tutorial: Getting Better Responses</b>\n\nThe secret to great AI answers is specificity.\n\n❌ Bad: \"Write about marketing\"\n✅ Good: \"Write a 3-paragraph intro about email marketing for small businesses\"\n\nBe specific. Get better results. 🎯\n\n📬 Try it in @FundzAiBot now!",
        "🧠 <b>Mastering AI Prompts</b>\n\nOne powerful technique: Add context.\n\n\"As a software developer, explain REST APIs in simple terms\"\n\nRole + task = significantly better output.\n\n💡 Try this with FundzAiBot today!",
        "📖 <b>Quick AI Guide</b>\n\nUsing /summarize in FundzAiBot:\n\nPaste any long article or document and get a clean summary instantly.\n\nPerfect for research, news, and reports. 📰\n\nTry it now!",
    ],
    "feature_spotlight": [
        "✨ <b>Feature Spotlight: AI Image Generation</b>\n\nFundzAiBot can generate custom images from your descriptions.\n\nJust use /image and describe what you want.\n\nExample: \"A futuristic city at sunset, cyberpunk style\"\n\n🎨 Try it now!",
        "🌍 <b>Feature: Multi-Language Support</b>\n\nFundzAiBot speaks your language!\n\nUse /language to switch between 10+ supported languages including English, Spanish, French, Arabic, Chinese, and more.\n\n🗣️ Set yours now!",
        "🎭 <b>Feature: AI Personalities</b>\n\nDid you know FundzAiBot has 8 different AI styles?\n\n🧠 Default • 📚 Teacher • 😂 Comedian\n🔬 Scientist • 📝 Writer • 💼 Business\n🧑‍💻 Coder • 🎭 Creative\n\nUse /style to switch. 🔄",
    ],
    "security_tip": [
        "🔒 <b>Security Reminder</b>\n\nNever share your account passwords, seed phrases, or private keys in any chat — including this one.\n\nFundzAiBot will NEVER ask for your financial credentials.\n\nStay safe! 🛡️",
        "⚠️ <b>AI Safety Tip</b>\n\nWhen using AI for research, always verify important facts from primary sources.\n\nAI is a powerful tool — use it wisely. 🧠\n\nFundzAiBot is designed to be helpful AND honest about its limitations.",
    ],
    "fun_fact": [
        "🤓 <b>AI Fun Fact</b>\n\nThe first chatbot, ELIZA, was created in 1966 at MIT — over 50 years ago!\n\nFast forward to today and AI can write code, generate images, and hold complex conversations.\n\nThe future is already here. 🚀",
        "💡 <b>Did You Know?</b>\n\nOver 77% of businesses are now using AI in some part of their operations.\n\nThe question isn't whether to use AI — it's how to use it effectively.\n\nFundzAiBot is here to help you do exactly that. 🤖",
        "📊 <b>AI Stat of the Day</b>\n\nPeople who use AI assistants daily report saving an average of 2+ hours per week on routine tasks.\n\nThat's over 100 hours per year — imagine what you could do with that time! ⏰",
    ],
    "quick_tip": [
        "💡 <b>Quick Tip</b>\n\nUse /clear in FundzAiBot to reset your conversation memory when starting a new topic.\n\nThis gives the AI a fresh context and often produces better answers. 🧹",
        "⚡ <b>Quick Tip</b>\n\n/ask is perfect for single questions that don't need memory.\n\n/chat is best for ongoing conversations.\n\nKnow the difference — use the right tool! 🎯",
    ],
    "use_case": [
        "💼 <b>Real Use Case</b>\n\nA small business owner uses FundzAiBot to:\n\n• Write product descriptions\n• Draft customer emails\n• Create social media content\n• Translate materials\n\n...all without hiring a copywriter. 🚀",
        "🎓 <b>Student Use Case</b>\n\nStudents use FundzAiBot to:\n\n• Understand complex subjects\n• Get study summaries\n• Practice language translation\n• Check code for assignments\n\nSmarter studying = better results. 📚",
    ],
    "faq": [
        "❓ <b>FAQ: How does FundzAiBot work?</b>\n\nFundzAiBot uses multiple AI providers (including advanced language models) to understand your questions and generate helpful, accurate responses.\n\nYour conversations are private and secure.\n\n💬 Start chatting anytime!",
        "❓ <b>FAQ: Is FundzAiBot free?</b>\n\nYes! FundzAiBot has a generous free tier:\n• 30 AI chats per day\n• 5 image generations per day\n\nWant more? Upgrade to VIP with Telegram Stars ⭐\n\nUse /subscribe to see plans!",
    ],
}


def get_channel_post_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _CHANNEL_POST_COUNT.get(today, 0)


def record_channel_post() -> None:
    global _LAST_CHANNEL_POST
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _CHANNEL_POST_COUNT[today] = _CHANNEL_POST_COUNT.get(today, 0) + 1
    _LAST_CHANNEL_POST = time.time()


def seconds_since_last_channel_post() -> float:
    return time.time() - _LAST_CHANNEL_POST


def should_post_to_channel(target_gap_seconds: int = 1800) -> bool:
    """Return True if it's time to post to the channel (default: every 30 min)."""
    daily_count = get_channel_post_today()
    if daily_count >= 30:
        return False
    return seconds_since_last_channel_post() >= target_gap_seconds


def get_next_content_type(daily_count: int) -> str:
    """Pick a content type based on daily variety rotation."""
    rotation = [
        "productivity_tip",
        "feature_spotlight",
        "ai_tutorial",
        "quick_tip",
        "fun_fact",
        "security_tip",
        "use_case",
        "faq",
        "productivity_tip",
        "feature_spotlight",
        "ai_tutorial",
        "quick_tip",
        "fun_fact",
        "productivity_tip",
        "feature_spotlight",
    ]
    idx = daily_count % len(rotation)
    return rotation[idx]


def generate_channel_post(content_type: str, post_number: int = 1) -> str:
    """Generate a channel post for the given content type."""
    templates = _CONTENT_TEMPLATES.get(content_type, _CONTENT_TEMPLATES["quick_tip"])
    template = random.choice(templates)
    return template.replace("{n}", str(post_number))


def generate_ai_channel_prompt(content_type: str, daily_count: int) -> list[dict]:
    """Build messages for AI to generate a fresh channel post."""
    type_descriptions = {
        "ai_tutorial": "an educational AI tutorial post showing users how to get better results from AI tools",
        "productivity_tip": "a productivity tip post showing how AI can save time and boost efficiency",
        "feature_spotlight": "a feature spotlight post highlighting one of FundzAiBot's key features",
        "security_tip": "a digital security awareness post relevant to AI and online safety",
        "community_highlight": "a warm community highlight post that celebrates the FundzAiBot community",
        "faq": "an FAQ post answering a common question about FundzAiBot or AI in general",
        "fun_fact": "an interesting AI or technology fun fact post",
        "quick_tip": "a short actionable tip for using FundzAiBot more effectively",
        "use_case": "a practical use case post showing how real people use AI in their daily lives",
    }

    description = type_descriptions.get(content_type, "a helpful AI-related post")

    return [
        {
            "role": "system",
            "content": (
                "You are the Communications Manager of FundzAiBot, writing professional channel posts. "
                "Posts should be:\n"
                "- Professional and polished\n"
                "- Educational and genuinely valuable\n"
                "- Engaging but not clickbait\n"
                "- 150-300 words maximum\n"
                "- Include relevant emojis (not excessive)\n"
                "- End with a call-to-action when appropriate\n"
                "- Never use markdown — use plain text with HTML-style formatting only (bold with <b>, italic with <i>)\n"
                "- NEVER include hashtags\n"
                "- Channel name is FundzAiBot"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Write {description} for the FundzAiBot Telegram channel. "
                f"This is post #{daily_count + 1} today. "
                "Make it fresh and different from typical posts. "
                "Do not add any intro like 'Here is a post:' — just write the post directly."
            ),
        },
    ]
