"""
FundAiBot — AI image generation service.
Uses HuggingFace Inference API (Stable Diffusion XL).
Handles cold-start 503s with automatic retry.
"""

import io
import time

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

_MAX_RETRIES     = 2
_COLD_START_WAIT = 20   # seconds to wait on 503 cold-start


def generate_image(
    prompt: str, style: str = "realistic", model: str = DEFAULT_IMAGE_MODEL
) -> io.BytesIO | None:
    """
    Generate an image. Returns a BytesIO buffer ready for Telegram send_photo, or None on failure.
    Retries once on HuggingFace 503 (model cold-start).
    """
    if not HUGGINGFACE_API_KEY:
        log.warning("No HUGGINGFACE_API_KEY — image generation skipped")
        return None

    prefix      = STYLE_PREFIXES.get(style, "")
    full_prompt = f"{prefix}{prompt}"

    log.info("Image gen — style=%s model=%s prompt=%s", style, model, prompt[:80])

    url     = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {
        "inputs": full_prompt,
        "parameters": {
            "negative_prompt":    NEGATIVE_PROMPT,
            "num_inference_steps": 30,
            "guidance_scale":      7.5,
            "width":  1024,
            "height": 1024,
        },
    }

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=IMAGE_TIMEOUT)

            # HuggingFace 503 = model is loading (cold start) — retry after wait
            if resp.status_code == 503:
                try:
                    estimated = resp.json().get("estimated_time", _COLD_START_WAIT)
                except Exception:
                    estimated = _COLD_START_WAIT
                wait = min(float(estimated), _COLD_START_WAIT)
                log.warning(
                    "HuggingFace image model loading — waiting %.0fs (attempt %d/%d)",
                    wait, attempt + 1, _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                log.warning("HuggingFace still loading after %d attempts — giving up", _MAX_RETRIES)
                return None

            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type:
                log.warning(
                    "Unexpected HuggingFace content-type: %s — body: %s",
                    content_type, resp.text[:200],
                )
                return None

            buf = io.BytesIO(resp.content)
            buf.name = "image.png"
            buf.seek(0)
            log.info("Image generated successfully (%d KB)", len(resp.content) // 1024)
            return buf

        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response else "?"
            body = (exc.response.text[:200] if exc.response else "") or ""
            log.warning("HuggingFace HTTP %s: %s | body: %s", code, exc, body)
            return None
        except requests.Timeout:
            log.warning(
                "Image generation timed out after %ds (attempt %d/%d)",
                IMAGE_TIMEOUT, attempt + 1, _MAX_RETRIES,
            )
            if attempt < _MAX_RETRIES - 1:
                continue
            return None
        except requests.ConnectionError as exc:
            log.error("HuggingFace connection error (network/DNS): %s", exc)
            return None
        except Exception as exc:
            log.error("Image generation unexpected error: %s", exc, exc_info=True)
            return None

    return None
