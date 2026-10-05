CREATE TABLE IF NOT EXISTS line_registration_sessions (
    line_user_id VARCHAR(80) PRIMARY KEY,
    step VARCHAR(30) NOT NULL,
    license_plate VARCHAR(40),
    phone VARCHAR(30),
    province VARCHAR(80),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE line_registration_sessions
    ADD COLUMN IF NOT EXISTS province VARCHAR(80);

CREATE INDEX IF NOT EXISTS idx_line_registration_sessions_updated_at
    ON line_registration_sessions(updated_at);
