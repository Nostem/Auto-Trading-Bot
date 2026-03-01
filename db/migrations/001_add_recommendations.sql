-- Migration 001: Add recommendations table and executor param defaults
-- Run against an existing database that already has the base schema.sql applied.
-- Safe to re-run (uses IF NOT EXISTS and ON CONFLICT DO NOTHING).

CREATE TABLE IF NOT EXISTS recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    setting_key VARCHAR(100) NOT NULL,
    current_value TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    trigger VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    denial_reason TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recommendations_created_at ON recommendations(created_at DESC);

-- Add default executor params to settings (won't overwrite existing values)
INSERT INTO settings (key, value) VALUES
    ('bond_stop_loss_cents', '0.06'),
    ('stop_loss_threshold', '0.50'),
    ('btc_take_profit_pct', '0.30'),
    ('mm_max_hold_hours', '4'),
    ('bond_pre_expiry_sec', '300'),
    ('mm_pre_expiry_sec', '600'),
    ('btc_pre_expiry_sec', '60')
ON CONFLICT (key) DO NOTHING;
