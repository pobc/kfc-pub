ALTER TYPE "access_link_history_status_enum" ADD VALUE IF NOT EXISTS 'PROCESSING';

ALTER TABLE "access_links" DROP CONSTRAINT IF EXISTS "CHK_access_link_status";
ALTER TABLE "access_links"
  ADD CONSTRAINT "CHK_access_link_status"
  CHECK ("status" IN ('UNUSED','PROCESSING','PICKING_UP','FAILED','COMPLETED'));

ALTER TABLE "coupon_application_attempts"
  ADD COLUMN IF NOT EXISTS "store_code" varchar(64),
  ADD COLUMN IF NOT EXISTS "store_snapshot" jsonb,
  ADD COLUMN IF NOT EXISTS "pack_type" smallint,
  ADD COLUMN IF NOT EXISTS "items_snapshot" jsonb,
  ADD COLUMN IF NOT EXISTS "worker_started_at" timestamptz,
  ADD COLUMN IF NOT EXISTS "finished_at" timestamptz;

ALTER TABLE "coupon_application_attempts"
  DROP CONSTRAINT IF EXISTS "CHK_coupon_application_attempt_status";
ALTER TABLE "coupon_application_attempts"
  ADD CONSTRAINT "CHK_coupon_application_attempt_status"
  CHECK (
    "status" IN (
      'WAITING','PROCESSING','CART_CREATED','COUPON_APPLIED',
      'UPSTREAM_SUCCEEDED','COMPLETED','FAILED','UNKNOWN'
    )
  );

CREATE INDEX IF NOT EXISTS "IDX_coupon_application_attempt_queue"
  ON "coupon_application_attempts" ("status","createdAt",id);
