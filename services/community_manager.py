"""
FundzAiBot — Community Manager (TestAudit role)

Manages the Telegram Group on behalf of the company:
  • Monitors group activity level
  • When quiet (few messages in the last N minutes), sends an AI-generated
    discussion topic related to AI, productivity, FundzAiBot, or technology
  • Backs off automatically when the community is naturally active
  • Avoids repetition — tracks recently posted topics
  • Never spams — enforces a minimum cooldown between posts

This is NOT a chatbot. It is a background intelligence service.
It integrates naturally with the existing group handlers.
"""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_INACTIVITY_THRESHOLD_MINS = 60    # trigger discussion after 60 min of silence
_MIN_POST_COOLDOWN_MINS    = 45    # never post more often than this
_MAX_POSTS_PER_DAY         = 8     # hard daily cap to avoid spam
_CHECK_INTERVAL_SECS       = 300   # check activity every 5 minutes

_running: bool = False
_thread:  threading.Thread | None = None

# In-memory state
_last_group_activity:  float = time.time()   # timestamp of last observed group message
_last_community_post:  float = 0.0           # timestamp of last community manager post
_posts_today:          int   = 0
_posts_today_date:     str   = ""
_recent_topics:        list[str] = []        # avoid repeating topics
_MAX_RECENT_TOPICS = 20

# ── Discussion topic templates ────────────────────────────────────────────────

_TOPIC_CATEGORIES = [
    "ai_education",
    "productivity",
    "fundzaibot_feature",
    "tech_insight",
    "community_question",
]

_STATIC_TOPICS: list[dict] = [
    {
        "category": "ai_education",
        "text": (
            "🧠 <b>AI Thought of the Day</b>\n\n"
            "Did you know that large language models like GPT-4 and Gemini don't actually "
            "'know' anything in the human sense? They predict the most statistically likely "
            "next token based on patterns in training data.\n\n"
            "This means the quality of your <b>prompt</b> directly determines the quality "
            "of the response. Better questions → better answers.\n\n"
            "What's the most useful prompt technique you've discovered? 💬"
        ),
    },
    {
        "category": "productivity",
        "text": (
            "⚡ <b>Productivity Tip</b>\n\n"
            "Instead of asking an AI 'What should I do?', try giving it context:\n\n"
            "<code>I'm a [role] working on [problem]. I've already tried [X]. "
            "What are 3 approaches I haven't considered?</code>\n\n"
            "Context transforms generic advice into genuinely useful guidance.\n\n"
            "How do you usually start your AI conversations? 👇"
        ),
    },
    {
        "category": "fundzaibot_feature",
        "text": (
            f"💡 <b>{BOT_NAME} Feature Spotlight</b>\n\n"
            "Did you know you can switch between 8 different AI personalities using /style?\n\n"
            "• 🎓 <b>Teacher</b> — explains concepts clearly\n"
            "• 💼 <b>Professional</b> — formal, business-ready\n"
            "• 😎 <b>Friend</b> — casual and relaxed\n"
            "• 🔬 <b>Scientist</b> — analytical and precise\n\n"
            "Each style changes how the AI thinks and responds. Try /style and see which "
            "fits your workflow best!\n\n"
            "Which style do you use most? 🤔"
        ),
    },
    {
        "category": "tech_insight",
        "text": (
            "🔮 <b>The Future of AI Assistants</b>\n\n"
            "In 2024, we saw AI move from chatbots to agents — AI that can take actions, "
            "not just answer questions. In 2025, multi-agent systems started coordinating "
            "complex tasks automatically.\n\n"
            "By 2026, the most powerful AI tools aren't the ones with the biggest models — "
            "they're the ones with the smartest workflows.\n\n"
            "What AI tool has genuinely changed your daily routine? Share below! 👇"
        ),
    },
    {
        "category": "community_question",
        "text": (
            "🌍 <b>Community Poll</b>\n\n"
            "What do you use AI for most often?\n\n"
            "💬 Writing & communication\n"
            "💻 Coding & debugging\n"
            "📖 Research & summarization\n"
            "🎨 Creative projects\n"
            "📊 Data & analysis\n"
            "🤔 Something else entirely\n\n"
            "Drop your answer below — curious to know how this community uses AI! 👇"
        ),
    },
    {
        "category": "ai_education",
        "text": (
            "🤖 <b>AI Myth vs Reality</b>\n\n"
            "<b>Myth:</b> AI will give the same answer to the same question every time.\n"
            "<b>Reality:</b> Most AI models have a 'temperature' setting that introduces "
            "controlled randomness. This is why asking the same question twice often gives "
            "different phrasing, examples, or suggestions.\n\n"
            "You can use this to your advantage — ask the same question 2-3 times and "
            "combine the best parts of each response.\n\n"
            "Have you tried this technique? 🤔"
        ),
    },
    {
        "category": "productivity",
        "text": (
            "📋 <b>The 3-Part Prompt Formula</b>\n\n"
            "The fastest way to get great AI responses:\n\n"
            "<b>1. Role</b> — tell the AI who it is\n"
            "<code>You are an expert marketing copywriter...</code>\n\n"
            "<b>2. Task</b> — be specific about what you want\n"
            "<code>Write a 3-sentence product description for...</code>\n\n"
            "<b>3. Constraint</b> — set boundaries\n"
            "<code>...in a professional tone, max 50 words.</code>\n\n"
            "Role + Task + Constraint = consistently excellent results.\n\n"
            "What's your go-to prompt formula? 👇"
        ),
    },
    {
        "category": "fundzaibot_feature",
        "text": (
            f"📸 <b>{BOT_NAME} Hidden Gem</b>\n\n"
            "You can send a photo to the bot and it will analyze it using Gemini Vision:\n\n"
            "• Identify objects, people, scenes\n"
            "• Describe what's happening in the image\n"
            "• Answer questions about the photo\n"
            "• Extract text from images (OCR)\n\n"
            "Just send any photo to the bot without a command — it works automatically!\n\n"
            "Try it now and share what you discover 🔍"
        ),
    },
    {
        "category": "tech_insight",
        "text": (
            "🔐 <b>AI & Privacy — What You Should Know</b>\n\n"
            "When you use AI services, your conversations may be used to train future models "
            "(depending on the provider and their terms of service).\n\n"
            "Best practices:\n"
            "• Never share passwords, API keys, or sensitive personal info with AI\n"
            "• Use private/incognito mode for sensitive queries where available\n"
            "• Check the privacy policy of any AI service you rely on\n\n"
            f"{BOT_NAME} does NOT store your conversations beyond your active session "
            "(clear with /clear).\n\n"
            "Have any AI privacy questions? Ask away! 👇"
        ),
    },
    {
        "category": "community_question",
        "text": (
            "🚀 <b>Quick Community Check-in</b>\n\n"
            "What's one thing you wish AI could do better right now?\n\n"
            "No wrong answers — every answer helps us understand what the community needs. "
            "The most requested improvements get added to our development backlog!\n\n"
            "Drop your answer below 👇"
        ),
    },
]


# ── Activity tracking ─────────────────────────────────────────────────────────

def record_group_activity() -> None:
    """Call this whenever a real message is sent in the group. Updates activity timestamp."""
    global _last_group_activity
    _last_group_activity = time.time()


def _is_group_inactive() -> bool:
    """True if no group activity for _INACTIVITY_THRESHOLD_MINS minutes."""
    inactive_secs = time.time() - _last_group_activity
    return inactive_secs >= (_INACTIVITY_THRESHOLD_MINS * 60)


def _can_post() -> bool:
    """True if cooldown passed and daily cap not reached."""
    global _posts_today, _posts_today_date

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _posts_today_date != today:
        _posts_today      = 0
        _posts_today_date = today

    if _posts_today >= _MAX_POSTS_PER_DAY:
        return False

    cooldown_secs = time.time() - _last_community_post
    return cooldown_secs >= (_MIN_POST_COOLDOWN_MINS * 60)


# ── Topic selection ───────────────────────────────────────────────────────────

def _pick_topic() -> str | None:
    """Pick a topic that hasn't been used recently."""
    available = [t for t in _STATIC_TOPICS if t["text"] not in _recent_topics]
    if not available:
        # All used — reset and pick any
        _recent_topics.clear()
        available = _STATIC_TOPICS

    chosen = random.choice(available)
    return chosen["text"]


def _try_ai_topic() -> str | None:
    """Try to generate a fresh AI-powered discussion topic via OpenRouter."""
    if not OPENROUTER_API_KEY:
        return None
    try:
        categories = ["AI trends", "productivity hacks", "Telegram automation", "prompt engineering", "tech insights"]
        category = random.choice(categories)
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
                            f"You are the community manager of {BOT_NAME}, an AI-powered Telegram bot platform. "
                            "You write engaging, educational discussion posts for a tech-savvy Telegram community. "
                            "Posts should be informative, conversational, and end with a question to spark discussion. "
                            "Keep posts between 100-180 words. Use relevant emojis. Format with HTML (bold/code tags). "
                            "Never spam or repeat yourself. Focus on genuinely useful content."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Write a community discussion post about: {category}. "
                            "Make it feel natural and conversational, not like a marketing message. "
                            "End with a genuine question to the community."
                        ),
                    },
                ],
                "max_tokens": 300,
                "temperature": 0.85,
            },
            timeout=20,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content and len(content) > 50:
                return content
    except Exception as exc:
        log.debug("community_manager._try_ai_topic: %s", exc)
    return None


# ── Post to group ─────────────────────────────────────────────────────────────

def _post_to_group(text: str) -> bool:
    """Send a message to the Telegram group. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        log.debug("community_manager: no token or group_id configured")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_GROUP_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if r.status_code == 200:
            log.info("Community Manager posted discussion topic to group")
            return True
        log.warning("community_manager post HTTP %d: %s", r.status_code, r.text[:80])
        return False
    except Exception as exc:
        log.warning("community_manager._post_to_group: %s", exc)
        return False


# ── Main action ───────────────────────────────────────────────────────────────

def _trigger_discussion() -> None:
    """Pick a topic and post it to the group."""
    global _last_community_post, _posts_today, _recent_topics

    from services.decision_engine import evaluate
    decision = evaluate(
        action_type="send_community_message",
        title="Post community discussion topic",
        description="Group has been inactive. Post a relevant AI/tech discussion starter.",
        payload={"group_id": TELEGRAM_GROUP_ID},
        confidence=0.92,
        business_risk=False,
        irreversible=False,
    )

    if decision["decision"] != "auto":
        log.info("community_manager: decision engine blocked post — %s", decision["reason"])
        return

    # Try AI-generated topic first, fall back to static
    text = _try_ai_topic() or _pick_topic()
    if not text:
        return

    success = _post_to_group(text)
    if success:
        _last_community_post = time.time()
        _posts_today += 1
        if text not in _recent_topics:
            _recent_topics.append(text)
            if len(_recent_topics) > _MAX_RECENT_TOPICS:
                _recent_topics.pop(0)

        from services.testaudit_core import log_memory
        log_memory(
            "action_taken",
            "Community Manager posted discussion topic",
            detail={"posts_today": _posts_today},
            category="community",
            confidence=0.92,
            outcome="resolved",
        )


# ── Monitor loop ──────────────────────────────────────────────────────────────

def _monitor_loop() -> None:
    log.info("👥 Community Manager started — monitoring group activity")
    time.sleep(120)  # let bot fully start

    while _running:
        try:
            if TELEGRAM_GROUP_ID and _is_group_inactive() and _can_post():
                log.info("Community Manager: group inactive — triggering discussion")
                _trigger_discussion()
        except Exception as exc:
            log.error("community_manager monitor error: %s", exc)

        for _ in range(_CHECK_INTERVAL_SECS):
            if not _running:
                break
            time.sleep(1)


def start_community_manager() -> None:
    global _running, _thread
    if _running:
        return
    if not TELEGRAM_GROUP_ID:
        log.warning("Community Manager: TELEGRAM_GROUP_ID not set — skipping start")
        return
    _running = True
    _thread  = threading.Thread(target=_monitor_loop, daemon=True, name="community-mgr")
    _thread.start()
    log.info("✅ Community Manager started (group: %s)", TELEGRAM_GROUP_ID)


def stop_community_manager() -> None:
    global _running
    _running = False
