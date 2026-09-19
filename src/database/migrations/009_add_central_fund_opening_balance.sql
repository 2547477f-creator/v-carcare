-- Snapshot the central-fund balance immediately before each shop opening.
ALTER TABLE central_fund
    ADD COLUMN IF NOT EXISTS opening_balance NUMERIC(12,2);
