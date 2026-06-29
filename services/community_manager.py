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
