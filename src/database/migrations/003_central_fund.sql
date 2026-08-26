-- Central fund, its auditable movement ledger, and the daily change-float workflow.
CREATE TABLE IF NOT EXISTS central_fund (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    balance NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    cash_float_balance NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (cash_float_balance >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO central_fund (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS central_fund_transactions (
    id BIGSERIAL PRIMARY KEY,
    movement_type VARCHAR(30) NOT NULL CHECK (movement_type IN
        ('income', 'expense', 'opening_float', 'closing_float', 'adjustment', 'fund_received')),
    amount NUMERIC(12,2) NOT NULL CHECK (amount <> 0),
    balance_after NUMERIC(12,2) NOT NULL CHECK (balance_after >= 0),
    description TEXT NOT NULL,
    finance_transaction_id BIGINT REFERENCES finance_transactions(id) ON DELETE SET NULL,
    created_by BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
    opening_date DATE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_central_fund_transaction_finance
    ON central_fund_transactions(finance_transaction_id)
    WHERE finance_transaction_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_central_fund_opening_per_day
    ON central_fund_transactions(opening_date)
    WHERE movement_type = 'opening_float';

CREATE INDEX IF NOT EXISTS idx_central_fund_transactions_occurred_at
    ON central_fund_transactions(occurred_at DESC);
