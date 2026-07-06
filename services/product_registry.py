"""
FundzAiBot — Fundz Product Registry (TestAudit role)

Central registry for every product in the Fundz ecosystem.

TestAudit is the Operations Manager of the entire Fundz Company —
not just FundzAiBot. This module makes that possible without redesign.

Architecture:
  • Built-in products loaded on startup (no DB required for basic operation)
  • CEO registers new products at runtime via CEO Office or /testaudit
  • All products persist to Supabase `fundz_products` table
  • Channel Manager, Executive Chat, and Reports all query this registry
  • Adding a new Fundz product never requires changes to any other module

Status values:  active | beta | planned | deprecated
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY, BOT_NAME
from utils.logger import get_logger

log = get_logger(__name__)

# ── Built-in product catalog ──────────────────────────────────────────────────
# Always available — no database required. Supabase overlays CEO additions.

_BUILTIN_PRODUCTS: list[dict] = [
    {
        "product_id":         "fundzaibot",
        "name":               "FundzAiBot",
        "description": (
            "AI-powered Telegram assistant — chat with memory, multi-model AI "
            "(GPT-4o / Gemini / Claude), image generation, VIP subscriptions, "
            "referral rewards, and a full productivity suite."
        ),
        "target_audience":    "Telegram users, AI enthusiasts, creators, professionals",
        "status":             "active",
        "features": [
            "AI Chat with persistent memory",
            "AI Image Generation (SDXL)",
            "8 AI Personality Styles",
            "Multi-model: GPT-4o, Gemini, Claude",
            "VIP Plans via Telegram Stars",
            "Referral & Credits System",
            "Tools: Weather, Crypto, News, QR, Wiki",
        ],
        "channel_categories": ["ai_education", "tutorial", "feature", "productivity"],
        "cross_promote_with": ["fundzmarket", "fundz_academy"],
        "launched_at":        "2025-01-01",
    },
    {
        "product_id":         "fundzmarket",
        "name":               "FundzMarket",
        "description": (
            "Fundz Company's official Telegram marketplace for buying, selling, and "
            "discovering products across Africa. Phase 1 Foundation is LIVE — "
            "product listings, 18 categories, enterprise database, user registration, "
            "9-step listing wizard, search, wishlist, moderation queue, safety centre, "
            "and the complete enterprise architecture. Phase 2 (escrow, FundzPay, "
            "wallet, orders, delivery) is in planning."
        ),
        "target_audience":    "African buyers and sellers — electronics, phones, fashion, "
                              "digital products, services, real estate, vehicles, and more",
        "status":             "active",
        "phase":              "Phase 1 — Foundation",
        "github":             "https://github.com/dervishjhay1/FundzMarket",
        "features": [
            "🛍 Browse Products — 18 categories, full search",
            "➕ Sell Product — 9-step guided listing wizard",
            "👤 My Account — profile, stats, listings, wallet-ready",
            "❤️ Wishlist — save products for later",
            "🔔 Notifications — product approvals, announcements",
            "🔍 Search — keyword, category, price, location",
            "🛡 Safety Centre — tips, rules, fraud reporting",
            "💬 Support — direct CEO/admin escalation",
            "⚙️ Settings — language, notifications, privacy",
            "🗄 Enterprise DB — 13-table Supabase schema for millions of users",
            "⏳ Phase 2 planned: Escrow, FundzPay, Wallet, Delivery Tracking",
        ],
        "channel_categories": ["ecosystem_update", "feature", "milestone"],
        "cross_promote_with": ["fundzaibot", "fundz_academy"],
        "launched_at":        "2026-07-06",
        "launch_phase":       "Phase 1 Foundation v1.0.0",
        "railway_service":    "FundzMarket (separate Railway service)",
        "bot_username":       "FundzMarketBot",
    },
    {
        "product_id":         "fundz_academy",
        "name":               "Fundz Academy",
        "description": (
            "Structured AI education — courses, tutorials, and certifications "
            "for mastering prompt engineering, AI tools, and automation."
        ),
        "target_audience":    "AI beginners, professionals upskilling, students",
        "status":             "planned",
        "features": [
            "Self-paced AI courses",
            "Prompt engineering certification",
            "Weekly live workshops",
            "Community study groups",
            "Progress tracking & certificates",
        ],
        "channel_categories": ["ai_education", "tutorial", "inspiration"],
        "cross_promote_with": ["fundzaibot", "fundzmarket"],
        "launched_at":        None,
    },
]

# ── In-memory registry ─────────────────────────────────────────────────────────

_products:     dict[str, dict] = {}   # product_id → product dict
_lock          = threading.Lock()
_initialized   = False
_last_featured: str = ""              # product_id last featured in channel (rotation)


# ── Supabase helpers ───────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _load_from_supabase() -> None:
    """Overlay CEO-registered products on top of built-ins."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/fundz_products",
            headers=_hdrs(),
            params={"select": "*", "order": "created_at.asc"},
            timeout=(5, 10),
        )
        if r.status_code == 200:
            rows = r.json()
            with _lock:
                for row in rows:
                    pid = row.get("product_id")
                    if pid:
                        _products[pid] = row
            log.info("product_registry: loaded %d products from Supabase", len(rows))
    except Exception as exc:
        log.debug("product_registry._load_from_supabase: %s", exc)


def _persist_product(product: dict) -> None:
    """Upsert one product to Supabase (best-effort — never blocks startup)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        h = dict(_hdrs())
        h["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/fundz_products",
            headers=h,
            json=product,
            timeout=(5, 12),
        )
        if r.status_code not in (200, 201):
            log.debug("product_registry._persist HTTP %d: %s", r.status_code, r.text[:80])
    except Exception as exc:
        log.debug("product_registry._persist_product: %s", exc)


# ── Initialization ─────────────────────────────────────────────────────────────

def initialize() -> None:
    """Load built-in products, overlay Supabase data. Idempotent."""
    global _initialized
    if _initialized:
        return
    with _lock:
        for p in _BUILTIN_PRODUCTS:
            _products[p["product_id"]] = dict(p)
    _load_from_supabase()
    _initialized = True
    active_count = len([p for p in _products.values() if p.get("status") in ("active", "beta")])
    log.info(
        "✅ Product Registry initialized — %d products, %d active",
        len(_products), active_count,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def get_all_products() -> list[dict]:
    initialize()
    with _lock:
        return list(_products.values())


def get_active_products() -> list[dict]:
    """Return products with status 'active' or 'beta'."""
    return [p for p in get_all_products() if p.get("status") in ("active", "beta")]


def get_product(product_id: str) -> dict | None:
    initialize()
    with _lock:
        return dict(_products[product_id]) if product_id in _products else None


def register_product(
    product_id: str,
    name: str,
    description: str,
    target_audience: str = "",
    status: str = "planned",
    features: list[str] | None = None,
    channel_categories: list[str] | None = None,
    cross_promote_with: list[str] | None = None,
) -> dict:
    """
    Register a new Fundz product or update an existing one.
    CEO calls this via /testaudit → Products → Register.
    Persists to Supabase so the product survives Railway redeploys.
    """
    initialize()
    now = datetime.now(timezone.utc).isoformat()
    product = {
        "product_id":         product_id.lower().strip().replace(" ", "_"),
        "name":               name,
        "description":        description,
        "target_audience":    target_audience,
        "status":             status,
        "features":           features or [],
        "channel_categories": channel_categories or ["ai_education", "feature"],
        "cross_promote_with": cross_promote_with or [],
        "launched_at":        now if status == "active" else None,
        "created_at":         now,
        "updated_at":         now,
    }
    with _lock:
        _products[product["product_id"]] = product
    _persist_product(product)
    log.info("Product registered: %s (%s) status=%s", name, product_id, status)
    return product


def update_product_status(product_id: str, status: str) -> bool:
    """
    Change a product's operational status.
    Triggers 'launched_at' timestamp when first set to 'active'.
    """
    initialize()
    with _lock:
        if product_id not in _products:
            return False
        _products[product_id]["status"]     = status
        _products[product_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        if status == "active" and not _products[product_id].get("launched_at"):
            _products[product_id]["launched_at"] = datetime.now(timezone.utc).isoformat()
        product = dict(_products[product_id])
    _persist_product(product)
    log.info("Product status updated: %s → %s", product_id, status)
    return True


def get_next_product_to_feature() -> dict | None:
    """
    Return the next product to feature in the channel, rotating across active products.
    Used by Channel Manager to avoid one product dominating the feed.
    """
    global _last_featured
    active = get_active_products()
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    # Rotate: skip the last featured product
    candidates = [p for p in active if p["product_id"] != _last_featured]
    if not candidates:
        candidates = active
    import random
    chosen = random.choice(candidates)
    _last_featured = chosen["product_id"]
    return chosen


def get_cross_promotions(product_id: str) -> list[dict]:
    """Return active products cross-linked with the given product."""
    product = get_product(product_id)
    if not product:
        return []
    cross_ids = product.get("cross_promote_with", [])
    return [p for p in get_active_products() if p["product_id"] in cross_ids]


def format_registry_summary() -> str:
    """Structured summary for CEO reports and executive chat context."""
    initialize()
    all_p = get_all_products()
    if not all_p:
        return "PRODUCT_REGISTRY: empty"

    lines = [f"FUNDZ_ECOSYSTEM ({len(all_p)} products):"]
    for status in ("active", "beta", "planned", "deprecated"):
        group = [p for p in all_p if p.get("status") == status]
        for p in group:
            feat_n = len(p.get("features", []))
            lines.append(
                f"  [{status.upper():10}] {p['name']:20} | {feat_n} features "
                f"| {p.get('description', '')[:70]}"
            )
    return "\n".join(lines)


def format_product_detail(product_id: str) -> str:
    """Full detail for one product — used in CEO Office product discussions."""
    p = get_product(product_id)
    if not p:
        return f"Product '{product_id}' not found in registry."
    features_str = "\n".join(f"    • {f}" for f in p.get("features", []))
    cross = ", ".join(p.get("cross_promote_with", [])) or "none"
    return (
        f"<b>{p['name']}</b> ({p['product_id']})\n"
        f"Status: <b>{p['status'].upper()}</b>\n"
        f"Audience: {p.get('target_audience', 'N/A')}\n"
        f"Description: {p.get('description', 'N/A')}\n"
        f"Features:\n{features_str}\n"
        f"Cross-promote with: {cross}"
    )
