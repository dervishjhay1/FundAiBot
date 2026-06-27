"""
FundzAiBot — AI Department Registry (TestAudit role)

Plugin-style framework for future AI departments.

Each department registers itself with TestAudit by calling register().
TestAudit coordinates all departments: starts them, monitors health,
collects status, and routes inter-department messages.

Current departments (built-in):
  • TestAudit Core     — continuous health monitoring
  • Executive Assistant — CEO reports and briefings
  • Community Manager  — Telegram group engagement
  • Channel Manager    — Telegram channel content
  • Customer Success   — user engagement monitoring

Future departments (plug in without redesign):
  • Support AI         — automated user support triage
  • Marketing AI       — campaign planning and analysis
  • Analytics AI       — advanced usage pattern analysis
  • Content AI         — advanced content generation
  • Automation AI      — workflow automation suggestions

To add a new department, call register() with the department spec.
No changes to any existing file required.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

from utils.logger import get_logger

log = get_logger(__name__)


# ── Department spec ───────────────────────────────────────────────────────────

class Department:
    """Represents one AI department registered with TestAudit."""

    def __init__(
        self,
        name: str,
        role: str,
        description: str,
        start_fn: Callable | None = None,
        stop_fn:  Callable | None = None,
        status_fn: Callable[[], dict] | None = None,
    ) -> None:
        self.name        = name
        self.role        = role
        self.description = description
        self.start_fn    = start_fn
        self.stop_fn     = stop_fn
        self.status_fn   = status_fn
        self.started_at: datetime | None = None
        self.healthy: bool = True

    def start(self) -> None:
        if self.start_fn:
            try:
                self.start_fn()
                self.started_at = datetime.now(timezone.utc)
                self.healthy    = True
                log.info("Department '%s' started", self.name)
            except Exception as exc:
                self.healthy = False
                log.error("Department '%s' failed to start: %s", self.name, exc)

    def stop(self) -> None:
        if self.stop_fn:
            try:
                self.stop_fn()
                log.info("Department '%s' stopped", self.name)
            except Exception as exc:
                log.warning("Department '%s' stop error: %s", self.name, exc)

    def get_status(self) -> dict:
        base = {
            "name":       self.name,
            "role":       self.role,
            "healthy":    self.healthy,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }
        if self.status_fn:
            try:
                base.update(self.status_fn())
            except Exception:
                pass
        return base


# ── Registry ──────────────────────────────────────────────────────────────────

_departments: dict[str, Department] = {}
_registry_lock = threading.Lock()


def register(
    name: str,
    role: str,
    description: str,
    start_fn: Callable | None = None,
    stop_fn:  Callable | None = None,
    status_fn: Callable[[], dict] | None = None,
) -> Department:
    """
    Register an AI department with TestAudit.
    Call this before start_all_departments().
    """
    dept = Department(name, role, description, start_fn, stop_fn, status_fn)
    with _registry_lock:
        _departments[name] = dept
        log.info("Department registered: %s (%s)", name, role)
    return dept


def start_all_departments() -> None:
    """Start all registered departments. Call once from post_init."""
    with _registry_lock:
        depts = list(_departments.values())
    for dept in depts:
        dept.start()
    log.info("✅ All %d AI departments started", len(depts))


def stop_all_departments() -> None:
    """Stop all registered departments cleanly."""
    with _registry_lock:
        depts = list(_departments.values())
    for dept in depts:
        dept.stop()


def get_all_status() -> list[dict]:
    """Return status for all registered departments."""
    with _registry_lock:
        depts = list(_departments.values())
    return [dept.get_status() for dept in depts]


def get_department(name: str) -> Department | None:
    with _registry_lock:
        return _departments.get(name)


def list_departments() -> list[str]:
    with _registry_lock:
        return list(_departments.keys())


# ── Bootstrap (called from main.py post_init) ─────────────────────────────────

def bootstrap_departments() -> None:
    """
    Register and start all built-in AI departments.
    This is the ONLY place department initialization happens.
    main.py calls this once — future departments just add a register() call here.
    """
    from services.testaudit_core      import start_testaudit_core, stop_testaudit_core
    from services.executive_assistant import start_executive_assistant, stop_executive_assistant
    from services.community_manager   import start_community_manager, stop_community_manager
    from services.channel_manager     import start_channel_manager, stop_channel_manager
    from services.customer_success    import start_customer_success, stop_customer_success

    # Core intelligence layer
    register(
        name="TestAudit Core",
        role="Chief Operations & Executive Intelligence",
        description=(
            "Continuous health monitoring, risk prediction, operational memory, "
            "and decision engine. Runs every 10 minutes. Never sleeps."
        ),
        start_fn=start_testaudit_core,
        stop_fn=stop_testaudit_core,
    )

    # Executive assistant
    register(
        name="Executive Assistant",
        role="CEO Reporting & Intelligence Briefing",
        description=(
            "Delivers Morning Brief (08:00 UTC), Evening Brief (20:00 UTC), "
            "Weekly Report (Monday 09:00 UTC), and Critical Alerts to the CEO."
        ),
        start_fn=start_executive_assistant,
        stop_fn=stop_executive_assistant,
    )

    # Community manager
    register(
        name="Community Manager",
        role="Telegram Group Engagement Manager",
        description=(
            "Monitors group activity. Starts AI-powered discussions when quiet. "
            "Backs off when naturally active. Max 8 posts/day, min 45min gap."
        ),
        start_fn=start_community_manager,
        stop_fn=stop_community_manager,
    )

    # Channel manager
    register(
        name="Channel Manager",
        role="Official Channel Content Manager",
        description=(
            "Posts 10–15 educational pieces per day to the official Telegram channel. "
            "Covers AI education, tutorials, productivity, security, and inspiration. "
            "Rotates categories. Never repeats content within 72h."
        ),
        start_fn=start_channel_manager,
        stop_fn=stop_channel_manager,
    )

    # Customer success
    register(
        name="Customer Success",
        role="User Engagement & Retention Manager",
        description=(
            "Daily check (14:00 UTC) for inactive users and onboarding dropouts. "
            "Sends summary to CEO. Never auto-messages users — all outreach requires CEO action."
        ),
        start_fn=start_customer_success,
        stop_fn=stop_customer_success,
    )

    # Start all registered departments
    start_all_departments()

    log.info(
        "🏢 FundzAiBot AI Company initialized — %d departments active",
        len(_departments),
    )
