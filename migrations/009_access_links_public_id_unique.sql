-- Ensure existing databases support access link conflict handling and uniqueness.
-- Fresh databases already get this invariant from database.sql.

CREATE UNIQUE INDEX IF NOT EXISTS "UX_access_links_public_id"
  ON access_links (public_id);
