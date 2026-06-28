"""
FundzAiBot — Channel Publisher Service (Enhanced)

Background service: publishes 15-30 quality posts/day to the channel.

Enhancement: Multi-draft generation with quality scoring.
1. Generate 2 AI drafts independently
2. Score both for quality
3. Publish the higher-scoring draft
4. Fall back to local template if both AI calls fail

Runs as asyncio.create_task() background loop (Railway-safe).
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone

from utils.logger import get_logger
from services.community_manager import (
    get_channel_post_today,
    record_channel_post,
    seconds_since_last_channel_post,
    get_next_content_type,
    get_fallback_post,
    build_channel_post_prompt,
    score_post_quality,
)

log = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

_MIN_POSTS_PER_DAY = 15
_MAX_POSTS_PER_DAY = 25

# Gap between posts: 28-65 min randomized
_MIN_GAP_MINUTES = 28
_MAX_GAP_MINUTES = 65

# Posting window: 06:00–23:00 UTC
_POST_START_HOUR = 6
_POST_END_HOUR = 23

# Quality thresholds
_MIN_QUALITY_SCORE = 0.35    # Minimum score to publish
_MIN_POST_LENGTH = 80        # Characters
_MAX_POST_LENGTH = 1200      # Characters

# Number of AI drafts to generate and compare
_DRAFT_COUNT = 2

_running = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_posting_hour() -> bool:
    hour = datetime.now(timezone.utc).hour
    return _POST_START_HOUR <= hour < _POST_END_HOUR


def _next_post_delay() -> int:
    return random.randint(_MIN_GAP_MINUTES * 60, _MAX_GAP_MINUTES * 60)


def _length_check(text: str) -> tuple[bool, str]:
    n = len(text.strip())
    if n < _MIN_POST_LENGTH:
        return False, f"too short ({n} chars)"
    if n > _MAX_POST_LENGTH:
        return False, f"too long ({n} chars)"
    if "{n}" in text or "{name}" in text:
        return False, "unfilled placeholder"
    if len(text.split()) < 12:
        return False, "too few words"
    return True, "ok"


# ── Multi-draft generation ─────────────────────────────────────────────────────

async def _generate_single_draft(
    content_type: str,
    daily_count: int,
    draft_num: int,
    loop: asyncio.AbstractEventLoop,
) -> tuple[str, float]:
    """Generate one AI draft and return (text, quality_score)."""
    from services.ai_service import get_ai_response

    messages = build_channel_post_prompt(content_type, daily_count, draft_num)
    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(messages),
        )
        if not response:
            return "", 0.0

        ok, reason = _length_check(response)
        if not ok:
            log.debug("Draft %d failed length check: %s", draft_num, reason)
            return "", 0.0

        score = score_post_quality(response)
        log.debug("Draft %d scored %.2f via %s (type=%s)", draft_num, score, provider, content_type)
        return response.strip(), score

    except Exception as exc:
        log.warning("Draft %d generation failed: %s", draft_num, exc)
        return "", 0.0


async def _generate_best_post(content_type: str, daily_count: int) -> str | None:
    """
    Generate multiple drafts, score them, return the best one.
    Falls back to local template if AI fails or scores too low.
    """
    loop = asyncio.get_running_loop()

    # Generate drafts in parallel
    draft_tasks = [
        _generate_single_draft(content_type, daily_count, i + 1, loop)
        for i in range(_DRAFT_COUNT)
    ]
    drafts = await asyncio.gather(*draft_tasks, return_exceptions=True)

    # Pick best draft
    best_text = ""
    best_score = 0.0
    for result in drafts:
        if isinstance(result, Exception):
            continue
        text, score = result
        if text and score > best_score:
            best_text = text
            best_score = score

    if best_text and best_score >= _MIN_QUALITY_SCORE:
        log.info(
            "Publishing best draft (score=%.2f, type=%s, daily#%d)",
            best_score, content_type, daily_count + 1,
        )
        return best_text

    if best_text and best_score > 0:
        log.warning(
            "Best draft score %.2f below threshold %.2f — using fallback",
            best_score, _MIN_QUALITY_SCORE,
        )

    # Fallback: local template
    fallback = get_fallback_post(content_type)
    ok, reason = _length_check(fallback)
    if ok:
        log.info("Using fallback template for type=%s", content_type)
        return fallback

    log.error("Fallback template also failed (%s) — skipping post cycle", reason)
    return None


# ── Publisher ──────────────────────────────────────────────────────────────────

async def _publish(bot, channel_id: str | int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=channel_id, text=text, parse_mode="HTML")
        record_channel_post()
        log.info(
            "Channel post published: channel=%s len=%d daily=%d",
            channel_id, len(text), get_channel_post_today(),
        )
        return True
    except Exception as exc:
        log.error("Channel publish failed: %s", exc)
        return False


async def run_channel_publisher(bot) -> None:
    """
    Long-running background task.
    Started once via asyncio.create_task() in post_init.
    """
    global _running

    from config.settings import TELEGRAM_CHANNEL_ID, FEATURE_FLAGS

    if not TELEGRAM_CHANNEL_ID:
        log.warning("TELEGRAM_CHANNEL_ID not configured — channel publisher disabled")
        return

    _running = True
    log.info(
        "Channel publisher started → %s  target: %d-%d posts/day",
        TELEGRAM_CHANNEL_ID, _MIN_POSTS_PER_DAY, _MAX_POSTS_PER_DAY,
    )

    # Startup delay: don't fire immediately after bot restarts
    await asyncio.sleep(random.randint(90, 240))

    while _running:
        try:
            if not FEATURE_FLAGS.get("chat_enabled", True):
                await asyncio.sleep(300)
                continue

            if not _is_posting_hour():
                await asyncio.sleep(300)
                continue

            daily_count = get_channel_post_today()
            if daily_count >= _MAX_POSTS_PER_DAY:
                now = datetime.now(timezone.utc)
                seconds_to_midnight = (
                    (24 * 3600)
                    - (now.hour * 3600 + now.minute * 60 + now.second)
                )
                log.info(
                    "Daily limit %d reached — sleeping %ds until tomorrow",
                    _MAX_POSTS_PER_DAY, seconds_to_midnight,
                )
                await asyncio.sleep(seconds_to_midnight + 300)
                continue

            content_type = get_next_content_type(daily_count)
            text = await _generate_best_post(content_type, daily_count)

            if text:
                await _publish(bot, TELEGRAM_CHANNEL_ID, text)

            delay = _next_post_delay()
            log.info(
                "Next channel post in %dm %ds  (today: %d/%d)",
                delay // 60, delay % 60,
                get_channel_post_today(), _MAX_POSTS_PER_DAY,
            )
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            log.info("Channel publisher cancelled.")
            _running = False
            break
        except Exception as exc:
            log.error("Channel publisher loop error: %s", exc)
            await asyncio.sleep(300)

    log.info("Channel publisher stopped.")


def is_running() -> bool:
    return _running


def stop() -> None:
    global _running
    _running = False
