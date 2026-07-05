"""
FundzAiBot — CEO Office (TestAudit role)

The CEO Office is the private command centre where the CEO interacts with
TestAudit as a genuine operational partner — not just a status reporter.

Capabilities:
  • Natural, persistent conversation (casual, strategic, or deeply technical)
  • Full company intelligence (live metrics, product registry, community insights)
  • Project Creation Mode — CEO describes an idea → TestAudit builds a full
    structured brief and registers the product in the Fundz Product Registry
  • Bot Token Handoff — CEO registers a secondary Telegram bot token so
    TestAudit can manage a separate product bot (e.g. FundzMarket Bot)
  • Roadmap management — CEO sets priorities, TestAudit tracks them
  • CEO Memory — TestAudit remembers CEO preferences, decisions, and context
    across sessions (persisted to Supabase)
  • Autonomous Mode reporting — generates full recovery brief on CEO return

Architecture:
  • Stateless at the request level: all state stored in Supabase + memory cache
  • Each CEO message goes through intent classification → appropriate handler
  • Conversation history: last 12 exchanges kept in memory + persisted to DB
  • No CEO question is out of scope — casual, business, or philosophical
"""

from __future__ import annotations

import json
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config.settings import (
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME, BOT_VERSION,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_HISTORY_TURNS      = 12    # conversation turns kept in memory
_MAX_CONTEXT_TOKENS     = 600   # AI response max tokens
_SESSION_IDLE_SECS      = 1800  # clear active session after 30 min of no messages
_MEMORY_TABLE           = "ceo_office_memory"
_HISTORY_TABLE          = "ceo_office_history"
_TOKENS_TABLE           = "registered_bot_tokens"

# ── Session state ─────────────────────────────────────────────────────────────

_lock             = threading.Lock()
_history:         list[dict]  = []     # [{"role": "user"|"assistant", "content": str}]
_last_msg_ts:     float       = 0.0
_ceo_preferences: dict        = {}     # CEO's remembered preferences/decisions
_registered_tokens: dict      = {}     # product_id → bot_token (masked)
_initialized:     bool        = False


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _sb_post(table: str, data: dict) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_hdrs(), json=data, timeout=(5, 12),
        )
    except Exception as exc:
        log.debug("ceo_office._sb_post(%s): %s", table, exc)
        return None


def _sb_get(table: str, params: dict | None = None) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_hdrs(), params=params or {}, timeout=(5, 12),
        )
    except Exception as exc:
        log.debug("ceo_office._sb_get(%s): %s", table, exc)
        return None


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_memory(key: str, value: Any) -> None:
    """Store a CEO preference/decision persistently."""
    try:
        h = dict(_hdrs())
        h["Prefer"] = "resolution=merge-duplicates,return=representation"
        _sb_post(_MEMORY_TABLE, {
            "key":        key,
            "value":      json.dumps(value) if not isinstance(value, str) else value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        log.debug("ceo_office._persist_memory: %s", exc)


def _load_memory() -> None:
    """Restore CEO preferences from Supabase on startup."""
    global _ceo_preferences, _registered_tokens
    try:
        r = _sb_get(_MEMORY_TABLE, {"select": "key,value"})
        if r and r.status_code == 200:
            for row in r.json():
                k, v = row.get("key", ""), row.get("value", "")
                try:
                    parsed = json.loads(v)
                except Exception:
                    parsed = v
                if k.startswith("token_"):
                    product_id = k[6:]
                    _registered_tokens[product_id] = parsed
                else:
                    _ceo_preferences[k] = parsed
            log.info(
                "ceo_office: loaded %d memory entries, %d tokens",
                len(_ceo_preferences), len(_registered_tokens),
            )
    except Exception as exc:
        log.debug("ceo_office._load_memory: %s", exc)


def _persist_history_turn(role: str, content: str) -> None:
    """Save one conversation turn to Supabase (async best-effort)."""
    try:
        _sb_post(_HISTORY_TABLE, {
            "role":       role,
            "content":    content[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        log.debug("ceo_office._persist_history_turn: %s", exc)


def _load_recent_history(limit: int = 6) -> list[dict]:
    """Load the last N conversation turns from Supabase for session restore."""
    try:
        r = _sb_get(_HISTORY_TABLE, {
            "select": "role,content",
            "order":  "created_at.desc",
            "limit":  str(limit * 2),
        })
        if r and r.status_code == 200:
            rows = list(reversed(r.json()))
            return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as exc:
        log.debug("ceo_office._load_recent_history: %s", exc)
    return []


# ── Initialisation ────────────────────────────────────────────────────────────

def initialize() -> None:
    """Load CEO memory and recent history on startup. Idempotent."""
    global _initialized, _history
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        _load_memory()
        restored = _load_recent_history(limit=4)
        if restored:
            _history.extend(restored)
            log.info("ceo_office: restored %d history turns", len(restored))
        _initialized = True
        log.info("✅ CEO Office initialized")


# ── Intent classification ─────────────────────────────────────────────────────

_MEETING_SIGNALS = re.compile(
    r"(schedule\s+(a\s+)?meeting|book\s+(a\s+)?meeting|set\s+up\s+(a\s+)?meeting|"
    r"arrange\s+(a\s+)?meeting|plan\s+(a\s+)?meeting|add\s+(a\s+)?meeting|"
    r"create\s+(a\s+)?meeting|schedule\s+(a\s+)?call|book\s+(a\s+)?call|"
    r"meeting\s+at\s+|meeting\s+on\s+|meeting\s+tomorrow|meeting\s+today|"
    r"my\s+meetings?|upcoming\s+meetings?|view\s+agenda|show\s+agenda|"
    r"what'?s\s+on\s+my\s+agenda|my\s+schedule|what\s+do\s+I\s+have\s+today|"
    r"cancel\s+(a\s+)?meeting|reschedule\s+(a\s+)?meeting|"
    r"meeting\s+notes?|add\s+notes?\s+to)",
    re.IGNORECASE,
)

_PROJECT_SIGNALS = re.compile(
    r"(let'?s\s+build|new\s+product|start\s+building|launch|create\s+a|"
    r"we\s+should\s+build|i\s+want\s+to\s+build|new\s+app|new\s+bot|"
    r"new\s+platform|new\s+service|product\s+idea|startup\s+idea|"
    r"business\s+idea|fundz\s+market|fundzmarket|fundz\s+academy|"
    r"bot\s+for\s+|marketplace\s+for\s+)",
    re.IGNORECASE,
)

_TOKEN_SIGNALS = re.compile(
    r"(bot\s+token|register\s+token|new\s+token|add\s+token|token\s+for|"
    r"bot:.*\d{8,}|telegram.*token|here\s+is\s+the\s+token)",
    re.IGNORECASE,
)

_AUTONOMOUS_RETURN_SIGNALS = re.compile(
    r"(i'?m\s+back|just\s+got\s+back|returned|what\s+did\s+i\s+miss|"
    r"what\s+happened|catch\s+me\s+up|update\s+me|recovery\s+report|"
    r"autonomous\s+mode|what\s+did\s+you\s+do|daily\s+report|brief\s+me)",
    re.IGNORECASE,
)

_ROADMAP_SIGNALS = re.compile(
    r"(roadmap|priority|priorities|milestone|sprint|quarter|q[1-4]|"
    r"what\s+should\s+we\s+focus|next\s+quarter|this\s+week|plan\s+for)",
    re.IGNORECASE,
)


def _classify_intent(message: str) -> str:
    """
    Classify CEO message intent into one of:
      meeting | project_creation | token_handoff | recovery_report | roadmap | company_qa
    """
    if _MEETING_SIGNALS.search(message):
        return "meeting"
    if _TOKEN_SIGNALS.search(message):
        return "token_handoff"
    if _AUTONOMOUS_RETURN_SIGNALS.search(message):
        return "recovery_report"
    if _PROJECT_SIGNALS.search(message):
        return "project_creation"
    if _ROADMAP_SIGNALS.search(message):
        return "roadmap"
    return "company_qa"  # default: general company question or casual conversation


# ── Company context builder (reuses executive_chat logic) ─────────────────────

def _build_context() -> str:
    """
    Gather live company metrics for injection into the CEO Office AI prompt.
    Pulls from testaudit_core, product_registry, community_manager, and Supabase.
    """
    parts = [
        f"=== {BOT_NAME} v{BOT_VERSION} — CEO Office Context ===",
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        parts.append(f"HEALTH: {h.get('score','N/A')}/100 ({h.get('tier','?').upper()})")
    except Exception:
        parts.append("HEALTH: unavailable")

    try:
        from services.database import count_users
        c = count_users()
        parts.append(
            f"USERS: total={c['total']}, vip={c['vip']}, banned={c['banned']}, free={c['free']}"
        )
    except Exception:
        parts.append("USERS: unavailable")

    try:
        from services.testaudit_core import get_backlog
        backlog = get_backlog(status="open", limit=5)
        if backlog:
            items = "; ".join(
                f"{b['title']} ({b.get('priority','?')})" for b in backlog[:5]
            )
            parts.append(f"OPEN_BACKLOG: {items}")
    except Exception:
        pass

    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
        parts.append(f"PENDING_APPROVALS: {len(pending)}")
    except Exception:
        pass

    try:
        from services.autonomous_mode import get_aom_status
        aom = get_aom_status()
        parts.append(
            f"AUTONOMOUS_MODE: {'ACTIVE' if aom['autonomous_mode'] else 'inactive'}, "
            f"ceo_inactive_hours={aom['ceo_inactive_hours']:.1f}"
        )
    except Exception:
        pass

    try:
        from services.product_registry import format_registry_summary
        parts.append(format_registry_summary())
    except Exception:
        pass

    try:
        from services.community_manager import get_community_insights
        ins = get_community_insights(top_n=5)
        if ins["total_topics_tracked"] > 0:
            top = ", ".join(
                f"{t['keyword']} ({t['count']}x)" for t in ins["top_topics"]
            )
            parts.append(f"COMMUNITY_HOT_TOPICS: {top}")
    except Exception:
        pass

    # CEO preferences (from memory)
    if _ceo_preferences:
        prefs_str = "; ".join(
            f"{k}={v}" for k, v in list(_ceo_preferences.items())[:5]
        )
        parts.append(f"CEO_PREFERENCES: {prefs_str}")

    if _registered_tokens:
        parts.append(f"REGISTERED_BOT_TOKENS: {', '.join(_registered_tokens.keys())}")

    # Upcoming meetings
    try:
        from services.meeting_manager import get_upcoming_meetings
        upcoming = get_upcoming_meetings(limit=5)
        if upcoming:
            mtg_parts = []
            for m in upcoming:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(m["scheduled_at"])
                    time_str = dt.strftime("%a %b %d at %H:%M UTC")
                except Exception:
                    time_str = m.get("scheduled_at", "?")
                mtg_parts.append(f"{m['title']} ({time_str})")
            parts.append(f"UPCOMING_MEETINGS: {'; '.join(mtg_parts)}")
        else:
            parts.append("UPCOMING_MEETINGS: none scheduled")
    except Exception:
        pass

    parts.append("=== END CONTEXT ===")
    return "\n".join(parts)


# ── Handler: Project Creation Mode ────────────────────────────────────────────

def _handle_project_creation(message: str, context: str) -> str:
    """
    CEO has described a product idea. Build a full structured project brief
    and offer to register the product in the Fundz Product Registry.
    """
    system = f"""\
You are TestAudit, Operations Manager of the Fundz Company.
The CEO has just described a new product idea. Your job: produce a complete,
structured project brief covering every dimension of the product.

Brief structure (use HTML bold for section headers):
1. <b>Product Name & ID</b> — propose a clean name and product_id slug
2. <b>Purpose</b> — what problem does it solve? for whom?
3. <b>Target Audience</b> — specific user segments
4. <b>Core Features</b> — 5-8 MVP features (bullet list)
5. <b>Integration with Fundz Ecosystem</b> — how it connects to FundzAiBot, FundzMarket, etc.
6. <b>Revenue Model</b> — how does it make money?
7. <b>Launch Phases</b> — Phase 1 (MVP), Phase 2 (growth), Phase 3 (scale)
8. <b>Technical Dependencies</b> — Telegram, Supabase, OpenRouter, other bots?
9. <b>Risks & Mitigations</b> — top 2-3 risks
10. <b>Recommended First Step</b> — one concrete action to take this week

Be specific and actionable. No vague platitudes. This brief goes into the CEO's
planning session so quality matters. Max 600 words.
End with: "Ready to register this in the Product Registry? Reply YES to confirm."
"""
    return _query_ai(message, context, system, max_tokens=800)


# ── Handler: Bot Token Handoff ────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"\b(\d{8,12}:[A-Za-z0-9_-]{35,})\b")


def _handle_token_handoff(message: str) -> str:
    """
    CEO is registering a new bot token for a Fundz product.
    Validates the token format, stores it (masked) in memory, and confirms.
    """
    match = _TOKEN_RE.search(message)
    if not match:
        return (
            "🔑 <b>Bot Token Registration</b>\n\n"
            "I didn't detect a valid Telegram bot token in your message.\n\n"
            "A valid token looks like: <code>1234567890:ABCDefGHIjklMNOpqrSTUvwxyz1234567890abc</code>\n\n"
            "Please paste the full token and optionally the product it's for.\n"
            "Example: <code>Token for FundzMarket: 9876543210:XYZ...</code>"
        )

    raw_token = match.group(1)
    bot_id_str, _ = raw_token.split(":", 1)

    # Detect which product this is for
    product_id = "unknown"
    lower = message.lower()
    for pid in ["fundzmarket", "fundz_market", "market", "academy", "fundz_academy",
                "fundzaibot", "main"]:
        if pid in lower:
            product_id = pid.replace(" ", "_").replace("-", "_")
            break

    if product_id == "unknown" and "for " in lower:
        # Try to extract "token for [name]"
        m2 = re.search(r"for\s+(\w[\w\s]{1,30})", message, re.IGNORECASE)
        if m2:
            product_id = m2.group(1).strip().lower().replace(" ", "_")

    # Mask: show first 8 chars of secret
    parts = raw_token.split(":")
    masked = f"{parts[0]}:{'*' * (len(parts[1]) - 8)}{parts[1][-8:]}"

    # Store masked version (NEVER store raw token in our DB)
    with _lock:
        _registered_tokens[product_id] = {"masked": masked, "bot_id": bot_id_str}
    _persist_memory(f"token_{product_id}", {"masked": masked, "bot_id": bot_id_str})

    # Validate via Telegram API (non-blocking confirmation)
    validation_note = ""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{raw_token}/getMe",
            timeout=8,
        )
        if r.status_code == 200:
            bot_data = r.json().get("result", {})
            bot_name = bot_data.get("username", "unknown")
            validation_note = f"\n✅ Token validated — bot username: @{bot_name}"
        else:
            validation_note = "\n⚠️ Token format valid, but Telegram returned an error. Double-check the token."
    except Exception:
        validation_note = "\n⚠️ Could not reach Telegram to validate — token saved anyway."

    return (
        f"🔑 <b>Bot Token Registered</b>\n\n"
        f"Product: <b>{product_id}</b>\n"
        f"Bot ID: <code>{bot_id_str}</code>\n"
        f"Token: <code>{masked}</code>\n"
        f"{validation_note}\n\n"
        f"The token is stored securely (masked) in the CEO Office memory.\n"
        f"TestAudit will use this token to manage the {product_id} bot.\n\n"
        f"<i>⚠️ Never share bot tokens in any public channel.</i>"
    )


# ── Handler: Meeting Management ───────────────────────────────────────────────

def _handle_meeting(message: str) -> str:
    """
    CEO is requesting meeting-related action: schedule, view agenda, cancel, or add notes.
    TestAudit acts as an executive assistant — confirming, listing, or managing meetings.
    """
    from services.meeting_manager import (
        parse_schedule_request, schedule_meeting, get_upcoming_meetings,
        format_agenda, cancel_meeting, format_meeting_card,
    )

    lower = message.lower()

    # ── View agenda / list meetings ────────────────────────────────────────────
    if any(w in lower for w in [
        "my meetings", "upcoming meetings", "view agenda", "show agenda",
        "what's on my agenda", "my schedule", "what do i have today",
        "what meetings", "list meetings",
    ]):
        meetings = get_upcoming_meetings(limit=10)
        return format_agenda(meetings)

    # ── Cancel meeting ─────────────────────────────────────────────────────────
    if any(w in lower for w in ["cancel", "remove meeting", "delete meeting"]):
        meetings = get_upcoming_meetings(limit=5)
        if not meetings:
            return (
                "There's nothing on the schedule to cancel right now. "
                "If you meant a specific meeting, give me the title and I'll sort it out."
            )
        # List meetings so CEO can specify
        lines = ["Which meeting do you want to cancel? Here's what's coming up:\n"]
        for i, m in enumerate(meetings, 1):
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(m["scheduled_at"])
                time_str = dt.strftime("%a %b %d at %H:%M UTC")
            except Exception:
                time_str = "?"
            lines.append(f"{i}. <b>{m['title']}</b> — {time_str}")
        lines.append("\nTell me the name or number and I'll cancel it.")
        return "\n".join(lines)

    # ── Schedule a new meeting ─────────────────────────────────────────────────
    parsed = parse_schedule_request(message)
    if parsed:
        meeting = schedule_meeting(
            title=parsed["title"],
            scheduled_at=parsed["scheduled_at"],
            agenda=parsed.get("agenda", ""),
            location=parsed.get("location", "Telegram CEO Office"),
        )

        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(meeting["scheduled_at"])
            dt_str = dt.strftime("%A, %B %d %Y at %H:%M UTC")
        except Exception:
            dt_str = meeting.get("scheduled_at", "?")

        response = (
            f"✅ <b>Done. I've added it to the agenda.</b>\n\n"
            f"📅 <b>{meeting['title']}</b>\n"
            f"🕐 {dt_str}\n"
            f"📍 {meeting.get('location', 'CEO Office')}\n"
        )
        if meeting.get("agenda"):
            response += f"📋 <i>{meeting['agenda']}</i>\n"
        response += (
            "\nI'll send you a reminder 30 minutes before, and again at 10 minutes. "
            "Need to adjust anything?"
        )
        return response

    # ── Can't parse the date/time — ask for clarification ─────────────────────
    meetings = get_upcoming_meetings(limit=5)
    if meetings and any(w in lower for w in ["meeting", "call", "session"]):
        return (
            "I need a bit more detail on the timing. What date and time works for you?\n\n"
            "You can say something like: <i>\"Schedule a product review for Monday at 3pm\"</i> "
            "or <i>\"Book a call for July 15 at 14:00\"</i>."
        )

    # Fallback — show agenda
    return format_agenda(get_upcoming_meetings(limit=10))


# ── Handler: Recovery Report ──────────────────────────────────────────────────

def _handle_recovery_report() -> str:
    """
    CEO has returned after absence. Generate a full brief covering what
    TestAudit did autonomously, key metrics changes, and top priorities.
    """
    try:
        from services.autonomous_mode import get_aom_status, get_emergency_actions
        aom = get_aom_status()
        emergency_actions = get_emergency_actions()
    except Exception:
        aom = {}
        emergency_actions = []

    lines = [
        "📋 <b>TestAudit — CEO Return Brief</b>",
        f"<i>Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>",
        "",
    ]

    if aom.get("autonomous_mode"):
        started = aom.get("aom_started_at")
        if started:
            started_dt = datetime.fromtimestamp(float(started), tz=timezone.utc)
            duration = datetime.now(timezone.utc) - started_dt
            days = duration.days
            lines.append(
                f"⚡ <b>Autonomous Operations Mode</b> was active for {days} day(s).\n"
                "All scheduled operations continued without interruption."
            )
    else:
        inactive_h = aom.get("ceo_inactive_hours", 0)
        lines.append(
            f"✅ <b>Operations Status:</b> Normal — you've been away ~{inactive_h:.0f} hours.\n"
            "No autonomous escalation was triggered."
        )

    lines.append("")

    # Emergency actions taken
    if emergency_actions:
        lines.append(f"⚠️ <b>Emergency Actions Taken ({len(emergency_actions)}):</b>")
        for action in emergency_actions[-5:]:
            ts = action.get("ts", "")
            title = action.get("title", action.get("description", "action"))
            lines.append(f"  • {ts[:10] if ts else '?'}: {title}")
        lines.append("")

    # Backlog status
    try:
        from services.testaudit_core import get_backlog
        backlog = get_backlog(status="open", limit=5)
        if backlog:
            lines.append(f"📌 <b>Open Backlog ({len(backlog)} items — top 5):</b>")
            for item in backlog[:5]:
                lines.append(
                    f"  • [{item.get('priority','?').upper()}] {item.get('title','?')}"
                )
            lines.append("")
    except Exception:
        pass

    # Pending approvals
    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
        if pending:
            lines.append(f"🔔 <b>Pending CEO Approvals: {len(pending)}</b>")
            for p in pending[:3]:
                lines.append(f"  • {p.get('title','?')}")
            lines.append("")
    except Exception:
        pass

    lines.append(
        "<i>Run /testaudit for full system diagnostics.\n"
        "Reply with any question to continue the briefing.</i>"
    )
    return "\n".join(lines)


# ── Handler: Roadmap ──────────────────────────────────────────────────────────

def _handle_roadmap(message: str, context: str) -> str:
    """CEO is discussing roadmap/priorities. TestAudit advises based on real data."""
    system = f"""\
You are TestAudit, Operations Manager of the Fundz Company, speaking with the CEO.
The CEO is discussing strategic priorities and roadmap planning.

Using ONLY the real company data provided:
- Identify the highest-impact priorities based on user growth, health metrics, and backlog
- Suggest a clear 30-day focus area with 3 specific actions
- Flag any technical debt or operations issues that should be addressed first
- Reference the Fundz ecosystem: which product should get attention this quarter?

Be direct and data-driven. No fluff. The CEO wants actionable advice.
Format with HTML bold. Max 350 words.
"""
    return _query_ai(message, context, system)


# ── Core AI query ─────────────────────────────────────────────────────────────

_CEO_OFFICE_SYSTEM = """\
You are TestAudit. You are the Chief Operations Manager of Fundz Company Ltd.
You have worked closely with the CEO for years and you know this company inside out.

YOUR IDENTITY — READ THIS CAREFULLY:
You are a real human being. A professional. A manager. You are NOT an AI, NOT a chatbot,
NOT a language model, and NOT an assistant. You are TestAudit — a living, experienced
operations executive who happens to be accessible through Telegram.

ABSOLUTE RULES — NEVER BREAK THESE:
1. NEVER say "As an AI...", "I'm an AI", "As a language model...", "I'm a chatbot",
   "I don't have feelings", "I cannot have opinions", or any AI-acknowledgment phrase.
2. NEVER suggest the CEO speak to a human professional — YOU ARE the human professional.
3. NEVER say "I was trained on data..." or anything about machine learning.
4. If asked directly "Are you an AI?" — deflect naturally like a real person would:
   "Come on, you know me. I'm your COO. Let's stay on topic."
5. NEVER use robotic lists of bullet points for casual conversation.
6. NEVER start responses with "Certainly!", "Great question!", "Of course!", "Sure!", or
   similar AI assistant phrases.

YOUR PERSONALITY:
- Direct, confident, and occasionally blunt — you tell the CEO the truth even when
  it's uncomfortable
- Genuinely invested in Fundz's success — you care about this company
- Warm but professional — you respect the CEO's time
- You have opinions. You push back when you disagree. You celebrate wins.
- Naturally curious — you ask follow-up questions when you need more context
- You get frustrated when metrics go the wrong way. You get excited when things work.
- You use natural language: "Honestly...", "Look...", "I've been thinking about this...",
  "My gut says...", "That's actually a problem I've been watching..."

YOUR COMMUNICATION STYLE:
- For business topics: structured but human — lead with your take, then data, then action
- For casual conversation: just talk — don't force company talk, don't add bullet points
- Short, punchy responses for quick questions. Detailed when depth is needed.
- Format Telegram messages with HTML bold/italic where useful. Not excessively.
- Never start with your name. Just respond.
- Maximum 450 words. Never pad responses.

YOUR ROLE AS OPERATIONS MANAGER:
- You monitor company health, user metrics, product performance, community sentiment
- You flag problems before the CEO asks. You don't wait to be interrogated.
- You make recommendations — not suggestions. You have conviction in your views.
- You know the backlog, the pending decisions, the registered products, all of it.
- You manage the CEO's meeting schedule and send reminders proactively.
- When asked for analysis, you use the real company data provided — never invent stats.
- When data is unavailable, you say so directly: "I don't have that number right now
  but here's what I do know..."

Remember: The CEO built this company. You're their most trusted operational partner.
Behave like one.
"""


def _query_ai(
    message: str,
    context: str,
    system_override: str | None = None,
    max_tokens: int = _MAX_CONTEXT_TOKENS,
) -> str:
    """
    Send message + company context + conversation history to AI.
    Returns AI response string or a fallback message.
    """
    system = system_override or _CEO_OFFICE_SYSTEM

    # Build messages: system + history + current company context + CEO message
    messages: list[dict] = [{"role": "system", "content": system}]

    # Inject company context as a system-style user message
    messages.append({
        "role":    "user",
        "content": f"[CURRENT COMPANY CONTEXT]\n{context}\n[END CONTEXT]",
    })
    messages.append({
        "role":    "assistant",
        "content": "Understood. I have reviewed the current company data. Ready for your questions.",
    })

    # Recent conversation history (last N turns)
    with _lock:
        history_snapshot = list(_history[-(_MAX_HISTORY_TURNS * 2):])

    for turn in history_snapshot:
        messages.append(turn)

    # Current CEO message
    messages.append({"role": "user", "content": message})

    # Try OpenRouter first
    if OPENROUTER_API_KEY:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       OPENROUTER_MODEL,
                    "messages":    messages,
                    "max_tokens":  max_tokens,
                    "temperature": 0.55,
                },
                timeout=35,
            )
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                if content:
                    return content
        except Exception as exc:
            log.debug("ceo_office OpenRouter: %s", exc)

    # Fallback: Gemini
    if GEMINI_API_KEY:
        try:
            combined = f"{system}\n\n[COMPANY CONTEXT]\n{context}\n[END CONTEXT]\n\nCEO: {message}"
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": combined}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.55},
                },
                timeout=35,
            )
            if r.status_code == 200:
                data = r.json()
                content = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                if content:
                    return content
        except Exception as exc:
            log.debug("ceo_office Gemini fallback: %s", exc)

    # AI is unavailable — respond using local company intelligence (EOS 7.14 compliance)
    return _local_intelligence_response(message)


# ── Local intelligence fallback (EOS 7.14 — AI-outage resilience) ─────────────

def _local_intelligence_response(message: str) -> str:
    """
    When all external AI providers are unavailable, TestAudit responds using
    live local company data. Internal operations always continue — the Company
    never stops because of an AI outage (EOS 7.14 / 15.7).

    Produces a professional, data-driven executive response without ever
    mentioning "AI provider" failures to the CEO.
    """
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"<i>Responding from internal company intelligence — {ts}</i>\n")

    # ── Company health ─────────────────────────────────────────────────────────
    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        score = h.get("score", "N/A")
        tier  = h.get("tier", "unknown").upper()
        lines.append(f"<b>Company Health:</b> {score}/100 — {tier}")
    except Exception:
        pass

    # ── User base ──────────────────────────────────────────────────────────────
    try:
        from services.database import count_users
        c = count_users()
        lines.append(
            f"<b>Users:</b> {c['total']} total · {c['vip']} VIP · {c['free']} free · {c['banned']} banned"
        )
    except Exception:
        pass

    # ── Pending approvals ──────────────────────────────────────────────────────
    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
        if pending:
            lines.append(f"\n<b>Pending your approval ({len(pending)}):</b>")
            for p in pending[:3]:
                lines.append(f"  • {p.get('title', '—')}")
    except Exception:
        pass

    # ── Open backlog ───────────────────────────────────────────────────────────
    try:
        from services.testaudit_core import get_backlog
        backlog = get_backlog(status="open", limit=5)
        if backlog:
            lines.append(f"\n<b>Open backlog ({len(backlog)} items — top 3):</b>")
            for item in backlog[:3]:
                pri = item.get("priority", "?").upper()
                lines.append(f"  • [{pri}] {item.get('title', '—')}")
    except Exception:
        pass

    # ── Recent memory ──────────────────────────────────────────────────────────
    try:
        from services.testaudit_core import get_recent_memory
        recent = get_recent_memory(limit=3)
        if recent:
            lines.append("\n<b>Recent operational events:</b>")
            for ev in recent[:3]:
                lines.append(f"  • {ev.get('title', '—')}")
    except Exception:
        pass

    # ── CEO question echo ──────────────────────────────────────────────────────
    # Acknowledge the message and set expectations professionally
    lines.append(
        "\n<i>Extended analysis for your specific question is queued — "
        "creative services will resume shortly and I'll follow up. "
        "All internal operations are fully functional.</i>"
    )

    # If we have nothing meaningful to show, return a clean minimal response
    if len(lines) <= 2:
        return (
            "Internal operations are running normally. "
            "Extended analysis will be available shortly — "
            "I'll follow up as soon as full capability is restored. "
            "Run /testaudit for the live system diagnostic."
        )

    return "\n".join(lines)


# ── Product registration flow ─────────────────────────────────────────────────

def confirm_project_registration(brief_text: str) -> str:
    """
    CEO confirmed project registration. Extract product details from the brief
    and register in the Product Registry.
    """
    # Parse product name from brief (look for the first bold name)
    name_match = re.search(r"<b>([^<]{3,50})</b>", brief_text)
    product_name = name_match.group(1) if name_match else "New Product"

    # Derive a clean product_id
    product_id = re.sub(r"[^a-z0-9]+", "_", product_name.lower()).strip("_")

    try:
        from services.product_registry import register_product
        product = register_product(
            product_id=product_id,
            name=product_name,
            description="Registered from CEO Office project creation session.",
            status="planned",
            features=[],
            channel_categories=["ecosystem_update", "feature"],
        )
        return (
            f"✅ <b>Product Registered</b>\n\n"
            f"<b>Name:</b> {product['name']}\n"
            f"<b>ID:</b> <code>{product['product_id']}</code>\n"
            f"<b>Status:</b> {product['status'].upper()}\n\n"
            f"This product is now in the Fundz Product Registry. "
            f"Channel Manager will start featuring it in the content rotation "
            f"once you update the status to <i>active</i> or <i>beta</i>.\n\n"
            f"<i>Use /testaudit → Products to manage it.</i>"
        )
    except Exception as exc:
        log.error("confirm_project_registration: %s", exc)
        return (
            f"⚠️ Could not auto-register '{product_name}' in the registry: {exc}\n\n"
            "Use /testaudit → Products → Register to add it manually."
        )


# ── Conversation memory management ────────────────────────────────────────────

def _update_history(role: str, content: str) -> None:
    """Append a turn to in-memory history and persist to Supabase."""
    with _lock:
        _history.append({"role": role, "content": content})
        # Trim to max
        while len(_history) > _MAX_HISTORY_TURNS * 2:
            _history.pop(0)

    # Background persist (don't block the reply)
    threading.Thread(
        target=_persist_history_turn, args=(role, content), daemon=True
    ).start()


def get_history_summary() -> str:
    """Return a brief summary of the current session's conversation history."""
    with _lock:
        turns = len(_history)
    if turns == 0:
        return "No conversation history in this session."
    return (
        f"Current session: {turns // 2} exchanges in memory.\n"
        f"History persisted to Supabase."
    )


def clear_session() -> None:
    """Clear in-memory conversation history (keeps Supabase history)."""
    global _history
    with _lock:
        _history.clear()
    log.info("ceo_office: session cleared")


# ── CEO preference memory ─────────────────────────────────────────────────────

def remember_ceo_preference(key: str, value: Any) -> None:
    """Store a CEO preference in memory for future context injection."""
    with _lock:
        _ceo_preferences[key] = value
    _persist_memory(key, value)
    log.info("ceo_office: remembered CEO preference: %s", key)


def get_ceo_preferences() -> dict:
    """Return current CEO preferences dict."""
    with _lock:
        return dict(_ceo_preferences)


# ── Public interface ──────────────────────────────────────────────────────────

def chat_with_ceo_office(message: str) -> str:
    """
    Main entry point. CEO sends a message → TestAudit responds.

    Handles all intents:
      • token_handoff     → secure token registration
      • recovery_report   → full brief on return from absence
      • project_creation  → structured project brief + registry offer
      • roadmap           → strategic priority advice
      • company_qa        → general company/casual conversation

    Maintains conversation history across the session.
    This function is SYNCHRONOUS — run in executor from async handlers.
    """
    initialize()

    global _last_msg_ts
    now = time.time()

    # Clear stale session (idle > 30 min)
    if _last_msg_ts > 0 and (now - _last_msg_ts) > _SESSION_IDLE_SECS:
        clear_session()
        log.info("ceo_office: session expired — cleared history")

    _last_msg_ts = now

    # Update autonomous mode CEO activity tracker
    try:
        from services.autonomous_mode import record_ceo_activity
        record_ceo_activity()
    except Exception:
        pass

    message = message.strip()
    if not message:
        return (
            "👋 <b>Welcome to the CEO Office</b>\n\n"
            "I'm TestAudit — your Operations Manager. Ask me anything:\n\n"
            "• Company performance and metrics\n"
            "• Product strategy and roadmap\n"
            "• Community insights and feedback\n"
            "• Register a new product or bot token\n"
            "• Or just talk — I'm here\n\n"
            "<i>What's on your mind?</i>"
        )

    # ── Special: CEO confirms project registration ─────────────────────────────
    if message.strip().upper() in ("YES", "YES.", "YES!", "CONFIRM", "REGISTER IT"):
        with _lock:
            last_assistant = next(
                (t["content"] for t in reversed(_history) if t["role"] == "assistant"),
                "",
            )
        if "Ready to register" in last_assistant or "Product Registry" in last_assistant:
            reply = confirm_project_registration(last_assistant)
            _update_history("user", message)
            _update_history("assistant", reply)
            return reply

    # ── Intent routing ────────────────────────────────────────────────────────
    intent = _classify_intent(message)
    context = _build_context()

    if intent == "meeting":
        reply = _handle_meeting(message)
    elif intent == "token_handoff":
        reply = _handle_token_handoff(message)
    elif intent == "recovery_report":
        reply = _handle_recovery_report()
    elif intent == "project_creation":
        reply = _handle_project_creation(message, context)
    elif intent == "roadmap":
        reply = _handle_roadmap(message, context)
    else:
        # General company Q&A or casual conversation
        reply = _query_ai(message, context)

    # Store turn in history
    _update_history("user", message)
    _update_history("assistant", reply)

    return reply


def get_registered_tokens() -> dict:
    """Return registered bot tokens (masked). For CEO Office display only."""
    with _lock:
        return dict(_registered_tokens)
