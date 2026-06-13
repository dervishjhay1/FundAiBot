-- FundAiBot — Supabase Schema
-- Run this once in the Supabase SQL Editor to create all tables and functions.
-- Dashboard → SQL Editor → New Query → Paste → Run

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGINT PRIMARY KEY,
    first_name      TEXT DEFAULT '',
    last_name       TEXT DEFAULT '',
    username        TEXT DEFAULT '',
    is_vip          BOOLEAN DEFAULT FALSE,
    vip_tier        TEXT DEFAULT NULL,
    vip_expires_at  TIMESTAMPTZ DEFAULT NULL,
    ai_style        TEXT DEFAULT 'default',
    is_banned       BOOLEAN DEFAULT FALSE,
    ban_reason      TEXT DEFAULT NULL,
    referral_code   TEXT UNIQUE,
    referred_by     BIGINT DEFAULT NULL,
    notifications   BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_seen       TIMESTAMPTZ DEFAULT NOW()
);

-- ── Credits ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_credits (
    user_id         BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    chat_today      INT DEFAULT 0,
    image_today     INT DEFAULT 0,
    chat_total      INT DEFAULT 0,
    image_total     INT DEFAULT 0,
    bonus_chat      INT DEFAULT 0,
    bonus_image     INT DEFAULT 0,
    last_reset      DATE DEFAULT CURRENT_DATE
);

-- ── Conversation memory ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('system','user','assistant')),
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Image history ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS image_history (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    prompt          TEXT NOT NULL,
    style           TEXT DEFAULT 'realistic',
    model           TEXT,
    image_url       TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Referrals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS referrals (
    id              BIGSERIAL PRIMARY KEY,
    referrer_id     BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
    referred_id     BIGINT REFERENCES users(user_id) ON DELETE CASCADE UNIQUE,
    rewarded        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Error logs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS error_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT DEFAULT NULL,
    error_type      TEXT,
    message         TEXT,
    context         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_conversations_user    ON conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_image_history_user    ON image_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer    ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_error_logs_created    ON error_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_vip             ON users(is_vip) WHERE is_vip = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_banned          ON users(is_banned) WHERE is_banned = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_last_seen       ON users(last_seen DESC);

-- ── Atomic increment RPC functions ────────────────────────────────────────────
-- These prevent race conditions when multiple users send messages simultaneously.
-- Called by services/database.py; falls back to read+patch if the RPC is not present.

CREATE OR REPLACE FUNCTION increment_chat(uid BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- Reset daily counter if it's a new day
    UPDATE user_credits
    SET
        chat_today  = CASE WHEN last_reset < CURRENT_DATE THEN 1 ELSE chat_today + 1 END,
        chat_total  = chat_total + 1,
        image_today = CASE WHEN last_reset < CURRENT_DATE THEN 0 ELSE image_today END,
        last_reset  = CURRENT_DATE
    WHERE user_id = uid;

    -- If no row existed yet, create it
    IF NOT FOUND THEN
        INSERT INTO user_credits (user_id, chat_today, chat_total, last_reset)
        VALUES (uid, 1, 1, CURRENT_DATE)
        ON CONFLICT (user_id) DO UPDATE
        SET chat_today = user_credits.chat_today + 1,
            chat_total = user_credits.chat_total + 1;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION increment_image(uid BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE user_credits
    SET
        image_today = CASE WHEN last_reset < CURRENT_DATE THEN 1 ELSE image_today + 1 END,
        image_total = image_total + 1,
        chat_today  = CASE WHEN last_reset < CURRENT_DATE THEN 0 ELSE chat_today END,
        last_reset  = CURRENT_DATE
    WHERE user_id = uid;

    IF NOT FOUND THEN
        INSERT INTO user_credits (user_id, image_today, image_total, last_reset)
        VALUES (uid, 1, 1, CURRENT_DATE)
        ON CONFLICT (user_id) DO UPDATE
        SET image_today = user_credits.image_today + 1,
            image_total = user_credits.image_total + 1;
    END IF;
END;
$$;

-- ── Row Level Security ────────────────────────────────────────────────────────
-- The bot uses the service_role key which bypasses RLS automatically.
-- RLS is disabled on all bot tables to avoid accidental permission errors.
-- Do NOT enable RLS unless you fully configure policies for the service_role.

ALTER TABLE users          DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_credits   DISABLE ROW LEVEL SECURITY;
ALTER TABLE conversations  DISABLE ROW LEVEL SECURITY;
ALTER TABLE image_history  DISABLE ROW LEVEL SECURITY;
ALTER TABLE referrals      DISABLE ROW LEVEL SECURITY;
ALTER TABLE error_logs     DISABLE ROW LEVEL SECURITY;

-- ── Admin accounts (multi-admin system) ───────────────────────────────────────
-- Secondary admins added via /admin_addadmin. The primary owner (ADMIN_USER_ID)
-- never appears here — they are identified by the env var, not the DB.
CREATE TABLE IF NOT EXISTS admin_accounts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT UNIQUE NOT NULL,
    added_by    BIGINT DEFAULT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_accounts_user ON admin_accounts(user_id);
ALTER TABLE admin_accounts DISABLE ROW LEVEL SECURITY;

-- ── Announcements (pinned messages system) ────────────────────────────────────
-- Admin creates pinned messages shown to users on /start.
-- Only one announcement is active at a time (is_active = true).
CREATE TABLE IF NOT EXISTS announcements (
    id          BIGSERIAL PRIMARY KEY,
    message     TEXT NOT NULL,
    photo_url   TEXT DEFAULT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_by  BIGINT DEFAULT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_announcements_active ON announcements(is_active) WHERE is_active = TRUE;
ALTER TABLE announcements DISABLE ROW LEVEL SECURITY;
