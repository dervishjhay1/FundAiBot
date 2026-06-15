"""
FundzAiBot — Real-time web search service.

Uses DuckDuckGo (no API key, no rate limits required) for live web queries.
Results are injected as context before AI calls so responses are factual
and up-to-date even for current-events queries.

All functions are SYNCHRONOUS — call via run_in_executor from async handlers.
"""

import re
from utils.logger import get_logger

log = get_logger(__name__)

# ── Search signal keywords ─────────────────────────────────────────────────────
_SEARCH_SIGNALS = (
    "latest", "current", "today", "yesterday", "this week", "this month",
    "news", "update", "right now", "what happened", "happening",
    "price", "cost", "how much", "rate", "stock", "crypto", "bitcoin",
    "weather", "temperature", "forecast",
    "2024", "2025", "2026", "2027",
    "who won", "score", "result", "election", "vote",
    "release date", "when did", "when is", "when will",
    "search", "look up", "google", "find online", "check online",
    "trending", "viral", "breaking",
    "schedule", "event", "fixture",
    "exchange rate", "usd", "eur", "gbp", "ngn", "naira",
)

_URL_RE = re.compile(
    r"https?://[^\s]+"
    r"|www\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s]*"
)


def should_search(message: str) -> bool:
    """
    Heuristic: decide if a user message warrants a live web search.
    Checks for time-sensitive signals or explicit search intent.
    """
    msg = message.lower()
    return any(s in msg for s in _SEARCH_SIGNALS)


def extract_urls(message: str) -> list[str]:
    """Extract all URLs found in a message."""
    return _URL_RE.findall(message)


def search_web(query: str, max_results: int = 3) -> list[dict]:
    """
    Search the web via DuckDuckGo. Returns list of {title, url, snippet}.
    Safe fallback: returns empty list on any error — never crashes the caller.
    Synchronous — call via run_in_executor.
    """
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   (r.get("title")  or "").strip(),
                    "url":     (r.get("href")   or "").strip(),
                    "snippet": (r.get("body")   or "")[:300].strip(),
                })
        log.info("Web search: %r → %d results", query[:60], len(results))
        return results
    except ImportError:
        log.warning("duckduckgo_search not installed — web search disabled")
        return []
    except Exception as exc:
        log.warning("Web search error (%s): %s", type(exc).__name__, str(exc)[:120])
        return []


def fetch_url_text(url: str, max_chars: int = 1500) -> str:
    """
    Fetch plain text from a URL (best-effort, stripped of HTML).
    Returns empty string on failure.
    Synchronous — call via run_in_executor.
    """
    try:
        import requests
        from html.parser import HTMLParser

        class _Strip(HTMLParser):
            def __init__(self):
                super().__init__()
                self._chunks: list[str] = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    t = data.strip()
                    if t:
                        self._chunks.append(t)

            def text(self) -> str:
                return " ".join(self._chunks)

        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "html" not in ct and "text" not in ct:
            return ""
        parser = _Strip()
        parser.feed(resp.text)
        return parser.text()[:max_chars]
    except Exception as exc:
        log.debug("URL fetch failed (%s): %s", url[:60], exc)
        return ""


def format_search_context(results: list[dict], query: str = "") -> str:
    """Format web search results as a system context block for AI injection."""
    if not results:
        return ""
    header = f"[Real-time Web Search Results for: {query[:80]}]" if query else "[Real-time Web Search Results]"
    lines = [header, "Use these results to answer accurately with current information:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["url"]:
            lines.append(f"   Source: {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    lines.append("[End of search results — cite sources when relevant]")
    return "\n".join(lines)


def format_url_context(url: str, text: str) -> str:
    """Format fetched URL content as a system context block."""
    if not text:
        return ""
    return (
        f"[Content fetched from: {url}]\n"
        f"{text}\n"
        "[End of URL content — summarize or answer based on the above]"
    )
