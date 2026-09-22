-- เก็บความสัมพันธ์ระหว่างบัญชี LINE และรถเป็นรายคัน
CREATE TABLE IF NOT EXISTS vehicle_line_links (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    line_user_id VARCHAR(80),
    link_token VARCHAR(128) NOT NULL UNIQUE,
    linked_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

ALTER TABLE vehicle_line_links
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

-- ปิดความสัมพันธ์เก่าที่ซ้ำก่อนบังคับให้รถหนึ่งคันมีเจ้าของเพียงหนึ่งบัญชี
WITH ranked_links AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY linked_at DESC, id DESC) AS row_number
    FROM vehicle_line_links
    WHERE linked_at IS NOT NULL AND revoked_at IS NULL
)
UPDATE vehicle_line_links AS links
SET revoked_at = NOW()
FROM ranked_links
WHERE links.id = ranked_links.id AND ranked_links.row_number > 1;

CREATE INDEX IF NOT EXISTS idx_vehicle_line_links_vehicle ON vehicle_line_links(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_vehicle_line_links_line_user
    ON vehicle_line_links(line_user_id) WHERE line_user_id IS NOT NULL;

-- รถหนึ่งคันมีเจ้าของ LINE ที่ผูกอยู่ได้เพียงหนึ่งบัญชีในเวลาเดียวกัน
CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicle_line_links_active_vehicle
    ON vehicle_line_links(vehicle_id) WHERE linked_at IS NOT NULL AND revoked_at IS NULL;
