-- Fundz Company Ltd. — Meetings Table
-- Run this in the Supabase SQL Editor to enable meeting scheduling.
--
-- TestAudit (Chief Operations Manager) stores all CEO meetings here.
-- Includes automatic reminders, agenda tracking, and post-meeting notes.

CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT        PRIMARY KEY,
    title         TEXT        NOT NULL,
    scheduled_at  TIMESTAMPTZ NOT NULL,
    agenda        TEXT        DEFAULT '',
    location      TEXT        DEFAULT 'Telegram CEO Office',
    status        TEXT        DEFAULT 'scheduled'   -- scheduled | completed | cancelled
                              CHECK (status IN ('scheduled', 'completed', 'cancelled')),
    notes         TEXT        DEFAULT '',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup of upcoming meetings
CREATE INDEX IF NOT EXISTS idx_meetings_scheduled_at
    ON meetings (scheduled_at ASC)
    WHERE status = 'scheduled';

-- Row Level Security (optional — enable if using per-user isolation)
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;

-- Service key bypasses RLS (used by the bot backend)
CREATE POLICY "service_key_full_access" ON meetings
    USING (true)
    WITH CHECK (true);
