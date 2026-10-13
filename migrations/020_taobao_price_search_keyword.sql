-- 历史价格没有可靠的来源关键词，保留为空，禁止根据商品名称猜测归属。
ALTER TABLE "taobao_product_prices"
  ADD COLUMN IF NOT EXISTS "search_keyword" varchar(200) NOT NULL DEFAULT '';

ALTER TABLE "taobao_product_prices" DROP CONSTRAINT IF EXISTS "UQ_taobao_product_price_item";
ALTER TABLE "taobao_product_prices"
  ADD CONSTRAINT "UQ_taobao_product_price_item" UNIQUE ("product_url", "item_name", "search_keyword");

CREATE INDEX IF NOT EXISTS "IDX_taobao_product_prices_keyword_scraped_at"
  ON "taobao_product_prices" ("search_keyword", "scraped_at");
