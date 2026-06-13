-- FundAiBot — Admin table schema
-- Run this ONCE in the Supabase SQL Editor to enable multi-admin support.
-- Safe to re-run (uses IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS admins (
    user_id    BIGINT      PRIMARY KEY,
    role       TEXT        NOT NULL DEFAULT 'admin',  -- 'owner' | 'admin'
    added_by   BIGINT,
    username   TEXT        DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast role lookups
CREATE INDEX IF NOT EXISTS idx_admins_role ON admins (role);

-- Comment
COMMENT ON TABLE admins IS
    'FundAiBot multi-admin store. Managed via /admin_addadmin and /admin_removeadmin.';
