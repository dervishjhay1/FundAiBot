"""
FundAiBot — Shared utility functions.
"""

import html
import re
from datetime import datetime


def escape_html(text: str) -> str:
    return html.escape(str(text))


def chunk_text(text: str, size: int = 4000) -> list[str]:
    """Split long text into Telegram-safe chunks (max 4096 chars)."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def sanitise_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    prompt = re.sub(r"\s+", " ", prompt)
    return prompt[:2000]  # hard cap


def mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape_html(name)}</a>'


def format_timestamp(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.utcnow()
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def pluralise(count: int, singular: str, plural: str | None = None) -> str:
    if plural is None:
        plural = singular + "s"
    return singular if count == 1 else plural


def format_number(n: int) -> str:
    """Format large numbers with commas."""
    return f"{n:,}"


def time_ago(dt_str: str) -> str:
    """Return a human-readable 'X ago' string from an ISO datetime string."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00").replace("+00:00", ""))
        diff = datetime.utcnow() - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception:
        return "recently"


def progress_bar(current: int, total: int, length: int = 10) -> str:
    """Return a text progress bar like ████░░░░░░ 40%"""
    if total == 0:
        return "░" * length + " 0%"
    filled = int(length * current / total)
    bar = "█" * filled + "░" * (length - filled)
    pct = int(100 * current / total)
    return f"{bar} {pct}%"
