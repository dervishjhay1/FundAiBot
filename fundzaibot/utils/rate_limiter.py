"""
FundAiBot — In-memory rate limiter.
Prevents spam and protects AI provider quotas.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from config.settings import RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW
from utils.logger import get_logger

log = get_logger(__name__)

# {user_id: deque of timestamps}
_windows: dict[int, deque] = defaultdict(deque)
_lock = Lock()


def is_rate_limited(user_id: int) -> bool:
    """
    Return True if the user has exceeded the rate limit.
    Uses a sliding window algorithm.
    """
    now = time.time()
    with _lock:
        window = _windows[user_id]
        # Drop old timestamps outside the window
        while window and now - window[0] > RATE_LIMIT_WINDOW:
            window.popleft()
        if len(window) >= RATE_LIMIT_MESSAGES:
            log.warning("Rate limit hit for user %s (%d msgs in %ds)", user_id, len(window), RATE_LIMIT_WINDOW)
            return True
        window.append(now)
        return False


def get_wait_time(user_id: int) -> int:
    """Return seconds until the user can send again."""
    now = time.time()
    with _lock:
        window = _windows.get(user_id)
        if not window:
            return 0
        oldest = window[0]
        wait = int(RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return max(0, wait)


def reset_user(user_id: int) -> None:
    """Clear rate limit for a user (admin use)."""
    with _lock:
        _windows.pop(user_id, None)
