ALTER TABLE access_links
ADD COLUMN IF NOT EXISTS store_code varchar(64)
GENERATED ALWAYS AS (NULLIF(redemption_payload ->> 'storeCode','')) STORED;
