-- FundzMarket — Seller Applications Schema
-- Run this in your Supabase SQL editor BEFORE deploying the seller feature.
-- This is shared between FundzAiBot (TestAudit monitoring) and FundzMarket (user-facing).

-- ── Seller Applications ───────────────────────────────────────────────────────
-- Every buyer who taps "Become a Seller" creates a pending row here.
-- TestAudit/CEO monitors these via the CEO Office context and approval queue.

CREATE TABLE IF NOT EXISTS seller_applications (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         bigint      NOT NULL,          -- Telegram user ID
    store_name      text        NOT NULL,
    business_name   text        DEFAULT '',
    description     text        DEFAULT '',
    category        text        DEFAULT 'general', -- e.g. electronics, fashion, food
    phone           text        DEFAULT '',
    country         text        DEFAULT '',
    status          text        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    reject_reason   text        DEFAULT '',
    reviewed_by     bigint,                        -- Telegram ID of admin who reviewed
    reviewed_at     timestamptz,
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);

-- Index: most frequent query pattern is pending applications
CREATE INDEX IF NOT EXISTS seller_applications_status_idx
    ON seller_applications (status, created_at DESC);

-- Index: look up applications by user
CREATE INDEX IF NOT EXISTS seller_applications_user_idx
    ON seller_applications (user_id);

-- Enable RLS (service key bypasses automatically)
ALTER TABLE seller_applications ENABLE ROW LEVEL SECURITY;

-- ── Sellers (approved sellers get a row here) ─────────────────────────────────
-- Populated when TestAudit/admin approves an application.
-- FundzMarket checks this table to determine if a user is a seller.

CREATE TABLE IF NOT EXISTS sellers (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         bigint      UNIQUE NOT NULL,   -- Telegram user ID
    store_name      text        NOT NULL,
    business_name   text        DEFAULT '',
    description     text        DEFAULT '',
    category        text        DEFAULT 'general',
    rating          numeric(3,2) DEFAULT 5.00,
    total_sales     int         DEFAULT 0,
    is_active       boolean     DEFAULT true,
    is_verified     boolean     DEFAULT false,     -- TestAudit/CEO verified badge
    application_id  uuid        REFERENCES seller_applications(id) ON DELETE SET NULL,
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sellers_user_idx ON sellers (user_id);
CREATE INDEX IF NOT EXISTS sellers_active_idx ON sellers (is_active, category);

ALTER TABLE sellers ENABLE ROW LEVEL SECURITY;

-- ── Helper: is_seller(user_id) ────────────────────────────────────────────────
-- Convenience function so other tables/services can check seller status.

CREATE OR REPLACE FUNCTION is_seller(p_user_id bigint)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM sellers WHERE user_id = p_user_id AND is_active = true
    );
$$ LANGUAGE sql STABLE;

-- ── Auto-update updated_at ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER seller_applications_updated_at
    BEFORE UPDATE ON seller_applications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER sellers_updated_at
    BEFORE UPDATE ON sellers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
