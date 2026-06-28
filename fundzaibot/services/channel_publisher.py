"""
FundzAiBot — Channel Publisher Service

Background service that automatically publishes 15-30 quality posts per day
to the configured Telegram channel. Uses AI to generate content, applies a
quality review filter, and respects daily limits and timing windows.

Runs as a background task via `asyncio.create_task()` in main.py.
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
    should_post_to_channel,
    get_next_content_type,
    generate_channel_post,
    generate_ai_channel_prompt,
)

log = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

_MIN_POSTS_PER_DAY = 15
_MAX_POSTS_PER_DAY = 25
_MIN_GAP_MINUTES = 28      # Minimum minutes between posts
_MAX_GAP_MINUTES = 65      # Maximum minutes between posts

# Posting hours (UTC) — only post between these hours
_POST_START_HOUR = 6   # 6 AM UTC
_POST_END_HOUR = 23    # 11 PM UTC

# Maximum length for a channel post
_MAX_POST_LENGTH = 1000

# Quality filter: minimum length for AI-generated post
_MIN_POST_LENGTH = 80

# Whether the publisher is currently running
_running = False


def _is_posting_hour() -> bool:
    """Return True if current UTC hour is within the posting window."""
    hour = datetime.now(timezone.utc).hour
    return _POST_START_HOUR <= hour < _POST_END_HOUR


def _next_post_delay_seconds() -> int:
    """Return a randomized delay until the next post (in seconds)."""
    min_s = _MIN_GAP_MINUTES * 60
    max_s = _MAX_GAP_MINUTES * 60
    return random.randint(min_s, max_s)


def _quality_check(text: str) -> tuple[bool, str]:
    """
    Basic quality filter for generated posts.
    Returns (passed: bool, reason: str).
    """
    if len(text.strip()) < _MIN_POST_LENGTH:
        return False, f"Too short ({len(text.strip())} chars, min {_MIN_POST_LENGTH})"
    if len(text.strip()) > _MAX_POST_LENGTH:
        return False, f"Too long ({len(text.strip())} chars, max {_MAX_POST_LENGTH})"
    # Must not be just a repetition of the template placeholder
    if "{n}" in text or "{name}" in text:
        return False, "Unfilled template placeholder"
    # Should have some substance
    word_count = len(text.split())
    if word_count < 15:
        return False, f"Too few words ({word_count})"
    return True, "OK"


async def _generate_post_text(content_type: str, daily_count: int, bot) -> str | None:
    """
    Attempt to generate post text.
    1. Try AI generation first.
    2. Fall back to local template on AI failure.
    """
    from services.ai_service import get_ai_response

    messages = generate_ai_channel_prompt(content_type, daily_count)
    loop = asyncio.get_running_loop()

    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(messages),
        )

        passed, reason = _quality_check(response)
        if passed:
            log.info("Channel post generated via AI (%s): type=%s len=%d",
                     provider, content_type, len(response))
            return response.strip()
        else:
            log.warning("AI post failed quality check: %s — falling back to template", reason)

    except Exception as exc:
        log.warning("AI post generation failed: %s — using template", exc)

    # Fallback: local template
    post = generate_channel_post(content_type, daily_count + 1)
    passed, reason = _quality_check(post)
    if passed:
        log.info("Using template post for type=%s", content_type)
        return post.strip()

    log.error("Template post also failed quality check (%s) — skipping post", reason)
    return None


async def _publish_post(bot, channel_id: str | int, text: str) -> bool:
    """Send a post to the channel. Returns True on success."""
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode="HTML",
        )
        record_channel_post()
        log.info("Channel post published: channel=%s chars=%d daily=%d",
                 channel_id, len(text), get_channel_post_today())
        return True
    except Exception as exc:
        log.error("Failed to publish channel post: %s", exc)
        return False


async def run_channel_publisher(bot) -> None:
    """
    Long-running background task: publishes posts to the channel on schedule.
    Should be started once at bot startup via asyncio.create_task().
    """
    global _running

    from config.settings import TELEGRAM_CHANNEL_ID, FEATURE_FLAGS

    if not TELEGRAM_CHANNEL_ID:
        log.warning("TELEGRAM_CHANNEL_ID not set — channel publisher disabled")
        return

    _running = True
    log.info("Channel publisher started. Target: %d-%d posts/day to %s",
             _MIN_POSTS_PER_DAY, _MAX_POSTS_PER_DAY, TELEGRAM_CHANNEL_ID)

    # Initial startup delay (don't post immediately on bot start)
    await asyncio.sleep(random.randint(120, 300))

    while _running:
        try:
            # Check feature flags
            if not FEATURE_FLAGS.get("chat_enabled", True):
                await asyncio.sleep(300)
                continue

            # Check posting window
            if not _is_posting_hour():
                await asyncio.sleep(300)  # Sleep 5 min, then check again
                continue

            # Check daily limit
            daily_count = get_channel_post_today()
            if daily_count >= _MAX_POSTS_PER_DAY:
                log.info("Daily post limit reached (%d/%d). Sleeping until tomorrow.",
                         daily_count, _MAX_POSTS_PER_DAY)
                # Sleep until next UTC midnight + buffer
                now = datetime.now(timezone.utc)
                seconds_to_midnight = (24 * 3600) - (now.hour * 3600 + now.minute * 60 + now.second)
                await asyncio.sleep(seconds_to_midnight + 300)
                continue

            # Generate and publish
            content_type = get_next_content_type(daily_count)
            text = await _generate_post_text(content_type, daily_count, bot)

            if text:
                success = await _publish_post(bot, TELEGRAM_CHANNEL_ID, text)
                if not success:
                    log.warning("Post failed — will retry after normal delay")

            # Wait until next post
            delay = _next_post_delay_seconds()
            log.info("Next channel post in %dm %ds (daily: %d/%d)",
                     delay // 60, delay % 60,
                     get_channel_post_today(), _MAX_POSTS_PER_DAY)
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            log.info("Channel publisher task cancelled.")
            _running = False
            break
        except Exception as exc:
            log.error("Channel publisher error: %s", exc)
            await asyncio.sleep(300)

    log.info("Channel publisher stopped.")


def is_running() -> bool:
    return _running


def stop() -> None:
    global _running
    _running = False
