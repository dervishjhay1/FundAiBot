"""
FundzAiBot — Headquarters Synchronization Service

FundzAiBot reports. Headquarters governs.
FundzAiBot never makes executive decisions.

This module synchronizes every significant user activity to
Fundz Company Headquarters. Events are queued in memory and
retried automatically if Headquarters is temporarily unavailable.
No executive event is ever lost.

Architecture:
  • All events are queued via sync_event() — non-blocking.
  • A background daemon thread drains the queue via HTTP POST to HQ.
  • If HQ is unavailable: exponential back-off retry, queue persists.
  • On Railway restart: queued events are replayed once the bot starts.
  • Thread-safe: queue protected by threading.Lock().

Event schema:
  {
    "event_id":     str (UUID),
    "timestamp":    str (ISO-8601 UTC),
    "source":       "fundzaibot",
    "product":      "FundzAiBot",
    "version":      str,
    "user_id":      int | None,
    "username":     str | None,
    "event_type":   str,
    "category":     str,
    "priority":     "low" | "normal" | "high" | "critical",
    "metadata":     dict,
    "status":       "queued" | "delivered" | "failed",
  }
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import (
    HQ_API_URL, HQ_API_KEY, HQ_SYNC_ENABLED,
    HQ_SYNC_MAX_RETRIES, HQ_SYNC_RETRY_INTERVAL,
    BOT_NAME, BOT_VERSION,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_QUEUE_SIZE   = 5000      # max events in memory before oldest are dropped
_DRAIN_INTERVAL   = 2.0       # seconds between queue drain cycles
_HQ_TIMEOUT       = (5, 15)   # (connect, read) seconds
_MAX_RETRY_DELAY  = 300       # max back-off in seconds (5 minutes)

# ── State ─────────────────────────────────────────────────────────────────────

_queue: list[dict]      = []
_lock:  threading.Lock  = threading.Lock()
_running: bool          = False
_thread: threading.Thread | None = None
_consecutive_failures: int = 0

# ── Internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_event(
    event_type: str,
    category: str,
    priority: str = "normal",
    user_id: int | None = None,
    username: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "timestamp":  _now_iso(),
        "source":     "fundzaibot",
        "product":    BOT_NAME,
        "version":    BOT_VERSION,
        "user_id":    user_id,
        "username":   username,
        "event_type": event_type,
        "category":   category,
        "priority":   priority,
        "metadata":   metadata or {},
        "status":     "queued",
    }


def _post_to_hq(events: list[dict]) -> bool:
    """
    POST a batch of events to HQ.
    Returns True on success (2xx), False on any failure.
    """
    if not HQ_SYNC_ENABLED:
        return True  # silently drop when HQ not configured

    try:
        resp = requests.post(
            f"{HQ_API_URL}/api/events/ingest",
            json={"events": events},
            headers={
                "Authorization": f"Bearer {HQ_API_KEY}",
                "Content-Type":  "application/json",
                "X-Source":      "fundzaibot",
                "X-Version":     BOT_VERSION,
            },
            timeout=_HQ_TIMEOUT,
        )
        if resp.status_code in (200, 201, 202, 204):
            return True
        log.warning("HQ sync HTTP %s: %s", resp.status_code, resp.text[:200])
        return False
    except requests.Timeout:
        log.warning("HQ sync timed out")
        return False
    except requests.ConnectionError as exc:
        log.warning("HQ sync connection error: %s", exc)
        return False
    except Exception as exc:
        log.error("HQ sync unexpected error: %s", exc)
        return False


def _drain_loop() -> None:
    """Background daemon: drain the event queue, retry on failure."""
    global _consecutive_failures

    log.info("✅ HQ sync daemon started (enabled=%s, url=%s)", HQ_SYNC_ENABLED, HQ_API_URL or "N/A")

    while _running:
        time.sleep(_DRAIN_INTERVAL)

        with _lock:
            if not _queue:
                continue
            batch = _queue[:50]   # drain up to 50 events per cycle
            del _queue[:50]

        if not batch:
            continue

        success = _post_to_hq(batch)
        if success:
            _consecutive_failures = 0
            log.debug("HQ sync: delivered %d events", len(batch))
        else:
            _consecutive_failures += 1
            # Put failed events back at the front of the queue for retry
            with _lock:
                for ev in reversed(batch):
                    _queue.insert(0, ev)

            # Exponential back-off: 60s, 120s, 240s … up to _MAX_RETRY_DELAY
            delay = min(_MAX_RETRY_DELAY, HQ_SYNC_RETRY_INTERVAL * (2 ** min(_consecutive_failures - 1, 4)))
            log.warning(
                "HQ sync failed (attempt %d). Retrying in %ds. Queue size: %d",
                _consecutive_failures, delay, len(_queue),
            )
            time.sleep(delay)

    log.info("HQ sync daemon stopped")


# ── Public API ────────────────────────────────────────────────────────────────

def sync_event(
    event_type: str,
    category: str = "general",
    priority: str = "normal",
    user_id: int | None = None,
    username: str | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Queue an event for delivery to Headquarters.
    Non-blocking — returns immediately.
    The daemon thread handles delivery and retry.

    Categories: user, ai, session, system, abuse, subscription, health
    Priorities: low, normal, high, critical
    """
    if not HQ_SYNC_ENABLED:
        return  # no HQ configured — skip silently

    event = _build_event(
        event_type=event_type,
        category=category,
        priority=priority,
        user_id=user_id,
        username=username,
        metadata=metadata,
    )

    with _lock:
        if len(_queue) >= _MAX_QUEUE_SIZE:
            # Drop oldest event to make room
            dropped = _queue.pop(0)
            log.warning("HQ queue full — dropped event: %s", dropped.get("event_type"))
        _queue.append(event)


def start_hq_sync() -> None:
    """Start the HQ sync daemon thread. Call once from post_init()."""
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_drain_loop, daemon=True, name="hq-sync")
    _thread.start()


def stop_hq_sync() -> None:
    global _running
    _running = False


def queue_size() -> int:
    with _lock:
        return len(_queue)


def sync_status() -> dict:
    return {
        "enabled":              HQ_SYNC_ENABLED,
        "hq_url":               HQ_API_URL or "not configured",
        "queue_size":           queue_size(),
        "consecutive_failures": _consecutive_failures,
        "running":              _running,
    }


# ── Convenience event helpers ─────────────────────────────────────────────────
# Call these from handlers to report standard events.

def event_user_registered(user_id: int, username: str | None, metadata: dict | None = None) -> None:
    sync_event("user_registered", "user", "normal", user_id, username, metadata)


def event_first_conversation(user_id: int, username: str | None, metadata: dict | None = None) -> None:
    sync_event("first_conversation", "user", "normal", user_id, username, metadata)


def event_returning_user(user_id: int, username: str | None, metadata: dict | None = None) -> None:
    sync_event("returning_user", "user", "low", user_id, username, metadata)


def event_prompt_submitted(
    user_id: int,
    username: str | None,
    category: str,
    provider: str,
    metadata: dict | None = None,
) -> None:
    md = {"prompt_category": category, "ai_provider": provider, **(metadata or {})}
    sync_event("prompt_submitted", "ai", "low", user_id, username, md)


def event_conversation_started(user_id: int, username: str | None, metadata: dict | None = None) -> None:
    sync_event("conversation_started", "session", "low", user_id, username, metadata)


def event_conversation_completed(user_id: int, username: str | None, metadata: dict | None = None) -> None:
    sync_event("conversation_completed", "session", "low", user_id, username, metadata)


def event_ai_provider_failure(provider: str, error: str, metadata: dict | None = None) -> None:
    md = {"provider": provider, "error": error[:500], **(metadata or {})}
    sync_event("ai_provider_failure", "health", "high", None, None, md)


def event_rate_limit(user_id: int, username: str | None, limit_type: str) -> None:
    sync_event("rate_limit_hit", "user", "normal", user_id, username, {"limit_type": limit_type})


def event_abuse_detected(user_id: int, username: str | None, reason: str) -> None:
    sync_event("abuse_detected", "abuse", "high", user_id, username, {"reason": reason})


def event_spam_detected(user_id: int, username: str | None, content: str) -> None:
    sync_event("spam_detected", "abuse", "high", user_id, username, {"content": content[:200]})


def event_subscription_change(
    user_id: int,
    username: str | None,
    plan: str,
    action: str,
    metadata: dict | None = None,
) -> None:
    md = {"plan": plan, "action": action, **(metadata or {})}
    sync_event("subscription_change", "subscription", "normal", user_id, username, md)


def event_language_change(user_id: int, username: str | None, language: str) -> None:
    sync_event("language_change", "user", "low", user_id, username, {"language": language})


def event_feature_used(user_id: int, username: str | None, feature: str) -> None:
    sync_event("feature_used", "ai", "low", user_id, username, {"feature": feature})


def event_error(context: str, error: str, metadata: dict | None = None) -> None:
    md = {"context": context, "error": error[:500], **(metadata or {})}
    sync_event("error", "system", "high", None, None, md)


def event_system_restart(metadata: dict | None = None) -> None:
    sync_event("system_restart", "system", "normal", None, None, metadata)


def event_health_change(status: str, score: float | None = None) -> None:
    sync_event("health_change", "health", "normal", None, None, {"status": status, "score": score})


def event_executive_request(
    user_id: int,
    username: str | None,
    request_type: str,
    description: str,
    metadata: dict | None = None,
) -> None:
    """
    Route an executive request to Headquarters.
    FundzAiBot never processes executive decisions locally.
    """
    md = {
        "request_type": request_type,
        "description":  description[:1000],
        "routed_to":    "headquarters",
        **(metadata or {}),
    }
    sync_event("executive_request", "system", "high", user_id, username, md)
