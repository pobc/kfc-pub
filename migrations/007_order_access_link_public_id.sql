ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS access_link_public_id varchar(16);

UPDATE orders o
SET access_link_public_id = l.public_id
FROM access_links l
WHERE o.access_link_public_id IS NULL
  AND o.access_link_id = l.id;

-- 旧库没有为 orders.access_link_id 建外键，历史上可能遗留 access_links
-- 已被删除的孤立订单。这类订单已经无法通过任何访问链接查询，不能构造假的
-- public_id 继续保留，因此迁移时直接清理，避免阻塞其余有效订单的回填。
DELETE FROM orders
WHERE access_link_public_id IS NULL;

ALTER TABLE orders
    ALTER COLUMN access_link_public_id SET NOT NULL;

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS "UQ_orders_access_link";

ALTER TABLE orders
    DROP COLUMN access_link_id;
