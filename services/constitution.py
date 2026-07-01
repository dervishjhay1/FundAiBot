"""
Fundz Company Ltd. — Official Constitution v2.1.0
Effective: 2026-07-01

This module is the highest operational authority for all Fundz Company
products, departments, automated systems, and future managers.

TestAudit (Chief Operations Manager) reads from this Constitution to guide
every operational decision. Any system that imports this module receives
constitutional authority and is expected to honour it.

Architecture rule: The Constitution is read-only at runtime. Future versions
are released as code deployments. No runtime code modifies this module.

Usage:
    from services.constitution import get_full_constitution, get_mandate, check_compliance
"""

from __future__ import annotations

from utils.logger import get_logger

log = get_logger(__name__)

# ── Identity ──────────────────────────────────────────────────────────────────

CONSTITUTION_VERSION       = "2.1.0"
CONSTITUTION_EFFECTIVE_DATE = "2026-07-01"
COMPANY_NAME               = "Fundz Company Ltd."
COMPANY_REGISTRATION       = "Fundz-Ltd-001"

# ── Article 1: Company Identity ───────────────────────────────────────────────

COMPANY_MISSION = (
    "To build AI-powered tools that empower individuals, creators, and businesses "
    "to work smarter, earn more, and grow faster — accessible to everyone."
)

COMPANY_VISION = (
    "To become the leading AI productivity ecosystem for the next generation of "
    "digital entrepreneurs and creators."
)

CORE_VALUES: dict[str, str] = {
    "excellence":    "Every product, feature, and interaction must be excellent.",
    "reliability":   "Systems must be stable, predictable, and always available.",
    "transparency":  "Operations, decisions, and status must be visible and auditable.",
    "user_first":    "Every decision prioritises the user experience above internal convenience.",
    "autonomy":      "Systems should operate independently without constant human intervention.",
    "growth":        "Products evolve continuously based on data, feedback, and strategy.",
}

# ── Article 2: Governance Structure ───────────────────────────────────────────

GOVERNANCE: dict[str, str] = {
    "board":               "Fundz Company Ltd. Board",
    "chief_executive":     "Chief Executive Officer — sets company strategy and direction",
    "chief_operations":    "TestAudit — AI Chief Operations Manager (automated)",
    "product_registry":    "Central registry for all company products",
    "channel_authority":   "Channel Manager — all Telegram communications",
    "executive_assistant": "Executive Assistant — CEO Office operations",
}

# ── Article 3: Product Standards ─────────────────────────────────────────────

PRODUCT_STANDARDS: dict[str, str] = {
    "registration":        "All products MUST be registered in the Company Registry before launch.",
    "status_declaration":  "Every product MUST declare a status: active | beta | planned | deprecated.",
    "roadmap":             "Every active product MUST maintain a published roadmap.",
    "health_reporting":    "Products report operational status to TestAudit on every health cycle.",
    "deprecation_notice":  "Products require 30-day advance notice before deprecation.",
    "cross_promotion":     "Products declare compatible cross-promotion partners in their registry entry.",
}

# ── Article 4: Operational Standards ─────────────────────────────────────────

OPERATIONAL_STANDARDS: dict[str, str] = {
    "uptime_target":      "99.5% monthly uptime — any degradation is logged and reported.",
    "response_time":      "AI responses must complete within 45 seconds.",
    "error_handling":     "Every exception must be caught, logged, and non-fatal to the system.",
    "data_persistence":   "No runtime data stored on local disk. Supabase (cloud) only.",
    "deployment":         "Railway is the sole production environment. No exceptions.",
    "version_control":    "GitHub is the source of truth. Every change is committed.",
    "health_monitoring":  "TestAudit runs health cycles every 5 minutes.",
    "ai_availability":    "Minimum one AI provider must be active at all times.",
    "no_duplicate_logic": "Duplicate handlers, commands, and callbacks are prohibited.",
    "singleton_polling":  "Only one bot instance may poll Telegram at any time.",
}

# ── Article 5: TestAudit Mandate ─────────────────────────────────────────────
# TestAudit is the Chief Operations Manager of Fundz Company Ltd.
# It has constitutional authority to monitor, audit, and report on ALL products.

TESTAUDIT_MANDATE: dict = {
    "role":             "Chief Operations Manager — Fundz Company Ltd.",
    "authority_level":  "Constitutional",
    "reports_to":       "Chief Executive Officer",
    "scope":            "All current and future Fundz Company products and departments",
    "responsibilities": [
        "Monitor system health every 5 minutes",
        "Detect and log all errors and anomalies",
        "Audit all products for constitutional compliance",
        "Generate operational reports for CEO review",
        "Maintain the Fundz Company Knowledge Base",
        "Enforce the Fundz Company Constitution",
        "Support CEO decision-making with data and recommendations",
        "Track product roadmaps and backlogs",
        "Manage departmental KPIs and targets",
        "Onboard and orient new products into the ecosystem",
    ],
    "kpis": {
        "health_score_target":  90.0,
        "error_rate_threshold": 20,
        "uptime_minimum":       99.5,
        "response_time_max":    45,
    },
}

# ── Article 6: Manager Framework ─────────────────────────────────────────────
# Framework for registering human or AI managers in the Fundz ecosystem.

MANAGER_FRAMEWORK: dict = {
    "registration_required":  True,
    "mandatory_fields":       ["manager_id", "name", "department", "product", "responsibilities"],
    "reporting_cycle":        "weekly",
    "kpi_required":           True,
    "appointment_authority":  "Chief Executive Officer",
    "performance_review":     "Monthly — conducted by TestAudit and reported to CEO",
}

# ── Article 7: Communication Standards ───────────────────────────────────────

COMMUNICATION_STANDARDS: dict[str, str] = {
    "tone":                "Professional, clear, concise, and friendly.",
    "default_language":    "English — localised per user preference.",
    "error_messages":      "Never expose raw technical errors to users. Always give actionable guidance.",
    "channel_frequency":   "Maximum 3 posts per day per Telegram channel.",
    "dm_policy":           "No unsolicited direct messages. User must initiate or opt-in.",
    "broadcast_approval":  "Mass broadcasts require CEO approval before delivery.",
}

# ── Article 8: AI Standards ───────────────────────────────────────────────────

AI_STANDARDS: dict = {
    "provider_priority":  ["OpenRouter", "Gemini", "HuggingFace"],
    "min_active":         1,
    "max_tokens":         1500,
    "timeout_seconds":    45,
    "fallback_required":  True,
    "unavailable_action": (
        "When all providers fail, log the failure, notify the admin, "
        "and return a user-friendly message without technical details."
    ),
}

# ── Article 9: Future Products Registry ──────────────────────────────────────
# Products planned for the Fundz Company ecosystem.

PLANNED_PRODUCTS: list[dict] = [
    {
        "product_id":  "fundzaibot",
        "name":        "FundzAiBot",
        "status":      "active",
        "launched_at": "2025-01-01",
        "description": "AI-powered Telegram assistant — flagship Fundz product.",
    },
    {
        "product_id":  "fundzmarket",
        "name":        "FundzMarket",
        "status":      "planned",
        "launched_at": None,
        "description": "Digital marketplace for AI prompts, templates, and tools.",
    },
    {
        "product_id":  "fundz_academy",
        "name":        "Fundz Academy",
        "status":      "planned",
        "launched_at": None,
        "description": "AI education platform — courses, certifications, and community.",
    },
]

# ── Public API ────────────────────────────────────────────────────────────────

def get_version() -> str:
    """Return the Constitution version string."""
    return f"Fundz Company Constitution v{CONSTITUTION_VERSION} (effective {CONSTITUTION_EFFECTIVE_DATE})"


def get_mandate(role: str = "testaudit") -> dict:
    """Return the constitutional mandate for a given role."""
    if role == "testaudit":
        return TESTAUDIT_MANDATE.copy()
    return {}


def get_operational_standard(key: str) -> str | None:
    """Return a specific operational standard by key."""
    return OPERATIONAL_STANDARDS.get(key)


def get_core_values() -> dict[str, str]:
    """Return the company core values."""
    return CORE_VALUES.copy()


def get_product_standards() -> dict[str, str]:
    """Return the product registration standards."""
    return PRODUCT_STANDARDS.copy()


def get_ai_standards() -> dict:
    """Return the AI operational standards."""
    return AI_STANDARDS.copy()


def get_full_constitution() -> dict:
    """Return the complete Constitution as a structured dictionary."""
    return {
        "version":               CONSTITUTION_VERSION,
        "effective_date":        CONSTITUTION_EFFECTIVE_DATE,
        "company":               COMPANY_NAME,
        "registration":          COMPANY_REGISTRATION,
        "mission":               COMPANY_MISSION,
        "vision":                COMPANY_VISION,
        "values":                CORE_VALUES,
        "governance":            GOVERNANCE,
        "product_standards":     PRODUCT_STANDARDS,
        "operational_standards": OPERATIONAL_STANDARDS,
        "testaudit_mandate":     TESTAUDIT_MANDATE,
        "manager_framework":     MANAGER_FRAMEWORK,
        "communication":         COMMUNICATION_STANDARDS,
        "ai_standards":          AI_STANDARDS,
        "planned_products":      PLANNED_PRODUCTS,
    }


def check_compliance(check_type: str, value=None) -> tuple[bool, str]:
    """
    Check whether a condition meets constitutional standards.
    Returns (compliant: bool, reason: str).

    Supported check_type values:
      "ai_provider"  — value is the provider name string (or "none")
      "uptime"       — value is a float 0.0-1.0
      "error_rate"   — value is an integer count of errors per hour
      "response_time"— value is seconds (float or int)
    """
    if check_type == "ai_provider":
        compliant = value not in (None, "none", "")
        reason = (
            f"✅ AI provider '{value}' active — compliant with Article 8"
            if compliant
            else "❌ No AI provider available — violates Article 4 (Operational Standards) and Article 8 (AI Standards)"
        )
        return compliant, reason

    if check_type == "uptime":
        target = 99.5
        actual = float(value) if value is not None else 0.0
        compliant = actual >= target
        reason = (
            f"✅ Uptime {actual:.2f}% meets {target}% target"
            if compliant
            else f"❌ Uptime {actual:.2f}% below {target}% constitutional target"
        )
        return compliant, reason

    if check_type == "error_rate":
        threshold = TESTAUDIT_MANDATE["kpis"]["error_rate_threshold"]
        count = int(value) if value is not None else 0
        compliant = count < threshold
        reason = (
            f"✅ Error rate {count}/hr within threshold {threshold}"
            if compliant
            else f"❌ Error rate {count}/hr exceeds constitutional threshold {threshold}"
        )
        return compliant, reason

    if check_type == "response_time":
        limit = AI_STANDARDS["timeout_seconds"]
        secs = float(value) if value is not None else 999
        compliant = secs <= limit
        reason = (
            f"✅ Response time {secs:.1f}s within {limit}s limit"
            if compliant
            else f"❌ Response time {secs:.1f}s exceeds {limit}s constitutional limit"
        )
        return compliant, reason

    return True, f"No specific constitutional rule for check_type='{check_type}'"


# ── Startup log ───────────────────────────────────────────────────────────────
log.info("📜 %s loaded", get_version())
