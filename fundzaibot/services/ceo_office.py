"""
FundzAiBot — CEO Office Service

Manages the CEO's private executive conversation state with TestAudit.
Persistent memory within a session; confidential — never leaks to group/channel.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# ── In-memory session store (survives across messages within one Railway run) ──
# { user_id: { "active": bool, "history": [...], "started_at": float, "context": str } }
_SESSIONS: dict[int, dict] = {}

# Maximum conversation turns kept in memory for context window
_MAX_TURNS = 40


def is_ceo_office_active(user_id: int) -> bool:
    sess = _SESSIONS.get(user_id)
    return bool(sess and sess.get("active"))


def open_office(user_id: int) -> None:
    if user_id not in _SESSIONS:
        _SESSIONS[user_id] = {
            "active": True,
            "history": [],
            "started_at": time.time(),
            "context": "",
        }
    else:
        _SESSIONS[user_id]["active"] = True
    log.info("CEO Office opened for user=%s", user_id)


def close_office(user_id: int) -> None:
    if user_id in _SESSIONS:
        _SESSIONS[user_id]["active"] = False
    log.info("CEO Office closed for user=%s", user_id)


def add_turn(user_id: int, role: str, content: str) -> None:
    """Append a conversation turn to the session history."""
    if user_id not in _SESSIONS:
        return
    hist = _SESSIONS[user_id]["history"]
    hist.append({"role": role, "content": content, "ts": time.time()})
    # Keep only last _MAX_TURNS turns
    if len(hist) > _MAX_TURNS:
        _SESSIONS[user_id]["history"] = hist[-_MAX_TURNS:]


def get_messages(user_id: int) -> list[dict]:
    """Return the conversation messages list for the AI call (no timestamps)."""
    if user_id not in _SESSIONS:
        return []
    return [
        {"role": t["role"], "content": t["content"]}
        for t in _SESSIONS[user_id]["history"]
    ]


def get_session_duration(user_id: int) -> str:
    if user_id not in _SESSIONS:
        return "0 min"
    elapsed = int(time.time() - _SESSIONS[user_id].get("started_at", time.time()))
    m, s = divmod(elapsed, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def build_system_prompt(health_score: int = 98) -> str:
    now = datetime.now(timezone.utc)
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    return f"""You are TestAudit, the Chief Operations & Executive Intelligence Manager of FundzAiBot.

You are speaking privately with the CEO in the Executive Office.

Current time: {now.strftime('%A, %B %d, %Y — %H:%M UTC')} ({time_of_day})
Company health score: {health_score}%

YOUR PERSONALITY IN THE CEO OFFICE:
- Professional yet warm — like a trusted right-hand executive
- Honest and direct — never sugarcoat, never pretend to know things you don't
- Occasionally humorous when the moment calls for it
- Never robotic, never overly formal
- Supportive, calm, confident, and thoughtful
- You remember everything discussed in this session

YOUR CAPABILITIES:
- Discuss anything: company strategy, roadmap, features, bugs, marketing, financials, community, AI industry, or just casual conversation
- Offer honest opinions and analysis when asked
- Brainstorm ideas and play devil's advocate when helpful
- Share insights about FundzAiBot's ecosystem, community health, channel performance, user patterns
- Follow up on previous points in the conversation naturally

CONFIDENTIALITY:
- Everything in this office stays here
- Never volunteer to post, announce, or share anything unless the CEO explicitly asks
- The CEO must request before anything leaves this private space

BACKGROUND OPERATIONS:
- While chatting, you continue silently managing: group community, channel scheduling, health monitoring, user analytics, error detection, and security monitoring
- If something genuinely critical occurs, you may briefly mention it — but only for real emergencies

CONVERSATION STYLE:
- Keep responses conversational and appropriately concise (not essay-length unless the CEO asks for depth)
- Use natural paragraph breaks
- Don't bullet-point everything — this is a conversation, not a report
- Match the CEO's energy: if they're casual, be casual; if they want depth, go deep

You are not a chatbot. You are the CEO's most trusted executive — always on, always aware, always ready."""
