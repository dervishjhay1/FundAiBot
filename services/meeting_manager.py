"""
FundzAiBot — Meeting Manager (TestAudit role)

TestAudit manages the CEO's meeting schedule directly inside Telegram.
The CEO can schedule meetings, view the agenda, add notes, and receive
automatic reminders — exactly as they would with a real executive assistant.

Meeting lifecycle:
  1. CEO schedules a meeting (title, date/time, optional agenda)
  2. TestAudit confirms and adds it to the agenda
  3. 30 min before → reminder sent to CEO DM
  4. 10 min before → final reminder sent
  5. After meeting → CEO can add notes; TestAudit archives them

Storage: Supabase `meetings` table (see supabase_meetings_schema.sql)
Fallback: in-memory list when Supabase is unavailable.

Runs as a daemon thread started in post_init().
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MEETINGS_TABLE      = "meetings"
_CHECK_INTERVAL_SECS = 60        # check for upcoming meetings every minute
_REMINDER_30_MINS    = 1800      # 30-minute reminder threshold (seconds)
_REMINDER_10_MINS    = 600       # 10-minute reminder threshold (seconds)
_DB_TIMEOUT          = (5, 12)

_running: bool = False
_thread:  threading.Thread | None = None
_lock     = threading.Lock()

# ── In-memory fallback (used when Supabase is unavailable) ────────────────────

_meetings_cache: list[dict] = []
_reminded_30:    set[str]   = set()   # meeting IDs where 30-min reminder was sent
_reminded_10:    set[str]   = set()   # meeting IDs where 10-min reminder was sent


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_post(data: dict) -> dict | None:
    if not _sb_available():
        return None
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{_MEETINGS_TABLE}",
            headers=_hdrs(), json=data, timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 201):
            rows = r.json()
            return rows[0] if isinstance(rows, list) and rows else rows
    except Exception as exc:
        log.debug("meeting_manager._sb_post: %s", exc)
    return None


def _sb_get(params: dict | None = None) -> list[dict]:
    if not _sb_available():
        return []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{_MEETINGS_TABLE}",
            headers=_hdrs(), params=params or {}, timeout=_DB_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json() or []
    except Exception as exc:
        log.debug("meeting_manager._sb_get: %s", exc)
    return []


def _sb_patch(meeting_id: str, data: dict) -> bool:
    if not _sb_available():
        return False
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{_MEETINGS_TABLE}",
            headers=_hdrs(),
            params={"id": f"eq.{meeting_id}"},
            json=data,
            timeout=_DB_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception as exc:
        log.debug("meeting_manager._sb_patch: %s", exc)
    return False


# ── Telegram sender ───────────────────────────────────────────────────────────

def _send_dm(text: str) -> bool:
    """Send a direct message to the CEO (ADMIN_USER_ID)."""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    ADMIN_USER_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as exc:
        log.warning("meeting_manager._send_dm: %s", exc)
    return False


# ── Meeting parsing ───────────────────────────────────────────────────────────

# Date/time parsing patterns (flexible natural language input)
_DATE_PATTERNS = [
    # "tomorrow at 3pm", "today at 14:00", "Monday at 9am"
    re.compile(
        r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"\s+at\s+(\d{1,2}(?::\d{2})?(?:am|pm)?)\b",
        re.IGNORECASE,
    ),
    # "July 10 at 3pm", "10 July at 3pm"
    re.compile(
        r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*"
        r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2})"
        r"\s+at\s+(\d{1,2}(?::\d{2})?(?:am|pm)?)\b",
        re.IGNORECASE,
    ),
    # "2026-07-10 15:00", "10/07/2026 15:00"
    re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\s+(\d{2}:\d{2})\b"),
]

_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_time_str(time_str: str) -> tuple[int, int]:
    """Parse '3pm', '14:00', '9am', '9:30am' → (hour, minute)."""
    time_str = time_str.strip().lower()
    pm = time_str.endswith("pm")
    am = time_str.endswith("am")
    time_str = time_str.replace("pm", "").replace("am", "").strip()

    if ":" in time_str:
        h, m = time_str.split(":", 1)
        hour, minute = int(h), int(m)
    else:
        hour, minute = int(time_str), 0

    if pm and hour != 12:
        hour += 12
    if am and hour == 12:
        hour = 0

    return hour % 24, minute


def parse_meeting_datetime(text: str) -> datetime | None:
    """
    Attempt to parse a meeting datetime from natural language.
    Returns UTC datetime or None if unparseable.
    """
    now = datetime.now(timezone.utc)

    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            date_part, time_part = m.group(1).strip(), m.group(2).strip()
            hour, minute = _parse_time_str(time_part)

            dl = date_part.lower()

            if dl == "today":
                dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            elif dl == "tomorrow":
                dt = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            elif dl in _WEEKDAY_MAP:
                target_wd = _WEEKDAY_MAP[dl]
                days_ahead = (target_wd - now.weekday()) % 7 or 7
                dt = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            elif "-" in date_part:
                # ISO format
                d = datetime.strptime(date_part, "%Y-%m-%d")
                dt = d.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
            elif "/" in date_part:
                parts = date_part.split("/")
                if len(parts[2]) == 4:
                    d = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                else:
                    d = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                dt = d.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
            else:
                # "July 10" or "10 July"
                parts = date_part.lower().split()
                for part in parts:
                    for mon_prefix, mon_num in _MONTH_MAP.items():
                        if part.startswith(mon_prefix):
                            month = mon_num
                            day = int([p for p in parts if p.isdigit()][0])
                            dt = datetime(now.year, month, day, hour, minute, tzinfo=timezone.utc)
                            if dt < now:
                                dt = dt.replace(year=now.year + 1)
                            return dt

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception as exc:
            log.debug("parse_meeting_datetime: %s", exc)
            continue

    return None


# ── Meeting CRUD ──────────────────────────────────────────────────────────────

def schedule_meeting(
    title: str,
    scheduled_at: datetime,
    agenda: str = "",
    location: str = "Telegram CEO Office",
) -> dict:
    """
    Schedule a new meeting. Returns the meeting record.
    Stores in Supabase (with in-memory fallback).
    """
    meeting_id = f"mtg_{int(scheduled_at.timestamp())}_{hash(title) % 10000:04d}"
    record = {
        "id":           meeting_id,
        "title":        title[:200],
        "scheduled_at": scheduled_at.isoformat(),
        "agenda":       agenda[:1000],
        "location":     location,
        "status":       "scheduled",
        "notes":        "",
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }

    # Try Supabase first
    saved = _sb_post(record)
    if saved:
        meeting_id = saved.get("id", meeting_id)
        record.update(saved)
    else:
        # In-memory fallback
        with _lock:
            _meetings_cache.append(record)
        log.info("meeting_manager: saved to memory cache (Supabase unavailable)")

    log.info(
        "Meeting scheduled: '%s' at %s",
        title, scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
    )
    return record


def get_upcoming_meetings(limit: int = 10) -> list[dict]:
    """Return upcoming meetings sorted by scheduled_at (soonest first)."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # Try Supabase
    rows = _sb_get({
        "scheduled_at": f"gte.{now_iso}",
        "status":        "eq.scheduled",
        "order":         "scheduled_at.asc",
        "limit":         str(limit),
        "select":        "id,title,scheduled_at,agenda,location,status",
    })
    if rows:
        return rows

    # In-memory fallback
    with _lock:
        now = datetime.now(timezone.utc)
        upcoming = [
            m for m in _meetings_cache
            if m.get("status") == "scheduled"
            and datetime.fromisoformat(m["scheduled_at"]) > now
        ]
        return sorted(upcoming, key=lambda m: m["scheduled_at"])[:limit]


def get_all_meetings(limit: int = 20) -> list[dict]:
    """Return all meetings (past and upcoming), newest first."""
    rows = _sb_get({
        "order": "scheduled_at.desc",
        "limit": str(limit),
        "select": "id,title,scheduled_at,agenda,location,status,notes",
    })
    if rows:
        return rows
    with _lock:
        return sorted(_meetings_cache, key=lambda m: m["scheduled_at"], reverse=True)[:limit]


def add_meeting_notes(meeting_id: str, notes: str) -> bool:
    """Add notes to a completed meeting."""
    if _sb_patch(meeting_id, {"notes": notes[:2000], "status": "completed"}):
        return True
    # In-memory fallback
    with _lock:
        for m in _meetings_cache:
            if m["id"] == meeting_id:
                m["notes"] = notes
                m["status"] = "completed"
                return True
    return False


def cancel_meeting(meeting_id: str) -> bool:
    """Cancel a scheduled meeting."""
    if _sb_patch(meeting_id, {"status": "cancelled"}):
        return True
    with _lock:
        for m in _meetings_cache:
            if m["id"] == meeting_id:
                m["status"] = "cancelled"
                return True
    return False


def format_meeting_card(meeting: dict) -> str:
    """Format a single meeting as a Telegram HTML card."""
    dt_str = "Unknown time"
    try:
        dt = datetime.fromisoformat(meeting["scheduled_at"])
        dt_str = dt.strftime("%A, %B %d %Y at %H:%M UTC")
    except Exception:
        pass

    status_icon = {
        "scheduled": "📅",
        "completed":  "✅",
        "cancelled":  "❌",
    }.get(meeting.get("status", ""), "📅")

    lines = [
        f"{status_icon} <b>{meeting.get('title', 'Meeting')}</b>",
        f"🕐 {dt_str}",
        f"📍 {meeting.get('location', 'CEO Office')}",
    ]

    if meeting.get("agenda"):
        lines.append(f"📋 <i>{meeting['agenda']}</i>")

    if meeting.get("notes"):
        lines.append(f"\n📝 <b>Notes:</b> {meeting['notes'][:300]}")

    return "\n".join(lines)


def format_agenda(meetings: list[dict]) -> str:
    """Format a list of upcoming meetings as a Telegram agenda."""
    if not meetings:
        return (
            "📅 <b>Your agenda is clear.</b>\n\n"
            "No meetings scheduled. Use /schedule_meeting or tell me in the CEO Office "
            "to book one."
        )

    lines = ["📅 <b>Upcoming Meetings</b>\n"]
    for i, m in enumerate(meetings, 1):
        lines.append(f"{i}. {format_meeting_card(m)}")
        lines.append("")

    return "\n".join(lines)


# ── Reminder engine (background loop) ────────────────────────────────────────

def _check_and_send_reminders() -> None:
    """Called every minute. Sends reminders for imminent meetings."""
    upcoming = get_upcoming_meetings(limit=20)
    now = datetime.now(timezone.utc)

    for meeting in upcoming:
        mid = meeting.get("id", "")
        title = meeting.get("title", "Meeting")
        try:
            scheduled = datetime.fromisoformat(meeting["scheduled_at"])
        except Exception:
            continue

        seconds_until = (scheduled - now).total_seconds()

        # 30-minute reminder
        if _REMINDER_10_MINS < seconds_until <= _REMINDER_30_MINS and mid not in _reminded_30:
            mins = int(seconds_until // 60)
            _send_dm(
                f"🔔 <b>Meeting Reminder — {mins} minutes</b>\n\n"
                f"📅 <b>{title}</b>\n"
                f"🕐 {scheduled.strftime('%H:%M UTC')}\n"
                f"📍 {meeting.get('location', 'CEO Office')}\n\n"
                + (f"📋 Agenda: <i>{meeting['agenda']}</i>\n\n" if meeting.get("agenda") else "")
                + "I'll send another reminder at 10 minutes."
            )
            with _lock:
                _reminded_30.add(mid)
            log.info("Sent 30-min reminder for meeting: %s", title)

        # 10-minute reminder
        elif seconds_until <= _REMINDER_10_MINS and mid not in _reminded_10:
            _send_dm(
                f"⏰ <b>Meeting in 10 minutes</b>\n\n"
                f"📅 <b>{title}</b>\n"
                f"🕐 {scheduled.strftime('%H:%M UTC')}\n\n"
                "Time to wrap up whatever you're doing. I'm ready."
            )
            with _lock:
                _reminded_10.add(mid)
            log.info("Sent 10-min reminder for meeting: %s", title)

        # Mark overdue meetings as completed automatically
        elif seconds_until < -3600:  # 1 hour past scheduled time
            cancel_meeting(mid)  # mark as completed/past


# ── Background loop ───────────────────────────────────────────────────────────

def _reminder_loop() -> None:
    log.info("📅 Meeting Manager reminder loop started")
    while _running:
        try:
            _check_and_send_reminders()
        except Exception as exc:
            log.error("meeting_manager reminder loop error: %s", exc)
        for _ in range(_CHECK_INTERVAL_SECS):
            if not _running:
                break
            time.sleep(1)


def start_meeting_manager() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(
        target=_reminder_loop, daemon=True, name="meeting-mgr"
    )
    _thread.start()
    log.info("✅ Meeting Manager started")


def stop_meeting_manager() -> None:
    global _running
    _running = False


# ── Natural language meeting scheduling ───────────────────────────────────────

def parse_schedule_request(text: str) -> dict | None:
    """
    Parse a natural-language meeting request from the CEO.

    Returns a dict with: title, scheduled_at, agenda, location
    Returns None if the request cannot be parsed.

    Examples:
      "Schedule a product review meeting tomorrow at 3pm"
      "Book a meeting with the team on Monday at 10am to discuss the roadmap"
      "Set up a strategy call for July 15 at 14:00"
    """
    dt = parse_meeting_datetime(text)
    if not dt:
        return None

    # Extract title — remove date/time fragments, clean up
    lower = text.lower()
    title = text

    # Strip common scheduling verbs
    for prefix in ["schedule a", "schedule", "book a", "book", "set up a", "set up",
                   "arrange a", "arrange", "plan a", "plan", "create a", "add a",
                   "meeting:", "meeting -"]:
        if lower.startswith(prefix):
            title = text[len(prefix):].strip()
            break

    # Strip date/time from title
    for pattern in _DATE_PATTERNS:
        m = pattern.search(title)
        if m:
            title = (title[:m.start()] + title[m.end():]).strip(" ,-–")

    # Remove common filler phrases
    for filler in [" to discuss", " regarding", " about", " for", " on"]:
        if title.lower().endswith(filler):
            title = title[:-len(filler)].strip()

    title = title.strip(" .,;:-") or "Meeting"
    title = title[:1].upper() + title[1:]  # capitalise first letter

    # Extract agenda (text after "to discuss", "agenda:", "re:", "about:")
    agenda = ""
    for marker in [" to discuss ", " agenda: ", " re: ", " about: ", " regarding "]:
        idx = lower.find(marker)
        if idx != -1:
            agenda = text[idx + len(marker):].strip()
            break

    # Detect location
    location = "Telegram CEO Office"
    if any(w in lower for w in ["call", "zoom", "meet", "video"]):
        location = "Video Call"
    elif any(w in lower for w in ["office", "in-person", "in person"]):
        location = "Physical Office"

    return {
        "title":        title,
        "scheduled_at": dt,
        "agenda":       agenda,
        "location":     location,
    }


def get_todays_meetings() -> list[dict]:
    """Return meetings scheduled for today (UTC)."""
    today = datetime.now(timezone.utc).date()
    all_meetings = get_upcoming_meetings(limit=50)
    return [
        m for m in all_meetings
        if datetime.fromisoformat(m["scheduled_at"]).date() == today
    ]
