# HOTFIX REPORT — EOS 2.1.0
## Repeated "Service Interruption" Posts on Official Channel

**Severity:** CRITICAL  
**Status:** RESOLVED  
**Date:** 2026-07-05  
**Engineer:** TestAudit CI (Replit Lead Engineer)

---

## Root Cause

**File:** `services/ai_service.py` → `get_ai_response()`  
**File:** `services/channel_publisher.py` → `_generate_single_draft()`

When **all AI providers fail** simultaneously (OpenAI, OpenRouter, Gemini, HuggingFace all unavailable or misconfigured), `get_ai_response()` returns a friendly user-facing error string with a sentinel provider value:

```python
return (
    "I'm currently experiencing a service interruption and can't process that request right now. "
    "Please try again in a moment — the system is working to restore full capability.",
    "none",   # ← sentinel for total failure
)
```

In `channel_publisher.py`, `_generate_single_draft()` checked only for falsy response:

```python
if not response:   # ← BUG: error string is truthy, bypasses this check
    return "", 0.0
```

The error string then passed through every downstream gate:
- **`_length_check()`** → PASS (string is ~140 chars, >12 words, no unfilled placeholders)
- **`score_post_quality()`** → PASS (coherent English, scores above 0.35 threshold)
- **`_publish()`** → PUBLISHED to the official channel

Because the channel publisher loops every 28–65 minutes, this repeated every cycle for 24+ hours.

---

## Affected Files

| File | Role | Status |
|------|------|--------|
| `services/channel_publisher.py` | Primary publisher (asyncio, started in main.py) | **FIXED** |
| `services/channel_manager.py` | Secondary publisher (threading, not started in main.py) | **FIXED defensively** |
| `services/ai_service.py` | AI provider cascade | No change — sentinel pattern is correct |

---

## Why Previous Testing Failed to Detect It

The bug is triggered only when **all** AI providers fail simultaneously — a condition that doesn't occur in normal development or isolated unit testing. The sentinel (`provider == "none"`) was already used correctly in `enhance_prompt()` within the same file, but was not applied to the channel publishing path.

---

## Fix Applied

### `services/channel_publisher.py` — `_generate_single_draft()`

Added **provider sentinel check** as the first gate after `get_ai_response()` returns:

```python
if provider == "none":
    log.warning("Draft %d skipped — all AI providers unavailable")
    return "", 0.0
```

This is consistent with how `enhance_prompt()` in `ai_service.py` already handles this case correctly.

### `_CHANNEL_GUARD_PHRASES` blocklist (defense-in-depth)

Added a tuple of forbidden phrases checked at three layers:
1. `_generate_single_draft()` — rejects error responses before scoring
2. `_generate_best_post()` — final check before returning to caller
3. `_publish()` — absolute last gate before sending to Telegram

### `services/channel_manager.py` (defensive fix)

Added `_is_channel_safe()` function with the same guard phrase list. Applied to:
- `_try_ai_content()` — AI-generated content validation
- `_try_product_ai_content()` — product spotlight content validation
- `_post_to_channel()` — absolute last gate before Telegram send

---

## Fail-Safe Publishing Policy (Post-Fix)

When content generation fails:
1. `_generate_single_draft()` returns `("", 0.0)` — silently, no exception
2. `_generate_best_post()` falls back to local static templates
3. If local templates also fail (or are blocked): returns `None`
4. `run_channel_publisher()` logs "skipping this cycle" internally — **nothing is published**
5. Scheduler waits the normal 28–65 min interval and tries again

The public channel receives either **valid professional content** or **nothing**. Never an error message.

---

## Risk Assessment

**Risk level:** Low  
The fix only adds early-exit conditions. No existing logic is removed. Static fallback templates remain intact and are the last resort before skipping a cycle entirely.

---

## Verification Checklist

- ✅ Provider sentinel check added to `_generate_single_draft()`
- ✅ Channel guard phrases block error text at 3 independent layers
- ✅ `_publish()` has absolute final safety gate
- ✅ Static fallback templates untouched
- ✅ CEO Office unaffected (separate code path)
- ✅ Community Management unaffected (separate code path)
- ✅ Scheduler loop continues normally after skipped cycles
- ✅ All errors logged internally only — never exposed publicly
