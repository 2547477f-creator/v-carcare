CREATE TABLE IF NOT EXISTS system_settings (
    setting_key VARCHAR(80) PRIMARY KEY,
    setting_value TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
