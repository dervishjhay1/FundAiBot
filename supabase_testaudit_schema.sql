-- FundzAiBot — TestAudit & Enterprise Intelligence Schema Extension
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New Query → Paste → Run)
-- Safe to run multiple times (IF NOT EXISTS guards everywhere)

-- ── TestAudit Operational Memory ──────────────────────────────────────────────
-- Stores every operational event TestAudit observes and learns from.
CREATE TABLE IF NOT EXISTS testaudit_memory (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,          -- 'health_check', 'risk_alert', 'action_taken', 'ceo_decision', etc.
    category        TEXT DEFAULT 'general', -- 'operations', 'community', 'channel', 'customer', 'security', 'ai'
    title           TEXT NOT NULL,
    detail          JSONB DEFAULT '{}',     -- structured context
    confidence      FLOAT DEFAULT 1.0,     -- 0.0 – 1.0
    outcome         TEXT DEFAULT NULL,      -- 'resolved', 'escalated', 'pending', 'ignored'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_testaudit_memory_type    ON testaudit_memory(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_testaudit_memory_cat     ON testaudit_memory(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_testaudit_memory_created ON testaudit_memory(created_at DESC);

-- ── Company Health Log ────────────────────────────────────────────────────────
-- Historical record of every health score calculation.
CREATE TABLE IF NOT EXISTS company_health_log (
    id              BIGSERIAL PRIMARY KEY,
    score           FLOAT NOT NULL,         -- 0–100
    tier            TEXT NOT NULL,          -- 'excellent','healthy','attention','at_risk','critical'
    breakdown       JSONB DEFAULT '{}',     -- per-dimension scores
    active_users    INT DEFAULT 0,
    error_count     INT DEFAULT 0,
    ai_providers_ok INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_health_log_created ON company_health_log(created_at DESC);

-- ── Product Improvement Backlog ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS testaudit_backlog (
    id              BIGSERIAL PRIMARY KEY,
    category        TEXT NOT NULL,           -- 'bug','feature','performance','security','ux'
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    priority        TEXT DEFAULT 'medium',   -- 'critical','high','medium','low'
    impact          TEXT DEFAULT 'medium',
    difficulty      TEXT DEFAULT 'medium',
    confidence      FLOAT DEFAULT 0.8,
    status          TEXT DEFAULT 'open',     -- 'open','in_progress','done','dismissed'
    source          TEXT DEFAULT 'testaudit',-- 'user_feedback','testaudit','ceo','system'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backlog_status   ON testaudit_backlog(status, priority);
CREATE INDEX IF NOT EXISTS idx_backlog_created  ON testaudit_backlog(created_at DESC);

-- ── Channel Posts Log ─────────────────────────────────────────────────────────
-- Track every post sent to the channel to avoid repetition.
CREATE TABLE IF NOT EXISTS channel_posts_log (
    id              BIGSERIAL PRIMARY KEY,
    category        TEXT NOT NULL,          -- 'ai_education','tutorial','feature','productivity','tip','news','quote'
    topic           TEXT NOT NULL,
    posted_at       TIMESTAMPTZ DEFAULT NOW(),
    message_id      BIGINT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_posts_created ON channel_posts_log(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_posts_cat     ON channel_posts_log(category, posted_at DESC);

-- ── CEO Approval Queue ────────────────────────────────────────────────────────
-- Actions that require CEO review before execution.
CREATE TABLE IF NOT EXISTS ceo_approval_queue (
    id              BIGSERIAL PRIMARY KEY,
    action_type     TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    payload         JSONB DEFAULT '{}',
    confidence      FLOAT DEFAULT 0.0,
    risk_level      TEXT DEFAULT 'medium',
    status          TEXT DEFAULT 'pending', -- 'pending','approved','rejected','expired'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_approval_status  ON ceo_approval_queue(status, created_at DESC);

-- ── Add missing columns to announcements (safe, idempotent) ──────────────────
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS schedule_at TIMESTAMPTZ DEFAULT NULL;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal';
