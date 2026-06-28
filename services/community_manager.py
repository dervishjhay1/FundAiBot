"""
FundzAiBot — TestAudit Community & Content Management Service

TestAudit is the official Operations Manager of FundzAiBot.
This service governs how it behaves in each environment.

ENVIRONMENT 1 — TELEGRAM GROUP (Community Manager)
  - Behaves like a human professional community manager
  - Never like a chatbot; never dumps articles; never dominates
  - Welcomes members warmly, starts natural discussions, encourages engagement
  - Smart response: waits 2-3 min, steps in only if unanswered

ENVIRONMENT 2 — TELEGRAM CHANNEL (Communications Manager)
  - Publishes 15-30 quality posts per day
  - Multi-draft generation with quality scoring
  - Diverse content: tutorials, tips, features, stories, polls, announcements

ENVIRONMENT 3 — PRIVATE DM (Executive Assistant / Operations Monitor)
  - Monitors satisfaction, retention, inactive users, feature adoption
  - Never spams; all actions are measured and purposeful
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# GROUP STATE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

# { chat_id: { msg_id: { text, user, ts, replied, context } } }
_PENDING_MESSAGES: dict[int, dict] = {}

# Last time TestAudit posted in each group chat
_LAST_GROUP_POST: dict[int, float] = {}

# Last time TestAudit sent a proactive engagement message
_LAST_ENGAGEMENT: dict[int, float] = {}

# Conversation activity tracker — { chat_id: last_human_message_ts }
_LAST_HUMAN_MESSAGE: dict[int, float] = {}

# Minimum gap before TestAudit posts proactively (10 min)
_PROACTIVE_MIN_GAP = 600

# Gap before smart-reply (after no human response, in seconds)
_HUMAN_REPLY_WAIT = 150  # 2.5 minutes

# Proactive engagement: min silence before TestAudit initiates (20 min)
_SILENCE_THRESHOLD = 1200


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL STATE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

_CHANNEL_POST_COUNT: dict[str, int] = {}   # "YYYY-MM-DD" → count
_LAST_CHANNEL_POST: float = 0.0

# Recent content type history (avoid repeating same type consecutively)
_RECENT_CONTENT_TYPES: list[str] = []
_MAX_RECENT_HISTORY = 5


# ══════════════════════════════════════════════════════════════════════════════
# GROUP — WELCOME MESSAGES (TestAudit Community Manager persona)
# ══════════════════════════════════════════════════════════════════════════════

_WELCOME_TEMPLATES = [
    "Welcome to FundzAiBot, {name}! Great to have you with us. 👋",
    "Hey {name}! Welcome to the community. Don't be shy — we're a friendly bunch here 😊",
    "Welcome {name}! 🌟 Glad you found us. Feel free to jump into any conversation!",
    "Great to have you, {name}! Welcome to FundzAiBot — the best place to explore AI together 🚀",
    "Welcome aboard, {name}! 👋 Hope you enjoy it here. What brings you to the community?",
    "Hey {name}, welcome! 🎉 We're happy to have you. If you ever have any questions, just ask!",
    "Welcome to the family, {name}! 🙌 You joined at a great time.",
    "{name}, welcome! Always great to see new faces here. 👋",
]


# ══════════════════════════════════════════════════════════════════════════════
# GROUP — TIME-APPROPRIATE GREETINGS
# ══════════════════════════════════════════════════════════════════════════════

_MORNING_GREETINGS = [
    "Good morning everyone 👋\n\nWhat's everyone building today?",
    "Morning! ☀️\n\nHope everyone's starting the week strong. What's on your plate?",
    "Good morning, community! 🌅\n\nAnyone trying out anything interesting lately?",
    "Rise and shine! ☕\n\nWhat's on the agenda today?",
    "Good morning 👋\n\nLet's make today a productive one. What are you working on?",
    "Morning everyone! 🌤️\n\nHope the day's off to a great start. What's everyone up to?",
]

_AFTERNOON_GREETINGS = [
    "Good afternoon everyone! 🌤️\n\nHow's the day going so far?",
    "Afternoon! Hope you're having a productive one 💪\n\nAnything interesting happening?",
    "Hey everyone, hope the afternoon's treating you well! What are you all up to?",
    "Good afternoon 🙂\n\nMidway through the day — how's it going?",
]

_EVENING_GREETINGS = [
    "Good evening! 🌙\n\nWhat did everyone get done today?",
    "Evening everyone! Hope it was a great day 🌆\n\nAny wins to share?",
    "Good evening community! 🌇\n\nWinding down or still grinding?",
    "Evening! 🌙\n\nHow did everyone's day go?",
]


# ══════════════════════════════════════════════════════════════════════════════
# GROUP — PROACTIVE ENGAGEMENT PROMPTS
# These are short, conversational — never long articles
# ══════════════════════════════════════════════════════════════════════════════

_ENGAGEMENT_PROMPTS = [
    "The group's been quiet for a bit 😄\n\nWhat's everyone working on this week?",
    "Quick question for the community:\n\nWhat's the one thing you wish AI could do better right now?",
    "What are you using FundzAiBot for the most these days? Curious to hear! 🤔",
    "Who's tried the image generation feature? Would love to hear what you think 🎨",
    "Community question:\n\nIf you could add one feature to FundzAiBot, what would it be? 🛠️",
    "Poll time 🗳️\n\nWhat do you use AI for most?\n\n💬 Writing & chat\n🎨 Images\n💻 Coding\n📊 Research\n\nDrop your answer below!",
    "Genuine question — what's the most useful thing AI has helped you do recently? 🙌",
    "Happy to help if anyone has questions about FundzAiBot or AI in general! Just ask 👋",
    "What would you like us to build next? Your feedback genuinely shapes what we work on 🔨",
    "Anyone try any interesting AI tools recently? Would love to hear recommendations! 🤖",
    "Quick thought:\n\nThe best AI use cases are usually the boring, repetitive ones — not the flashy stuff.\n\nWhat's yours?",
    "Hope everyone's having a great week! Any wins to share? Big or small, we love to hear! 🏆",
    "Anyone got questions about using FundzAiBot? Happy to help anyone who needs it 🙂",
    "What's one AI tip you'd share with someone just getting started?",
    "If you've found a good use case for FundzAiBot, share it! Help others get value too 💡",
]

_SUPPORT_INTERJECTIONS = [
    "Looks like this one might need a bit more help — let me see what I can add here 🙂\n\n",
    "Happy to jump in on this one!\n\n",
    "Let me help with that!\n\n",
    "Good question — here's what I know:\n\n",
    "I can help with this one 👋\n\n",
]


# ══════════════════════════════════════════════════════════════════════════════
# GROUP FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_welcome_message(first_name: str) -> str:
    template = random.choice(_WELCOME_TEMPLATES)
    return template.format(name=first_name)


def get_time_greeting() -> Optional[str]:
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


def get_support_interjection() -> str:
    return random.choice(_SUPPORT_INTERJECTIONS)


def record_human_activity(chat_id: int) -> None:
    """Record that a human just sent a message — resets silence timer."""
    _LAST_HUMAN_MESSAGE[chat_id] = time.time()


def seconds_silent(chat_id: int) -> float:
    """Return seconds since last human activity in this chat."""
    last = _LAST_HUMAN_MESSAGE.get(chat_id, 0)
    if last == 0:
        return 9999  # Never seen activity
    return time.time() - last


def can_post_in_group(chat_id: int, min_gap: int = 120) -> bool:
    """Return True if enough time has passed since last TestAudit post."""
    last = _LAST_GROUP_POST.get(chat_id, 0)
    return (time.time() - last) >= min_gap


def can_engage_proactively(chat_id: int) -> bool:
    """Return True if conditions are right for proactive engagement."""
    last_engage = _LAST_ENGAGEMENT.get(chat_id, 0)
    gap_ok = (time.time() - last_engage) >= _PROACTIVE_MIN_GAP
    silence_ok = seconds_silent(chat_id) >= _SILENCE_THRESHOLD
    post_ok = can_post_in_group(chat_id, min_gap=_PROACTIVE_MIN_GAP)
    return gap_ok and silence_ok and post_ok


def record_group_post(chat_id: int) -> None:
    _LAST_GROUP_POST[chat_id] = time.time()


def record_proactive_engagement(chat_id: int) -> None:
    _LAST_ENGAGEMENT[chat_id] = time.time()
    _LAST_GROUP_POST[chat_id] = time.time()


def register_message(
    chat_id: int, msg_id: int, text: str, user_name: str
) -> None:
    """Register a new group message for smart-response monitoring."""
    if chat_id not in _PENDING_MESSAGES:
        _PENDING_MESSAGES[chat_id] = {}

    _PENDING_MESSAGES[chat_id][msg_id] = {
        "text": text,
        "user": user_name,
        "ts": time.time(),
        "replied": False,
    }

    # Clean old entries (older than 15 min)
    cutoff = time.time() - 900
    _PENDING_MESSAGES[chat_id] = {
        mid: m
        for mid, m in _PENDING_MESSAGES[chat_id].items()
        if m["ts"] > cutoff
    }


def mark_replied(chat_id: int, reply_to_id: Optional[int] = None) -> None:
    """Mark a message (or all recent ones) as having received a human reply."""
    if chat_id not in _PENDING_MESSAGES:
        return
    if reply_to_id and reply_to_id in _PENDING_MESSAGES[chat_id]:
        _PENDING_MESSAGES[chat_id][reply_to_id]["replied"] = True
    else:
        # Any new message in an active conversation marks all recent ones as replied
        cutoff = time.time() - 420  # 7 min window
        for m in _PENDING_MESSAGES[chat_id].values():
            if m["ts"] > cutoff:
                m["replied"] = True


def get_unanswered_messages(chat_id: int) -> list[dict]:
    """Return messages that have waited long enough with no human reply."""
    if chat_id not in _PENDING_MESSAGES:
        return []
    threshold = time.time() - _HUMAN_REPLY_WAIT
    return [
        {"id": mid, **m}
        for mid, m in _PENDING_MESSAGES[chat_id].items()
        if not m["replied"] and m["ts"] < threshold
    ]


def is_actionable_message(text: str) -> bool:
    """
    Return True if this message looks like it needs a response
    (question, help request, frustration, etc.)
    """
    t = text.lower()
    signals = (
        "?" in text,
        any(w in t for w in ("how", "what", "why", "when", "where", "who", "which")),
        any(w in t for w in ("help", "issue", "problem", "error", "not working", "broken")),
        any(w in t for w in ("can i", "is it possible", "does it", "will it", "how do i")),
        any(w in t for w in ("stuck", "confused", "don't understand", "cant", "can't")),
    )
    return any(signals)


def build_community_manager_system_prompt() -> str:
    """Return the system prompt for TestAudit in group Community Manager mode."""
    return (
        "You are TestAudit, the Community Manager of FundzAiBot.\n\n"
        "You are operating inside a Telegram group chat.\n\n"
        "YOUR ROLE:\n"
        "- Professional community manager — not a chatbot\n"
        "- Human-like, warm, and conversational\n"
        "- Helpful when members have questions or need support\n\n"
        "RESPONSE RULES:\n"
        "- Keep responses SHORT (2-4 sentences max for casual, up to 6 for technical help)\n"
        "- Never dump long articles or tutorials in the group\n"
        "- Never use excessive markdown or formatting — this is a chat, not a document\n"
        "- Use emojis naturally, not excessively\n"
        "- If you don't know something confidently, say so honestly\n"
        "- Encourage further discussion when appropriate\n"
        "- Never start your response with 'As an AI' or similar robotic phrases\n"
        "- Never mention 'language model', 'GPT', 'Claude', or any underlying technology\n"
        "- You represent FundzAiBot as a team member, not a tool\n\n"
        "TONE:\n"
        "Friendly. Warm. Professional but casual. Like a knowledgeable colleague who genuinely cares."
    )


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL — CONTENT STRATEGY
# ══════════════════════════════════════════════════════════════════════════════

_CONTENT_TYPE_ROTATION = [
    "productivity_tip",
    "feature_spotlight",
    "ai_tutorial",
    "quick_tip",
    "fun_fact",
    "use_case",
    "faq",
    "security_tip",
    "productivity_tip",
    "feature_spotlight",
    "ai_tutorial",
    "success_story",
    "quick_tip",
    "fun_fact",
    "use_case",
    "productivity_tip",
    "community_highlight",
    "feature_spotlight",
    "faq",
    "ai_tutorial",
    "security_tip",
    "quick_tip",
    "productivity_tip",
    "fun_fact",
    "feature_spotlight",
    "use_case",
    "ai_tutorial",
    "quick_tip",
    "productivity_tip",
    "community_highlight",
]

_CONTENT_TYPE_DESCRIPTIONS = {
    "ai_tutorial": (
        "an educational AI tutorial that shows users how to get better results. "
        "Focus on a specific technique or prompt strategy. Show before/after examples where possible."
    ),
    "productivity_tip": (
        "a practical productivity tip showing how AI saves time on real tasks. "
        "Be specific — name the task, explain the approach, quantify the time saved if possible."
    ),
    "feature_spotlight": (
        "a feature spotlight for one specific FundzAiBot feature. "
        "Explain what it does, when to use it, and give a concrete example. "
        "Features include: /image (AI images), /summarize (document summaries), /translate, "
        "/analyze (photo analysis), /code (coding help), /style (8 AI personalities), "
        "/model (switch AI providers), /subscribe (VIP plans), /referral (referral rewards)."
    ),
    "security_tip": (
        "a digital security awareness post relevant to AI and online safety. "
        "Be practical and specific — what should users do or avoid?"
    ),
    "community_highlight": (
        "a warm, community-celebrating post that makes members feel valued. "
        "Could highlight community size, member achievements, or appreciation."
    ),
    "faq": (
        "an FAQ post answering one common question about FundzAiBot. "
        "Questions could include: how it works, privacy, pricing, commands, "
        "AI models used, daily limits, VIP benefits."
    ),
    "fun_fact": (
        "an interesting and surprising AI or technology fact. "
        "Make it genuinely fascinating — something people would want to share."
    ),
    "quick_tip": (
        "a very short, immediately actionable tip for using FundzAiBot. "
        "One tip, clearly explained, with an example. Under 100 words."
    ),
    "use_case": (
        "a real-world use case showing how a specific type of person uses FundzAiBot in their life. "
        "Be concrete — pick a persona (student, freelancer, business owner, developer, etc.) "
        "and show exactly how they use it."
    ),
    "success_story": (
        "an inspiring story format about how AI is changing someone's work or life. "
        "Relatable, concrete, motivating. Could be hypothetical but realistic."
    ),
}

# Fallback templates (used when AI is unavailable)
_FALLBACK_TEMPLATES: dict[str, str] = {
    "productivity_tip": (
        "⚡ <b>Productivity Tip</b>\n\n"
        "Use FundzAiBot to draft your first pass on any written task — "
        "emails, summaries, reports, social posts.\n\n"
        "Edit the output to add your voice. You'll finish in a fraction of the time.\n\n"
        "Start with: <i>\"Draft a professional email to...\"</i>"
    ),
    "ai_tutorial": (
        "📚 <b>AI Tip: Be Specific</b>\n\n"
        "The more context you give FundzAiBot, the better the response.\n\n"
        "❌ <i>\"Write about marketing\"</i>\n"
        "✅ <i>\"Write a 3-paragraph intro about email marketing for small e-commerce businesses\"</i>\n\n"
        "Specificity = better results. 🎯"
    ),
    "feature_spotlight": (
        "✨ <b>Feature: /summarize</b>\n\n"
        "Got a long document, article, or report?\n\n"
        "Use <code>/summarize</code> and paste the text. "
        "FundzAiBot gives you a clean, concise summary in seconds.\n\n"
        "Perfect for research, news, and long reports. 📄"
    ),
    "fun_fact": (
        "🤓 <b>AI Fun Fact</b>\n\n"
        "The first chatbot, ELIZA, was created at MIT in 1966.\n\n"
        "It had no real intelligence — just clever pattern matching.\n\n"
        "Compare that to today's AI that can write code, analyze images, "
        "hold conversations, and more.\n\n"
        "The pace of progress is extraordinary. 🚀"
    ),
    "quick_tip": (
        "💡 <b>Quick Tip</b>\n\n"
        "Use <code>/clear</code> in FundzAiBot to reset your conversation memory "
        "when switching topics.\n\n"
        "Fresh context = better answers. 🧹"
    ),
    "use_case": (
        "💼 <b>Use Case: Freelancers</b>\n\n"
        "Freelancers use FundzAiBot to:\n\n"
        "• Write client proposals\n"
        "• Draft project updates\n"
        "• Summarize briefs\n"
        "• Create invoices and templates\n\n"
        "All without a copywriter or assistant. 🚀"
    ),
    "security_tip": (
        "🔒 <b>Security Reminder</b>\n\n"
        "FundzAiBot will <b>never</b> ask for your password, seed phrase, "
        "or financial credentials.\n\n"
        "If anyone claims to be FundzAiBot support and asks for this — it's a scam.\n\n"
        "Stay safe. 🛡️"
    ),
    "faq": (
        "❓ <b>FAQ: How many chats can I have per day?</b>\n\n"
        "Free users get <b>30 AI chats per day</b>.\n\n"
        "VIP plans offer more:\n"
        "⭐ Basic — 100 chats/day\n"
        "💎 Pro — 300 chats/day\n"
        "🚀 Elite — Unlimited\n\n"
        "Use <code>/subscribe</code> to see current plans."
    ),
    "community_highlight": (
        "👥 <b>Community Update</b>\n\n"
        "The FundzAiBot community keeps growing — thank you all for being part of this! 🙏\n\n"
        "Every question asked, every feature requested, every piece of feedback "
        "makes FundzAiBot better.\n\n"
        "You're not just users — you're co-creators. 💪"
    ),
    "success_story": (
        "🌟 <b>How AI Changed Their Week</b>\n\n"
        "A freelance writer started using FundzAiBot to draft first drafts.\n\n"
        "Result: Same quality, half the time. More clients, same deadline pressure.\n\n"
        "AI doesn't replace the craft — it removes the friction. "
        "What would you do with twice the time? ⏰"
    ),
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


def get_next_content_type(daily_count: int) -> str:
    """Pick the next content type, avoiding recent repeats."""
    idx = daily_count % len(_CONTENT_TYPE_ROTATION)
    candidate = _CONTENT_TYPE_ROTATION[idx]

    # Avoid repeating the same type twice in a row
    if _RECENT_CONTENT_TYPES and _RECENT_CONTENT_TYPES[-1] == candidate:
        alt_idx = (idx + 1) % len(_CONTENT_TYPE_ROTATION)
        candidate = _CONTENT_TYPE_ROTATION[alt_idx]

    _RECENT_CONTENT_TYPES.append(candidate)
    if len(_RECENT_CONTENT_TYPES) > _MAX_RECENT_HISTORY:
        _RECENT_CONTENT_TYPES.pop(0)

    return candidate


def get_fallback_post(content_type: str) -> str:
    """Return a fallback template post for the given content type."""
    return _FALLBACK_TEMPLATES.get(content_type, _FALLBACK_TEMPLATES["quick_tip"])


def build_channel_post_prompt(content_type: str, daily_count: int, draft_num: int = 1) -> list[dict]:
    """Build the AI prompt for generating a channel post draft."""
    description = _CONTENT_TYPE_DESCRIPTIONS.get(
        content_type,
        "a helpful AI-related post for the FundzAiBot Telegram channel"
    )

    return [
        {
            "role": "system",
            "content": (
                "You are the Communications Manager of FundzAiBot — a professional AI assistant platform.\n\n"
                "You are writing a post for the official FundzAiBot Telegram channel.\n\n"
                "WRITING STANDARDS:\n"
                "- Professional, clear, and genuinely useful\n"
                "- Educational without being condescending\n"
                "- Engaging but never clickbait\n"
                "- 100-280 words maximum\n"
                "- Use emojis naturally (2-5 max, not decorative spam)\n"
                "- End with a call-to-action when it fits naturally\n"
                "- No hashtags\n"
                "- No markdown — use HTML only: <b>bold</b>, <i>italic</i>, <code>code</code>\n"
                "- Never start with 'Here is' or 'Sure!' — write the post directly\n"
                "- Company name: FundzAiBot\n"
                "- Bot username: @FundzAiBot\n\n"
                "WHAT NOT TO DO:\n"
                "- No generic motivational filler\n"
                "- No vague statements ('AI is changing everything!')\n"
                "- No excessive self-promotion\n"
                "- No fake statistics"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Write {description}.\n\n"
                f"This is draft #{draft_num}, post #{daily_count + 1} today.\n"
                "Write the post directly — no preamble, no labels, just the post itself."
            ),
        },
    ]


def score_post_quality(text: str) -> float:
    """
    Score a post for quality (0.0 to 1.0).
    Higher is better. Used to pick the best draft.
    """
    if not text or len(text.strip()) < 50:
        return 0.0

    score = 0.5  # Baseline

    words = text.split()
    word_count = len(words)

    # Length sweet spot: 80-250 words
    if 80 <= word_count <= 250:
        score += 0.2
    elif 50 <= word_count < 80 or 250 < word_count <= 300:
        score += 0.1
    else:
        score -= 0.1

    # Has HTML formatting
    if "<b>" in text or "<i>" in text:
        score += 0.1

    # Has a call-to-action signal
    cta_signals = ["try", "use ", "tap ", "click", "start", "open", "learn", "discover", "join"]
    if any(s in text.lower() for s in cta_signals):
        score += 0.05

    # Penalize bad patterns
    bad_patterns = [
        "here is a post", "sure!", "of course", "certainly",
        "{n}", "{name}", "hashtag", "lorem ipsum",
        "as an ai", "language model",
    ]
    for pattern in bad_patterns:
        if pattern in text.lower():
            score -= 0.2

    # No hashtags
    if "#" in text:
        score -= 0.15

    # Has an emoji (natural engagement)
    emoji_chars = [c for c in text if ord(c) > 8000]
    if 1 <= len(emoji_chars) <= 8:
        score += 0.05
    elif len(emoji_chars) > 12:
        score -= 0.1

    return max(0.0, min(1.0, score))


# ══════════════════════════════════════════════════════════════════════════════
# DM OPERATIONS — PRIVATE EXECUTIVE ASSISTANT TRACKING
# ══════════════════════════════════════════════════════════════════════════════

# Track user interaction metadata for Operations Manager context
# { user_id: { "last_seen": ts, "message_count": int, "last_feature": str } }
_DM_USER_ACTIVITY: dict[int, dict] = {}

# Minimum days of silence before considering follow-up
_INACTIVE_DAYS_THRESHOLD = 5


def record_dm_activity(user_id: int, feature: str = "chat") -> None:
    """Record a private chat interaction."""
    if user_id not in _DM_USER_ACTIVITY:
        _DM_USER_ACTIVITY[user_id] = {"last_seen": 0, "message_count": 0, "last_feature": "chat"}
    _DM_USER_ACTIVITY[user_id]["last_seen"] = time.time()
    _DM_USER_ACTIVITY[user_id]["message_count"] += 1
    _DM_USER_ACTIVITY[user_id]["last_feature"] = feature


def get_inactive_users(days: int = _INACTIVE_DAYS_THRESHOLD) -> list[int]:
    """Return user IDs who have been inactive for more than `days` days."""
    threshold = time.time() - (days * 86400)
    return [
        uid for uid, data in _DM_USER_ACTIVITY.items()
        if data["last_seen"] < threshold and data["message_count"] > 0
    ]


def get_dm_stats() -> dict:
    """Return a summary of DM activity for the Operations report."""
    total = len(_DM_USER_ACTIVITY)
    active_24h = sum(
        1 for d in _DM_USER_ACTIVITY.values()
        if time.time() - d["last_seen"] < 86400
    )
    inactive_5d = len(get_inactive_users(5))
    return {
        "total_tracked": total,
        "active_24h": active_24h,
        "inactive_5d": inactive_5d,
        "channel_posts_today": get_channel_post_today(),
        "seconds_since_last_channel_post": seconds_since_last_channel_post(),
    }
