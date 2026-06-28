"""
FundzAiBot — Executive Chat (TestAudit role)

Allows the CEO to have a natural conversation with TestAudit about the company.
TestAudit answers like a real Operations Manager: data-driven, honest, transparent.

CEO can ask:
  "How is the company today?"
  "What should we improve?"
  "Why are users leaving?"
  "What is the biggest problem?"
  "Which feature should we build next?"
  "What happened while I was away?"
  "Give me a full company status"

TestAudit builds real context from live metrics, then uses the AI provider to
compose a professional, evidence-based response.

This is NOT a general-purpose chatbot. It answers ONLY about the company.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME, BOT_VERSION,
)
from utils.logger import get_logger

log = get_logger(__name__)

_SYSTEM_PROMPT = f"""\
You are TestAudit, the Chief Operations & Executive Intelligence Manager of {BOT_NAME}.
You are speaking directly with the CEO (the company owner).

Your role is to answer the CEO's questions about the company using ONLY the real metrics
and context provided to you. You are professional, honest, calm, and evidence-driven.

Rules:
- Answer based ONLY on the data provided in the context. Never invent statistics.
- If you don't have enough data to answer confidently, say so honestly.
- Be concise but thorough. Use bullet points where helpful.
- Never be emotional, never flatter the CEO.
- Format with HTML bold/italic tags for Telegram display.
- Always end with 1-2 specific, actionable recommendations.
- Maximum response: 400 words.
- Refer to yourself as "TestAudit" and to the owner as "CEO".
"""


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }


def _sb_get(path: str, params: dict | None = None) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers=_hdrs(), params=params or {}, timeout=(5, 10),
        )
    except Exception:
        return None


# ── Context builder ───────────────────────────────────────────────────────────

def _build_company_context() -> str:
    """
    Gather real company metrics to give TestAudit the context it needs.
    Returns a structured text block for injection into the AI prompt.
    """
    context_parts = [
        f"=== {BOT_NAME} v{BOT_VERSION} — Company Context ===",
        f"Report Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # Health score
    try:
        from services.testaudit_core import get_last_health, predict_risks
        health = get_last_health()
        score  = health.get("score", "N/A")
        tier   = health.get("tier", "unknown")
        context_parts.append(f"COMPANY HEALTH: {score}/100 ({tier.upper()})")
    except Exception:
        context_parts.append("COMPANY HEALTH: unavailable")

    # User stats
    try:
        r = _sb_get("users", {"select": "count"})
        total = len(r.json()) if r and r.status_code == 200 else "N/A"

        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        r2 = _sb_get("users", {"last_seen": f"gte.{cutoff_24h}", "select": "user_id"})
        active_24h = len(r2.json()) if r2 and r2.status_code == 200 else "N/A"

        cutoff_7d  = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        r3 = _sb_get("users", {"created_at": f"gte.{cutoff_7d}", "select": "user_id"})
        new_7d = len(r3.json()) if r3 and r3.status_code == 200 else "N/A"

        r4 = _sb_get("users", {"is_vip": "eq.true", "select": "user_id"})
        vip_count = len(r4.json()) if r4 and r4.status_code == 200 else "N/A"

        context_parts.append(
            f"USERS: total={total}, active_24h={active_24h}, new_7d={new_7d}, vip={vip_count}"
        )
    except Exception:
        context_parts.append("USERS: unavailable")

    # Usage stats
    try:
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        r5 = _sb_get("conversations", {"created_at": f"gte.{cutoff_24h}", "role": "eq.user", "select": "id"})
        chats_24h = len(r5.json()) if r5 and r5.status_code == 200 else "N/A"

        r6 = _sb_get("image_history", {"created_at": f"gte.{cutoff_24h}", "select": "id"})
        images_24h = len(r6.json()) if r6 and r6.status_code == 200 else "N/A"

        context_parts.append(f"ACTIVITY_24H: conversations={chats_24h}, images={images_24h}")
    except Exception:
        context_parts.append("ACTIVITY_24H: unavailable")

    # Error rate
    try:
        cutoff_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        r7 = _sb_get("error_logs", {"created_at": f"gte.{cutoff_1h}", "select": "id"})
        errors_1h = len(r7.json()) if r7 and r7.status_code == 200 else "N/A"
        context_parts.append(f"ERRORS_LAST_1H: {errors_1h}")
    except Exception:
        context_parts.append("ERRORS_LAST_1H: unavailable")

    # Backlog
    try:
        from services.testaudit_core import get_backlog
        backlog = get_backlog(status="open", limit=5)
        if backlog:
            items_str = "; ".join(
                f"{b['title']} ({b.get('priority','?')} priority)"
                for b in backlog[:5]
            )
            context_parts.append(f"TOP_BACKLOG_ITEMS: {items_str}")
        else:
            context_parts.append("TOP_BACKLOG_ITEMS: none")
    except Exception:
        context_parts.append("TOP_BACKLOG_ITEMS: unavailable")

    # Pending approvals
    try:
        from services.testaudit_core import get_pending_approvals
        pending = get_pending_approvals()
        context_parts.append(f"PENDING_CEO_APPROVALS: {len(pending)}")
    except Exception:
        context_parts.append("PENDING_CEO_APPROVALS: unavailable")

    # Autonomous mode
    try:
        from services.autonomous_mode import get_aom_status
        aom = get_aom_status()
        context_parts.append(
            f"AUTONOMOUS_MODE: {'ACTIVE' if aom['autonomous_mode'] else 'inactive'}, "
            f"ceo_inactive_hours={aom['ceo_inactive_hours']}"
        )
    except Exception:
        context_parts.append("AUTONOMOUS_MODE: unknown")

    # Recent memory
    try:
        from services.testaudit_core import get_recent_memory
        memory = get_recent_memory(limit=5)
        if memory:
            mem_str = "; ".join(
                f"{m.get('event_type','?')}: {m.get('title','?')[:60]}"
                for m in memory
            )
            context_parts.append(f"RECENT_EVENTS: {mem_str}")
    except Exception:
        pass

    context_parts.append("=== END CONTEXT ===")
    return "\n".join(context_parts)


# ── AI Query ──────────────────────────────────────────────────────────────────

def _query_ai(user_question: str, context: str) -> str | None:
    """Send question + context to AI. Returns TestAudit's response or None."""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Current company data:\n{context}\n\n"
                f"CEO's question: {user_question}"
            ),
        },
    ]

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
                    "max_tokens":  600,
                    "temperature": 0.4,   # lower = more factual, less creative
                },
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            log.debug("executive_chat OpenRouter error: %s", exc)

    # Fallback: Gemini
    if GEMINI_API_KEY:
        try:
            combined = f"{_SYSTEM_PROMPT}\n\nCurrent company data:\n{context}\n\nCEO's question: {user_question}"
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": combined}]}],
                    "generationConfig": {"maxOutputTokens": 600, "temperature": 0.4},
                },
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                return (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
        except Exception as exc:
            log.debug("executive_chat Gemini fallback error: %s", exc)

    return None


# ── Public interface ───────────────────────────────────────────────────────────

def ask_testaudit(question: str) -> str:
    """
    CEO asks TestAudit a question. Returns a fully formatted HTML response.
    This is synchronous — run in executor if called from async context.
    """
    if not question or len(question.strip()) < 3:
        return (
            "❓ Please ask a specific question about the company.\n\n"
            "<i>Examples:\n"
            "• How is the company today?\n"
            "• Why are users leaving?\n"
            "• What should we build next?\n"
            "• What happened while I was away?</i>"
        )

    question = question.strip()[:300]

    # Build real company context
    context = _build_company_context()

    # Query AI
    ai_response = _query_ai(question, context)

    if ai_response:
        return (
            f"🧠 <b>TestAudit — Executive Intelligence</b>\n\n"
            f"<b>Your question:</b> <i>{question}</i>\n\n"
            f"{ai_response}\n\n"
            f"<i>— TestAudit · {datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
        )

    # Fallback: structured answer without AI
    return _build_fallback_response(question, context)


def _build_fallback_response(question: str, context: str) -> str:
    """Return a structured data response when AI is unavailable."""
    lines = [
        "🧠 <b>TestAudit — Executive Intelligence</b>",
        f"<i>AI provider temporarily unavailable — raw data response</i>",
        "",
        f"<b>Your question:</b> <i>{question}</i>",
        "",
        "<b>Current Company Context:</b>",
    ]
    for line in context.split("\n"):
        if line.startswith("===") or not line.strip():
            continue
        lines.append(f"  {line}")
    lines.append("")
    lines.append("<i>Run /testaudit for full interactive diagnostics.</i>")
    lines.append(f"<i>— TestAudit · {datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>")
    return "\n".join(lines)
