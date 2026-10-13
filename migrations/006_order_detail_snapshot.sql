ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS status_code varchar(32) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS status_name varchar(80) NOT NULL DEFAULT '';
