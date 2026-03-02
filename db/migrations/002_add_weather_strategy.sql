-- Migration 002: Add weather strategy support
-- Run against an existing database that already has schema.sql + 001 applied.
-- Safe to re-run (uses IF NOT EXISTS and ON CONFLICT DO NOTHING).

-- 1. Update trades table strategy CHECK constraint to allow 'weather'
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_strategy_check;
ALTER TABLE trades ADD CONSTRAINT trades_strategy_check
    CHECK (strategy IN ('bond', 'market_making', 'btc_15min', 'weather'));

-- 2. Add weather settings defaults
INSERT INTO settings (key, value) VALUES
    ('weather_strategy_enabled', 'false'),
    ('weather_pre_expiry_sec', '300')
ON CONFLICT (key) DO NOTHING;
