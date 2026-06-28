"""
FundzAiBot — TestAudit DM Operations Manager

Environment 3: Private Bot (Direct Message)

TestAudit works behind the scenes as the Executive Assistant / Operations Monitor.
It tracks customer satisfaction indicators, feature adoption, inactive users,
and usage patterns — and can generate reports for the CEO.

This module also runs the proactive group engagement scheduler as a background task.

All monitoring is non-invasive. No spamming users. No unsolicited cold DMs.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# ── Engagement scheduler config ────────────────────────────────────────────────

# How often to check if groups need proactive engagement (seconds)
_ENGAGEMENT_CHECK_INTERVAL = 300   # 5 minutes

# Posting window for proactive engagement: 08:00–22:00 UTC
_ENGAGE_START_HOUR = 8
_ENGAGE_END_HOUR = 22

# Whether the scheduler is running
_running = False

# Tracked group chat IDs for proactive engagement
# These are populated when the bot sees activity in a group
_KNOWN_GROUP_CHATS: set[int] = set()


def register_group_chat(chat_id: int) -> None:
    """Register a group chat for proactive engagement monitoring."""
    _KNOWN_GROUP_CHATS.add(chat_id)


def get_known_groups() -> set[int]:
    return set(_KNOWN_GROUP_CHATS)


def _is_engagement_hour() -> bool:
    hour = datetime.now(timezone.utc).hour
    return _ENGAGE_START_HOUR <= hour < _ENGAGE_END_HOUR


# ── Proactive group engagement background scheduler ────────────────────────────

async def run_group_engagement_scheduler(bot) -> None:
    """
    Background task: periodically checks each known group and sends a
    proactive engagement message when the group has been silent long enough.

    Runs alongside the channel publisher as a separate asyncio task.
    """
    global _running
    _running = True

    from config.settings import TELEGRAM_GROUP_ID, FEATURE_FLAGS

    # Register the configured group if set
    if TELEGRAM_GROUP_ID:
        try:
            register_group_chat(int(TELEGRAM_GROUP_ID))
        except (ValueError, TypeError):
            pass

    log.info("Group engagement scheduler started.")

    # Initial delay before first check
    await asyncio.sleep(random.randint(300, 600))

    while _running:
        try:
            if not FEATURE_FLAGS.get("chat_enabled", True):
                await asyncio.sleep(_ENGAGEMENT_CHECK_INTERVAL)
                continue

            if not _is_engagement_hour():
                await asyncio.sleep(_ENGAGEMENT_CHECK_INTERVAL)
                continue

            from handlers.group import send_proactive_engagement

            for chat_id in list(_KNOWN_GROUP_CHATS):
                try:
                    sent = await send_proactive_engagement(bot, chat_id)
                    if sent:
                        log.info("Proactive engagement sent to group %s", chat_id)
                        # Space out group messages if multiple groups
                        await asyncio.sleep(random.randint(30, 90))
                except Exception as exc:
                    log.warning(
                        "Proactive engagement error for group %s: %s", chat_id, exc
                    )

            await asyncio.sleep(_ENGAGEMENT_CHECK_INTERVAL)

        except asyncio.CancelledError:
            log.info("Group engagement scheduler cancelled.")
            _running = False
            break
        except Exception as exc:
            log.error("Engagement scheduler error: %s", exc)
            await asyncio.sleep(120)

    log.info("Group engagement scheduler stopped.")


# ── DM Operations report builder ───────────────────────────────────────────────

def build_ops_report() -> str:
    """
    Generate a text summary of current operations status.
    Used by CEO Office briefings and /testaudit ops section.
    """
    from services.community_manager import get_dm_stats, get_channel_post_today

    stats = get_dm_stats()
    now = datetime.now(timezone.utc)
    hour = now.hour

    channel_today = get_channel_post_today()
    last_post_min = int(stats["seconds_since_last_channel_post"] / 60)

    lines = [
        f"📊 Operations Summary — {now.strftime('%H:%M UTC')}",
        "",
        f"📡 Channel: {channel_today} posts today",
        f"   Last post: {last_post_min}m ago" if last_post_min < 1440 else "   No posts yet today",
        f"👥 DM Activity: {stats['active_24h']} active in last 24h",
        f"💤 Inactive (5d+): {stats['inactive_5d']} users",
        f"🌐 Known groups: {len(_KNOWN_GROUP_CHATS)}",
        "",
        f"⚙️ Status: {'Active' if _running else 'Stopped'}",
    ]

    return "\n".join(lines)


def stop() -> None:
    global _running
    _running = False
