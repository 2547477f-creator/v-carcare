-- Required only when migration 003 was applied before the fund-receipt action existed.
ALTER TABLE central_fund_transactions
    DROP CONSTRAINT IF EXISTS central_fund_transactions_movement_type_check;

ALTER TABLE central_fund_transactions
    ADD CONSTRAINT central_fund_transactions_movement_type_check
    CHECK (movement_type IN
        ('income', 'expense', 'opening_float', 'closing_float', 'adjustment', 'fund_received'));
