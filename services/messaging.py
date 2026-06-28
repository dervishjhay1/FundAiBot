"""
FundzAiBot — Centralised Message Routing

ARCHITECTURE RULE (FINAL — DO NOT BYPASS):
  Three environments. Three completely independent delivery paths.
  These functions MUST NEVER call each other automatically.

  ┌─────────────────────┬────────────────────────────────────────────┐
  │ Function            │ Destination                                │
  ├─────────────────────┼────────────────────────────────────────────┤
  │ send_channel_post() │ TELEGRAM_CHANNEL_ID only                   │
  │ send_group_message()│ TELEGRAM_GROUP_ID only                     │
  │ send_private_message│ user_id (private DM) only                  │
  └─────────────────────┴────────────────────────────────────────────┘

EVENT ROUTING MATRIX:
  • New member joins          → send_group_message()
  • Daily educational content → send_channel_post()
  • CEO / admin report        → send_private_message()
  • User command reply        → send_private_message()
  • Group discussion starter  → send_group_message()
  • Company announcement      → send_channel_post()

No function in this module may import or invoke any other routing
function in this module. Cross-environment message delivery is
ALWAYS an explicit, deliberate admin action only.
"""

from __future__ import annotations

import requests
from utils.logger import get_logger
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID

log = get_logger(__name__)

_API = "https://api.telegram.org/bot"
_TIMEOUT = (5, 15)

# ── Routing verification cache ─────────────────────────────────────────────────
# Populated once on startup by verify_routing(). Guards in send functions use it.
_routing_verified: bool = False
_channel_type: str = ""       # "channel", "group", "supergroup", "private", or ""
_group_type: str = ""         # "channel", "group", "supergroup", "private", or ""
_ids_are_identical: bool = False  # True = critical misconfiguration


# ── Private helpers ─────────────────────────────────────────────────────────────

def _send(chat_id: str | int, text: str, **kwargs) -> dict | None:
    """
    Low-level Telegram sendMessage. Returns parsed JSON on success, None on failure.
    Internal only — callers must use the three public routing functions below.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        log.debug("messaging._send: missing token or chat_id=%s", chat_id)
        return None
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", **kwargs}
        r = requests.post(
            f"{_API}{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result")
        log.warning("messaging._send chat_id=%s HTTP %d: %s", chat_id, r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("messaging._send chat_id=%s error: %s", chat_id, exc)
    return None


def _send_photo(chat_id: str | int, photo: str, caption: str = "", **kwargs) -> dict | None:
    """Low-level sendPhoto. Internal only."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return None
    try:
        payload = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML", **kwargs}
        r = requests.post(
            f"{_API}{TELEGRAM_BOT_TOKEN}/sendPhoto",
            json=payload,
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result")
        log.warning("messaging._send_photo chat_id=%s HTTP %d: %s", chat_id, r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("messaging._send_photo chat_id=%s error: %s", chat_id, exc)
    return None


def _get_chat_info(chat_id: str) -> dict | None:
    """Call Telegram getChat API. Returns the chat object or None on failure."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return None
    try:
        r = requests.get(
            f"{_API}{TELEGRAM_BOT_TOKEN}/getChat",
            params={"chat_id": chat_id},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("result")
        log.debug("_get_chat_info(%s) HTTP %d: %s", chat_id, r.status_code, r.text[:100])
    except Exception as exc:
        log.debug("_get_chat_info(%s) error: %s", chat_id, exc)
    return None


# ── Startup verification ────────────────────────────────────────────────────────

def verify_routing() -> dict:
    """
    Verify that CHANNEL_ID and GROUP_ID are correctly configured and point to
    the right Telegram chat types.  Call once from post_init() at startup.

    Checks performed:
      1. Both IDs are configured (non-empty).
      2. The two IDs are DIFFERENT strings.
      3. TELEGRAM_CHANNEL_ID resolves to a Telegram chat of type "channel".
      4. TELEGRAM_GROUP_ID resolves to a chat of type "supergroup" or "group".
      5. The channel does NOT have a linked_chat_id that matches GROUP_ID.
         (If it does, Telegram's Discussion Group feature is enabled and will
         automatically forward every channel post to the group — this is a
         Telegram platform feature, not a bot bug.  The admin must unlink the
         group in Channel Settings → Discussion to stop cross-posting.)

    Returns a dict with keys:
      ok            bool  — True only when all checks pass
      ids_different bool
      channel_type  str   — "channel" / "supergroup" / "group" / "" / "error"
      group_type    str   — "channel" / "supergroup" / "group" / "" / "error"
      discussion_linked bool — True = Telegram is forwarding channel posts to group
      issues        list[str]
      warnings      list[str]
    """
    global _routing_verified, _channel_type, _group_type, _ids_are_identical

    issues:   list[str] = []
    warnings: list[str] = []
    result = {
        "ok": False,
        "ids_different": False,
        "channel_configured": bool(TELEGRAM_CHANNEL_ID),
        "group_configured":   bool(TELEGRAM_GROUP_ID),
        "channel_type": "",
        "group_type": "",
        "channel_id": TELEGRAM_CHANNEL_ID or "(not set)",
        "group_id": TELEGRAM_GROUP_ID or "(not set)",
        "discussion_linked": False,
        "issues": issues,
        "warnings": warnings,
    }

    log.info("=" * 60)
    log.info("ROUTING VERIFICATION — starting")
    log.info("  TELEGRAM_CHANNEL_ID = %s", TELEGRAM_CHANNEL_ID or "(not set)")
    log.info("  TELEGRAM_GROUP_ID   = %s", TELEGRAM_GROUP_ID or "(not set)")

    # ── Check 1: IDs configured ──────────────────────────────────────────────
    if not TELEGRAM_CHANNEL_ID:
        issues.append("TELEGRAM_CHANNEL_ID is not set — channel posts disabled")
        log.error("  [FAIL] TELEGRAM_CHANNEL_ID not configured")
    if not TELEGRAM_GROUP_ID:
        issues.append("TELEGRAM_GROUP_ID is not set — group messages disabled")
        log.error("  [FAIL] TELEGRAM_GROUP_ID not configured")

    if not TELEGRAM_CHANNEL_ID or not TELEGRAM_GROUP_ID:
        log.info("ROUTING VERIFICATION — incomplete (missing IDs)")
        log.info("=" * 60)
        _routing_verified = True
        return result

    # ── Check 2: IDs must be different ───────────────────────────────────────
    if str(TELEGRAM_CHANNEL_ID).strip() == str(TELEGRAM_GROUP_ID).strip():
        _ids_are_identical = True
        issues.append(
            f"CRITICAL: TELEGRAM_CHANNEL_ID == TELEGRAM_GROUP_ID ({TELEGRAM_CHANNEL_ID}). "
            "Every message tagged 'channel' will also hit the group. "
            "Fix: set two different IDs in Railway environment variables."
        )
        log.error(
            "  [FAIL] IDs are IDENTICAL (%s) — every send hits the SAME chat!",
            TELEGRAM_CHANNEL_ID,
        )
    else:
        result["ids_different"] = True
        log.info("  [PASS] IDs are different")

    # ── Check 3: Verify channel chat type ────────────────────────────────────
    channel_info = _get_chat_info(TELEGRAM_CHANNEL_ID)
    if channel_info:
        ctype = channel_info.get("type", "")
        result["channel_type"] = ctype
        _channel_type = ctype
        if ctype == "channel":
            log.info("  [PASS] CHANNEL_ID=%s type=%s title=%s",
                     TELEGRAM_CHANNEL_ID, ctype, channel_info.get("title", "?"))
        else:
            issues.append(
                f"TELEGRAM_CHANNEL_ID ({TELEGRAM_CHANNEL_ID}) is type '{ctype}', "
                "expected 'channel'. It may be pointing to the wrong chat."
            )
            log.error(
                "  [FAIL] CHANNEL_ID=%s has type='%s' — expected 'channel'",
                TELEGRAM_CHANNEL_ID, ctype,
            )

        # Check 5: Discussion Group linking
        linked_id = channel_info.get("linked_chat_id")
        if linked_id:
            result["discussion_linked"] = True
            if str(linked_id) == str(TELEGRAM_GROUP_ID) or str(-linked_id) == str(TELEGRAM_GROUP_ID):
                issues.append(
                    "CRITICAL — TELEGRAM DISCUSSION GROUP IS ENABLED: "
                    f"Channel {TELEGRAM_CHANNEL_ID} is linked to group {TELEGRAM_GROUP_ID} "
                    "as its Discussion Group. Telegram's platform will automatically forward "
                    "EVERY channel post to the group's feed. "
                    "This is a Telegram platform feature — the bot code cannot prevent it. "
                    "FIX: Go to your channel in Telegram → Edit → Discussion → "
                    "tap the linked group → Remove (unlink). Then redeploy."
                )
                log.error(
                    "  [FAIL] *** DISCUSSION GROUP LINKED *** "
                    "Channel %s is linked to Group %s — Telegram forwards ALL channel "
                    "posts to the group automatically. Unlink via Channel Settings → Discussion.",
                    TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID,
                )
            else:
                warnings.append(
                    f"Channel {TELEGRAM_CHANNEL_ID} has a linked Discussion Group "
                    f"(linked_chat_id={linked_id}) that does not match TELEGRAM_GROUP_ID "
                    f"({TELEGRAM_GROUP_ID}). This is probably fine but worth checking."
                )
                log.warning(
                    "  [WARN] Channel has linked_chat_id=%s (not matching GROUP_ID=%s)",
                    linked_id, TELEGRAM_GROUP_ID,
                )
        else:
            log.info("  [PASS] Channel has no linked Discussion Group — no Telegram cross-posting")
    else:
        result["channel_type"] = "error"
        issues.append(
            f"Could not fetch info for TELEGRAM_CHANNEL_ID={TELEGRAM_CHANNEL_ID}. "
            "The bot may not be a member/admin of the channel, or the ID is wrong."
        )
        log.error("  [FAIL] Could not fetch channel info — bot not in channel or wrong ID")

    # ── Check 4: Verify group chat type ──────────────────────────────────────
    group_info = _get_chat_info(TELEGRAM_GROUP_ID)
    if group_info:
        gtype = group_info.get("type", "")
        result["group_type"] = gtype
        _group_type = gtype
        if gtype in ("group", "supergroup"):
            log.info("  [PASS] GROUP_ID=%s type=%s title=%s",
                     TELEGRAM_GROUP_ID, gtype, group_info.get("title", "?"))
        else:
            issues.append(
                f"TELEGRAM_GROUP_ID ({TELEGRAM_GROUP_ID}) is type '{gtype}', "
                "expected 'supergroup' or 'group'. It may be pointing to the wrong chat."
            )
            log.error(
                "  [FAIL] GROUP_ID=%s has type='%s' — expected 'supergroup' or 'group'",
                TELEGRAM_GROUP_ID, gtype,
            )
    else:
        result["group_type"] = "error"
        issues.append(
            f"Could not fetch info for TELEGRAM_GROUP_ID={TELEGRAM_GROUP_ID}. "
            "The bot may not be a member/admin of the group, or the ID is wrong."
        )
        log.error("  [FAIL] Could not fetch group info — bot not in group or wrong ID")

    # ── Final verdict ─────────────────────────────────────────────────────────
    result["ok"] = (
        len(issues) == 0
        and result["ids_different"]
        and result["channel_type"] == "channel"
        and result["group_type"] in ("group", "supergroup")
        and not result["discussion_linked"]
    )

    if result["ok"]:
        log.info("  [PASS] All routing checks passed — environments are correctly isolated")
    else:
        log.error("  ROUTING VERIFICATION FAILED — %d issue(s) found:", len(issues))
        for i, issue in enumerate(issues, 1):
            log.error("    %d. %s", i, issue)

    if warnings:
        for w in warnings:
            log.warning("  [WARN] %s", w)

    log.info("ROUTING VERIFICATION — complete  ok=%s", result["ok"])
    log.info("=" * 60)

    _routing_verified = True
    return result


# ── PUBLIC ROUTING FUNCTIONS ───────────────────────────────────────────────────
# Each function routes to exactly ONE destination. They NEVER call each other.

def send_channel_post(
    text: str,
    photo_url: str = "",
    reply_markup: dict | None = None,
) -> dict | None:
    """
    Publish a post to the OFFICIAL CHANNEL only.

    Use for:
      • Product announcements
      • Daily AI tips
      • Feature releases
      • Educational content
      • Company milestones
      • TestAudit scheduled content

    NEVER use for group messages or private DMs.
    NEVER call send_group_message() or send_private_message() from here.
    """
    if not TELEGRAM_CHANNEL_ID:
        log.debug("send_channel_post: TELEGRAM_CHANNEL_ID not configured")
        return None

    # Safety guard: refuse to send if both IDs are identical — this would
    # cause the "channel" post to land in the group (same chat).
    if TELEGRAM_GROUP_ID and str(TELEGRAM_CHANNEL_ID).strip() == str(TELEGRAM_GROUP_ID).strip():
        log.error(
            "send_channel_post ABORTED: TELEGRAM_CHANNEL_ID == TELEGRAM_GROUP_ID (%s). "
            "Fix your Railway env vars so the two IDs are different.",
            TELEGRAM_CHANNEL_ID,
        )
        return None

    kwargs = {}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    if photo_url:
        result = _send_photo(TELEGRAM_CHANNEL_ID, photo_url, caption=text, **kwargs)
    else:
        result = _send(TELEGRAM_CHANNEL_ID, text, **kwargs)

    if result:
        log.info("Channel post sent (msg_id=%s)", result.get("message_id"))
    return result


def send_group_message(
    text: str,
    reply_to_message_id: int | None = None,
    reply_markup: dict | None = None,
) -> dict | None:
    """
    Send a message to the COMMUNITY GROUP only.

    Use for:
      • Welcome messages for new members
      • Discussion starters when group is quiet
      • Moderation actions that need visibility
      • Community engagement questions

    NEVER use for channel content or private DMs.
    NEVER call send_channel_post() or send_private_message() from here.
    """
    if not TELEGRAM_GROUP_ID:
        log.debug("send_group_message: TELEGRAM_GROUP_ID not configured")
        return None

    # Safety guard: refuse to send if both IDs are identical — this would
    # cause the "group" message to land in the channel (same chat).
    if TELEGRAM_CHANNEL_ID and str(TELEGRAM_GROUP_ID).strip() == str(TELEGRAM_CHANNEL_ID).strip():
        log.error(
            "send_group_message ABORTED: TELEGRAM_GROUP_ID == TELEGRAM_CHANNEL_ID (%s). "
            "Fix your Railway env vars so the two IDs are different.",
            TELEGRAM_GROUP_ID,
        )
        return None

    kwargs = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    result = _send(TELEGRAM_GROUP_ID, text, **kwargs)
    if result:
        log.info("Group message sent (msg_id=%s)", result.get("message_id"))
    return result


def send_private_message(
    user_id: int,
    text: str,
    reply_markup: dict | None = None,
    photo_url: str = "",
) -> dict | None:
    """
    Send a private DM to a specific user.

    Use for:
      • CEO reports and alerts
      • User command replies
      • Customer success follow-ups
      • Admin notifications
      • Inactive user re-engagement

    NEVER use for group posts or channel content.
    NEVER call send_channel_post() or send_group_message() from here.
    """
    if not user_id:
        log.debug("send_private_message: user_id not provided")
        return None

    # Safety guard: refuse to DM the channel or group ID
    if TELEGRAM_CHANNEL_ID and str(user_id) == str(TELEGRAM_CHANNEL_ID):
        log.error(
            "send_private_message ABORTED: user_id=%s matches TELEGRAM_CHANNEL_ID — "
            "this would post to the channel instead of a private DM.",
            user_id,
        )
        return None
    if TELEGRAM_GROUP_ID and str(user_id) == str(TELEGRAM_GROUP_ID):
        log.error(
            "send_private_message ABORTED: user_id=%s matches TELEGRAM_GROUP_ID — "
            "this would post to the group instead of a private DM.",
            user_id,
        )
        return None

    kwargs = {}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    if photo_url:
        result = _send_photo(user_id, photo_url, caption=text, **kwargs)
    else:
        result = _send(user_id, text, **kwargs)

    if result:
        log.debug("Private message sent to user=%s (msg_id=%s)", user_id, result.get("message_id"))
    return result
