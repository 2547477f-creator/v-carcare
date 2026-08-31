-- Opening the shop is now an explicit action; prevent only duplicate openings in application logic.
DROP INDEX IF EXISTS uq_central_fund_opening_per_day;
