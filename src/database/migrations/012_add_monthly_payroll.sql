-- Monthly payroll is finalized on the first day of the following month.
-- A paid period is also the authoritative lock for its attendance records.
CREATE TABLE IF NOT EXISTS payroll_periods (
    period_start DATE PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid')),
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payroll_entries (
    id BIGSERIAL PRIMARY KEY,
    period_start DATE NOT NULL REFERENCES payroll_periods(period_start) ON DELETE RESTRICT,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE RESTRICT,
    work_days INTEGER NOT NULL DEFAULT 0,
    daily_wage NUMERIC(12,2) NOT NULL DEFAULT 0,
    base_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    attendance_bonus NUMERIC(12,2) NOT NULL DEFAULT 0,
    amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    finance_transaction_id BIGINT REFERENCES finance_transactions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period_start, staff_id)
);

CREATE INDEX IF NOT EXISTS idx_payroll_entries_period
    ON payroll_entries(period_start, staff_id);

ALTER TABLE payroll_entries
    ADD COLUMN IF NOT EXISTS base_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS attendance_bonus NUMERIC(12,2) NOT NULL DEFAULT 0;

-- Record a payroll expense on the final day of the month it pays for. This
-- also corrects entries created before this rule was introduced.
UPDATE finance_transactions AS finance
SET occurred_at = (
    (entry.period_start + INTERVAL '1 month' - INTERVAL '1 second')
    AT TIME ZONE 'Asia/Bangkok'
)
FROM payroll_entries AS entry
WHERE finance.id = entry.finance_transaction_id
  AND finance.occurred_at::date <>
      (entry.period_start + INTERVAL '1 month' - INTERVAL '1 day')::date;
