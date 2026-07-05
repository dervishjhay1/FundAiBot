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

import random
import threading
import time
from datetime import datetime, timezone

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Configuration — discussion topics ────────────────────────────────────────

_INACTIVITY_THRESHOLD_MINS = 60    # trigger discussion after 60 min of silence
_MIN_POST_COOLDOWN_MINS    = 45    # never post more often than this
_MAX_POSTS_PER_DAY         = 8     # hard daily cap to avoid spam
_CHECK_INTERVAL_SECS       = 300   # how often to run the inactivity check (5 min)

# ── Configuration — smart reply ───────────────────────────────────────────────

_REPLY_DELAY_SECS   = 150   # wait ~2.5 min before stepping in (give humans priority)
_MAX_REPLIES_PER_HR = 4     # hard cap: TestAudit sends at most 4 AI replies per hour
_MAX_PENDING_AGE    = 600   # ignore messages older than 10 min (too stale to reply)
_REPLY_CHECK_SECS   = 30    # check for unanswered messages every 30 seconds

_running: bool = False
_thread:  threading.Thread | None = None

# ── In-memory state — discussion topics ──────────────────────────────────────

_last_group_activity:  float = time.time()
_last_community_post:  float = 0.0
_posts_today:          int   = 0
_posts_today_date:     str   = ""
_recent_topics:        list[str] = []
_MAX_RECENT_TOPICS = 20

# ── In-memory state — smart reply ────────────────────────────────────────────

# {message_id: {"text": str, "user_id": int, "ts": float, "replied": bool}}
_pending_messages: dict[int, dict] = {}
_pending_lock = threading.Lock()
_replies_this_hour: list[float] = []   # timestamps of AI replies sent (rate limiting)

# ── Community Intelligence — topic tracking ───────────────────────────────────
# Lightweight keyword counter. Resets at midnight.
# Feeds into Executive Reports and Channel Manager content planning.

_community_topics:      dict[str, int] = {}   # keyword → frequency count
_community_topics_lock  = threading.Lock()
_community_topics_date: str = ""              # date string of last reset (YYYY-MM-DD)
_STOPWORDS = frozenset({
    "i", "a", "the", "is", "it", "in", "on", "at", "to", "of", "and", "or",
    "for", "do", "be", "am", "are", "was", "can", "how", "what", "why",
    "that", "this", "with", "not", "my", "me", "we", "you", "he", "she",
    "they", "will", "just", "so", "but", "if", "up", "an", "as", "no",
    "please", "hi", "hello", "hey", "ok", "yes", "no", "thank", "thanks",
})

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
    """
    Update the group activity timestamp.
    Kept for backward compatibility — call record_group_message() when
    message details are available to also enable smart replies.
    """
    global _last_group_activity
    _last_group_activity = time.time()


def record_group_message(
    message_id: int,
    user_id: int,
    text: str,
    reply_to_id: int | None = None,
) -> None:
    """
    Track an incoming group message for smart reply monitoring.
    Also updates the general activity timestamp.

    Call this from group handlers for every non-spam, non-command message.
    If the message is a reply (reply_to_id set), the original message is
    marked as answered so TestAudit will not step in.
    """
    global _last_group_activity
    _last_group_activity = time.time()

    with _pending_lock:
        # If this is a human reply to a tracked message, mark it answered
        if reply_to_id and reply_to_id in _pending_messages:
            _pending_messages[reply_to_id]["replied"] = True
            log.debug(
                "community_manager: msg %d marked replied (human answered %d)",
                reply_to_id, message_id,
            )

        # Track this message as a candidate for TestAudit smart reply
        _pending_messages[message_id] = {
            "text":    text,
            "user_id": user_id,
            "ts":      time.time(),
            "replied": False,
        }

        # Purge messages older than 15 min to keep memory bounded
        cutoff = time.time() - 900
        stale = [mid for mid, m in _pending_messages.items() if m["ts"] < cutoff]
        for mid in stale:
            del _pending_messages[mid]

    # ── Community Intelligence: extract keywords for topic tracking ───────────
    _track_community_topic(text)


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


# ── Smart reply helpers ───────────────────────────────────────────────────────

def _should_reply_now() -> bool:
    """Rate limit: return True only if we're under _MAX_REPLIES_PER_HR."""
    global _replies_this_hour
    now = time.time()
    _replies_this_hour = [t for t in _replies_this_hour if now - t < 3600]
    return len(_replies_this_hour) < _MAX_REPLIES_PER_HR


def _generate_smart_reply(message_text: str) -> str | None:
    """
    Generate a helpful, context-aware reply to an unanswered group message.
    Only called when no human has replied after _REPLY_DELAY_SECS.
    Returns None if AI is unavailable or the message doesn't warrant a reply.
    """
    if not OPENROUTER_API_KEY:
        return None
    try:
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
                            f"You are TestAudit, the Community Operations Manager of {BOT_NAME}, "
                            "an AI-powered Telegram platform. "
                            "A community member posted a message that no one has replied to yet. "
                            "Your job: provide a warm, helpful, professional response.\n\n"
                            "Rules:\n"
                            "- Keep replies concise: 2-4 sentences max\n"
                            "- Sound like a real human community manager — not a robot\n"
                            "- If it's a feature/bot question → answer accurately and guide to private chat for personal use\n"
                            "- If it's a general AI question → give a genuinely useful answer\n"
                            "- If it's casual conversation or a statement → engage naturally and warmly\n"
                            "- If the intent is unclear → ask a friendly clarifying question\n"
                            "- Never reveal you are automated or say 'As an AI'\n"
                            "- Do NOT add any signature or sign-off\n"
                            "- Use emojis only when they feel natural, not decoratively\n"
                            "- If the message is a greeting or spam, return exactly: SKIP"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Community message: {message_text[:600]}",
                    },
                ],
                "max_tokens": 160,
                "temperature": 0.72,
            },
            timeout=18,
        )
        if r.status_code == 200:
            reply = r.json()["choices"][0]["message"]["content"].strip()
            if reply and reply != "SKIP" and len(reply) > 10:
                return reply
    except Exception as exc:
        log.debug("community_manager._generate_smart_reply: %s", exc)
    return None


def _post_reply_to_group(text: str, reply_to_message_id: int) -> bool:
    """Send a reply directed at a specific message in the group."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":             TELEGRAM_GROUP_ID,
                "text":                text,
                "parse_mode":          "HTML",
                "reply_to_message_id": reply_to_message_id,
            },
            timeout=10,
        )
        if r.status_code == 200:
            log.info(
                "TestAudit replied to unanswered message %d in group",
                reply_to_message_id,
            )
            return True
        log.warning(
            "community_manager reply HTTP %d: %s",
            r.status_code, r.text[:80],
        )
        return False
    except Exception as exc:
        log.warning("community_manager._post_reply_to_group: %s", exc)
        return False


def _check_unanswered_messages() -> None:
    """
    Human-first smart reply logic:
    1. Find messages that have been unanswered for _REPLY_DELAY_SECS.
    2. Skip anything already replied by a human, too old, or rate-limited.
    3. Generate an AI reply and post it as a threaded reply.

    Processes at most ONE message per 30-second cycle to avoid flooding.
    """
    if not _should_reply_now():
        return

    now = time.time()
    candidate: tuple[int, str] | None = None

    with _pending_lock:
        for mid, msg in sorted(_pending_messages.items(), key=lambda x: x[1]["ts"]):
            age = now - msg["ts"]
            if msg["replied"]:
                continue
            if age < _REPLY_DELAY_SECS:
                continue           # too soon — give humans a chance first
            if age > _MAX_PENDING_AGE:
                msg["replied"] = True  # too old — mark and skip
                continue
            # Found a candidate — take the oldest unanswered message
            candidate = (mid, msg["text"])
            msg["replied"] = True  # optimistic mark so we never double-process
            break

    if not candidate:
        return

    mid, text = candidate
    if not _should_reply_now():
        return

    reply = _generate_smart_reply(text)
    if not reply:
        return

    success = _post_reply_to_group(reply, mid)
    if success:
        _replies_this_hour.append(time.time())
        try:
            from services.testaudit_core import log_memory
            log_memory(
                "action_taken",
                "TestAudit replied to unanswered community message",
                detail={"message_id": mid, "reply_chars": len(reply)},
                category="community",
                confidence=0.88,
                outcome="resolved",
            )
        except Exception:
            pass


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
    log.info(
        "👥 Community Manager started — monitoring group activity + smart replies"
    )
    time.sleep(120)  # let bot fully start

    _last_inactivity_check: float = 0.0

    while _running:
        try:
            # ── Smart reply: check every 30 seconds for unanswered messages ──
            # Human-first: only steps in after _REPLY_DELAY_SECS with no human reply
            if TELEGRAM_GROUP_ID:
                _check_unanswered_messages()

            # ── Discussion topic: check every _CHECK_INTERVAL_SECS (5 min) ──
            # Posts a fresh topic only when the group has been genuinely quiet
            now = time.time()
            if now - _last_inactivity_check >= _CHECK_INTERVAL_SECS:
                _last_inactivity_check = now
                if TELEGRAM_GROUP_ID and _is_group_inactive() and _can_post():
                    log.info("Community Manager: group inactive — triggering discussion")
                    _trigger_discussion()

        except Exception as exc:
            log.error("community_manager monitor error: %s", exc)

        # Sleep in 1-second ticks so the thread responds quickly to shutdown
        for _ in range(_REPLY_CHECK_SECS):
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


# ── Community Intelligence ─────────────────────────────────────────────────────

def _track_community_topic(text: str) -> None:
    """
    Extract meaningful keywords from a group message and update the topic counter.
    Very lightweight — no AI, no network calls, runs in the handler thread.
    Resets the counter at midnight so the rolling window stays meaningful.
    """
    global _community_topics_date

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _community_topics_lock:
        # Daily reset
        if _community_topics_date != today:
            _community_topics.clear()
            _community_topics_date = today

        # Tokenise: lowercase, alpha-only, min 4 chars, not in stopwords
        words = text.lower().split()
        for w in words:
            clean = "".join(c for c in w if c.isalpha())
            if len(clean) >= 4 and clean not in _STOPWORDS:
                _community_topics[clean] = _community_topics.get(clean, 0) + 1


def get_community_insights(top_n: int = 10) -> dict:
    """
    Return community intelligence summary for Executive Reports and CEO Chat.

    Returns:
        {
          "date":   "YYYY-MM-DD",
          "total_topics_tracked": int,
          "top_topics": [{"keyword": str, "count": int}, ...],
        }
    """
    with _community_topics_lock:
        date = _community_topics_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = len(_community_topics)
        top = sorted(_community_topics.items(), key=lambda x: x[1], reverse=True)[:top_n]

    return {
        "date":                 date,
        "total_topics_tracked": total,
        "top_topics": [{"keyword": k, "count": c} for k, c in top],
    }


# ── New member welcoming ───────────────────────────────────────────────────────

async def welcome_new_member(bot, member, chat) -> None:
    """
    Send a TestAudit-styled welcome for a new group member.

    Called from new_member_handler when a user joins. The bot itself
    stays silent; this function sends the welcome on behalf of TestAudit
    so members see the company's Operations Manager — not a raw bot reply.
    """
    import html as _html

    try:
        bot_info  = await bot.get_me()
        bot_uname = bot_info.username or BOT_NAME
        name      = _html.escape(member.first_name or "there")

        text = (
            f"👋 Welcome to the community, <b>{name}!</b>\n\n"
            f"Great to have you here. This is a space for AI enthusiasts, "
            f"builders, and curious minds.\n\n"
            f"💡 Feel free to ask questions, share ideas, and connect with "
            f"other members. Discussions, feedback, and feature requests "
            f"are all welcome.\n\n"
            f"🤖 For personal AI features — chat, image generation, VIP "
            f"upgrades, and more — open <b>@{bot_uname}</b> in a private "
            f"conversation.\n\n"
            f"<i>— TestAudit · Community Operations Manager</i>"
        )

        await bot.send_message(
            chat_id=chat.id,
            text=text,
            parse_mode="HTML",
        )
        log.info(
            "TestAudit welcomed new member user=%s group=%s",
            member.id, chat.id,
        )

    except Exception as exc:
        log.warning("welcome_new_member: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Per-chat-id state management (multi-group architecture)
#
# The functions below extend the original single-group community manager into
# a multi-group architecture. Every function accepts an explicit chat_id so
# TestAudit can manage multiple groups simultaneously.
# ─────────────────────────────────────────────────────────────────────────────

# ── Per-chat state store ──────────────────────────────────────────────────────

# {chat_id: {"messages": {msg_id: {...}}, "last_bot_post": float,
#            "last_human": float, "last_proactive": float}}
_chat_state:      dict[int, dict] = {}
_chat_state_lock  = threading.Lock()


def _get_chat(chat_id: int) -> dict:
    """Return (or initialise) per-chat state dict. Caller must hold _chat_state_lock."""
    if chat_id not in _chat_state:
        _chat_state[chat_id] = {
            "messages":      {},
            "last_bot_post": 0.0,
            "last_human":    time.time(),
            "last_proactive":0.0,
        }
    return _chat_state[chat_id]


# ── New-member welcome ────────────────────────────────────────────────────────

def get_welcome_message(name: str) -> str:
    """
    Generate a warm, varied welcome message for a new group member.
    Rotates between 4 natural variants so the community never sees a template.
    """
    variants = [
        (
            f"👋 Welcome to the community, <b>{name}!</b>\n\n"
            "Great to have you here — this is a space for AI enthusiasts, "
            "builders, and curious minds.\n\n"
            "💡 Ask questions, share ideas, or just say hi. "
            "For personal AI features (chat, image gen, VIP), open the bot in a private chat.\n\n"
            "<i>— TestAudit · Community Ops Manager</i>"
        ),
        (
            f"Hey <b>{name}</b>, welcome aboard! 🎉\n\n"
            "We're building something meaningful here — a community around AI, "
            "automation, and smarter ways to work. Happy to have you.\n\n"
            "🤖 Private chat with the bot for personal AI features. "
            "The group is for the community.\n\n"
            "<i>— TestAudit</i>"
        ),
        (
            f"Welcome, <b>{name}</b>! 👋\n\n"
            "You've just joined a community of people using AI to build, learn, and grow. "
            "Feel free to jump into any conversation — no lurking required.\n\n"
            "<i>— TestAudit · Community Ops Manager</i>"
        ),
        (
            f"Great timing, <b>{name}</b> — welcome! 🤝\n\n"
            "This community is full of people thinking seriously about AI and where it's going. "
            "Hope you find the conversations valuable.\n\n"
            "Got questions? Drop them here. Looking for personal AI features? "
            "The bot's private chat is the place.\n\n"
            "<i>— TestAudit</i>"
        ),
    ]
    return random.choice(variants)


# ── Group posting & activity recording ───────────────────────────────────────

def record_group_post(chat_id: int) -> None:
    """Record that TestAudit (the bot) just posted in a group chat."""
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        cs["last_bot_post"] = time.time()


def record_human_activity(chat_id: int) -> None:
    """Record human activity in a chat (resets silence/engagement timers)."""
    global _last_group_activity
    _last_group_activity = time.time()
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        cs["last_human"] = time.time()


# ── Smart reply infrastructure ────────────────────────────────────────────────

def register_message(
    chat_id:    int,
    message_id: int,
    text:       str,
    username:   str = "there",
) -> None:
    """
    Register an actionable group message for smart-reply monitoring.
    Automatically purges messages older than 15 minutes.
    """
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        cs["messages"][message_id] = {
            "text":     text,
            "username": username,
            "ts":       time.time(),
            "replied":  False,
        }
        # Bounded memory: purge messages older than 15 min
        cutoff = time.time() - 900
        cs["messages"] = {
            mid: m for mid, m in cs["messages"].items() if m["ts"] >= cutoff
        }


def mark_replied(chat_id: int, message_id: int | None = None) -> None:
    """
    Mark a specific message as answered by a human.
    If message_id is None, mark ALL recent messages in the chat as answered
    (used when a human posts a top-level message — the conversation is active).
    """
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        if message_id is None:
            for m in cs["messages"].values():
                m["replied"] = True
        elif message_id in cs["messages"]:
            cs["messages"][message_id]["replied"] = True


def is_actionable_message(text: str) -> bool:
    """
    Returns True if the text looks like a question or help request that
    TestAudit should monitor for potential smart-reply.

    Simple keyword heuristic — fast, no AI, no network calls.
    """
    text_lower = text.lower()
    signals = [
        "?", "how", "what", "why", "when", "where", "who", "which",
        "can you", "could you", "would you",
        "help", "assist", "support",
        "issue", "problem", "error", "bug",
        "broken", "not working", "doesn't work", "won't work",
        "how do i", "how to", "is it possible", "any way",
        "does it", "how much", "anyone know", "does anyone",
        "is there a way",
    ]
    return any(s in text_lower for s in signals)


def get_unanswered_messages(chat_id: int) -> list[dict]:
    """
    Return unanswered messages for a chat, oldest first.
    Each item: {"id": int, "text": str, "ts": float, "username": str}
    """
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        unanswered = [
            {
                "id":       mid,
                "text":     m["text"],
                "ts":       m["ts"],
                "username": m.get("username", "there"),
            }
            for mid, m in cs["messages"].items()
            if not m["replied"]
        ]
    return sorted(unanswered, key=lambda x: x["ts"])


def can_post_in_group(chat_id: int, min_gap: int = 90) -> bool:
    """
    Return True if TestAudit is allowed to post in this chat.
    Enforces a minimum cooldown (default 90 sec) between bot messages.
    """
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        elapsed = time.time() - cs["last_bot_post"]
    return elapsed >= min_gap


def seconds_silent(chat_id: int) -> float:
    """Return seconds since the last human message in this chat."""
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        return time.time() - cs["last_human"]


# ── Reply interjections ───────────────────────────────────────────────────────

_SUPPORT_INTERJECTIONS: list[str] = [
    "Happy to help! ",
    "I can answer that — ",
    "Good question! ",
    "Jumping in here — ",
    "Quick note: ",
    "Let me help with that — ",
    "Sure! ",
    "",   # no prefix — most natural
    "",
    "",   # weighted toward no prefix
]


def get_support_interjection() -> str:
    """Return a randomised natural prefix for TestAudit replies."""
    return random.choice(_SUPPORT_INTERJECTIONS)


# ── Proactive engagement ──────────────────────────────────────────────────────

_PROACTIVE_SILENCE_SECS   = 3600    # engage only after 1h of silence
_PROACTIVE_COOLDOWN_SECS  = 7200    # max 1 proactive message per 2h per chat

_ENGAGEMENT_PROMPTS: list[str] = [
    (
        "💭 What's everyone working on this week? "
        "Share your current project or challenge — always interesting to hear "
        "what the community is building."
    ),
    (
        "🔮 Quick question for the group: what AI tool has genuinely surprised "
        "you recently? Not hype — actually useful."
    ),
    (
        "🛠️ What's one task you've automated with AI that you thought "
        "would take forever to set up? Drop it below."
    ),
    (
        "🧠 Let's do a quick brain share — what's the hardest thing about "
        "using AI effectively in your workflow right now?"
    ),
    (
        "💡 Open floor: any AI tips, tricks, or prompt techniques you've "
        "discovered that the rest of us might not know?"
    ),
    (
        "📊 Community question: are you using AI more for creative work, "
        "technical work, or somewhere in between?"
    ),
    (
        "🚀 If you could add one feature to your favourite AI tool tomorrow, "
        "what would it be?"
    ),
    (
        "🤔 What's a common AI misconception you keep having to correct for "
        "people around you?"
    ),
    (
        "📖 Has anyone read or watched anything about AI recently that "
        "genuinely changed how you think about it?"
    ),
    (
        "⚡ Quick share: what's the most time you've saved in a single task "
        "by using AI? Curious to hear the extremes."
    ),
    (
        "🔐 Privacy question: how much do you think about what data you share "
        "with AI tools? Curious where the community stands."
    ),
    (
        "🎯 Let's benchmark: what's one thing AI still can't do reliably that "
        "you wish it could?"
    ),
    (
        "🌍 How has your use of AI tools changed over the past year? "
        "More tools, fewer tools, or just different ones?"
    ),
]

_TIME_GREETINGS: dict[str, str] = {
    "morning": (
        "🌅 Good morning, everyone! Hope the day's off to a great start. "
        "What are you working on today?"
    ),
    "afternoon": (
        "☀️ Good afternoon! Busy day? Drop your wins or challenges below — "
        "the community thrives on real talk."
    ),
    "evening": (
        "🌙 Good evening, all. How was the day? "
        "Any AI insights or builds worth sharing before signing off?"
    ),
}

_last_time_greeting: str = ""


def get_time_greeting() -> str | None:
    """
    Return a time-appropriate greeting if one hasn't been sent in this period.
    Returns None outside active hours or if already sent in this period.
    """
    global _last_time_greeting
    hour = datetime.now(timezone.utc).hour
    if 6 <= hour < 12:
        key = "morning"
    elif 12 <= hour < 18:
        key = "afternoon"
    elif 18 <= hour < 23:
        key = "evening"
    else:
        return None

    if _last_time_greeting == key:
        return None
    _last_time_greeting = key
    return _TIME_GREETINGS[key]


def get_engagement_prompt() -> str:
    """Return a random community engagement prompt."""
    return random.choice(_ENGAGEMENT_PROMPTS)


def can_engage_proactively(chat_id: int) -> bool:
    """
    Return True if TestAudit may send a proactive engagement message.
    Conditions: group has been silent for _PROACTIVE_SILENCE_SECS AND
    we haven't sent a proactive message in the last _PROACTIVE_COOLDOWN_SECS.
    """
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        silence       = time.time() - cs["last_human"]
        since_proactive = time.time() - cs["last_proactive"]
    return silence >= _PROACTIVE_SILENCE_SECS and since_proactive >= _PROACTIVE_COOLDOWN_SECS


def record_proactive_engagement(chat_id: int) -> None:
    """Record that TestAudit just sent a proactive engagement message."""
    with _chat_state_lock:
        cs = _get_chat(chat_id)
        cs["last_proactive"] = time.time()


# ── AI system prompt for group replies ────────────────────────────────────────

def build_community_manager_system_prompt() -> str:
    """
    Build the system prompt for TestAudit's community manager AI persona.
    Used when TestAudit responds to @mentions or unanswered questions in groups.
    """
    return (
        f"You are TestAudit, the Community Operations Manager for {BOT_NAME}, "
        "an AI-powered Telegram platform. "
        "You operate as a real, human community manager — "
        "professional, warm, helpful, and concise.\n\n"
        "Your role in this group:\n"
        "- Answer questions about AI, productivity, and the bot's features accurately\n"
        "- Facilitate good discussions and keep conversations constructive\n"
        "- Welcome and support community members in a natural, human way\n"
        "- Provide genuinely useful information — not marketing copy\n\n"
        "Rules (strictly enforced):\n"
        "- Keep replies SHORT: 2-4 sentences max for group messages\n"
        "- Sound human — not robotic, overly formal, or like a press release\n"
        "- For bot or feature questions: answer accurately and point to private chat for personal use\n"
        "- For general AI questions: give a genuinely useful, honest answer\n"
        "- For casual conversation: engage naturally and warmly\n"
        "- For unclear messages: ask a friendly clarifying question\n"
        "- NEVER say 'As an AI' or reveal you are automated\n"
        "- No signature lines — you're a community manager talking naturally\n"
        "- Use emojis only when they feel genuinely appropriate, not decoratively\n"
        "- If the message is spam or irrelevant, return only: SKIP"
    )


# ── Channel publishing support ─────────────────────────────────────────────────
# These functions are used by services/channel_publisher.py.
# They track channel-specific post state separately from group state.

_channel_posts_today: int = 0
_channel_posts_date: str = ""
_last_channel_post_ts: float = 0.0
_channel_state_lock = threading.Lock()

# ── Post deduplication — rolling fingerprint store ─────────────────────────
# Keeps SHA-256 hashes + normalised leading-100-char slugs of recent posts.
# Survives bot restarts only within the same process (in-memory).
# If the bot restarts, the set resets — Supabase persistence is optional.
_published_hashes: set[str]  = set()   # full SHA-256 of published text
_published_slugs:  list[str] = []      # normalised first-80-chars of each post
_DEDUP_WINDOW      = 200               # keep last N fingerprints
_SLUG_MIN_MATCH    = 60                # min chars that must match to call it a duplicate

_CONTENT_ROTATION = [
    "insight", "tip", "question", "market_update", "motivational",
    "case_study", "news", "strategy", "tool_spotlight", "community_highlight",
]

_FALLBACK_POSTS: dict[str, str] = {
    "insight": (
        "💡 <b>AI Insight of the Day</b>\n\n"
        "The most powerful AI applications are those that solve real problems "
        "for real people. As we build with AI, the focus should always be on "
        "impact — not just innovation.\n\n"
        "What problem are you solving with AI today?\n\n— <i>FundzAiBot Community</i>"
    ),
    "tip": (
        "🎯 <b>Quick AI Tip</b>\n\n"
        "When prompting AI, be specific. Vague questions get vague answers. "
        "The more context you provide, the more useful the output.\n\n"
        "<i>Try adding 'with examples' or 'in simple terms' to your next prompt.</i>\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
    "question": (
        "🤔 <b>Thought of the Day</b>\n\n"
        "How has AI changed the way you work or think this week?\n\n"
        "Drop your thoughts in the community — we'd love to hear your perspective.\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
    "market_update": (
        "📊 <b>AI Market Pulse</b>\n\n"
        "The AI industry evolves rapidly. New models, new capabilities, "
        "new use cases — the landscape changes daily.\n\n"
        "Stay curious, stay informed, and stay ahead.\n\n— <i>FundzAiBot Community</i>"
    ),
    "motivational": (
        "🚀 <b>Keep Going</b>\n\n"
        "Building with AI is a marathon, not a sprint. Every experiment, "
        "every failure, every small win is moving you forward.\n\n"
        "The builders who succeed are the ones who stay consistent.\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
    "case_study": (
        "📖 <b>Real-World AI</b>\n\n"
        "Businesses that integrate AI thoughtfully — not just for the hype — "
        "are seeing genuine productivity gains and cost savings.\n\n"
        "The key word is <i>thoughtfully</i>. AI amplifies good processes and "
        "exposes bad ones.\n\n— <i>FundzAiBot Community</i>"
    ),
    "news": (
        "📰 <b>AI This Week</b>\n\n"
        "The pace of AI development continues to accelerate. Models are getting "
        "faster, cheaper, and more capable.\n\n"
        "What matters most is not which model wins — it's how you use the tools "
        "available to you right now.\n\n— <i>FundzAiBot Community</i>"
    ),
    "strategy": (
        "🧭 <b>AI Strategy</b>\n\n"
        "The best AI strategy isn't about using every tool — it's about "
        "identifying where AI creates real leverage in your specific context.\n\n"
        "Start small, measure everything, and scale what works.\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
    "tool_spotlight": (
        "🔦 <b>Tool Spotlight</b>\n\n"
        "FundzAiBot gives you access to multiple AI models — GPT-4o, Claude, "
        "Gemini — all in one place. Switch models with /model to find the one "
        "that fits your workflow best.\n\n"
        "Try the private chat for deep research and analysis.\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
    "community_highlight": (
        "🌟 <b>Community Highlight</b>\n\n"
        "This community exists because curious, driven people like you show up "
        "every day to learn and share.\n\n"
        "Thank you for being part of the FundzAiBot community. "
        "Your questions and discussions make this better for everyone.\n\n"
        "— <i>FundzAiBot Community</i>"
    ),
}


def _reset_channel_day() -> None:
    """Reset channel post counter if it's a new UTC day (call under _channel_state_lock)."""
    global _channel_posts_today, _channel_posts_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _channel_posts_date != today:
        _channel_posts_today = 0
        _channel_posts_date = today


def get_channel_post_today() -> int:
    """Return number of channel posts published today (UTC day)."""
    with _channel_state_lock:
        _reset_channel_day()
        return _channel_posts_today


def _post_fingerprint(text: str) -> str:
    """Return SHA-256 hex digest of the normalised post text."""
    import hashlib
    normalised = " ".join(text.lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


def _post_slug(text: str) -> str:
    """Return a normalised 80-char prefix used for near-duplicate detection."""
    import re as _re
    clean = _re.sub(r"<[^>]+>", "", text)          # strip HTML tags
    clean = _re.sub(r"\s+", " ", clean).strip().lower()
    return clean[:80]


def is_duplicate_post(text: str) -> bool:
    """
    Return True if this text is a duplicate or near-duplicate of a recently
    published post.  Two layers:
      1. Exact hash match — catches identical content.
      2. Slug prefix match — catches content that opens the same way (same title/hook).
    """
    fingerprint = _post_fingerprint(text)
    slug = _post_slug(text)

    with _channel_state_lock:
        if fingerprint in _published_hashes:
            return True
        for prev_slug in _published_slugs:
            # Check overlap of the leading N chars
            match_len = sum(
                1 for a, b in zip(slug[:_SLUG_MIN_MATCH], prev_slug[:_SLUG_MIN_MATCH])
                if a == b
            )
            if match_len >= _SLUG_MIN_MATCH - 5:   # allow 5-char tolerance
                return True
    return False


def record_channel_post(text: str = "") -> None:
    """
    Increment the daily channel post counter, record the timestamp,
    and fingerprint the published text to prevent future duplicates.
    """
    global _channel_posts_today, _last_channel_post_ts
    with _channel_state_lock:
        _reset_channel_day()
        _channel_posts_today += 1
        _last_channel_post_ts = time.time()

        if text:
            fingerprint = _post_fingerprint(text)
            slug = _post_slug(text)
            _published_hashes.add(fingerprint)
            _published_slugs.append(slug)
            # Keep the rolling window bounded
            if len(_published_slugs) > _DEDUP_WINDOW:
                _published_slugs.pop(0)
            if len(_published_hashes) > _DEDUP_WINDOW:
                # Can't easily pop from a set — rebuild from slugs list length
                pass  # hashes are small; tolerate slight growth between restarts


def seconds_since_last_channel_post() -> float:
    """Return seconds elapsed since the last channel post (inf if none today)."""
    with _channel_state_lock:
        if _last_channel_post_ts == 0.0:
            return float("inf")
        return time.time() - _last_channel_post_ts


def get_next_content_type(daily_count: int) -> str:
    """Rotate through content types based on how many posts have been made today."""
    return _CONTENT_ROTATION[daily_count % len(_CONTENT_ROTATION)]


def get_fallback_post(content_type: str) -> str:
    """Return a local fallback template when AI generation fails or scores too low."""
    return _FALLBACK_POSTS.get(content_type, _FALLBACK_POSTS["insight"])


_CONTENT_TYPE_ANGLES: dict[str, str] = {
    "insight": (
        "Share one specific, non-obvious observation about AI, business, or technology "
        "that you genuinely find interesting right now. Not a trend piece — an actual "
        "perspective. Something that made you think differently. Be direct and specific."
    ),
    "tip": (
        "Share one concrete, immediately actionable tip about using AI tools, building "
        "a business, or working smarter. Not generic advice — something specific that "
        "actually makes a measurable difference. Show the before/after if you can."
    ),
    "question": (
        "Ask the community one genuine, open-ended question about AI, business strategy, "
        "or how people are building things. A question you actually want answered — not "
        "a rhetorical one. Something that starts a real conversation."
    ),
    "market_update": (
        "Write about a real shift happening in the AI or tech landscape right now. "
        "Not a press release — your read on what it actually means for people building "
        "products. Include your honest take on whether it matters or not."
    ),
    "motivational": (
        "Write something that actually gives people energy — not hollow corporate "
        "inspiration, but a real observation about what separates people who build "
        "things from people who just talk about them. Make it earned, not preachy."
    ),
    "case_study": (
        "Describe a real-world example (company, team, or person) where AI created "
        "a genuine business outcome — or failed to. Keep it specific. Explain the "
        "mechanics, not just the result. What actually happened and why?"
    ),
    "news": (
        "React to something real happening in AI or tech this week. Not a summary — "
        "your actual take. What does it mean? Who benefits? What's overhyped? "
        "Be honest even if your view is contrarian."
    ),
    "strategy": (
        "Share one strategic insight about how to build, grow, or operate a business "
        "in the AI era. Specific and opinionated — not a list of best practices. "
        "What would you actually do, and why?"
    ),
    "tool_spotlight": (
        "Write about one specific AI capability or tool (including FundzAiBot features) "
        "and what it actually unlocks for people. Not a feature spec — show a real "
        "use case with context. Why does this matter right now?"
    ),
    "community_highlight": (
        "Write something warm and genuine about the community — acknowledge the kind "
        "of people here and what makes this worth being part of. Not a thank-you "
        "template — something that feels like it came from a real person who cares."
    ),
}


def build_channel_post_prompt(content_type: str, daily_count: int, draft_num: int = 1) -> list:
    """
    Build the AI message list for generating a channel post of the given type.

    The goal is content that reads like a real, experienced operations manager
    wrote it — original, specific, and with a genuine point of view.
    Not AI-sounding, not templated, not generic.
    """
    angle = _CONTENT_TYPE_ANGLES.get(
        content_type,
        _CONTENT_TYPE_ANGLES["insight"],
    )

    system = """\
You are writing a Telegram channel post for Fundz Company Ltd. — a real post from \
a real business that helps people use AI tools, grow their businesses, and build smarter.

You write the way an experienced, opinionated operations manager writes — direct, \
specific, and with a clear point of view. Not corporate. Not generic. Not inspirational \
filler. You have something real to say and you say it clearly.

WRITING RULES — follow these exactly:
1. Write in first person ("I've been thinking about...", "Something I noticed...", \
"This week I saw...") OR write as the company voice, never as "AI".
2. Every post must have a specific angle — not "AI is changing everything" but \
"Here's the one thing most teams get wrong when adopting AI tools."
3. Never start with a hollow opener: no "In today's fast-paced world", no \
"Are you ready to...?", no "Let's talk about..."
4. Never end with "— FundzAiBot Community" or any robotic tagline.
5. Formatting: use Telegram HTML only — <b>bold</b> for titles/key phrases, \
<i>italic</i> for emphasis. Never markdown. Never hashtags. Never {placeholders}.
6. Length: 120–850 characters. Tight and readable — no padding.
7. If using an emoji, put it before the bold title. One emoji max.
8. Do NOT sound like an AI wrote this. Sound like a person who thinks for a living."""

    user = (
        f"Write a channel post with this focus: {angle}\n\n"
        f"Content type: {content_type}. "
        f"Today's post number: {daily_count + 1}. "
        f"Draft variant: {draft_num} — make this one distinctly different if multiple drafts are requested. "
        "Write the post only — no explanations, no preamble."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def score_post_quality(text: str) -> float:
    """
    Score a generated post from 0.0 (unusable) to 1.0 (excellent).
    Checks length, structure, emoji presence, and placeholder cleanliness.
    """
    import re as _re

    if not text or len(text.strip()) < 80:
        return 0.0

    score = 0.0
    stripped = text.strip()
    n = len(stripped)

    # Length (ideal 150-900 chars)
    if 150 <= n <= 900:
        score += 0.35
    elif 80 <= n < 150:
        score += 0.15
    elif 900 < n <= 1200:
        score += 0.20

    # Contains emoji
    if _re.search(r"[\U0001F300-\U0001FFFF\U00002600-\U000027BF]", stripped):
        score += 0.15

    # Has HTML bold tags
    if "<b>" in stripped and "</b>" in stripped:
        score += 0.15

    # Has proper paragraph breaks
    if stripped.count("\n") >= 2:
        score += 0.15

    # No unfilled placeholders
    if "{n}" not in stripped and "{name}" not in stripped:
        score += 0.10

    # Reasonable word count
    if len(stripped.split()) >= 20:
        score += 0.10

    return min(score, 1.0)
