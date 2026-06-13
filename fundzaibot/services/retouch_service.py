"""
FundzAiBot — AI image retouching service.
Downloads a Telegram photo and enhances it via HuggingFace Inference API.

Supported retouch modes:
  enhance    — sharpen, improve quality, professional look
  beautify   — skin smoothing, portrait enhancement
  upscale    — super-resolution / detail boost
  artistic   — apply artistic paint style
  brighten   — fix exposure, vivid colours

Uses timbrooks/instruct-pix2pix (instruction-guided img2img).
Falls back to stabilityai/stable-diffusion-xl-refiner-1.0 on cold-start.
"""

import base64
import io
import time

import requests

from config.settings import HUGGINGFACE_API_KEY, IMAGE_TIMEOUT
from utils.logger import get_logger

log = get_logger(__name__)

# ── Model choices ─────────────────────────────────────────────────────────────

PRIMARY_MODEL   = "timbrooks/instruct-pix2pix"
FALLBACK_MODEL  = "stabilityai/stable-diffusion-xl-refiner-1.0"
UPSCALE_MODEL   = "goofyai/3d_render_style_xl"

HF_BASE = "https://api-inference.huggingface.co/models"

# ── Retouch instructions (sent as prompt to pix2pix) ─────────────────────────

INSTRUCTIONS: dict[str, str] = {
    "enhance":  (
        "enhance this photo, improve quality, sharpen details, "
        "professional photography, crisp and clear, high resolution"
    ),
    "beautify": (
        "beautify this portrait, smooth skin, enhance features, "
        "professional headshot lighting, natural look"
    ),
    "upscale":  (
        "upscale and sharpen this image, 4k quality, fine details, "
        "crisp edges, studio quality"
    ),
    "artistic": (
        "convert this photo into an oil painting, impressionist style, "
        "vibrant colours, artistic masterpiece"
    ),
    "brighten": (
        "brighten this photo, fix exposure, vivid colours, "
        "high contrast, professional colour grading"
    ),
}

NEGATIVE = "blurry, low quality, deformed, ugly, noise, artifacts, watermark"

_COLD_WAIT = 20


def retouch_image(image_bytes: bytes, mode: str = "enhance") -> io.BytesIO | None:
    """
    Send an image to HuggingFace for instruction-guided retouching.
    Returns a BytesIO buffer (PNG/JPEG) ready for Telegram send_photo, or None.
    """
    if not HUGGINGFACE_API_KEY:
        log.warning("No HUGGINGFACE_API_KEY — retouch skipped")
        return None

    instruction = INSTRUCTIONS.get(mode, INSTRUCTIONS["enhance"])
    b64_img     = base64.b64encode(image_bytes).decode("utf-8")

    headers = {
        "Authorization":  f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type":   "application/json",
    }
    payload = {
        "inputs":     b64_img,
        "parameters": {
            "prompt":          instruction,
            "negative_prompt": NEGATIVE,
            "num_inference_steps": 20,
            "image_guidance_scale": 1.5,
            "guidance_scale":  7.0,
        },
    }

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        url = f"{HF_BASE}/{model}"
        log.info("Retouch — mode=%s model=%s", mode, model)

        for attempt in range(2):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=IMAGE_TIMEOUT)

                if resp.status_code == 503:
                    try:
                        wait = float(resp.json().get("estimated_time", _COLD_WAIT))
                    except Exception:
                        wait = _COLD_WAIT
                    wait = min(wait, _COLD_WAIT)
                    log.warning("Model loading — waiting %.0fs (attempt %d)", wait, attempt + 1)
                    if attempt == 0:
                        time.sleep(wait)
                        continue
                    break

                if resp.status_code == 400:
                    log.warning("Model %s rejected payload (400) — trying fallback", model)
                    break

                resp.raise_for_status()

                ct = resp.headers.get("content-type", "")
                if "image" not in ct:
                    log.warning("Unexpected content-type from %s: %s", model, ct)
                    break

                buf      = io.BytesIO(resp.content)
                buf.name = "retouched.png"
                buf.seek(0)
                log.info("Retouch success — model=%s size=%dKB", model, len(resp.content) // 1024)
                return buf

            except requests.Timeout:
                log.warning("Retouch timed out on model=%s attempt=%d", model, attempt)
                if attempt == 0:
                    continue
            except requests.ConnectionError as exc:
                log.error("Retouch connection error: %s", exc)
                break
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response else "?"
                log.warning("Retouch HTTP %s from model=%s", code, model)
                break
            except Exception as exc:
                log.error("Retouch unexpected error: %s", exc, exc_info=True)
                break

    log.warning("All retouch attempts failed for mode=%s", mode)
    return None
