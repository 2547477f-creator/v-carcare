-- Run once on existing databases before using the "picked_up" status.
ALTER TABLE service_orders DROP CONSTRAINT IF EXISTS service_orders_status_check;
ALTER TABLE service_orders
    ADD CONSTRAINT service_orders_status_check
    CHECK (status IN ('pending', 'in_progress', 'drying', 'ready', 'completed', 'picked_up', 'cancelled'));
