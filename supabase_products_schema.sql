-- Fundz Product Registry — Supabase Schema
-- Run this once in your Supabase SQL editor.
-- The product_registry.py service upserts products here and loads them on startup.

CREATE TABLE IF NOT EXISTS fundz_products (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          text        UNIQUE NOT NULL,
    name                text        NOT NULL,
    description         text        DEFAULT '',
    target_audience     text        DEFAULT '',
    status              text        DEFAULT 'planned'
                            CHECK (status IN ('active', 'beta', 'planned', 'deprecated')),
    features            jsonb       DEFAULT '[]',
    channel_categories  jsonb       DEFAULT '[]',
    cross_promote_with  jsonb       DEFAULT '[]',
    launched_at         timestamptz,
    created_at          timestamptz DEFAULT now(),
    updated_at          timestamptz DEFAULT now()
);

-- Index for status queries (get_active_products is called frequently)
CREATE INDEX IF NOT EXISTS fundz_products_status_idx
    ON fundz_products (status);

-- Enable Row Level Security (service key bypasses RLS automatically)
ALTER TABLE fundz_products ENABLE ROW LEVEL SECURITY;

-- Community Intelligence Insights (produced by Community Manager)
CREATE TABLE IF NOT EXISTS fundz_community_insights (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    topic       text        NOT NULL,
    frequency   int         DEFAULT 1,
    category    text        DEFAULT 'general',  -- question | suggestion | complaint | praise
    product_id  text        REFERENCES fundz_products(product_id) ON DELETE SET NULL,
    first_seen  timestamptz DEFAULT now(),
    last_seen   timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fundz_community_insights_topic_idx
    ON fundz_community_insights (topic);

CREATE INDEX IF NOT EXISTS fundz_community_insights_freq_idx
    ON fundz_community_insights (frequency DESC);

ALTER TABLE fundz_community_insights ENABLE ROW LEVEL SECURITY;
