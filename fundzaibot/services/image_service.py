"""
FundAiBot — AI image generation service.

Provider priority (Railway-compatible):
  1. Pollinations.ai  — free, no API key, GET request, works from any network
  2. HuggingFace      — fallback (may be blocked on Railway; used when available)

All functions are SYNCHRONOUS — call via run_in_executor from async handlers.
"""

import io
import time
from urllib.parse import quote

import requests

from config.settings import HUGGINGFACE_API_KEY, DEFAULT_IMAGE_MODEL, IMAGE_TIMEOUT
from utils.logger import get_logger

log = get_logger(__name__)

STYLE_PREFIXES: dict[str, str] = {
    "realistic":  "photorealistic, 8k, sharp focus, highly detailed, professional photography, ",
    "artistic":   "oil painting, impressionist, vibrant colors, artistic masterpiece, brush strokes, ",
    "fantasy":    "epic fantasy art, magical atmosphere, dramatic lighting, intricate details, concept art, ",
    "cyberpunk":  "cyberpunk city, neon lights, rain, futuristic, high-tech, dark atmosphere, blade runner, ",
    "classical":  "classical oil painting, renaissance style, museum quality, old masters, detailed, ",
    "anime":      "anime style, Studio Ghibli inspired, cel-shaded, vibrant colors, Japanese animation, ",
}

NEGATIVE_PROMPT = (
    "blurry, low quality, bad anatomy, deformed, ugly, watermark, text, "
    "signature, nsfw, nude, explicit, violence, gore"
)

_HF_MAX_RETRIES     = 2
_HF_COLD_START_WAIT = 20


# ── Provider 1: Pollinations.ai ───────────────────────────────────────────────

def _pollinations(full_prompt: str) -> io.BytesIO | None:
    """
    Generate an image via Pollinations.ai.
    - No API key required
    - Plain GET request → JPEG bytes
    - Works from Railway and any other network
    - Model: FLUX (best quality free model as of 2025)
    """
    url = (
        f"https://image.pollinations.ai/prompt/{quote(full_prompt)}"
        "?width=1024&height=1024&model=flux&nologo=true&enhance=false"
    )
    try:
        resp = requests.get(url, timeout=IMAGE_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type:
            log.warning(
                "Pollinations unexpected content-type: %s — body: %s",
                content_type, resp.text[:120],
            )
            return None

        buf = io.BytesIO(resp.content)
        buf.name = "image.jpg"
        buf.seek(0)
        log.info("Pollinations image OK (%d KB)", len(resp.content) // 1024)
        return buf

    except requests.Timeout:
        log.warning("Pollinations timed out after %ds", IMAGE_TIMEOUT)
        return None
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response else "?"
        log.warning("Pollinations HTTP %s", code)
        return None
    except requests.ConnectionError as exc:
        log.warning("Pollinations connection error: %s", str(exc)[:80])
        return None
    except Exception as exc:
        log.error("Pollinations unexpected error: %s", exc)
        return None


# ── Provider 2: HuggingFace (fallback) ───────────────────────────────────────

def _huggingface_image(full_prompt: str, model: str) -> io.BytesIO | None:
    """
    Generate an image via HuggingFace Inference API (Stable Diffusion XL).
    May be unreachable from Railway's network — used as fallback only.
    """
    if not HUGGINGFACE_API_KEY:
        log.debug("HuggingFace image skipped — no HUGGINGFACE_API_KEY")
        return None

    url     = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {
        "inputs": full_prompt,
        "parameters": {
            "negative_prompt":     NEGATIVE_PROMPT,
            "num_inference_steps": 30,
            "guidance_scale":      7.5,
            "width":  1024,
            "height": 1024,
        },
    }

    for attempt in range(_HF_MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=IMAGE_TIMEOUT)

            if resp.status_code == 503:
                try:
                    wait = min(float(resp.json().get("estimated_time", _HF_COLD_START_WAIT)), _HF_COLD_START_WAIT)
                except Exception:
                    wait = _HF_COLD_START_WAIT
                log.warning("HuggingFace image model loading — waiting %.0fs (attempt %d/%d)", wait, attempt + 1, _HF_MAX_RETRIES)
                if attempt < _HF_MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                return None

            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type:
                log.warning("HuggingFace unexpected content-type: %s — body: %s", content_type, resp.text[:120])
                return None

            buf = io.BytesIO(resp.content)
            buf.name = "image.png"
            buf.seek(0)
            log.info("HuggingFace image OK (%d KB)", len(resp.content) // 1024)
            return buf

        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response else "?"
            log.warning("HuggingFace HTTP %s (attempt %d/%d)", code, attempt + 1, _HF_MAX_RETRIES)
            return None
        except requests.Timeout:
            log.warning("HuggingFace timed out (attempt %d/%d)", attempt + 1, _HF_MAX_RETRIES)
            if attempt < _HF_MAX_RETRIES - 1:
                continue
            return None
        except requests.ConnectionError as exc:
            log.warning("HuggingFace connection error (likely Railway network restriction): %s", str(exc)[:80])
            return None
        except Exception as exc:
            log.error("HuggingFace unexpected error: %s", exc, exc_info=True)
            return None

    return None


# ── Public API ────────────────────────────────────────────────────────────────

def generate_image(
    prompt: str,
    style: str = "realistic",
    model: str = DEFAULT_IMAGE_MODEL,
) -> io.BytesIO | None:
    """
    Generate an image. Returns BytesIO (ready for Telegram send_photo) or None.

    Provider chain:
      1. Pollinations.ai  — free, Railway-compatible, no API key
      2. HuggingFace      — fallback (may be blocked on Railway)

    Synchronous — always call via run_in_executor() from async code.
    """
    prefix      = STYLE_PREFIXES.get(style, "")
    full_prompt = f"{prefix}{prompt}"

    log.info("Image gen start — style=%s prompt=%s", style, prompt[:80])

    # ── 1. Pollinations.ai (primary, works on Railway) ──────────────────────
    result = _pollinations(full_prompt)
    if result:
        return result

    log.warning("Pollinations failed — trying HuggingFace fallback")

    # ── 2. HuggingFace (may be blocked on Railway) ───────────────────────────
    result = _huggingface_image(full_prompt, model)
    if result:
        return result

    log.error("All image providers failed for prompt: %s", prompt[:80])
    return None
