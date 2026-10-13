ALTER TABLE "access_links"
  ADD COLUMN IF NOT EXISTS "redemption_status" varchar(32),
  ADD COLUMN IF NOT EXISTS "redemption_payload" jsonb,
  ADD COLUMN IF NOT EXISTS "redemption_error" text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS "redemption_started_at" timestamptz,
  ADD COLUMN IF NOT EXISTS "redemption_finished_at" timestamptz;

ALTER TABLE "access_links" DROP CONSTRAINT IF EXISTS "CHK_access_link_redemption_status";
ALTER TABLE "access_links"
  ADD CONSTRAINT "CHK_access_link_redemption_status"
  CHECK (
    "redemption_status" IS NULL OR
    "redemption_status" IN ('WAITING','PROCESSING','CART_CREATED','COUPON_APPLIED','COMPLETED','FAILED','UNKNOWN')
  );

DO $$
BEGIN
  IF to_regclass('public.coupon_application_attempts') IS NOT NULL THEN
    EXECUTE $migration$
      WITH latest AS (
        SELECT DISTINCT ON (access_link_id)
          access_link_id,
          status,
          store_code,
          store_snapshot,
          pack_type,
          items_snapshot,
          cart_id,
          error_message,
          worker_started_at,
          finished_at
        FROM coupon_application_attempts
        ORDER BY access_link_id, "createdAt" DESC, id DESC
      )
      UPDATE access_links AS l
      SET redemption_status = CASE
            WHEN latest.status = 'UPSTREAM_SUCCEEDED' THEN 'COUPON_APPLIED'
            ELSE latest.status
          END,
          redemption_payload = jsonb_build_object(
            'storeCode', COALESCE(latest.store_code, ''),
            'store', COALESCE(latest.store_snapshot, '{}'::jsonb),
            'packType', latest.pack_type,
            'items', COALESCE(latest.items_snapshot, '[]'::jsonb)
          ),
          redemption_error = COALESCE(latest.error_message, ''),
          redemption_started_at = latest.worker_started_at,
          redemption_finished_at = latest.finished_at,
          coupon_cart_id = COALESCE(l.coupon_cart_id, latest.cart_id),
          "updatedAt" = now()
      FROM latest
      WHERE l.id = latest.access_link_id
    $migration$;
  END IF;
END $$;

DROP TABLE IF EXISTS "coupon_application_attempts";

CREATE INDEX IF NOT EXISTS "IDX_access_links_redemption_queue"
  ON "access_links" ("redemption_status","updatedAt",id);
