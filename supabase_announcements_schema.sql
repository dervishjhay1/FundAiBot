-- FundzAiBot — Announcements table schema
-- Run this ONCE in the Supabase SQL Editor to enable the pinned announcement system.
-- Safe to re-run (uses IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS announcements (
    id          BIGSERIAL    PRIMARY KEY,
    message     TEXT         NOT NULL,
    photo_url   TEXT         DEFAULT '',
    is_active   BOOLEAN      DEFAULT TRUE,
    set_by      BIGINT       DEFAULT 0,        -- admin user_id who set it (0 = system default)
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast active-announcement lookup
CREATE INDEX IF NOT EXISTS idx_announcements_active
    ON announcements (is_active, created_at DESC);

-- Auto-update updated_at on row change
CREATE OR REPLACE FUNCTION update_announcements_timestamp()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_announcements_timestamp ON announcements;
CREATE TRIGGER set_announcements_timestamp
    BEFORE UPDATE ON announcements
    FOR EACH ROW EXECUTE FUNCTION update_announcements_timestamp();

ALTER TABLE announcements DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE announcements IS
    'FundzAiBot pinned announcement store. Managed via /pin, /unpin, /updateannouncement.';
