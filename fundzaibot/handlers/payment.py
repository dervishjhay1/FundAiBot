"""
FundzAiBot — Telegram Stars payment handler.
Users pay with Telegram Stars (XTR) to unlock VIP plans.
Admin cannot subscribe — they have full access by default.
"""

from datetime import datetime, timedelta

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

from config.settings import VIP_PLANS, BOT_NAME, is_admin
from services.database import get_or_create_user, activate_vip
from utils.keyboards import back_to_menu, vip_plans_keyboard, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)


async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show VIP plans with Stars pricing. Admin gets redirected."""
    user = update.effective_user
    if not user:
        return

    # ── Admin cannot subscribe to VIP ─────────────────────────────────────────
    if is_admin(user.id):
        await update.effective_message.reply_text(
            "🛡️ <b>You are the Bot Administrator.</b>\n\n"
            "You already have <b>unlimited access</b> to all features — "
            "no subscription needed.\n\n"
            "Use /admin to manage your bot.",
            parse_mode="HTML",
            reply_markup=admin_main_menu(),
        )
        return

    loop = __import__("asyncio").get_running_loop()
    db_user = await loop.run_in_executor(None, get_or_create_user, user.id)
    is_vip = (db_user or {}).get("is_vip", False)
    tier = (db_user or {}).get("vip_tier", "")
    expires = (db_user or {}).get("vip_expires_at", "")

    status_line = ""
    if is_vip and tier:
        status_line = f"✅ You are currently on <b>{tier.upper()} VIP</b>"
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                days_left = (exp_dt.replace(tzinfo=None) - datetime.utcnow()).days
                status_line += f" ({days_left} days left)"
            except Exception:
                pass
        status_line += "\n\n"

    text = (
        f"💎 <b>FundzAiBot VIP Plans</b>\n\n"
        f"{status_line}"
        f"Upgrade with <b>Telegram Stars</b> ⭐ — instant, secure, no card needed.\n\n"
        f"<b>⭐ Basic — {VIP_PLANS['basic']['stars']} Stars/month</b>\n"
        f"  • {VIP_PLANS['basic']['chat_limit']} chats per day\n"
        f"  • {VIP_PLANS['basic']['image_limit']} images per day\n"
        f"  • Priority support\n\n"
        f"<b>💎 Pro — {VIP_PLANS['pro']['stars']} Stars/month</b>\n"
        f"  • {VIP_PLANS['pro']['chat_limit']} chats per day\n"
        f"  • {VIP_PLANS['pro']['image_limit']} images per day\n"
        f"  • Priority AI queue\n\n"
        f"<b>🚀 Elite — {VIP_PLANS['elite']['stars']} Stars/month</b>\n"
        f"  • Unlimited chats\n"
        f"  • {VIP_PLANS['elite']['image_limit']} images per day\n"
        f"  • Custom AI persona + highest priority\n\n"
        f"<i>⭐ Buy Telegram Stars: Telegram Settings → My Stars</i>"
    )

    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=vip_plans_keyboard(),
    )


async def send_vip_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str) -> None:
    """Send a Telegram Stars invoice. Admin is blocked."""
    user = update.effective_user
    query = update.callback_query
    if not user:
        return

    if is_admin(user.id):
        if query:
            await query.answer("You are the admin — no subscription needed!", show_alert=True)
        return

    plan = VIP_PLANS.get(tier)
    if not plan:
        if query:
            await query.answer("Unknown plan.", show_alert=True)
        return

    icons = {"basic": "⭐", "pro": "💎", "elite": "🚀"}
    icon = icons.get(tier, "💎")
    title = f"{icon} {tier.capitalize()} VIP — 30 Days"
    description = (
        f"{plan['chat_limit']} chats/day • "
        f"{plan['image_limit']} images/day • "
        f"30-day VIP access on {BOT_NAME}"
    )

    await context.bot.send_invoice(
        chat_id=user.id,
        title=title,
        description=description,
        payload=f"vip:{tier}:30",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=plan["stars"])],
    )


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    await query.answer(ok=True)
    log.info("Pre-checkout approved: user=%s payload=%s", query.from_user.id, query.invoice_payload)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    payment = update.message.successful_payment
    if not user or not payment:
        return

    payload = payment.invoice_payload
    stars = payment.total_amount
    log.info("Payment: user=%s payload=%s stars=%s", user.id, payload, stars)

    try:
        parts = payload.split(":")
        tier = parts[1] if len(parts) >= 2 else "basic"
        days = int(parts[2]) if len(parts) >= 3 else 30
    except Exception:
        tier, days = "basic", 30

    expires_at = datetime.utcnow() + timedelta(days=days)

    try:
        activate_vip(user.id, tier=tier, days=days)
        log.info("VIP activated: user=%s tier=%s expires=%s", user.id, tier, expires_at.date())
    except Exception as exc:
        log.error("Failed to activate VIP for user %s: %s", user.id, exc)

    plan = VIP_PLANS.get(tier, {})
    icons = {"basic": "⭐", "pro": "💎", "elite": "🚀"}
    icon = icons.get(tier, "💎")

    await update.message.reply_text(
        f"{icon} <b>Welcome to {tier.capitalize()} VIP!</b>\n\n"
        f"✅ Your VIP is now active for <b>{days} days</b>\n"
        f"⭐ Stars paid: <b>{stars}</b>\n\n"
        f"Your new limits:\n"
        f"  • 💬 {plan.get('chat_limit', '∞')} chats per day\n"
        f"  • 🎨 {plan.get('image_limit', 50)} images per day\n\n"
        f"<i>Thank you for supporting {BOT_NAME}! 🙏</i>",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
