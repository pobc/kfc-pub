ALTER TABLE product_options
  ADD COLUMN select_count integer NOT NULL DEFAULT 1
  CONSTRAINT "CHK_product_option_select_count" CHECK (select_count > 0);
