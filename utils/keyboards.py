"""
FundzAiBot — All InlineKeyboardMarkup factories.
Version 5.0.1 — Executive admin menus removed.
Only user-facing and basic operational menus remain.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import TELEGRAM_CHANNEL_URL, TELEGRAM_GROUP_URL, BOT_WEB_URL


# ── Regular user menus ────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 AI Chat",    callback_data="menu:chat"),
            InlineKeyboardButton("🎨 Image Gen",  callback_data="menu:image"),
        ],
        [
            InlineKeyboardButton("👤 My Profile", callback_data="menu:profile"),
            InlineKeyboardButton("📊 My Stats",   callback_data="menu:stats"),
        ],
        [
            InlineKeyboardButton("🔗 Referral",   callback_data="menu:referral"),
            InlineKeyboardButton("⚙️ Settings",   callback_data="menu:settings"),
        ],
        [
            InlineKeyboardButton("💎 VIP Plans",  callback_data="menu:vip"),
            InlineKeyboardButton("🌐 Language",   callback_data="menu:language"),
        ],
        [
            InlineKeyboardButton("ℹ️ Help",       callback_data="menu:help"),
        ],
    ])


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("« Main Menu", callback_data="menu:back")]
    ])


def ai_styles_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧠 Default",   callback_data="style:default"),
            InlineKeyboardButton("📚 Teacher",   callback_data="style:teacher"),
        ],
        [
            InlineKeyboardButton("😂 Comedian",  callback_data="style:comedian"),
            InlineKeyboardButton("🔬 Scientist", callback_data="style:scientist"),
        ],
        [
            InlineKeyboardButton("📝 Writer",    callback_data="style:writer"),
            InlineKeyboardButton("💼 Business",  callback_data="style:business"),
        ],
        [
            InlineKeyboardButton("🧑‍💻 Coder",   callback_data="style:coder"),
            InlineKeyboardButton("🎭 Creative",  callback_data="style:creative"),
        ],
        [InlineKeyboardButton("« Back", callback_data="menu:back")],
    ])


def image_styles_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📷 Realistic",  callback_data="imgstyle:realistic"),
            InlineKeyboardButton("🎨 Artistic",   callback_data="imgstyle:artistic"),
        ],
        [
            InlineKeyboardButton("🌌 Fantasy",    callback_data="imgstyle:fantasy"),
            InlineKeyboardButton("🤖 Cyberpunk",  callback_data="imgstyle:cyberpunk"),
        ],
        [
            InlineKeyboardButton("🏛️ Classical",  callback_data="imgstyle:classical"),
            InlineKeyboardButton("🌸 Anime",      callback_data="imgstyle:anime"),
        ],
        [InlineKeyboardButton("« Back", callback_data="menu:back")],
    ])


def settings_menu(current_style: str = "default", notifications: bool = True) -> InlineKeyboardMarkup:
    notif_text = "🔔 Notifs: ON" if notifications else "🔕 Notifs: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🤖 AI Style: {current_style.capitalize()}", callback_data="menu:styles")],
        [InlineKeyboardButton(notif_text, callback_data="settings:toggle_notif")],
        [InlineKeyboardButton("🌐 Change Language", callback_data="menu:language")],
        [InlineKeyboardButton("🗑️ Clear Chat History", callback_data="settings:clear_history")],
        [InlineKeyboardButton("📤 Export My Data",     callback_data="settings:export")],
        [InlineKeyboardButton("« Back",                callback_data="menu:back")],
    ])


def vip_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Basic  — 250 Stars/mo  (500 chats, 50 images)",    callback_data="vip:basic")],
        [InlineKeyboardButton("💎 Pro    — 500 Stars/mo  (2000 chats + priority)",   callback_data="vip:pro")],
        [InlineKeyboardButton("🚀 Elite  — 1000 Stars/mo (Unlimited + custom AI)",   callback_data="vip:elite")],
        [InlineKeyboardButton("« Back",                                               callback_data="menu:back")],
    ])


def vip_plans_keyboard() -> InlineKeyboardMarkup:
    """Alias used by the payment handler."""
    return vip_menu()


def announcement_keyboard(ann_id: str | None = None) -> InlineKeyboardMarkup:
    """Simple dismiss button for announcements."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Got it!", callback_data="ann:dismiss")]
    ])


def admin_main_menu() -> InlineKeyboardMarkup:
    """
    Minimal admin menu — operational controls only.
    Executive authority belongs to Fundz Company Headquarters.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",      callback_data="admin:stats"),
            InlineKeyboardButton("🩺 AI Health",  callback_data="admin:health"),
        ],
        [
            InlineKeyboardButton("🔄 Queue",      callback_data="admin:queue"),
            InlineKeyboardButton("📝 Errors",     callback_data="admin:errors"),
        ],
        [
            InlineKeyboardButton("⚙️ Features",   callback_data="admin:botsettings"),
        ],
        [InlineKeyboardButton("« Main Menu", callback_data="menu:back")],
    ])
