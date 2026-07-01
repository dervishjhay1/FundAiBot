"""
FundAiBot — Multi-provider AI chat service.
Priority: OpenAI → OpenRouter → Gemini → HuggingFace.
Graceful fallback if any provider is missing or fails.

All functions are SYNCHRONOUS — call them via run_in_executor from async handlers.
"""

import time
import requests

from config.settings import (
    OPENAI_API_KEY,
    OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY,
    OPENAI_MODEL, OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL,
    BOT_NAME, AI_TIMEOUT,
)
from utils.logger import get_logger

log = get_logger(__name__)

_OPENAI_URL      = "https://api.openai.com/v1/chat/completions"
_OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
_GEMINI_BASE     = "https://generativelanguage.googleapis.com/v1beta/models"

# ── Startup provider diagnostics ──────────────────────────────────────────────
# Logged once at import time so Railway logs show provider state on every boot.
log.info(
    "AI provider config — OpenAI: %s | OpenRouter: %s | Gemini: %s | HuggingFace: %s",
    "✅ KEY SET" if OPENAI_API_KEY       else "❌ NO KEY",
    "✅ KEY SET" if OPENROUTER_API_KEY   else "❌ NO KEY",
    "✅ KEY SET" if GEMINI_API_KEY       else "❌ NO KEY",
    "✅ KEY SET" if HUGGINGFACE_API_KEY  else "❌ NO KEY",
)
log.info(
    "AI model config — OpenAI: %s | OpenRouter: %s | Gemini: %s | HF: %s",
    OPENAI_MODEL, OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL,
)

ENHANCE_PREFIX = (
    "Please respond in a clear, well-structured way. "
    "Use paragraphs, bullet points, or code blocks where appropriate. "
)

# ── Retry helper ──────────────────────────────────────────────────────────────

def _retry_request(fn, retries: int = 2, base_delay: float = 1.0):
    """
    Call fn() with simple exponential-backoff retry on transient errors.
    Only retries on: Timeout, ConnectionError, or 5xx server errors.
    4xx client errors (401, 402, 403, 404, 429) are NOT retried — fall through
    to the next provider in get_ai_response().
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except requests.Timeout as exc:
            last_exc = exc
            log.warning("Request timed out (attempt %d/%d)", attempt + 1, retries + 1)
        except requests.ConnectionError as exc:
            last_exc = exc
            log.warning("Connection error (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status >= 500:
                last_exc = exc
                log.warning("Server error %s (attempt %d/%d)", status, attempt + 1, retries + 1)
            else:
                raise  # 4xx: do not retry, propagate to get_ai_response fallback
        if attempt < retries:
            delay = base_delay * (2 ** attempt)
            log.debug("Retrying in %.1fs…", delay)
            time.sleep(delay)
    raise last_exc


# ── OpenAI ───────────────────────────────────────────────────────────────────

def _openai(messages: list[dict], model: str) -> str:
    if not OPENAI_API_KEY:
        raise ValueError("no OPENAI_API_KEY")

    def _call():
        resp = requests.post(
            _OPENAI_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.75,
            },
            timeout=(10, AI_TIMEOUT),
        )
        if resp.status_code == 401:
            log.warning("OpenAI 401 — invalid API key. Check OPENAI_API_KEY in Railway.")
        elif resp.status_code == 429:
            log.warning("OpenAI 429 — rate limit or quota exceeded. Falling back.")
        elif resp.status_code == 402:
            log.warning("OpenAI 402 — billing issue. Check your OpenAI account.")
        elif resp.status_code == 404:
            log.warning("OpenAI 404 — model '%s' not found. Check OPENAI_MODEL env var.", model)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return (content or "").strip()

    return _retry_request(_call, retries=1)


# ── OpenRouter ────────────────────────────────────────────────────────────────

def _openrouter(messages: list[dict], model: str) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("no OPENROUTER_API_KEY")

    def _call():
        resp = requests.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://github.com/FundAiBot",
                "X-Title": BOT_NAME,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.75,
            },
            timeout=(10, AI_TIMEOUT),
        )
        # Log actionable details for common 4xx errors before raising
        if resp.status_code == 402:
            log.warning(
                "OpenRouter 402 — insufficient credits. "
                "Top up at https://openrouter.ai/credits — falling back to Gemini."
            )
        elif resp.status_code == 429:
            log.warning("OpenRouter 429 — rate limit hit. Falling back to Gemini.")
        elif resp.status_code == 404:
            log.warning(
                "OpenRouter 404 — model '%s' not found. "
                "Check OPENROUTER_MODEL env var. Falling back to Gemini.", model
            )
        elif resp.status_code == 401:
            log.warning("OpenRouter 401 — invalid API key. Falling back to Gemini.")
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return (content or "").strip()

    return _retry_request(_call, retries=1)


# ── Gemini ────────────────────────────────────────────────────────────────────

def _gemini(messages: list[dict]) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("no GEMINI_API_KEY")

    contents = []
    for m in messages:
        if m["role"] == "system":
            contents.append({"role": "user", "parts": [{"text": f"[System instruction] {m['content']}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will follow these instructions."}]})
        else:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    def _call():
        resp = requests.post(
            f"{_GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.75},
            },
            timeout=(10, AI_TIMEOUT),
        )
        if resp.status_code == 429:
            log.warning("Gemini 429 — quota exceeded. Falling back to HuggingFace.")
        elif resp.status_code == 400:
            log.warning("Gemini 400 — bad request or invalid API key. Falling back to HuggingFace.")
        elif resp.status_code == 403:
            log.warning("Gemini 403 — API key restricted. Falling back to HuggingFace.")
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no candidates (response may be blocked)")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ValueError("Gemini returned empty parts")
        text = parts[0].get("text", "")
        return (text or "").strip()

    return _retry_request(_call, retries=1)


# ── HuggingFace ───────────────────────────────────────────────────────────────

def _huggingface(messages: list[dict]) -> str:
    if not HUGGINGFACE_API_KEY:
        raise ValueError("no HUGGINGFACE_API_KEY")

    conversation = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in messages
        if m["role"] != "system"
    )
    prompt = f"{conversation}\nAssistant:"

    def _call():
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{HF_CHAT_MODEL}",
            headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 400,
                    "temperature": 0.75,
                    "return_full_text": False,
                },
            },
            timeout=(10, AI_TIMEOUT),
        )

        if response.status_code == 503:
            log.warning("HuggingFace 503 — model warming up (~20s). Will be skipped this request.")
            raise requests.HTTPError("503 Model loading", response=response)
        if response.status_code == 401:
            log.warning("HuggingFace 401 — invalid token. Check HUGGINGFACE_API_KEY.")
        response.raise_for_status()

        data = response.json()
        if isinstance(data, list) and data:
            text = data[0].get("generated_text", "")
            return text.strip() if text else ""
        if isinstance(data, dict):
            text = data.get("generated_text", "")
            return text.strip() if text else ""
        return ""

    return _retry_request(_call, retries=1)


# ── Public API ────────────────────────────────────────────────────────────────

def get_ai_response(messages: list[dict], model: str = "") -> tuple[str, str]:
    """
    Get an AI response. Returns (response_text, provider_name).
    Tries OpenRouter → Gemini → HuggingFace in order.
    Each provider's 4xx errors are logged then the next provider is tried.

    IMPORTANT: This function is synchronous (uses requests).
    Always call it via asyncio.get_running_loop().run_in_executor() in async handlers.
    """
    if not model:
        model = OPENAI_MODEL if OPENAI_API_KEY else OPENROUTER_MODEL

    providers = [
        ("OpenAI",      lambda: _openai(messages, OPENAI_MODEL)),
        ("OpenRouter",  lambda: _openrouter(messages, OPENROUTER_MODEL)),
        ("Gemini",      lambda: _gemini(messages)),
        ("HuggingFace", lambda: _huggingface(messages)),
    ]

    for name, fn in providers:
        try:
            log.debug("Trying provider: %s", name)
            result = fn()
            if result and result.strip():
                log.info("AI response from %s (%d chars)", name, len(result))
                return result, name
            log.warning("%s returned empty response — trying next provider", name)
        except ValueError as exc:
            log.debug("Skipping %s — %s", name, exc)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            log.warning("%s HTTP %s — trying next provider", name, status)
        except requests.Timeout:
            log.warning("%s timed out after %ds — trying next provider", name, AI_TIMEOUT)
        except requests.ConnectionError as exc:
            log.warning("%s connection error — trying next provider (%s)", name, str(exc)[:60])
        except Exception as exc:
            log.error("%s unexpected error: %s — trying next provider", name, exc)

    log.error("All AI providers failed — returning unavailable message to user")
    return (
        "⚠️ All AI providers are currently unavailable. Please try again in a moment.",
        "none",
    )


def enhance_prompt(prompt: str) -> str:
    """Enhance a user's image prompt using AI. Synchronous — run in executor."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a prompt engineer. Enhance the following image generation prompt to be "
                "more detailed, vivid, and specific. Return only the enhanced prompt, nothing else. "
                "Keep it under 300 words."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    response, _ = get_ai_response(messages)
    if response and "unavailable" not in response.lower() and len(response) > 5:
        return response
    return prompt


def check_provider_health() -> dict[str, str]:
    """
    Connectivity + auth check for every configured AI provider and Supabase DB.
    Returns {provider_name: status_string}.
    All network errors are caught and described with actionable notes.
    """
    statuses: dict[str, str] = {}

    # ── OpenAI ────────────────────────────────────────────────────────────────
    if OPENAI_API_KEY:
        try:
            r = requests.get(
                "https://api.openai.com/v1/models",
                timeout=8,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            )
            if r.status_code == 200:
                statuses["OpenAI"] = f"✅ OK — model: {OPENAI_MODEL}"
            elif r.status_code == 401:
                statuses["OpenAI"] = "❌ Invalid API key (401) — re-check OPENAI_API_KEY in Railway"
            elif r.status_code == 429:
                statuses["OpenAI"] = "⚠️ Rate limited (429) — quota exceeded"
            else:
                statuses["OpenAI"] = f"⚠️ HTTP {r.status_code} — unexpected response"
        except requests.Timeout:
            statuses["OpenAI"] = "⚠️ Timeout — check Railway outbound rules"
        except requests.ConnectionError:
            statuses["OpenAI"] = "❌ Unreachable — network or DNS error"
        except Exception as exc:
            statuses["OpenAI"] = f"❌ {type(exc).__name__}: {str(exc)[:60]}"
    else:
        statuses["OpenAI"] = "⬜ Not configured (OPENAI_API_KEY missing)"

    # ── OpenRouter ────────────────────────────────────────────────────────────
    if OPENROUTER_API_KEY:
        try:
            r = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                timeout=8,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                usage_label = data.get("label", "")
                statuses["OpenRouter"] = (
                    "✅ OK — key valid"
                    + (f" ({usage_label})" if usage_label else "")
                )
            elif r.status_code == 401:
                statuses["OpenRouter"] = "❌ Invalid API key (401) — re-check OPENROUTER_API_KEY in Railway"
            elif r.status_code == 402:
                statuses["OpenRouter"] = "⚠️ No credits (402) — top up at openrouter.ai/credits"
            elif r.status_code == 403:
                statuses["OpenRouter"] = "❌ API key forbidden (403) — check key permissions"
            elif r.status_code == 404:
                statuses["OpenRouter"] = (
                    "❌ Key not found (404) — OPENROUTER_API_KEY may be wrong or deleted. "
                    "Generate a new key at openrouter.ai/keys"
                )
            else:
                statuses["OpenRouter"] = f"⚠️ HTTP {r.status_code} — unexpected response from OpenRouter"
        except requests.Timeout:
            statuses["OpenRouter"] = "⚠️ Timeout — check Railway outbound rules"
        except requests.ConnectionError:
            statuses["OpenRouter"] = "❌ Unreachable — network or DNS error"
        except Exception as exc:
            statuses["OpenRouter"] = f"❌ {type(exc).__name__}: {str(exc)[:60]}"
    else:
        statuses["OpenRouter"] = "⬜ Not configured (OPENROUTER_API_KEY missing)"

    # ── Gemini ────────────────────────────────────────────────────────────────
    if GEMINI_API_KEY:
        try:
            r = requests.get(f"{_GEMINI_BASE}?key={GEMINI_API_KEY}", timeout=8)
            if r.status_code == 200:
                statuses["Gemini"] = "✅ OK"
            elif r.status_code == 429:
                statuses["Gemini"] = (
                    "⚠️ Quota exceeded (429) — free-tier limit reached. "
                    "Bot auto-falls back to HuggingFace."
                )
            elif r.status_code in (400, 403):
                statuses["Gemini"] = f"❌ Auth error ({r.status_code}) — check GEMINI_API_KEY"
            else:
                statuses["Gemini"] = f"⚠️ HTTP {r.status_code}"
        except requests.Timeout:
            statuses["Gemini"] = "⚠️ Timeout (8s)"
        except requests.ConnectionError:
            statuses["Gemini"] = "❌ Unreachable"
        except Exception as exc:
            statuses["Gemini"] = f"❌ {type(exc).__name__}: {str(exc)[:60]}"
    else:
        statuses["Gemini"] = "⬜ Not configured (GEMINI_API_KEY missing)"

    # ── HuggingFace ───────────────────────────────────────────────────────────
    if HUGGINGFACE_API_KEY:
        try:
            r = requests.get(
                f"https://api-inference.huggingface.co/models/{HF_CHAT_MODEL}",
                headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
                timeout=8,
            )
            if r.status_code == 200:
                statuses["HuggingFace"] = "✅ OK — model ready"
            elif r.status_code == 503:
                statuses["HuggingFace"] = "⏳ Model warming up (503) — available in ~20s"
            elif r.status_code == 401:
                statuses["HuggingFace"] = "❌ Invalid HF token (401)"
            else:
                statuses["HuggingFace"] = f"⚠️ HTTP {r.status_code}"
        except (requests.Timeout, requests.ConnectionError):
            statuses["HuggingFace"] = (
                "⚠️ Unreachable from this host — likely Railway network restriction. "
                "OpenRouter + Gemini handle all traffic automatically."
            )
        except Exception as exc:
            statuses["HuggingFace"] = f"❌ {type(exc).__name__}: {str(exc)[:60]}"
    else:
        statuses["HuggingFace"] = "⬜ Not configured (HUGGINGFACE_API_KEY missing)"

    # ── Supabase DB ───────────────────────────────────────────────────────────
    try:
        from services.database import count_users
        counts = count_users()
        statuses["DB (Supabase)"] = (
            f"✅ OK — {counts['total']} users | {counts['vip']} VIP | {counts['banned']} banned"
        )
    except Exception as exc:
        err = str(exc)
        if "404" in err:
            statuses["DB (Supabase)"] = (
                "❌ Table not found (404) — run supabase_schema.sql in the Supabase SQL Editor"
            )
        else:
            statuses["DB (Supabase)"] = f"❌ DB error: {err[:120]}"

    return statuses


# ── Gemini Vision — Image Analysis ────────────────────────────────────────────

def analyze_image_gemini(image_bytes: bytes, mime_type: str, question: str) -> str:
    """
    Analyze an image using Gemini Vision (gemini-1.5-flash).
    Returns the AI's textual description / answer.
    Synchronous — always call via run_in_executor from async handlers.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WEBP, etc.)
        mime_type:   MIME type string, e.g. "image/jpeg"
        question:    What to ask about the image; falls back to a generic description prompt.
    """
    import base64

    if not GEMINI_API_KEY:
        return (
            "⚠️ <b>Image analysis requires GEMINI_API_KEY.</b>\n\n"
            "Add it to your Railway environment variables."
        )

    vision_model = "gemini-1.5-flash"
    url = f"{_GEMINI_BASE}/{vision_model}:generateContent?key={GEMINI_API_KEY}"

    contents = [
        {
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode(),
                    }
                },
                {"text": question or "Describe this image in detail."},
            ]
        }
    ]

    try:
        resp = requests.post(
            url,
            json={
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.4},
            },
            timeout=(10, AI_TIMEOUT),
        )

        if resp.status_code == 400:
            log.warning("Gemini Vision 400: %s", resp.text[:200])
            return "⚠️ Could not analyse this image — it may be unsupported format or too large."
        if resp.status_code == 403:
            log.warning("Gemini Vision 403: API key restricted")
            return "⚠️ Gemini API key is invalid or restricted. Check GEMINI_API_KEY in Railway."
        if resp.status_code == 429:
            log.warning("Gemini Vision 429: quota exceeded")
            return "⚠️ Gemini quota exceeded. Try again in a moment."
        resp.raise_for_status()

        data       = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "⚠️ Gemini returned no analysis (response may have been filtered)."
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return "⚠️ Gemini returned an empty analysis."
        return (parts[0].get("text", "") or "").strip()

    except requests.Timeout:
        log.warning("Gemini Vision: request timed out")
        return "⚠️ Image analysis timed out. Please try again."
    except requests.ConnectionError as exc:
        log.warning("Gemini Vision: connection error — %s", exc)
        return "⚠️ Could not reach Gemini. Check Railway network connectivity."
    except Exception as exc:
        log.error("Gemini Vision unexpected error: %s", exc)
        return f"⚠️ Analysis failed: {str(exc)[:120]}"
