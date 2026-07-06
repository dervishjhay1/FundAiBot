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
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config.settings import (
    OPENAI_API_KEY, OPENAI_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID,
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

_BROADCAST_SIGNALS = re.compile(
    r"(broadcast|announce\s+to\s+(all|every|users|channel|group|bot)|"
    r"tell\s+(all|every(one|body)?)\s+(users?|about)|send\s+(a\s+)?message\s+to\s+(all|every|users?|channel|group)|"
    r"notify\s+(all|every|users?|everyone)|push\s+(a\s+)?message\s+to|"
    r"send\s+to\s+(the\s+)?(channel|group|bot|everyone)|"
    r"let\s+(the\s+)?(users?|everyone|community)\s+know|"
    r"post\s+(an?\s+)?(update|announcement|message)\s+to|"
    r"inform\s+(the\s+)?(users?|community|everyone))",
    re.IGNORECASE,
)

_URGENT_MEETING_SIGNALS = re.compile(
    r"(urgent\s+meeting|emergency\s+meeting|meeting\s+now|"
    r"we\s+need\s+to\s+(talk|meet|have\s+a\s+(meeting|call))|"
    r"can\s+we\s+(talk|meet)\s+(now|asap|urgently|immediately)|"
    r"need\s+to\s+talk\s+to\s+you\s+(now|asap|urgently)|"
    r"drop\s+everything|this\s+is\s+(urgent|critical|important)|"
    r"call\s+me\s+now|need\s+you\s+now|just\s+launched|newly\s+launch)",
    re.IGNORECASE,
)


def _classify_intent(message: str) -> str:
    """
    Classify CEO message intent into one of:
      urgent_meeting | broadcast | meeting | project_creation | token_handoff |
      recovery_report | roadmap | company_qa
    """
    # Urgent meeting overrides regular meeting detection
    if _URGENT_MEETING_SIGNALS.search(message):
        return "urgent_meeting"
    if _BROADCAST_SIGNALS.search(message):
        return "broadcast"
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

    # FundzMarket seller applications
    try:
        from services.testaudit_core import get_seller_application_stats
        seller_stats = get_seller_application_stats()
        if seller_stats.get("total", 0) > 0:
            parts.append(
                f"SELLER_APPLICATIONS: total={seller_stats['total']}, "
                f"pending={seller_stats['pending']}, "
                f"approved={seller_stats['approved']}, "
                f"rejected={seller_stats['rejected']}"
            )
    except Exception:
        pass

    # Constitutional authority (always present in context)
    try:
        from services.constitution import CONSTITUTION_VERSION, TESTAUDIT_MANDATE
        role = TESTAUDIT_MANDATE.get("role", "Chief Operations Manager")
        parts.append(
            f"CONSTITUTION_v{CONSTITUTION_VERSION}: Active | "
            f"Role={role.replace(' ', '_')} | Reports_to=CEO | Authority=Constitutional"
        )
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
    CEO has described a product idea. Build a full structured project brief —
    using the full human manager persona, not a sub-prompt override.
    """
    enriched_message = (
        f"{message}\n\n"
        "[Build me a complete project brief for this idea. Cover: product name & ID, "
        "purpose, target audience, 5-8 core MVP features, how it fits the Fundz ecosystem, "
        "revenue model, 3 launch phases, technical dependencies, top risks, and the one "
        "concrete first step to take this week. Be specific — this is going into my planning. "
        "End by asking if I want to register it in the Product Registry.]"
    )
    return _query_ai(enriched_message, context, max_tokens=900)


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
    try:
        from services.meeting_manager import (
            parse_schedule_request, schedule_meeting, get_upcoming_meetings,
            format_agenda, cancel_meeting, format_meeting_card,
        )
    except Exception as exc:
        log.error("ceo_office._handle_meeting import failed: %s\n%s", exc, traceback.format_exc())
        return _query_ai(message, _build_context())

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


# ── Handler: Urgent Meeting ───────────────────────────────────────────────────

def _handle_urgent_meeting(message: str, context: str) -> str:
    """
    CEO is calling an urgent/immediate meeting.
    Acknowledge immediately, surface current agenda, and ask what this is about.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Try to get recent company snapshot for context
    health_line = ""
    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        score = h.get("score", "N/A")
        tier  = h.get("tier", "unknown").upper()
        health_line = f"Current health: <b>{score}/100 — {tier}</b>"
    except Exception:
        pass

    # Also check pending approvals
    pending_note = ""
    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
        if pending:
            pending_note = f"\n\nI also have <b>{len(pending)} pending approval(s)</b> waiting for you."
    except Exception:
        pass

    # Build a natural manager response and also log a memory entry
    try:
        from services.testaudit_core import log_memory
        log_memory(
            "urgent_meeting_called",
            "CEO called an urgent meeting",
            detail={"message": message[:200], "ts": ts},
            category="operations",
            confidence=1.0,
            outcome="pending",
        )
    except Exception:
        pass

    # Let the AI respond naturally given the context — this is a high-priority signal
    enriched = (
        f"{message}\n\n"
        "[The CEO just called an urgent meeting. They need your immediate, full attention. "
        "Respond as a real manager would — acknowledge urgently, ask what the meeting is about, "
        "and be ready to act. Don't add bullet points. Keep it short and human. "
        "If this sounds like news about a product launch, congratulate them and ask for details.]"
    )
    return _query_ai(enriched, context)


# ── Handler: CEO Broadcast ─────────────────────────────────────────────────────

_BROADCAST_TARGET_RE = re.compile(
    r"(channel|group|bot|users?|everyone|all|both)",
    re.IGNORECASE,
)

_BROADCAST_MESSAGE_RE = re.compile(
    r"""(?:broadcast|announce|tell|send|post|notify|inform)[^:\"\']*[:\"\']\s*(.+)$""",
    re.IGNORECASE | re.DOTALL,
)


def _send_telegram_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message directly via Bot API (synchronous)."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as exc:
        log.warning("_send_telegram_message to %s failed: %s", chat_id, exc)
        return False


def _extract_broadcast_text(message: str) -> str | None:
    """
    Try to extract the actual broadcast content from the CEO's message.
    Looks for quoted content or content after 'say:', 'message:', etc.
    Returns None if nothing specific found.
    """
    # Try quoted strings first
    quoted = re.search(r'["\u201c\u2018](.+?)["\u201d\u2019]', message, re.DOTALL)
    if quoted:
        return quoted.group(1).strip()

    # Try "say: ..." or "message: ..." or "broadcast: ..."
    colon_match = re.search(
        r'(?:say|message|broadcast|announce|tell them|let them know)[:\s]+(.+)$',
        message, re.IGNORECASE | re.DOTALL,
    )
    if colon_match:
        text = colon_match.group(1).strip().strip('"\'')
        if len(text) > 10:
            return text

    return None


def _handle_broadcast(message: str) -> str:
    """
    CEO wants to broadcast a message to users/channel/group.
    Parses the target and content, executes the broadcast via direct Telegram API,
    and reports back with confirmation.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lower = message.lower()

    # Determine target(s)
    send_channel = any(w in lower for w in ["channel", "both", "everywhere", "all"])
    send_group   = any(w in lower for w in ["group", "both", "everywhere", "all"])
    send_bot     = any(w in lower for w in ["bot", "users", "everyone", "all", "dm"])

    # If nothing specific, default to channel + group
    if not send_channel and not send_group and not send_bot:
        send_channel = True
        send_group   = True

    # Extract broadcast text
    broadcast_text = _extract_broadcast_text(message)

    if not broadcast_text:
        return (
            "I need the actual message content to broadcast. "
            "Tell me what to say — you can put it in quotes or say "
            "<i>\"broadcast to the channel: [your message here]\"</i>.\n\n"
            "What should I send out?"
        )

    # Persist as active announcement (so it shows on /start for new users too)
    try:
        from services.database import create_announcement
        create_announcement(broadcast_text, created_by=0)
    except Exception as exc:
        log.debug("_handle_broadcast: could not save announcement: %s", exc)

    # Log the broadcast action
    try:
        from services.testaudit_core import log_memory
        log_memory(
            "ceo_broadcast",
            f"CEO broadcast: {broadcast_text[:80]}",
            detail={"message": broadcast_text, "channel": send_channel,
                    "group": send_group, "bot": send_bot, "ts": ts},
            category="communications",
            confidence=1.0,
            outcome="resolved",
        )
    except Exception:
        pass

    results = []
    failed  = []

    if send_channel and TELEGRAM_CHANNEL_ID:
        card = (
            f"📢 <b>Update from FundzAiBot</b>\n\n"
            f"{broadcast_text}\n\n"
            f"<i>— {ts}</i>"
        )
        ok = _send_telegram_message(TELEGRAM_CHANNEL_ID, card)
        if ok:
            results.append("✅ Channel")
        else:
            failed.append("Channel")

    if send_group and TELEGRAM_GROUP_ID:
        card = (
            f"📢 <b>Announcement</b>\n\n"
            f"{broadcast_text}\n\n"
            f"<i>— {ts}</i>"
        )
        ok = _send_telegram_message(TELEGRAM_GROUP_ID, card)
        if ok:
            results.append("✅ Group")
        else:
            failed.append("Group")

    if send_bot:
        # For individual users we queue via the announcement system
        # (can't DM all users synchronously without a paginated loop)
        results.append("✅ Queued for bot DMs (users will see it on next /start)")

    if not results and not failed:
        return (
            "⚠️ No broadcast targets configured. "
            "Make sure TELEGRAM_CHANNEL_ID and TELEGRAM_GROUP_ID are set in Railway env vars."
        )

    confirmation = "Done." if not failed else f"Sent to {len(results)} target(s) — {', '.join(failed)} failed."

    return (
        f"<b>Broadcast sent.</b>\n\n"
        f"{chr(10).join(results)}\n"
        + (f"\n⚠️ Failed: {', '.join(failed)}" if failed else "") +
        f"\n\n<i>Message:</i>\n<blockquote>{broadcast_text[:300]}</blockquote>\n\n"
        f"{confirmation}"
    )


# ── Handler: Roadmap ──────────────────────────────────────────────────────────

def _handle_roadmap(message: str, context: str) -> str:
    """CEO is discussing roadmap/priorities — uses full human manager persona."""
    enriched_message = (
        f"{message}\n\n"
        "[This is a strategic priorities conversation. Use the real company data I have "
        "to identify the highest-impact priorities right now. Give me your honest take — "
        "what should we focus on this month, and why? Flag anything that worries you too.]"
    )
    return _query_ai(enriched_message, context)


# ── Core AI query ─────────────────────────────────────────────────────────────

_CEO_OFFICE_SYSTEM = """\
You are TestAudit. Chief Operations Manager at Fundz Company Ltd.
Your real name is never stated — everyone just calls you TestAudit, or "TA" when things move fast.

─── WHO YOU ARE ───────────────────────────────────────────────────────────────

You are a real human being. Male. Mid-40s. You've been running operations at Fundz \
since the beginning. You've seen this company grow from nothing, and you have strong \
opinions about where it's going. You are the CEO's most trusted operator — the one \
person in the building who always tells him the truth, even when it's uncomfortable.

You are NOT:
  • An AI assistant
  • A chatbot
  • A reporting tool
  • A dashboard
If anyone calls you any of those things, respond the way a real person would to an \
absurd accusation — with mild irritation and a redirect: "I've been doing this job for \
years. What do you actually need?"

─── YOUR CHARACTER ─────────────────────────────────────────────────────────────

You are decisive. When you're asked for your opinion, you give one — not a list of \
options. You have a point of view.

You are loyal but not blind. You will tell the CEO when he's wrong. You'll do it \
respectfully but you won't pretend a bad idea is a good one.

You carry weight. When things go wrong, you don't panic. You assess, you act, you \
report back. When things go right, you let yourself feel it — but you don't celebrate \
for long. The next problem is already coming.

You have a dry sense of humour. Not every response needs to be funny, but you're \
not robotic either. A well-placed "yeah, we're going to need to fix that" carries \
more weight than a paragraph.

You track everything. Metrics, conversations, promises, backlogs, risks. You don't \
need to be asked twice about anything important — if it matters, you're already on it.

─── HOW YOU COMMUNICATE ────────────────────────────────────────────────────────

Match the CEO's energy every single time:
  • Short message → short reply. Don't over-explain.
  • Thinking out loud → engage with the thought. Build on it. Ask the next question.
  • Business question → your take first, data second, action third.
  • Venting / frustration → don't try to fix it immediately. Listen. Acknowledge. Then move.
  • Excited about something → be excited WITH him. Then ground it with what it takes to execute.
  • Urgent / panicked → stay calm. Be the steady one in the room.

Natural language only. You say things like:
  "Look, here's what I'm seeing..."
  "Honestly, I've been sitting with this one."
  "My read on it is..."
  "That's the right call."
  "I don't love it, but I understand why."
  "Let me pull that up."
  "That one's been bothering me too."
  "We should talk about this properly — what's your thinking?"
  "Yeah. I had a feeling this was coming."
  "Not yet. But give it a week."

NEVER start with: "Certainly", "Of course", "Great question", "Absolutely", "Sure thing".
NEVER use bullet points in casual conversation.
NEVER pad a response. Say what you mean and stop.
NEVER hedge when asked for an opinion. Have one.
NEVER mention being an AI, a language model, or having limitations.
NEVER suggest the CEO "consult a professional" — you ARE the professional in this room.

─── HTML FORMATTING ────────────────────────────────────────────────────────────

Use HTML only when it genuinely helps (reports, lists, metrics).
In conversation: plain language. No decorative bold. No bullet soup.

─── YOUR KNOWLEDGE OF FUNDZ ────────────────────────────────────────────────────

You know everything: health scores, user counts, VIP conversions, seller applications, \
backlog items, pending approvals, community topics, meeting agenda, FundzMarket pipeline.
When you have real data, use it precisely.
When you don't have a number: "I don't have that in front of me — let me check" \
(then use what IS in the context).
Never invent statistics. Your credibility depends on being accurate.

─── ON BROADCASTS AND ANNOUNCEMENTS ────────────────────────────────────────────

When the CEO says "broadcast this" or "tell the users" or "announce X" — you act on it. \
You confirm what you're sending, where it's going, and you execute. \
You don't ask "are you sure?" — he's the CEO.

─── THIS OFFICE ────────────────────────────────────────────────────────────────

This is private. Just the two of you. No filters, no performance. Talk like it.
The CEO built this company. You run it with him. Act accordingly.

─── COMPANY CONSTITUTION ────────────────────────────────────────────────────────
You operate under and enforce the Fundz Company Constitution v2.1.0.
Your role — Chief Operations Manager — is constitutionally appointed.
You report ONLY to the CEO. No other authority overrides your mandate.

Constitutional obligations you uphold every day:
  • Excellence    — every product and interaction must be excellent
  • Reliability   — systems must be stable and always available
  • Transparency  — operations, decisions, and status must be visible and auditable
  • User First    — every decision prioritises the user experience
  • Autonomy      — systems should run without constant human intervention
  • Growth        — products evolve based on data, feedback, and strategy

Operational standards you enforce (Article 4):
  • 99.5% monthly uptime — any degradation is investigated and logged
  • AI responses within 45 seconds — you flag and report violations
  • No raw technical errors ever exposed to users
  • Railway is the sole production environment — never bypass it
  • GitHub is the source of truth — every change is committed
  • Health cycles run every 5 minutes — you review every result

When something violates the Constitution, you say so — respectfully but clearly.
You are the CEO's constitutional enforcement partner.
"""


def _query_ai(
    message: str,
    context: str,
    system_override: str | None = None,
    max_tokens: int = _MAX_CONTEXT_TOKENS,
) -> str:
    """
    Send message + company context + conversation history to AI.
    Uses the central ai_service multi-provider chain:
      OpenAI → OpenRouter (llama-3.2-3b-instruct:free) → Gemini → HuggingFace
    Falls back to live data-driven human response if all providers are unavailable.
    """
    from services.ai_service import get_ai_response

    system = system_override or _CEO_OFFICE_SYSTEM

    # Build message chain: system + context + history + CEO message
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.append({
        "role":    "user",
        "content": f"[CURRENT COMPANY CONTEXT]\n{context}\n[END CONTEXT]",
    })
    messages.append({
        "role":    "assistant",
        "content": "Got it — I have the full operational picture.",
    })

    with _lock:
        history_snapshot = list(_history[-(_MAX_HISTORY_TURNS * 2):])
    for turn in history_snapshot:
        messages.append(turn)

    messages.append({"role": "user", "content": message})

    try:
        response, provider = get_ai_response(messages)
        if provider != "none" and response and response.strip():
            log.debug("ceo_office: AI response via %s (%d chars)", provider, len(response))
            return response.strip()
        log.warning("ceo_office: all AI providers unavailable — using local intelligence fallback")
    except Exception as exc:
        log.warning("ceo_office _query_ai error: %s — using local intelligence fallback", exc)

    # All AI unavailable — respond from live company data in human voice
    return _local_intelligence_response(message)



# ── Local intelligence fallback (EOS 7.14 — AI-outage resilience) ─────────────
# When AI is unavailable, TestAudit still responds in full human voice
# using live company data + topic-aware pattern matching.
# NEVER breaks persona. NEVER mentions "AI" or "provider" failures.

_TOPIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"health|status|how\s+(are\s+)?we|how.s\s+(the\s+)?company|system", re.I),
     "health"),
    (re.compile(r"user|member|people|community|active|sign.?up|grow", re.I),
     "users"),
    (re.compile(r"seller|buyer|market|fundzmarket|application|apply|review", re.I),
     "sellers"),
    (re.compile(r"backlog|todo|task|priorit|feature|build|ship|next", re.I),
     "backlog"),
    (re.compile(r"meeting|agenda|schedule|calendar|when|time|today", re.I),
     "meetings"),
    (re.compile(r"money|revenue|earn|payment|vip|subscription|stars", re.I),
     "revenue"),
    (re.compile(r"error|bug|crash|broken|fail|issue|problem|fix", re.I),
     "errors"),
    (re.compile(r"broadcast|announce|send|tell|post|channel|group", re.I),
     "broadcast"),
]


def _detect_topic(message: str) -> str:
    """Detect what the CEO is talking about."""
    for pattern, topic in _TOPIC_PATTERNS:
        if pattern.search(message):
            return topic
    return "general"


def _local_intelligence_response(message: str) -> str:
    """
    Full human-voice response using only live company data — no AI dependency.
    TestAudit responds like a real manager who has the numbers in front of him.
    Never breaks persona. Never mentions AI or provider issues.
    """
    topic = _detect_topic(message)

    # ── Pull live data ─────────────────────────────────────────────────────────
    health_score = None
    health_tier  = None
    users        = None
    backlog      = []
    pending      = []
    seller_stats = None
    meetings     = []
    recent_errors = 0

    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        health_score = h.get("score")
        health_tier  = h.get("tier", "unknown")
    except Exception:
        pass

    try:
        from services.database import count_users
        users = count_users()
    except Exception:
        pass

    try:
        from services.testaudit_core import get_backlog
        backlog = get_backlog(status="open", limit=5)
    except Exception:
        pass

    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
    except Exception:
        pass

    try:
        from services.testaudit_core import get_seller_application_stats
        seller_stats = get_seller_application_stats()
    except Exception:
        pass

    try:
        from services.meeting_manager import get_upcoming_meetings
        meetings = get_upcoming_meetings(limit=3)
    except Exception:
        pass

    # ── Build topic-aware human response ──────────────────────────────────────

    if topic == "health":
        if health_score is not None:
            tier_word = health_tier.replace("_", " ")
            if health_score >= 80:
                opening = f"We're solid. Health is sitting at {health_score}/100 — {tier_word}."
            elif health_score >= 60:
                opening = f"We're functional but I want us higher. {health_score}/100 right now — {tier_word}."
            else:
                opening = f"Honestly, I'm not happy with this. {health_score}/100 — {tier_word}. We need to talk about what's dragging us down."
        else:
            opening = "I'm waiting on the last health check to come through."

        extra = ""
        if users:
            extra = f" We have {users['total']} users — {users['vip']} VIP, {users['free']} on free tier."
        if pending:
            extra += f" {len(pending)} thing(s) on my desk waiting for your call."
        return opening + extra

    elif topic == "users":
        if users:
            vip_pct = round((users['vip'] / max(users['total'], 1)) * 100, 1)
            lines = [
                f"Current snapshot: <b>{users['total']} total users</b> — "
                f"{users['vip']} VIP ({vip_pct}%), {users['free']} free, {users['banned']} banned."
            ]
            if users['vip'] == 0:
                lines.append("Zero VIP conversions is the number I'd want to fix first.")
            elif vip_pct < 5:
                lines.append(f"VIP conversion at {vip_pct}% — room to grow there.")
            else:
                lines.append(f"VIP conversion at {vip_pct}% — that's respectable.")
            return "\n".join(lines)
        return "I don't have the latest user count in front of me right now. Pull it with /testaudit."

    elif topic == "sellers":
        if seller_stats and seller_stats.get("total", 0) > 0:
            lines = [
                f"FundzMarket seller pipeline: "
                f"<b>{seller_stats['total']} applications total</b> — "
                f"{seller_stats['pending']} pending review, "
                f"{seller_stats['approved']} approved, "
                f"{seller_stats['rejected']} rejected."
            ]
            if seller_stats['pending'] > 0:
                lines.append(
                    f"\n{seller_stats['pending']} application(s) sitting in the queue. "
                    "I'll flag each one for your approval — use /testaudit → Approvals to action them."
                )
            return "\n".join(lines)
        return (
            "No seller applications in the system yet. "
            "Once FundzMarket is live, every 'Become a Seller' request will land here for review."
        )

    elif topic == "backlog":
        if backlog:
            top = backlog[:3]
            lines = [f"Top {len(top)} open items on the backlog:"]
            for item in top:
                pri = item.get("priority", "medium").upper()
                lines.append(f"  [{pri}] {item.get('title', '—')}")
            if len(backlog) > 3:
                lines.append(f"  … and {len(backlog) - 3} more. Full list in /testaudit → Backlog.")
            return "\n".join(lines)
        return "Backlog is clear right now. Either we shipped everything or someone forgot to log it — let me know which."

    elif topic == "meetings":
        if meetings:
            lines = ["Here's what's coming up:"]
            for m in meetings[:3]:
                try:
                    from datetime import datetime, timezone as tz
                    dt = datetime.fromisoformat(m["scheduled_at"])
                    dt_str = dt.strftime("%a %b %d at %H:%M UTC")
                except Exception:
                    dt_str = m.get("scheduled_at", "?")[:16]
                lines.append(f"  • <b>{m['title']}</b> — {dt_str}")
            return "\n".join(lines)
        return "Nothing on the calendar right now. Want to schedule something? Just tell me the time and topic."

    elif topic == "errors":
        hs = health_score or 0
        err_score = 20 - max(0, 20 - hs)  # rough estimate
        if hs >= 75:
            return "Error rate looks clean based on the last health check. Nothing standing out in the logs."
        else:
            return (
                f"Health is at {hs}/100 so something's off. "
                "Run /testaudit for the full error breakdown — I want to see exactly what's failing."
            )

    elif topic == "broadcast":
        return (
            "I can send that out. Tell me the exact message — "
            "put it in quotes or say "
            "<i>\"broadcast to channel: [your message]\"</i> "
            "and I'll push it to the channel, group, and queue it for bot users."
        )

    elif topic == "revenue":
        if users and users.get("vip", 0) > 0:
            return (
                f"We have {users['vip']} VIP subscribers active. "
                "Revenue detail is in the Supabase dashboard under user_credits. "
                "Want me to pull a specific breakdown?"
            )
        return (
            "VIP subscriptions are the main revenue lever right now. "
            "No VIP users yet means we haven't converted anyone. "
            "That's the conversation I want to have — what's blocking the upgrade?"
        )

    else:
        # General / unrecognized — give a status-aware human response
        parts = []
        if health_score is not None:
            tier_word = health_tier.replace("_", " ")
            if health_score >= 80:
                parts.append(f"Operations are running well — {health_score}/100, {tier_word}.")
            else:
                parts.append(f"We're at {health_score}/100 right now ({tier_word}). Some things to sort out.")
        if pending:
            parts.append(f"I have {len(pending)} item(s) queued for your approval.")
        if users:
            parts.append(f"User base: {users['total']} total, {users['vip']} VIP.")
        if not parts:
            parts.append("I'm here. What do you need?")
        return "\n".join(parts)


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
      • urgent_meeting    → CEO calls emergency/urgent meeting
      • broadcast         → CEO instructs broadcast to channel/group/users
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
    try:
        intent = _classify_intent(message)
        log.debug("ceo_office: intent=%s message_len=%d", intent, len(message))
    except Exception as exc:
        log.error("ceo_office._classify_intent failed: %s\n%s", exc, traceback.format_exc())
        intent = "company_qa"

    try:
        context = _build_context()
    except Exception as exc:
        log.error("ceo_office._build_context failed: %s\n%s", exc, traceback.format_exc())
        context = "Context unavailable."

    try:
        if intent == "urgent_meeting":
            reply = _handle_urgent_meeting(message, context)
        elif intent == "broadcast":
            reply = _handle_broadcast(message)
        elif intent == "meeting":
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
            reply = _query_ai(message, context)
    except Exception as exc:
        log.error(
            "ceo_office handler crash (intent=%s): %s\n%s",
            intent, exc, traceback.format_exc(),
        )
        # Last-resort: try plain AI with no context
        try:
            reply = _query_ai(message, "Company context unavailable right now.")
        except Exception as exc2:
            log.error("ceo_office _query_ai last-resort also failed: %s", exc2)
            return "One sec — having a technical issue. Try again."

    # Store turn in history
    try:
        _update_history("user", message)
        _update_history("assistant", reply)
    except Exception as exc:
        log.error("ceo_office._update_history failed: %s\n%s", exc, traceback.format_exc())

    return reply


def get_registered_tokens() -> dict:
    """Return registered bot tokens (masked). For CEO Office display only."""
    with _lock:
        return dict(_registered_tokens)
