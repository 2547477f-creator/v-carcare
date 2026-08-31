-- Store the receipt channel on finance entries so income history can be filtered.
ALTER TABLE finance_transactions
    ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_payment_method
    ON finance_transactions(payment_method);

-- Existing service-income records remain filterable through payments.method.
