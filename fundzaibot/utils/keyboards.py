"""
FundzAiBot — All InlineKeyboardMarkup factories live here.
Handlers stay clean; all button logic is centralised.

Ecosystem menus (v3):
  main_menu()            — 8-button ecosystem hub (channel, community, AI, VIP, etc.)
  join_screen_keyboard() — force-join screen for new/unverified users
  verify_join_keyboard() — partial-join state (shows only missing buttons)
  aitools_menu()         — AI tools submenu
  community_keyboard()   — community section
  updates_keyboard()     — latest updates section
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config.settings import TELEGRAM_CHANNEL_URL, TELEGRAM_GROUP_URL, BOT_WEB_URL


# ── Ecosystem main menu (regular users) ───────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    """
    Professional 8-button ecosystem hub shown to verified users.
    Covers all primary journeys: updates, community, AI, VIP, referrals, profile,
    settings, and support.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Latest Updates", callback_data="menu:updates"),
            InlineKeyboardButton("👥 Community",      callback_data="menu:community"),
        ],
        [
            InlineKeyboardButton("🤖 AI Tools",       callback_data="menu:aitools"),
            InlineKeyboardButton("💎 VIP",             callback_data="menu:vip"),
        ],
        [
            InlineKeyboardButton("🎁 Referrals",      callback_data="menu:referral"),
            InlineKeyboardButton("👤 Profile",         callback_data="menu:profile"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings",       callback_data="menu:settings"),
            InlineKeyboardButton("🆘 Support",         callback_data="menu:support"),
        ],
    ])


# ── Force-join screens ────────────────────────────────────────────────────────

def join_screen_keyboard() -> InlineKeyboardMarkup:
    """
    Shown to new users who haven't yet joined both the channel and group.
    Three buttons: Join Channel, Join Community, Verify Access.
    """
    channel_url = TELEGRAM_CHANNEL_URL or "https://t.me/FundzAiChannel"
    group_url   = TELEGRAM_GROUP_URL   or "https://t.me/FundzAiGroup"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",     url=channel_url)],
        [InlineKeyboardButton("👥 Join Community",   url=group_url)],
        [InlineKeyboardButton("✅ Verify Access",    callback_data="menu:verify")],
    ])


def verify_join_keyboard(
    channel_ok: bool = False,
    group_ok:   bool = False,
    need_channel: bool = True,
    need_group:   bool = True,
) -> InlineKeyboardMarkup:
    """
    Shown after a failed verification — only shows buttons for the parts still missing.
    Always shows the Verify Access button again.
    """
    channel_url = TELEGRAM_CHANNEL_URL or "https://t.me/FundzAiChannel"
    group_url   = TELEGRAM_GROUP_URL   or "https://t.me/FundzAiGroup"

    rows: list[list[InlineKeyboardButton]] = []
    if need_channel and not channel_ok:
        rows.append([InlineKeyboardButton("📢 Join Channel",   url=channel_url)])
    if need_group and not group_ok:
        rows.append([InlineKeyboardButton("👥 Join Community", url=group_url)])
    rows.append([InlineKeyboardButton("🔄 Try Again",          callback_data="menu:verify")])
    return InlineKeyboardMarkup(rows)


# ── Ecosystem sub-menus ───────────────────────────────────────────────────────

def aitools_menu() -> InlineKeyboardMarkup:
    """AI tools hub — reached from main menu 🤖 AI Tools button."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 AI Chat",         callback_data="menu:chat"),
            InlineKeyboardButton("🎨 Image Gen",        callback_data="menu:image"),
        ],
        [
            InlineKeyboardButton("🎭 AI Style",        callback_data="menu:style_picker"),
            InlineKeyboardButton("📚 AI Commands",     callback_data="menu:aicommands"),
        ],
        [InlineKeyboardButton("« Back",                callback_data="menu:back")],
    ])


def updates_keyboard() -> InlineKeyboardMarkup:
    """Latest updates section keyboard."""
    channel_url = TELEGRAM_CHANNEL_URL or "https://t.me/FundzAiChannel"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Open Channel",       url=channel_url)],
        [InlineKeyboardButton("📌 View Announcements", callback_data="menu:announcements")],
        [InlineKeyboardButton("« Back",                callback_data="menu:back")],
    ])


def community_keyboard() -> InlineKeyboardMarkup:
    """Community section keyboard."""
    group_url = TELEGRAM_GROUP_URL or "https://t.me/FundzAiGroup"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Open Community",     url=group_url)],
        [
            InlineKeyboardButton("📋 Community Rules", callback_data="menu:rules"),
            InlineKeyboardButton("🐛 Report Issue",    callback_data="menu:report"),
        ],
        [InlineKeyboardButton("💡 Suggest Feature",    callback_data="menu:suggest")],
        [InlineKeyboardButton("« Back",                callback_data="menu:back")],
    ])


def support_keyboard() -> InlineKeyboardMarkup:
    """Support section keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Contact Support",    url="https://t.me/Biodunfund")],
        [InlineKeyboardButton("❓ Help Guide",         callback_data="menu:help")],
        [InlineKeyboardButton("🐛 Report a Bug",       callback_data="menu:report")],
        [InlineKeyboardButton("« Back",                callback_data="menu:back")],
    ])


# ── Regular user sub-menus (unchanged) ────────────────────────────────────────

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
        [InlineKeyboardButton(f"🤖 AI Style: {current_style.capitalize()}", callback_data="menu:style_picker")],
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Basic — 250 Stars/month",  callback_data="vip:basic")],
        [InlineKeyboardButton("💎 Pro — 500 Stars/month",    callback_data="vip:pro")],
        [InlineKeyboardButton("🚀 Elite — 1000 Stars/month", callback_data="vip:elite")],
        [InlineKeyboardButton("❓ What are Stars?",          callback_data="vip:stars_info")],
        [InlineKeyboardButton("« Main Menu",                 callback_data="menu:back")],
    ])


def confirm_action(action: str, label: str = "Confirm") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ {label}", callback_data=f"confirm:{action}"),
            InlineKeyboardButton("❌ Cancel",   callback_data="cancel"),
        ]
    ])


def pagination(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("« Prev", callback_data=f"{prefix}:page:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("Next »", callback_data=f"{prefix}:page:{page + 1}"))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("« Back", callback_data="menu:back")]])


# ── Admin menus ───────────────────────────────────────────────────────────────

def admin_main_menu() -> InlineKeyboardMarkup:
    """Main menu shown to the admin — ecosystem menu + admin panel."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Latest Updates", callback_data="menu:updates"),
            InlineKeyboardButton("👥 Community",      callback_data="menu:community"),
        ],
        [
            InlineKeyboardButton("🤖 AI Tools",       callback_data="menu:aitools"),
            InlineKeyboardButton("💎 VIP",             callback_data="menu:vip"),
        ],
        [
            InlineKeyboardButton("🛡️ Admin Panel",    callback_data="admin:panel"),
            InlineKeyboardButton("📊 Live Stats",      callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton("🩺 Health Check",   callback_data="admin:health"),
            InlineKeyboardButton("🌐 Language",        callback_data="menu:language"),
        ],
        [
            InlineKeyboardButton("👤 Profile",         callback_data="menu:profile"),
            InlineKeyboardButton("ℹ️ Help",            callback_data="menu:help"),
        ],
    ])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Full admin control panel keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Users",          callback_data="admin:users"),
            InlineKeyboardButton("📊 Live Stats",      callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",      callback_data="admin:broadcast"),
            InlineKeyboardButton("🚫 Banned",          callback_data="admin:banned"),
        ],
        [
            InlineKeyboardButton("💎 Set VIP",        callback_data="admin:vip"),
            InlineKeyboardButton("💳 Credits",         callback_data="admin:credits"),
        ],
        [
            InlineKeyboardButton("📋 Error Logs",     callback_data="admin:logs"),
            InlineKeyboardButton("🔄 Queue",           callback_data="admin:queue"),
        ],
        [
            InlineKeyboardButton("🖼️ Images",         callback_data="admin:images"),
            InlineKeyboardButton("🩺 AI Health",       callback_data="admin:health"),
        ],
        [
            InlineKeyboardButton("⚙️ Bot Settings",   callback_data="admin:botsettings"),
            InlineKeyboardButton("🔍 Find User",       callback_data="admin:finduser"),
        ],
        [
            InlineKeyboardButton("🚀 Onboarding",     callback_data="admin:onboarding_stats"),
            InlineKeyboardButton("📌 Announcement",    callback_data="admin:announcement"),
        ],
        [
            InlineKeyboardButton("📢 Channel Post",   callback_data="admin:post_channel"),
            InlineKeyboardButton("👥 Group Post",      callback_data="admin:post_group"),
        ],
        [
            InlineKeyboardButton("🩺 Audit Center",   callback_data="audit:dashboard"),
        ],
        [InlineKeyboardButton("« Main Menu",           callback_data="admin:back_home")],
    ])


def bot_settings_keyboard(flags: dict) -> InlineKeyboardMarkup:
    """Admin bot settings — toggle feature flags."""
    def toggle(name: str, label: str, key: str) -> InlineKeyboardButton:
        state = "✅ ON" if flags.get(key) else "❌ OFF"
        return InlineKeyboardButton(f"{label}: {state}", callback_data=f"botsetting:{key}")

    return InlineKeyboardMarkup([
        [toggle("chat",  "💬 Chat",        "chat_enabled")],
        [toggle("img",   "🎨 Image Gen",   "image_enabled")],
        [toggle("users", "🌐 New Users",   "new_users_enabled")],
        [toggle("maint", "🚧 Maintenance", "maintenance_mode")],
        [InlineKeyboardButton("« Back to Admin Panel", callback_data="admin:panel")],
    ])


# ── Announcement card keyboard ────────────────────────────────────────────────

def announcement_keyboard(
    support_url: str = "https://t.me/Biodunfund",
    ann_count: int = 1,
    ann_idx: int = 0,
) -> InlineKeyboardMarkup:
    """
    Premium announcement keyboard.

    Row 1 — navigation (only shown when there are multiple announcements):
      ◀ Prev  •  1 / 3  •  Next ▶

    Row 2 — action links:
      🔧 Support  |  📢 Channel  |  👥 Community

    Row 3 — Open full Overlay (only when BOT_WEB_URL is configured):
      🔔 Open Announcement Panel   (Telegram Web App)
    """
    channel_url = TELEGRAM_CHANNEL_URL or "https://t.me/FundzAiChannel"
    group_url   = TELEGRAM_GROUP_URL   or "https://t.me/FundzAiGroup"

    rows = []

    if ann_count > 1:
        nav_row = []
        if ann_idx > 0:
            nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"announce:nav:{ann_idx - 1}"))
        nav_row.append(
            InlineKeyboardButton(f"📌 {ann_idx + 1}/{ann_count}", callback_data="noop")
        )
        if ann_idx < ann_count - 1:
            nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"announce:nav:{ann_idx + 1}"))
        rows.append(nav_row)

    rows.append([
        InlineKeyboardButton("🔧 Support",   url=support_url),
        InlineKeyboardButton("📢 Channel",   url=channel_url),
        InlineKeyboardButton("👥 Community", url=group_url),
    ])

    if BOT_WEB_URL:
        rows.append([
            InlineKeyboardButton(
                "🔔 Open Announcement Panel",
                web_app=WebAppInfo(url=f"{BOT_WEB_URL}/announcement"),
            )
        ])

    return InlineKeyboardMarkup(rows)


# ── Legacy alias ──────────────────────────────────────────────────────────────
def admin_menu() -> InlineKeyboardMarkup:
    """Alias kept for backward-compatibility."""
    return admin_panel_keyboard()
