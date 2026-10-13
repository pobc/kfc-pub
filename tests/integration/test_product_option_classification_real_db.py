from __future__ import annotations

import secrets

from app.database import db, products


def _create_product() -> str:
    marker = secrets.token_hex(6)
    row = db.fetchone(
        """INSERT INTO products
           (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
           VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now())
           RETURNING id""",
        (f"option-classification-{marker}", f"option-link-{marker}"),
    )
    return str(row["id"])


def test_product_option_classification_uses_database_relationship() -> None:
    product_without_options = _create_product()
    product_with_options = _create_product()
    db.execute(
        """INSERT INTO product_options
           (product_id,group_no,group_name,option_name,image_url,sort)
           VALUES (%s,1,'主食','香辣鸡腿堡',NULL,10)""",
        (product_with_options,),
    )

    assert products.has_product_options(product_without_options) is False
    assert products.has_product_options(product_with_options) is True
