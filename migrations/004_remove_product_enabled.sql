DROP INDEX IF EXISTS "IDX_products_enabled";
ALTER TABLE "products" DROP COLUMN IF EXISTS "enabled";
