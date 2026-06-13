"""
FundzAiBot — Live service test script.
Run: python test_services.py
Tests: Telegram, Supabase, OpenRouter, Gemini, HuggingFace (chat + image)
"""

import os, sys, json, time
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import requests

TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_ID        = os.getenv("ADMIN_USER_ID", "")
SUPABASE_URL         = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY  = os.getenv("HUGGINGFACE_API_KEY", "")

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⚠️  SKIP"

results = {}

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def result(name, ok, detail=""):
    tag = PASS if ok else FAIL
    results[name] = ok
    print(f"  {tag}  {name}" + (f"  — {detail}" if detail else ""))

# ── 1. Config presence ─────────────────────────────────────────────────────────
section("1. Environment Variables")
for k, v in [
    ("TELEGRAM_BOT_TOKEN",   TELEGRAM_BOT_TOKEN),
    ("ADMIN_USER_ID",        ADMIN_USER_ID),
    ("SUPABASE_URL",         SUPABASE_URL),
    ("SUPABASE_SERVICE_KEY", SUPABASE_SERVICE_KEY),
    ("OPENROUTER_API_KEY",   OPENROUTER_API_KEY),
    ("GEMINI_API_KEY",       GEMINI_API_KEY),
    ("HUGGINGFACE_API_KEY",  HUGGINGFACE_API_KEY),
]:
    result(k, bool(v), f"{len(v)} chars" if v else "MISSING")

# ── 2. Telegram ────────────────────────────────────────────────────────────────
section("2. Telegram Bot API")
try:
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
        timeout=10
    )
    d = r.json()
    if d.get("ok"):
        result("getMe", True, f"@{d['result']['username']} id={d['result']['id']}")
    else:
        result("getMe", False, d.get("description",""))
except Exception as e:
    result("getMe", False, str(e))

try:
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo",
        timeout=10
    )
    d = r.json()
    wh_url = d.get("result",{}).get("url","")
    last_err = d.get("result",{}).get("last_error_message","none")
    result("webhook", True, f"url='{wh_url or 'none (polling)'}' last_error={last_err}")
except Exception as e:
    result("webhook", False, str(e))

# ── 3. Supabase ────────────────────────────────────────────────────────────────
section("3. Supabase Database")
SUPA_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}

# Test table existence (SELECT 1 row from users)
for table in ["users", "user_credits", "conversations", "image_history", "referrals", "error_logs"]:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}?limit=1",
            headers={**SUPA_HEADERS, "Accept": "application/json"},
            timeout=10
        )
        if r.status_code in (200, 206):
            rows = r.json() if r.content else []
            result(f"table:{table}", True, f"{len(rows)} rows returned")
        elif r.status_code == 404:
            result(f"table:{table}", False, "table not found — run supabase_schema.sql")
        else:
            result(f"table:{table}", False, f"HTTP {r.status_code}: {r.text[:80]}")
    except Exception as e:
        result(f"table:{table}", False, str(e))

# Test RPC functions
for fn in ["increment_chat", "increment_image"]:
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/{fn}",
            headers=SUPA_HEADERS,
            json={"uid": 0},  # uid=0 won't match any real user — safe test
            timeout=10
        )
        # 200 or 204 = function exists; 404 = not created yet
        if r.status_code in (200, 204):
            result(f"rpc:{fn}", True, "function exists")
        elif r.status_code == 404:
            result(f"rpc:{fn}", False, "not found — paste supabase_schema.sql in Supabase SQL Editor")
        else:
            result(f"rpc:{fn}", False, f"HTTP {r.status_code}: {r.text[:80]}")
    except Exception as e:
        result(f"rpc:{fn}", False, str(e))

# ── 4. OpenRouter (GPT-3.5 Turbo) ─────────────────────────────────────────────
section("4. OpenRouter AI")
if not OPENROUTER_API_KEY:
    print(f"  {SKIP}  OpenRouter — no key")
    results["openrouter"] = None
else:
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 5,
            },
            timeout=20,
        )
        d = r.json()
        if r.status_code == 200 and d.get("choices"):
            reply = d["choices"][0]["message"]["content"].strip()
            result("openrouter:gpt-3.5-turbo", True, f"reply='{reply}'")
        elif r.status_code == 402:
            result("openrouter:gpt-3.5-turbo", False, "402 — no credits/balance on OpenRouter account")
        elif r.status_code == 401:
            result("openrouter:gpt-3.5-turbo", False, "401 — invalid API key")
        else:
            err = d.get("error", {}).get("message", r.text[:100])
            result("openrouter:gpt-3.5-turbo", False, f"HTTP {r.status_code}: {err}")
    except Exception as e:
        result("openrouter:gpt-3.5-turbo", False, str(e))

# ── 5. Gemini ──────────────────────────────────────────────────────────────────
section("5. Google Gemini")
if not GEMINI_API_KEY:
    print(f"  {SKIP}  Gemini — no key")
    results["gemini"] = None
else:
    for model in ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-pro"]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                json={"contents": [{"role": "user", "parts": [{"text": "Reply with exactly: OK"}]}]},
                timeout=20,
            )
            d = r.json()
            if r.status_code == 200:
                try:
                    reply = d["candidates"][0]["content"]["parts"][0]["text"].strip()
                    result(f"gemini:{model}", True, f"reply='{reply[:30]}'")
                except Exception:
                    result(f"gemini:{model}", True, "response received (no text extracted)")
                break  # stop at first working model
            elif r.status_code == 404:
                result(f"gemini:{model}", False, "model not found")
            elif r.status_code == 400:
                err = d.get("error", {}).get("message", "bad request")
                result(f"gemini:{model}", False, f"400: {err[:80]}")
            elif r.status_code == 403:
                result(f"gemini:{model}", False, "403 — invalid API key or quota exceeded")
            else:
                result(f"gemini:{model}", False, f"HTTP {r.status_code}: {str(d)[:80]}")
        except Exception as e:
            result(f"gemini:{model}", False, str(e))

# ── 6. HuggingFace — Chat ─────────────────────────────────────────────────────
section("6. HuggingFace — Chat (Mistral-7B)")
if not HUGGINGFACE_API_KEY:
    print(f"  {SKIP}  HuggingFace — no key")
    results["hf_chat"] = None
else:
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2",
            headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
            json={"inputs": "<s>[INST] Reply with exactly: OK [/INST]", "parameters": {"max_new_tokens": 10}},
            timeout=30,
        )
        if r.status_code == 200:
            d = r.json()
            reply = (d[0].get("generated_text","") if isinstance(d, list) else str(d))[:60]
            result("hf:mistral-7b-chat", True, f"reply='{reply}'")
        elif r.status_code == 503:
            result("hf:mistral-7b-chat", False, "503 — model loading (cold start) — retry in 60s")
        elif r.status_code == 401:
            result("hf:mistral-7b-chat", False, "401 — invalid HuggingFace token")
        else:
            result("hf:mistral-7b-chat", False, f"HTTP {r.status_code}: {r.text[:80]}")
    except Exception as e:
        result("hf:mistral-7b-chat", False, str(e))

# ── 7. HuggingFace — Image (SDXL) ─────────────────────────────────────────────
section("7. HuggingFace — Image (Stable Diffusion XL)")
if not HUGGINGFACE_API_KEY:
    print(f"  {SKIP}  HuggingFace — no key")
    results["hf_image"] = None
else:
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0",
            headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
            json={"inputs": "a red circle", "parameters": {"num_inference_steps": 1}},
            timeout=60,
        )
        ct = r.headers.get("content-type","")
        if r.status_code == 200 and "image" in ct:
            result("hf:sdxl-image", True, f"{len(r.content)//1024} KB image returned")
        elif r.status_code == 503:
            result("hf:sdxl-image", False, "503 — model loading (cold start) — try /image in 60s")
        elif r.status_code == 401:
            result("hf:sdxl-image", False, "401 — invalid HuggingFace token")
        else:
            result("hf:sdxl-image", False, f"HTTP {r.status_code} ct={ct}: {r.text[:80]}")
    except Exception as e:
        result("hf:sdxl-image", False, str(e))

# ── Summary ────────────────────────────────────────────────────────────────────
section("SUMMARY")
passed  = sum(1 for v in results.values() if v is True)
failed  = sum(1 for v in results.values() if v is False)
skipped = sum(1 for v in results.values() if v is None)
total   = len(results)
print(f"  Total: {total}  |  ✅ {passed} passed  |  ❌ {failed} failed  |  ⚠️  {skipped} skipped")
if failed:
    print("\n  Failed checks:")
    for k, v in results.items():
        if v is False:
            print(f"    ❌ {k}")
else:
    print("\n  🎉 All checks passed!")
print()
