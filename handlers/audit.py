"""
FundzAiBot — Enterprise /testaudit command.
Interactive inline-button audit dashboard for administrators.
Admin-only. Never visible to regular users.
"""

import asyncio
import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import is_admin, BOT_NAME, BOT_VERSION, IS_RAILWAY
from utils.logger import get_logger

log = get_logger(__name__)

# ── Visual helpers ─────────────────────────────────────────────────────────────

_S_ICON = {"ok": "✅", "warning": "⚠️", "critical": "❌"}


def _icon(status: str) -> str:
    return _S_ICON.get(status, "❔")


def _score_bar(score: int) -> str:
    filled = min(score // 10, 10)
    return "█" * filled + "░" * (10 - filled) + f"  {score}%"


# ── Dashboard builders ────────────────────────────────────────────────────────

def _dashboard_text(summary: dict | None = None) -> str:
    env  = "🚂 Railway LIVE" if IS_RAILWAY else "💻 Dev / Replit"
    ts   = datetime.utcnow().strftime("%H:%M UTC  %d %b %Y")

    if summary:
        score    = summary.get("score", 0)
        passed   = summary.get("passed", 0)
        warnings = summary.get("warnings", 0)
        critical = summary.get("critical", 0)
        if critical > 0:
            h_icon, h_label = "🔴", "Critical Issues Found"
        elif warnings > 0:
            h_icon, h_label = "🟡", "Needs Attention"
        else:
            h_icon, h_label = "🟢", "Production Ready"
        bar = _score_bar(score)
    else:
        h_icon, h_label, bar = "🔵", "Ready to Audit", "Press Full Retest to score"
        passed = warnings = critical = 0

    return (
        f"🩺 <b>{BOT_NAME} Audit Center</b>  <code>v{BOT_VERSION}</code>\n"
        f"{'─' * 33}\n"
        f"{h_icon} <b>{h_label}</b>\n"
        f"📊 {bar}\n\n"
        f"✅ <b>{passed}</b> passed   "
        f"⚠️ <b>{warnings}</b> warnings   "
        f"❌ <b>{critical}</b> critical\n\n"
        f"🌐 Environment: {env}\n"
        f"🕐 {ts}\n\n"
        f"<i>Select a section to run, or use Full Retest for all checks:</i>"
    )


def _dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Bot Core",       callback_data="audit:section:bot"),
            InlineKeyboardButton("🧠 AI Providers",   callback_data="audit:section:ai"),
        ],
        [
            InlineKeyboardButton("🗄️ Database",       callback_data="audit:section:db"),
            InlineKeyboardButton("🚂 Railway",         callback_data="audit:section:railway"),
        ],
        [
            InlineKeyboardButton("📢 Channel",         callback_data="audit:section:channel"),
            InlineKeyboardButton("👥 Community",       callback_data="audit:section:group"),
        ],
        [
            InlineKeyboardButton("👑 Admin",           callback_data="audit:section:admin"),
            InlineKeyboardButton("🎁 Referrals",       callback_data="audit:section:referrals"),
        ],
        [
            InlineKeyboardButton("💎 VIP",             callback_data="audit:section:vip"),
            InlineKeyboardButton("📌 Announcements",   callback_data="audit:section:announcements"),
        ],
        [
            InlineKeyboardButton("🔒 Security",        callback_data="audit:section:security"),
            InlineKeyboardButton("📋 Error Logs",      callback_data="audit:section:logs"),
        ],
        [
            InlineKeyboardButton("🛠 Auto Fix All",    callback_data="audit:fixall"),
            InlineKeyboardButton("📄 Generate Report", callback_data="audit:report"),
        ],
        [
            InlineKeyboardButton("🔄 Full Retest",     callback_data="audit:fullretest"),
        ],
    ])


def _section_text(result: dict) -> str:
    title    = result.get("title", "Audit")
    checks   = result.get("checks", [])
    score    = result.get("score", 0)
    critical = result.get("critical", 0)
    warnings = result.get("warnings", 0)
    passed   = result.get("passed", 0)

    lines = [
        f"<b>{title}</b>",
        f"{'─' * 30}",
        f"📊 {_score_bar(score)}",
        f"✅ {passed}  ⚠️ {warnings}  ❌ {critical}",
        "",
    ]
    for c in checks:
        icon  = _icon(c["status"])
        label = html.escape(str(c.get("label", "")))
        value = html.escape(str(c.get("value", "")))
        lines.append(f"{icon} <b>{label}:</b>  {value}")
        detail = str(c.get("detail") or "")
        if detail and c["status"] != "ok":
            lines.append(f"   <i>↳ {html.escape(detail[:130])}</i>")

    return "\n".join(lines)


def _section_keyboard(section: str, has_fix: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_fix:
        rows.append([
            InlineKeyboardButton("🛠 Auto Fix",  callback_data=f"audit:fix:{section}"),
            InlineKeyboardButton("🔄 Retest",    callback_data=f"audit:retest:{section}"),
        ])
    else:
        rows.append([InlineKeyboardButton("🔄 Retest", callback_data=f"audit:retest:{section}")])
    rows.append([InlineKeyboardButton("« Back to Audit", callback_data="audit:dashboard")])
    return InlineKeyboardMarkup(rows)


# ── /testaudit command ─────────────────────────────────────────────────────────

async def testaudit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text(
            "⛔ This command is for administrators only."
        )
        return

    loop = asyncio.get_running_loop()
    msg  = await update.effective_message.reply_text("🩺 Loading Audit Center…")

    from services.audit_service import run_quick_summary
    summary = await loop.run_in_executor(None, run_quick_summary)

    try:
        await msg.edit_text(
            _dashboard_text(summary),
            parse_mode="HTML",
            reply_markup=_dashboard_keyboard(),
        )
    except Exception as exc:
        log.warning("testaudit dashboard render failed: %s", exc)

    log.info("Admin %s opened /testaudit", user.id)


# ── Callback router (called from callbacks.py) ─────────────────────────────────

async def audit_callback(query, action: str) -> None:
    """
    Main router for all audit: callbacks.
    action = everything after "audit:"  (e.g. "section:bot", "fix:ai", "report")
    """
    loop = asyncio.get_running_loop()

    # ── Back to dashboard ──────────────────────────────────────────────────────
    if action == "dashboard":
        from services.audit_service import run_quick_summary
        await query.answer("Loading…")
        summary = await loop.run_in_executor(None, run_quick_summary)
        try:
            await query.edit_message_text(
                _dashboard_text(summary), parse_mode="HTML",
                reply_markup=_dashboard_keyboard(),
            )
        except Exception:
            pass
        return

    # ── Individual section ─────────────────────────────────────────────────────
    if action.startswith("section:"):
        section = action[len("section:"):]
        await query.answer(f"Checking {section}…")
        from services.audit_service import run_section
        result = await loop.run_in_executor(None, lambda: run_section(section))
        text = _section_text(result)
        kbd  = _section_keyboard(section, result.get("fix_available", False))
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            log.warning("Section edit failed: %s", exc)
        return

    # ── Retest one section ─────────────────────────────────────────────────────
    if action.startswith("retest:"):
        section = action[len("retest:"):]
        await query.answer("Retesting…")
        from services.audit_service import run_section
        result = await loop.run_in_executor(None, lambda: run_section(section))
        text = _section_text(result)
        kbd  = _section_keyboard(section, result.get("fix_available", False))
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass
        return

    # ── Auto-fix one section ───────────────────────────────────────────────────
    if action.startswith("fix:"):
        section = action[len("fix:"):]
        await query.answer("Applying fixes…")
        from services.audit_service import run_section, perform_auto_fix
        result    = await loop.run_in_executor(None, lambda: run_section(section))
        fix_keys  = [c["fix"] for c in result.get("checks", []) if c.get("fix")]
        all_acts: list[str] = []
        for fk in fix_keys:
            fr = await loop.run_in_executor(None, lambda k=fk: perform_auto_fix(k))
            all_acts.extend(fr.get("actions", []))
        if not all_acts:
            all_acts = ["ℹ️ No safe auto-fixes available for this section"]

        text = (
            f"🛠 <b>Auto Fix — {html.escape(result.get('title', section))}</b>\n"
            f"{'─' * 30}\n\n"
            + "\n".join(html.escape(a) if not any(a.startswith(p) for p in ("✅","⚠️","ℹ️","❌")) else a
                        for a in all_acts)
            + "\n\n<i>Run Retest to verify fixes took effect.</i>"
        )
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Retest", callback_data=f"audit:retest:{section}"),
            InlineKeyboardButton("« Back",    callback_data="audit:dashboard"),
        ]])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass
        return

    # ── Auto-fix ALL ───────────────────────────────────────────────────────────
    if action == "fixall":
        await query.answer("Running all auto-fixes…")
        from services.audit_service import SECTION_RUNNERS, run_section, perform_auto_fix
        all_acts: list[str] = []
        for s in SECTION_RUNNERS:
            res = await loop.run_in_executor(None, lambda sec=s: run_section(sec))
            for c in res.get("checks", []):
                if c.get("fix"):
                    fr = await loop.run_in_executor(None,
                                                    lambda k=c["fix"]: perform_auto_fix(k))
                    all_acts.extend(fr.get("actions", []))
        if not all_acts:
            all_acts = ["✅ System is healthy — nothing needed auto-fixing"]

        text = (
            f"🛠 <b>Auto Fix All — Results</b>\n"
            f"{'─' * 30}\n\n"
            + "\n".join(all_acts[:25])
            + ("\n<i>…and more</i>" if len(all_acts) > 25 else "")
            + "\n\n<i>⚠️ Manual action required for API keys and Railway config.</i>"
        )
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Full Retest", callback_data="audit:fullretest"),
            InlineKeyboardButton("« Back",         callback_data="audit:dashboard"),
        ]])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass
        return

    # ── Full retest (all sections, hits external APIs) ─────────────────────────
    if action == "fullretest":
        await query.answer("Running full audit… (15–40s)")
        try:
            await query.edit_message_text(
                "⏳ <b>Full Audit Running…</b>\n\n"
                "Checking: Bot · AI · DB · Railway · Channel · Group\n"
                "Admin · Referrals · VIP · Announcements · Security · Logs\n\n"
                "<i>Calling external APIs — please wait…</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        from services.audit_service import run_full_audit
        summary = await loop.run_in_executor(None, run_full_audit)
        try:
            await query.edit_message_text(
                _dashboard_text(summary), parse_mode="HTML",
                reply_markup=_dashboard_keyboard(),
            )
        except Exception:
            pass
        return

    # ── Generate full report ───────────────────────────────────────────────────
    if action == "report":
        await query.answer("Generating report…")
        try:
            await query.edit_message_text(
                "⏳ <b>Generating Audit Report…</b>\n<i>Running all checks…</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        from services.audit_service import run_full_audit, SECTION_TITLES
        full = await loop.run_in_executor(None, run_full_audit)
        ts   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"📄 <b>{BOT_NAME} Audit Report</b>",
            f"Generated: {ts}   Version: v{BOT_VERSION}",
            f"{'═' * 32}",
            f"",
            f"🏥 <b>Health Score: {full['score']}%</b>",
            f"✅ Passed: {full['passed']}   "
            f"⚠️ Warnings: {full['warnings']}   "
            f"❌ Critical: {full['critical']}",
            f"📊 Status: {full['status']}",
            f"",
            f"<b>Section Scores:</b>",
        ]
        for s_name, s_res in full["sections"].items():
            icon  = _icon(s_res["status"])
            title = SECTION_TITLES.get(s_name, s_name)
            lines.append(
                f"{icon} {title}: {s_res['score']}%"
                f"  ({s_res['passed']}✅ {s_res['warnings']}⚠️ {s_res['critical']}❌)"
            )

        lines += ["", "<b>Critical Issues:</b>"]
        had_crit = False
        for s_name, s_res in full["sections"].items():
            for c in s_res.get("checks", []):
                if c["status"] == "critical":
                    had_crit = True
                    t = SECTION_TITLES.get(s_name, s_name)
                    lines.append(f"❌ [{t}] {html.escape(c['label'])}: {html.escape(c['value'])}")
                    if c.get("detail"):
                        lines.append(f"   → {html.escape(str(c['detail'])[:100])}")
        if not had_crit:
            lines.append("✅ No critical issues")

        lines += ["", "<b>Warnings:</b>"]
        had_warn = False
        for s_name, s_res in full["sections"].items():
            for c in s_res.get("checks", []):
                if c["status"] == "warning":
                    had_warn = True
                    t = SECTION_TITLES.get(s_name, s_name)
                    lines.append(f"⚠️ [{t}] {html.escape(c['label'])}: {html.escape(c['value'])}")
        if not had_warn:
            lines.append("✅ No warnings")

        lines += [
            "",
            f"<b>Production Readiness: {full['status']}</b>",
            f"<i>— FundzAiBot Audit Center v{BOT_VERSION}</i>",
        ]

        report = "\n".join(lines)
        if len(report) > 4000:
            report = report[:3900] + "\n\n<i>… (report truncated at 4000 chars)</i>"

        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Regenerate", callback_data="audit:report"),
            InlineKeyboardButton("« Back",        callback_data="audit:dashboard"),
        ]])
        try:
            await query.edit_message_text(report, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass
        return

    await query.answer("Unknown audit action.", show_alert=True)
