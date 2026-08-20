-- Synchronise the original schema dump with the tables and columns used by
-- the current Flask application.  This migration is idempotent and is safe to
-- run against an existing v_carcare database.

ALTER TABLE service_orders
    ADD COLUMN IF NOT EXISTS damage_note TEXT;

ALTER TABLE staff_attendance
    ADD COLUMN IF NOT EXISTS status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS late_minutes INTEGER NOT NULL DEFAULT 0;

UPDATE staff_attendance
SET status = CASE
    WHEN check_in_at::time <= TIME '08:00' THEN 'on_time'
    ELSE 'late'
END
WHERE status IS NULL;

ALTER TABLE staff_attendance
    ALTER COLUMN status SET DEFAULT 'on_time';

CREATE TABLE IF NOT EXISTS face_profiles (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE CASCADE,
    app_user_id BIGINT REFERENCES app_users(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    embedding BYTEA NOT NULL,
    model_name VARCHAR(80) NOT NULL DEFAULT 'Facenet512',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (staff_id IS NOT NULL OR app_user_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_face_profiles_app_user_id
    ON face_profiles(app_user_id);

CREATE TABLE IF NOT EXISTS promotions (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    description TEXT,
    discount_type VARCHAR(10) NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
    discount_value NUMERIC(10,2) NOT NULL CHECK (discount_value >= 0),
    starts_at DATE,
    ends_at DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS staff_withdrawals (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT NOT NULL REFERENCES staff(id),
    request_amount NUMERIC(10,2) NOT NULL CHECK (request_amount > 0),
    approved_amount NUMERIC(10,2) CHECK (approved_amount IS NULL OR approved_amount > 0),
    reason TEXT,
    note TEXT,
    withdraw_week INTEGER NOT NULL CHECK (withdraw_week BETWEEN 1 AND 53),
    withdraw_year INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'paid', 'cancelled')),
    approved_by BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_staff_withdrawals_staff_week
    ON staff_withdrawals(staff_id, withdraw_year, withdraw_week);
