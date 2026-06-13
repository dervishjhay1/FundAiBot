-- FundzAiBot — Language column migration
-- Run this once in Supabase SQL Editor to add language support.
-- Safe to run multiple times (idempotent).

ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'en';

-- Index for fast language-based queries
CREATE INDEX IF NOT EXISTS idx_users_language ON users(language);

-- Verify
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'language';
