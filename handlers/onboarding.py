"""
FundzAiBot — Onboarding handler.
Manages the new-user onboarding flow: welcome popup, channel/group join,
membership verification, reward granting, and admin controls.
"""

import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config.settings import (
    is_admin,
    TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_URL, TELEGRAM_CHANNEL_NAME,
    TELEGRAM_GROUP_ID,   TELEGRAM_GROUP_URL,   TELEGRAM_GROUP_NAME,
    ONBOARDING_CHANNEL_REWARD_CHAT, ONBOARDING_CHANNEL_REWARD_IMAGE,
    ONBOARDING_GROUP_REWARD_CHAT,   ONBOARDING_GROUP_REWARD_IMAGE,
    ONBOARDING_REQUIRED,
    FREE_DAILY_CHAT, FREE_DAILY_IMAGE,
)
from services.onboarding import (
    get_onboarding, init_onboarding, mark_onboarding_complete,
    mark_channel_joined, mark_group_joined,
    grant_channel_reward, grant_group_reward,
    get_onboarding_stats,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Source labels used in referral-aware messaging ────────────────────────────
_SOURCE_LABELS = {
    "channel":  "channel",
    "group":    "community group",
    "referral": "a friend's referral link",
    "bot":      "the bot",
    "direct":   "direct",
}


def _source_from_args(args: list[str]) -> str:
    """Determine referral_source from /start deep-link args."""
    if not args:
        return "direct"
    code = args[0]
    if code.startswith("CHAN"):
        return "channel"
    if code.startswith("GRP"):
        return "group"
    if code.startswith("REF"):
        return "referral"
    return "bot"


def _onboarding_text(source: str, first_name: str) -> str:
    """
    Build the onboarding welcome message, adapted to the user's referral source.
    """
    channel_has  = bool(TELEGRAM_CHANNEL_ID)
    group_has    = bool(TELEGRAM_GROUP_ID)

    # Base greeting
    greet = f"👋 <b>Welcome to FundzAiBot, {first_name}!</b>\n\n"

    # Source-aware intro
    if source == "channel":
        intro = (
            "You found us through our channel — great!\n\n"
            "FundzAiBot is your intelligent AI assistant powered by GPT-4, Gemini & Stable Diffusion.\n\n"
            "We also have a vibrant <b>community group</b> where members share tips, get support, and stay updated. 👥"
        )
    elif source == "group":
        intro = (
            "You found us through our community group — welcome!\n\n"
            "FundzAiBot gives you <b>AI chat</b>, <b>image generation</b>, and much more.\n\n"
            "Don't forget to follow our <b>official channel</b> for announcements and updates. 📢"
        )
    elif source == "referral":
        intro = (
            "A friend sent you here — smart choice! 🎁\n\n"
            "FundzAiBot is your intelligent AI assistant powered by GPT-4, Gemini & Stable Diffusion.\n\n"
            "Join our <b>channel</b> and <b>community group</b> to unlock bonus credits and stay connected."
        )
    else:
        intro = (
            "Your intelligent AI assistant — powered by GPT-4, Gemini &amp; Stable Diffusion.\n\n"
            "Before you dive in, join our official community to unlock <b>bonus credits</b> and stay updated!"
        )

    # Rewards block
    rewards = []
    if channel_has:
        rewards.append(
            f"📢 <b>Join Channel</b> → +{ONBOARDING_CHANNEL_REWARD_CHAT} chat credits "
            f"& +{ONBOARDING_CHANNEL_REWARD_IMAGE} image credit"
        )
    if group_has:
        rewards.append(
            f"👥 <b>Join Group</b> → +{ONBOARDING_GROUP_REWARD_CHAT} chat credits "
            f"& +{ONBOARDING_GROUP_REWARD_IMAGE} image credit"
        )

    reward_block = ""
    if rewards:
        reward_block = "\n\n<b>🎁 Join Rewards:</b>\n" + "\n".join(f"  {r}" for r in rewards)

    skip_note = ""
    if not ONBOARDING_REQUIRED:
        skip_note = "\n\n<i>You can skip and use the bot with your {chat} free daily chats &amp; {image} images.</i>".format(
            chat=FREE_DAILY_CHAT, image=FREE_DAILY_IMAGE
        )

    return greet + intro + reward_block + skip_note


def _onboarding_keyboard(source: str, row: dict | None) -> InlineKeyboardMarkup:
    """Build the onboarding inline keyboard based on current join state."""
    channel_has   = bool(TELEGRAM_CHANNEL_ID)
    group_has     = bool(TELEGRAM_GROUP_ID)
    ch_joined     = (row or {}).get("channel_joined", False)
    grp_joined    = (row or {}).get("group_joined", False)

    buttons = []

    # Channel button
    if channel_has:
        ch_label = f"✅ {TELEGRAM_CHANNEL_NAME}" if ch_joined else f"📢 Join {TELEGRAM_CHANNEL_NAME}"
        buttons.append([InlineKeyboardButton(ch_label, url=TELEGRAM_CHANNEL_URL)])

    # Group button
    if group_has:
        grp_label = f"✅ {TELEGRAM_GROUP_NAME}" if grp_joined else f"👥 Join {TELEGRAM_GROUP_NAME}"
        buttons.append([InlineKeyboardButton(grp_label, url=TELEGRAM_GROUP_URL)])

    # If at least one community exists, show verify button
    if channel_has or group_has:
        buttons.append([
            InlineKeyboardButton("✅ I've Joined — Verify & Claim Rewards", callback_data="onboarding:verify")
        ])

    # Continue button (always visible but labelled differently)
    if ONBOARDING_REQUIRED and (channel_has or group_has):
        if ch_joined or grp_joined:
            buttons.append([InlineKeyboardButton("🚀 Continue to Bot »", callback_data="onboarding:continue")])
    else:
        # Skip / Continue always available when not required
        continue_label = "🚀 Continue to Bot »" if (ch_joined or grp_joined or not (channel_has or group_has)) else "⏩ Skip for Now"
        buttons.append([InlineKeyboardButton(continue_label, callback_data="onboarding:continue")])

    return InlineKeyboardMarkup(buttons)


async def show_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str = "direct") -> None:
    """
    Send the onboarding popup to the user.
    Called from start_handler for new/incomplete users.
    """
    user = update.effective_user
    if not user:
        return

    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, get_onboarding, user.id)
    text = _onboarding_text(source, user.first_name or "friend")
    kbd  = _onboarding_keyboard(source, row)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kbd)
    log.info("Onboarding shown to user=%s source=%s", user.id, source)


async def handle_onboarding_verify(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Callback: user tapped 'I've Joined'.
    Verify membership via Telegram API, grant rewards, update keyboard.
    """
    user = query.from_user
    loop = asyncio.get_running_loop()

    channel_has = bool(TELEGRAM_CHANNEL_ID)
    group_has   = bool(TELEGRAM_GROUP_ID)

    ch_ok  = False
    grp_ok = False
    rewards_text = []

    # ── Check channel membership ──────────────────────────────────────────────
    if channel_has:
        try:
            member = await context.bot.get_chat_member(
                chat_id=TELEGRAM_CHANNEL_ID, user_id=user.id
            )
            ch_ok = member.status in ("creator", "administrator", "member", "restricted")
        except TelegramError as exc:
            log.warning("Channel membership check failed for user %s: %s", user.id, exc)
            # Graceful degradation: if bot lacks permission, assume joined
            ch_ok = True

        if ch_ok:
            await loop.run_in_executor(None, mark_channel_joined, user.id)
            granted = await loop.run_in_executor(None, grant_channel_reward, user.id)
            if granted:
                rewards_text.append(
                    f"📢 Channel: +{ONBOARDING_CHANNEL_REWARD_CHAT} chat & +{ONBOARDING_CHANNEL_REWARD_IMAGE} image credit"
                )

    # ── Check group membership ────────────────────────────────────────────────
    if group_has:
        try:
            member = await context.bot.get_chat_member(
                chat_id=TELEGRAM_GROUP_ID, user_id=user.id
            )
            grp_ok = member.status in ("creator", "administrator", "member", "restricted")
        except TelegramError as exc:
            log.warning("Group membership check failed for user %s: %s", user.id, exc)
            grp_ok = True

        if grp_ok:
            await loop.run_in_executor(None, mark_group_joined, user.id)
            granted = await loop.run_in_executor(None, grant_group_reward, user.id)
            if granted:
                rewards_text.append(
                    f"👥 Group: +{ONBOARDING_GROUP_REWARD_CHAT} chat & +{ONBOARDING_GROUP_REWARD_IMAGE} image credit"
                )

    # ── Refresh row and rebuild keyboard ──────────────────────────────────────
    row = await loop.run_in_executor(None, get_onboarding, user.id)
    row = row or {}

    # Determine source from stored row
    source = row.get("referral_source", "direct")
    kbd = _onboarding_keyboard(source, row)

    # ── Build response message ────────────────────────────────────────────────
    joined_any = ch_ok or grp_ok

    if not joined_any and (channel_has or group_has):
        await query.answer(
            "⚠️ Hmm, we couldn't verify your membership yet.\n"
            "Please join using the buttons above, then try again!",
            show_alert=True,
        )
        return

    if rewards_text:
        reward_block = "\n".join(f"  ✅ {r}" for r in rewards_text)
        await query.answer("🎉 Rewards granted!", show_alert=False)
        try:
            await query.edit_message_text(
                f"🎉 <b>Welcome to the community!</b>\n\n"
                f"<b>Rewards claimed:</b>\n{reward_block}\n\n"
                f"Credits have been added to your account.\n"
                f"Tap <b>Continue</b> to start using FundzAiBot! 👇",
                parse_mode="HTML",
                reply_markup=kbd,
            )
        except Exception:
            pass
    else:
        # Already rewarded or no new rewards
        await query.answer("Already verified! Tap Continue to start.", show_alert=False)
        try:
            await query.edit_message_reply_markup(reply_markup=kbd)
        except Exception:
            pass


async def handle_onboarding_continue(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Callback: user tapped 'Continue to Bot' / 'Skip'.
    Mark onboarding complete and trigger the normal start flow.
    """
    user = query.from_user
    loop = asyncio.get_running_loop()

    # If required mode is on, check at least one community joined
    if ONBOARDING_REQUIRED and (TELEGRAM_CHANNEL_ID or TELEGRAM_GROUP_ID):
        row = await loop.run_in_executor(None, get_onboarding, user.id)
        row = row or {}
        if not row.get("channel_joined") and not row.get("group_joined"):
            await query.answer(
                "⚠️ Please join our channel or group first to continue!",
                show_alert=True,
            )
            return

    await loop.run_in_executor(None, mark_onboarding_complete, user.id)
    await query.answer("Welcome to FundzAiBot! 🚀")

    # Show the normal start welcome
    from config.settings import FREE_DAILY_CHAT, FREE_DAILY_IMAGE
    from utils.keyboards import main_menu
    from services.database import get_or_create_user, set_system_prompt
    from services.database import get_active_announcement
    from services.language import get_string, get_user_language

    db_user = await loop.run_in_executor(
        None, lambda: get_or_create_user(user.id, first_name=user.first_name or "", username=user.username or "")
    )
    style = (db_user or {}).get("ai_style", "default")
    lang  = get_user_language(db_user, user.id)
    await loop.run_in_executor(None, set_system_prompt, user.id, style)

    welcome = get_string(lang, "welcome_new", chat=FREE_DAILY_CHAT, image=FREE_DAILY_IMAGE)

    try:
        await query.edit_message_text(welcome, parse_mode="HTML", reply_markup=main_menu())
    except Exception:
        try:
            await context.bot.send_message(user.id, welcome, parse_mode="HTML", reply_markup=main_menu())
        except Exception:
            pass

    # Show active announcement if any
    try:
        ann = await loop.run_in_executor(None, get_active_announcement)
        if ann and ann.get("message"):
            await context.bot.send_message(
                user.id, f"📢 {ann['message']}", parse_mode="HTML"
            )
    except Exception as exc:
        log.debug("Announcement after onboarding skipped: %s", exc)

    log.info("Onboarding completed for user=%s", user.id)


# ── Admin controls ────────────────────────────────────────────────────────────

async def admin_onboarding_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /admin_onboarding — Show onboarding stats and settings for admins.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, get_onboarding_stats)

    channel_cfg = (
        f"  📢 Channel: <code>{TELEGRAM_CHANNEL_ID or 'Not set'}</code>\n"
        f"     URL: {TELEGRAM_CHANNEL_URL}\n"
        f"     Reward: +{ONBOARDING_CHANNEL_REWARD_CHAT} chat, +{ONBOARDING_CHANNEL_REWARD_IMAGE} image\n"
    )
    group_cfg = (
        f"  👥 Group: <code>{TELEGRAM_GROUP_ID or 'Not set'}</code>\n"
        f"     URL: {TELEGRAM_GROUP_URL}\n"
        f"     Reward: +{ONBOARDING_GROUP_REWARD_CHAT} chat, +{ONBOARDING_GROUP_REWARD_IMAGE} image\n"
    )

    text = (
        f"🚀 <b>Onboarding System — Admin Dashboard</b>\n\n"
        f"<b>📊 Stats:</b>\n"
        f"  Users shown onboarding: {stats['total']}\n"
        f"  Completed onboarding:   {stats['complete']}\n"
        f"  Joined channel:         {stats['channel']}\n"
        f"  Joined group:           {stats['group']}\n\n"
        f"<b>⚙️ Configuration:</b>\n"
        f"{channel_cfg}"
        f"{group_cfg}"
        f"  🔒 Required mode: {'✅ ON' if ONBOARDING_REQUIRED else '❌ OFF (users can skip)'}\n\n"
        f"<b>🛠️ How to configure:</b>\n"
        f"Set these Railway environment variables:\n"
        f"  <code>TELEGRAM_CHANNEL_ID</code> — @username or numeric ID\n"
        f"  <code>TELEGRAM_CHANNEL_URL</code> — invite link\n"
        f"  <code>TELEGRAM_CHANNEL_NAME</code> — display name\n"
        f"  <code>TELEGRAM_GROUP_ID</code> — @username or numeric ID\n"
        f"  <code>TELEGRAM_GROUP_URL</code> — invite link\n"
        f"  <code>TELEGRAM_GROUP_NAME</code> — display name\n"
        f"  <code>ONBOARDING_CHANNEL_REWARD_CHAT</code> — default 5\n"
        f"  <code>ONBOARDING_CHANNEL_REWARD_IMAGE</code> — default 1\n"
        f"  <code>ONBOARDING_GROUP_REWARD_CHAT</code> — default 5\n"
        f"  <code>ONBOARDING_GROUP_REWARD_IMAGE</code> — default 1\n"
        f"  <code>ONBOARDING_REQUIRED</code> — true/false\n"
    )

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Refresh Stats", callback_data="admin:onboarding_stats"),
        InlineKeyboardButton("« Admin Panel",    callback_data="admin:panel"),
    ]])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kbd)
