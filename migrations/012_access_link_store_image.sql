ALTER TABLE access_links ADD COLUMN IF NOT EXISTS store_image_url text;
-- Reuse restaurant photos already captured by the previous checkout flow.
UPDATE access_links SET store_image_url=COALESCE(
    NULLIF(redemption_payload->'selectedStore'->>'imageUrl',''),
    NULLIF(redemption_payload->'store'->'storePic'->'storeSelfPic'->>0,''),
    NULLIF(redemption_payload->'store'->'storePic'->'storeEnvirPic'->>0,''),
    NULLIF(redemption_payload->'store'->'storePic'->'storeBuzAreaPic'->>0,'')
) WHERE store_image_url IS NULL;
