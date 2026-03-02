CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market_id VARCHAR(255) NOT NULL,
    market_title TEXT NOT NULL,
    strategy VARCHAR(50) NOT NULL CHECK (strategy IN ('bond', 'market_making', 'btc_15min', 'weather')),
    side VARCHAR(10) NOT NULL CHECK (side IN ('yes', 'no')),
    size INTEGER NOT NULL,
    entry_price DECIMAL(6,4) NOT NULL,
    exit_price DECIMAL(6,4),
    gross_pnl DECIMAL(10,2),
    fees DECIMAL(10,2),
    net_pnl DECIMAL(10,2),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    entry_reasoning TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    market_id VARCHAR(255) NOT NULL UNIQUE,
    market_title TEXT NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    size INTEGER NOT NULL,
    entry_price DECIMAL(6,4) NOT NULL,
    current_price DECIMAL(6,4),
    unrealized_pnl DECIMAL(10,2),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reflections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trade_id UUID REFERENCES trades(id),
    summary TEXT NOT NULL,
    what_worked TEXT,
    what_failed TEXT,
    confidence_score INTEGER CHECK (confidence_score BETWEEN 1 AND 10),
    strategy_suggestion TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weekly_reflections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    total_trades INTEGER,
    win_rate DECIMAL(5,2),
    net_pnl DECIMAL(10,2),
    top_strategy VARCHAR(50),
    summary TEXT,
    key_learnings TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- Default settings
INSERT INTO settings (key, value) VALUES
    ('bot_enabled', 'true'),
    ('bond_strategy_enabled', 'true'),
    ('market_making_enabled', 'true'),
    ('btc_strategy_enabled', 'true'),
    ('max_position_pct', '0.15'),
    ('daily_loss_limit_pct', '0.03'),
    ('current_bankroll', '5000'),
    ('sizing_mode', 'fixed_dollar'),
    ('fixed_trade_amount', '5'),
    ('bond_stop_loss_cents', '0.06'),
    ('stop_loss_threshold', '0.50'),
    ('btc_take_profit_pct', '0.30'),
    ('mm_max_hold_hours', '4'),
    ('bond_pre_expiry_sec', '300'),
    ('mm_pre_expiry_sec', '600'),
    ('btc_pre_expiry_sec', '60'),
    ('weather_strategy_enabled', 'false'),
    ('weather_pre_expiry_sec', '300')
ON CONFLICT (key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_reflections_trade_id ON reflections(trade_id);
CREATE INDEX IF NOT EXISTS idx_reflections_created_at ON reflections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recommendations_created_at ON recommendations(created_at DESC);
