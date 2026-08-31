-- Keep explicit shop-opening state and before/after balances for audit history.
ALTER TABLE central_fund
    ADD COLUMN IF NOT EXISTS shop_opened_at TIMESTAMPTZ;

ALTER TABLE central_fund_transactions
    ADD COLUMN IF NOT EXISTS balance_before NUMERIC(12,2);
