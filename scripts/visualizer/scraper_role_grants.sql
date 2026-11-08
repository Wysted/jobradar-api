-- Grants of the `getonbrd_scraper` role: only what the scraper writes (backend/scrapers/getonbrd/src/.../store.py).
-- Run by create_scraper_role.sh inside its transaction, and by the store tests with the role name swapped, so the
-- grants are exercised. The role must exist. The database is the current one.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM getonbrd_scraper;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;  -- the default on 15+, set here so an older volume matches
DO $$ BEGIN
  -- TEMP comes from PUBLIC, so it is revoked there; the app runs as the superuser and is unaffected.
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM getonbrd_scraper', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO getonbrd_scraper', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO getonbrd_scraper;
GRANT SELECT ON platforms TO getonbrd_scraper;
GRANT SELECT ON jobs TO getonbrd_scraper;
-- scraped_at: the model's Python-side default is part of every INSERT the scraper sends.
GRANT INSERT (id, platform_id, external_id, title, company, location, posted_date, url, description, tags, scraped_at)
  ON jobs TO getonbrd_scraper;
GRANT UPDATE (description) ON jobs TO getonbrd_scraper;
