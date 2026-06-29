-- ─────────────────────────────────────────────────────────────────────────────
-- CEO Office tables — EOS v2.0
-- Run once in Supabase SQL Editor (safe to re-run: IF NOT EXISTS throughout)
-- ─────────────────────────────────────────────────────────────────────────────

-- CEO persistent memory: preferences, decisions, registered tokens (masked)
CREATE TABLE IF NOT EXISTS ceo_office_memory (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- CEO Office conversation history (restores context across sessions)
CREATE TABLE IF NOT EXISTS ceo_office_history (
  id         BIGSERIAL PRIMARY KEY,
  role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ceo_office_history_ts_idx
  ON ceo_office_history (created_at DESC);

-- Autonomous Operations Mode event log
CREATE TABLE IF NOT EXISTS testaudit_autonomous_log (
  id         BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  title      TEXT,
  detail     JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aom_event_type_idx ON testaudit_autonomous_log (event_type);
CREATE INDEX IF NOT EXISTS aom_created_idx    ON testaudit_autonomous_log (created_at DESC);

-- Fundz Product Registry
CREATE TABLE IF NOT EXISTS fundz_products (
  product_id         TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  description        TEXT,
  target_audience    TEXT,
  status             TEXT DEFAULT 'planned'
                     CHECK (status IN ('active', 'beta', 'planned', 'deprecated')),
  features           JSONB DEFAULT '[]',
  channel_categories JSONB DEFAULT '[]',
  cross_promote_with JSONB DEFAULT '[]',
  launched_at        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);
