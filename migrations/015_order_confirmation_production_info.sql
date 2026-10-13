ALTER TABLE order_confirmation_logs
  ADD COLUMN IF NOT EXISTS production_info jsonb;

UPDATE order_confirmation_logs
SET production_info = '[]'::jsonb
WHERE production_info IS NULL;

ALTER TABLE order_confirmation_logs
  ALTER COLUMN production_info SET DEFAULT '[]'::jsonb,
  ALTER COLUMN production_info SET NOT NULL;
