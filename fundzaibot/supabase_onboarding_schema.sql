-- FundzAiBot — Onboarding Schema Extension
-- Run this in the Supabase SQL Editor AFTER supabase_schema.sql has been applied.
-- Dashboard → SQL Editor → New Query → Paste → Run

-- ── Onboarding tracking table ─────────────────────────────────────────────────
-- Tracks each user's onboarding status, community join state, and rewards.
CREATE TABLE IF NOT EXISTS onboarding (
    user_id               BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    onboarding_complete   BOOLEAN DEFAULT FALSE,
    channel_joined        BOOLEAN DEFAULT FALSE,
    group_joined          BOOLEAN DEFAULT FALSE,
    channel_reward_given  BOOLEAN DEFAULT FALSE,
    group_reward_given    BOOLEAN DEFAULT FALSE,
    referral_source       TEXT DEFAULT 'direct',   -- 'direct' | 'bot' | 'channel' | 'group' | 'referral'
    completed_at          TIMESTAMPTZ DEFAULT NULL,
    created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_onboarding_complete  ON onboarding(onboarding_complete);
CREATE INDEX IF NOT EXISTS idx_onboarding_channel   ON onboarding(channel_joined);
CREATE INDEX IF NOT EXISTS idx_onboarding_group     ON onboarding(group_joined);
CREATE INDEX IF NOT EXISTS idx_onboarding_source    ON onboarding(referral_source);

ALTER TABLE onboarding DISABLE ROW LEVEL SECURITY;
