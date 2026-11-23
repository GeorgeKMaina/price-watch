-- Competitor Price Watch: PostgreSQL schema (Supabase).
-- Applied by:  python -m pricewatch.setup_db      (safe to run again; it only creates what's missing
-- and replaces the views)
--
-- Storage idea: a price row is written only when something CHANGES (normal price, promo price or
-- stock). A product seen today with the same price as yesterday only updates products.last_seen.
-- The v_* views turn that compact history back into the tables Power BI uses.

CREATE TABLE IF NOT EXISTS products (
    id               BIGSERIAL PRIMARY KEY,
    competitor       TEXT NOT NULL,
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    url              TEXT,
    currency         TEXT,
    site_category    TEXT,              -- the competitor's own category path, e.g. "Electronics > Small Appliances > Kettles"
    target_category  TEXT,              -- category from config/targets.csv (or the Carrefour folder name)
    my_category      TEXT,              -- from config/product_map.csv (overrides the others)
    product_group    TEXT,              -- from config/product_map.csv: same item across competitors
    key_item         BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen       TIMESTAMPTZ NOT NULL,
    last_seen        TIMESTAMPTZ NOT NULL,
    UNIQUE (competitor, sku)
);

CREATE TABLE IF NOT EXISTS runs (
    id               BIGSERIAL PRIMARY KEY,
    competitor       TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    status           TEXT NOT NULL,     -- running | ok | partial | failed
    items_seen       INTEGER NOT NULL DEFAULT 0,
    items_changed    INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    id               BIGSERIAL PRIMARY KEY,
    product_id       BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    run_id           BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    normal_price     NUMERIC(12,2) NOT NULL,
    promo_price      NUMERIC(12,2),     -- NULL = no promo
    in_stock         BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_prices_product_time ON prices (product_id, observed_at);

-- How many days the day-by-day view covers (Power BI's daily grid). Change it with:
--   UPDATE report_settings SET daily_days = 60;
CREATE TABLE IF NOT EXISTS report_settings (
    id               INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    daily_days       INTEGER NOT NULL DEFAULT 21,   -- ~34k products x 21 days = ~0.7M rows; more days = bigger refreshes
    timezone         TEXT NOT NULL DEFAULT 'Africa/Nairobi'
);
INSERT INTO report_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------------------
-- ITEM MASTER (from the retailers' monthly files, see pricewatch/items.py) and LINKS from
-- website products to those items (see pricewatch/link.py).
-- ----------------------------------------------------------------------------------------
ALTER TABLE products ADD COLUMN IF NOT EXISTS retailer_code TEXT;   -- retailer's own item code when the website shows it
ALTER TABLE products ADD COLUMN IF NOT EXISTS barcode TEXT;         -- barcode when the website gives it (Naivas does)

CREATE TABLE IF NOT EXISTS items (
    item_key            TEXT PRIMARY KEY,      -- 'B' + barcode, or retailer prefix + code when there's no barcode
    barcode             TEXT,
    uniform_name        TEXT NOT NULL,
    brand               TEXT,
    supplier            TEXT,
    all_suppliers       TEXT,
    category_1          TEXT,
    category_2          TEXT,
    category_3          TEXT,
    retailers_in_files  TEXT,
    watched             BOOLEAN NOT NULL DEFAULT TRUE
);

ALTER TABLE items ADD COLUMN IF NOT EXISTS supplier_group TEXT;     -- one spelling per supplier
ALTER TABLE items ADD COLUMN IF NOT EXISTS ours BOOLEAN NOT NULL DEFAULT FALSE;  -- settings.json our_suppliers_contain
ALTER TABLE items ADD COLUMN IF NOT EXISTS pack_size NUMERIC(12,2);  -- grams, millilitres or pieces
ALTER TABLE items ADD COLUMN IF NOT EXISTS pack_unit TEXT;           -- g | ml | pc

-- ---- Decision tables, rebuilt after every crawl by pricewatch.refresh (sql/insights.sql) ----
-- Today's price of each item at each retailer (from the linked website product).
CREATE TABLE IF NOT EXISTS item_prices_now (
    item_key       TEXT NOT NULL,
    retailer       TEXT NOT NULL,
    product_id     BIGINT,
    web_name       TEXT,
    url            TEXT,
    normal_price   NUMERIC(12,2),
    promo_price    NUMERIC(12,2),
    price_paid     NUMERIC(12,2),
    discount_pct   NUMERIC(6,1),
    in_stock       BOOLEAN,
    normal_7d_ago  NUMERIC(12,2),
    change_7d_pct  NUMERIC(6,1),
    per_unit       NUMERIC(12,1),       -- price paid per kg / litre / piece
    unit_label     TEXT,
    compare_group  TEXT,                -- the narrowest category: what "rivals" are compared within
    ours           BOOLEAN,
    in_our_groups  BOOLEAN,             -- a rival listing in a category where we're listed at that retailer
    PRIMARY KEY (item_key, retailer)
);
-- Each of our items vs its cheapest rival (per kg / litre / piece, similar pack size, same retailer).
CREATE TABLE IF NOT EXISTS price_position (
    item_key        TEXT NOT NULL,
    retailer        TEXT NOT NULL,
    compare_group   TEXT,
    our_price       NUMERIC(12,2),
    our_per_unit    NUMERIC(12,1),
    unit_label      TEXT,
    rival_item_key  TEXT,
    rival_name      TEXT,
    rival_supplier  TEXT,
    rival_price     NUMERIC(12,2),
    rival_per_unit  NUMERIC(12,1),
    gap_pct         NUMERIC(8,1),       -- + = we're dearer per unit than the cheapest rival
    rivals          INTEGER,            -- rivals compared
    cheaper_rivals  INTEGER,            -- how many of them are cheaper than us
    PRIMARY KEY (item_key, retailer)
);
-- The "needs action" list, ranked.
CREATE TABLE IF NOT EXISTS actions (
    action_id      SERIAL PRIMARY KEY,
    priority       INTEGER NOT NULL,    -- 1 act now, 2 look this week, 3 good to know
    action_type    TEXT NOT NULL,
    retailer       TEXT,
    category       TEXT,
    item_key       TEXT,
    item_name      TEXT,
    supplier       TEXT,
    headline       TEXT NOT NULL,
    detail         TEXT,
    metric_pct     NUMERIC(8,1),
    since          DATE
);
ALTER TABLE price_position ADD COLUMN IF NOT EXISTS rival_median_per_unit NUMERIC(12,1);  -- typical similar rival
ALTER TABLE price_position ADD COLUMN IF NOT EXISTS gap_vs_median_pct NUMERIC(8,1);       -- + = dearer than typical
ALTER TABLE item_prices_now ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_position ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS retailer_items (
    retailer       TEXT NOT NULL,
    retailer_code  TEXT NOT NULL,
    item_key       TEXT NOT NULL,
    barcode        TEXT,
    retailer_name  TEXT,
    supplier       TEXT,
    brand          TEXT,
    category_1     TEXT,
    category_2     TEXT,
    category_3     TEXT,
    last_month     TEXT,
    PRIMARY KEY (retailer, retailer_code)
);

CREATE TABLE IF NOT EXISTS product_links (
    product_id     BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    retailer       TEXT NOT NULL,
    retailer_code  TEXT NOT NULL,
    item_key       TEXT NOT NULL,
    method         TEXT NOT NULL,              -- code | name | confirmed
    score          NUMERIC(5,3),
    linked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_links_item ON product_links (item_key);

CREATE TABLE IF NOT EXISTS match_review (
    retailer       TEXT NOT NULL,
    retailer_code  TEXT NOT NULL,
    item_key       TEXT NOT NULL,
    retailer_name  TEXT,
    rank           INTEGER NOT NULL,
    product_id     BIGINT,
    web_sku        TEXT,
    web_name       TEXT,
    web_url        TEXT,
    score          NUMERIC(5,3),
    PRIMARY KEY (retailer, retailer_code, rank)
);

ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE retailer_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_review ENABLE ROW LEVEL SECURITY;

-- Supabase: tables are private (row level security on, no policies). Only the v_* views below are
-- readable with the read-only key, so Power BI can never change or see anything else.
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_settings ENABLE ROW LEVEL SECURITY;


-- ================================ VIEWS FOR POWER BI ================================
DROP VIEW IF EXISTS v_products, v_price_changes, v_promotions, v_scraper_runs, v_price_periods,
                    v_items, v_match_review, v_item_prices, v_position, v_actions CASCADE;
-- (v_daily_prices is a stored table built from v_daily_prices_live; it is never dropped here,
--  so Power BI keeps its data while the crawlers run. Older versions had it as a plain view.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'v_daily_prices' AND schemaname = current_schema()) THEN
        DROP VIEW v_daily_prices;
    END IF;
END $$;

-- One row per product per competitor, with every label Power BI filters on.
CREATE VIEW v_products AS
SELECT
    p.id                                                     AS product_id,
    p.competitor,
    p.sku,
    p.name,
    COALESCE(p.my_category, p.target_category,
             NULLIF(split_part(p.site_category, ' > ', 1), ''), 'Uncategorised') AS category,
    p.site_category,
    NULLIF(split_part(p.site_category, ' > ', 1), '')        AS site_category_1,
    NULLIF(split_part(p.site_category, ' > ', 2), '')        AS site_category_2,
    NULLIF(split_part(p.site_category, ' > ', 3), '')        AS site_category_3,
    p.product_group,
    COALESCE(p.product_group, p.name)                        AS item,
    CASE WHEN p.key_item THEN 'Yes' ELSE 'No' END            AS key_item,
    p.currency,
    p.url,
    (p.first_seen AT TIME ZONE s.timezone)::date             AS first_seen,
    (p.last_seen  AT TIME ZONE s.timezone)::date             AS last_seen,
    ((p.last_seen AT TIME ZONE s.timezone)::date - (p.first_seen AT TIME ZONE s.timezone)::date + 1) AS days_tracked,
    concat_ws(' | ', p.name, p.competitor, p.site_category, p.my_category, p.sku, p.product_group) AS search_text,
    l.item_key,                                              -- the item-master product this listing is (NULL = not linked)
    l.method                                                 AS link_method,
    COALESCE(l.retailer_code, p.retailer_code)               AS retailer_code
FROM products p CROSS JOIN report_settings s
LEFT JOIN product_links l ON l.product_id = p.id;

-- The item master: one row per product (barcode), uniform name, supplier, category.
-- "followed" = in config/watchlist.csv (or every item when the watchlist is empty).
CREATE VIEW v_items AS
SELECT i.item_key, i.barcode, i.uniform_name, i.brand, i.supplier, i.all_suppliers,
       COALESCE(i.category_1, 'Uncategorised') AS category_1, i.category_2, i.category_3,
       i.retailers_in_files,
       CASE WHEN i.watched THEN 'Yes' ELSE 'No' END AS followed,
       COALESCE(l.retailers_linked, 0) AS retailers_linked,
       COALESCE(i.supplier_group, 'Unknown') AS supplier_group,
       CASE WHEN i.ours THEN 'Ours' ELSE 'Rival' END AS ours,
       i.pack_size, i.pack_unit,
       COALESCE(i.category_3, i.category_2, i.category_1, 'Uncategorised') AS compare_group
FROM items i
LEFT JOIN (SELECT item_key, count(DISTINCT retailer) AS retailers_linked
           FROM product_links GROUP BY item_key) l ON l.item_key = i.item_key;

-- Items listed in a retailer's file that aren't linked to a website product yet, with the best
-- website candidates (Quickmart has no codes online, so it's matched by name + pack size).
CREATE VIEW v_match_review AS
SELECT r.retailer, r.retailer_code, r.item_key, i.uniform_name, r.retailer_name, r.rank,
       r.web_sku, r.web_name, r.web_url, r.score
FROM match_review r JOIN items i ON i.item_key = r.item_key;

-- Day-by-day prices for the last report_settings.daily_days days: for each day a product was
-- listed (between first and last seen), the latest price known by the end of that day.
-- Power BI reads the stored copy v_daily_prices (below), rebuilt after each daily crawl by
-- `python -m pricewatch.refresh`, so refreshes are fast and light on Supabase's free plan.
CREATE OR REPLACE VIEW v_daily_prices_live AS
WITH s AS (SELECT daily_days, timezone, (now() AT TIME ZONE timezone)::date AS today FROM report_settings),
days AS (
    SELECT d::date AS day FROM s, generate_series(s.today - (s.daily_days - 1), s.today, interval '1 day') AS d
)
SELECT
    p.id                                                     AS product_id,
    days.day                                                 AS date,
    pr.normal_price,
    pr.promo_price,
    COALESCE(pr.promo_price, pr.normal_price)                AS effective_price,
    CASE WHEN pr.promo_price IS NOT NULL THEN 1 ELSE 0 END   AS on_promo,
    CASE WHEN pr.promo_price IS NOT NULL
         THEN round((pr.normal_price - pr.promo_price) * 100 / pr.normal_price, 2) END AS promo_discount_pct,
    CASE WHEN pr.in_stock IS NULL THEN NULL WHEN pr.in_stock THEN 1 ELSE 0 END AS in_stock
FROM s
CROSS JOIN days
JOIN products p
  ON (p.first_seen AT TIME ZONE s.timezone)::date <= days.day
 AND (p.last_seen  AT TIME ZONE s.timezone)::date >= days.day
CROSS JOIN LATERAL (
    SELECT x.normal_price, x.promo_price, x.in_stock
    FROM prices x
    WHERE x.product_id = p.id
      AND x.observed_at < ((days.day + 1)::timestamp AT TIME ZONE s.timezone)
    ORDER BY x.observed_at DESC
    LIMIT 1
) pr;

-- Stored copy for Power BI. To change its definition: edit v_daily_prices_live above, then run
--   python -m pricewatch.refresh --rebuild
CREATE MATERIALIZED VIEW IF NOT EXISTS v_daily_prices AS SELECT * FROM v_daily_prices_live WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS idx_v_daily_prices ON v_daily_prices (product_id, date);

-- Full price history, compact: one row per period during which a product's price stayed the same.
-- Power BI draws price-evolution lines over ANY date range from this (measures "... On Date"),
-- without downloading a row per product per day.
CREATE VIEW v_price_periods AS
WITH s AS (SELECT timezone FROM report_settings),
o AS (
    SELECT x.product_id, x.normal_price, x.promo_price, x.in_stock,
           (x.observed_at AT TIME ZONE s.timezone)::date AS valid_from,
           ((LEAD(x.observed_at) OVER (PARTITION BY x.product_id ORDER BY x.observed_at, x.id)) AT TIME ZONE s.timezone)::date - 1 AS next_minus_1,
           (p.last_seen AT TIME ZONE s.timezone)::date AS last_seen
    FROM prices x JOIN products p ON p.id = x.product_id CROSS JOIN s
)
SELECT product_id,
       valid_from,
       COALESCE(next_minus_1, last_seen)                     AS valid_to,
       normal_price,
       promo_price,
       COALESCE(promo_price, normal_price)                   AS effective_price,
       CASE WHEN promo_price IS NOT NULL THEN 1 ELSE 0 END   AS on_promo,
       CASE WHEN in_stock IS NULL THEN NULL WHEN in_stock THEN 1 ELSE 0 END AS in_stock
FROM o
WHERE COALESCE(next_minus_1, last_seen) >= valid_from;      -- several observations on one day: keep the last

-- Every classified change: increase / decrease of the normal price, promo start / change / end.
CREATE VIEW v_price_changes AS
WITH s AS (SELECT timezone FROM report_settings),
o AS (
    SELECT x.product_id, x.observed_at, x.normal_price, x.promo_price,
           LAG(x.normal_price) OVER w AS prev_normal,
           LAG(x.promo_price)  OVER w AS prev_promo,
           LAG(x.id)           OVER w AS prev_id
    FROM prices x
    WINDOW w AS (PARTITION BY x.product_id ORDER BY x.observed_at, x.id)
),
c AS (
    SELECT product_id, observed_at,
           CASE WHEN normal_price > prev_normal THEN 'increase' ELSE 'decrease' END AS change_type,
           prev_normal AS from_price, normal_price AS to_price
    FROM o WHERE prev_id IS NOT NULL AND normal_price <> prev_normal
    UNION ALL
    SELECT product_id, observed_at, 'promo_start', normal_price, promo_price
    FROM o WHERE prev_id IS NOT NULL AND prev_promo IS NULL AND promo_price IS NOT NULL
    UNION ALL
    SELECT product_id, observed_at, 'promo_end', prev_promo, normal_price
    FROM o WHERE prev_id IS NOT NULL AND prev_promo IS NOT NULL AND promo_price IS NULL
    UNION ALL
    SELECT product_id, observed_at, 'promo_change', prev_promo, promo_price
    FROM o WHERE prev_id IS NOT NULL AND prev_promo IS NOT NULL AND promo_price IS NOT NULL AND promo_price <> prev_promo
)
SELECT
    c.product_id,
    (c.observed_at AT TIME ZONE s.timezone)::date            AS date,
    c.change_type,
    CASE c.change_type
        WHEN 'increase'     THEN 'Normal price increase'
        WHEN 'decrease'     THEN 'Normal price drop'
        WHEN 'promo_start'  THEN 'Promo started'
        WHEN 'promo_change' THEN 'Promo price changed'
        ELSE 'Promo ended' END                               AS change_label,
    CASE c.change_type
        WHEN 'increase' THEN 1 WHEN 'decrease' THEN 2 WHEN 'promo_start' THEN 3
        WHEN 'promo_change' THEN 4 ELSE 5 END                AS change_order,
    c.from_price,
    c.to_price,
    round((c.to_price - c.from_price) * 100 / NULLIF(c.from_price, 0), 2) AS change_pct
FROM c CROSS JOIN s;

-- One row per promo episode (consecutive observations with a promo price).
CREATE VIEW v_promotions AS
WITH s AS (SELECT timezone FROM report_settings),
o AS (
    SELECT x.product_id, x.observed_at, x.normal_price, x.promo_price,
           LAG(x.promo_price) OVER w AS prev_promo,
           ROW_NUMBER()       OVER w AS rn
    FROM prices x
    WINDOW w AS (PARTITION BY x.product_id ORDER BY x.observed_at, x.id)
),
g AS (
    SELECT o.*,
           SUM(CASE WHEN promo_price IS NOT NULL AND (prev_promo IS NULL OR rn = 1) THEN 1 ELSE 0 END)
               OVER (PARTITION BY product_id ORDER BY observed_at, rn) AS episode
    FROM o
),
e AS (
    SELECT product_id, episode,
           MIN(observed_at) FILTER (WHERE promo_price IS NOT NULL) AS start_at,
           MIN(observed_at) FILTER (WHERE promo_price IS NULL)     AS ended_at,
           (ARRAY_AGG(normal_price ORDER BY observed_at))[1]       AS normal_price_at_start,
           MIN(promo_price)                                        AS lowest_promo_price,
           AVG((normal_price - promo_price) * 100 / normal_price) FILTER (WHERE promo_price IS NOT NULL) AS avg_discount_pct,
           MAX((normal_price - promo_price) * 100 / normal_price) FILTER (WHERE promo_price IS NOT NULL) AS max_discount_pct
    FROM g WHERE episode > 0
    GROUP BY product_id, episode
),
f AS (
    SELECT e.*,
           (e.start_at AT TIME ZONE s.timezone)::date AS start_date,
           (e.ended_at AT TIME ZONE s.timezone)::date AS ended_on,
           (e.start_at = (SELECT MIN(x.observed_at) FROM prices x WHERE x.product_id = e.product_id)) AS from_first_observation,
           CASE WHEN e.ended_at IS NULL THEN (p.last_seen AT TIME ZONE s.timezone)::date
                ELSE (e.ended_at AT TIME ZONE s.timezone)::date - 1 END AS last_promo_date
    FROM e JOIN products p ON p.id = e.product_id CROSS JOIN s
)
SELECT
    (f.product_id * 1000 + f.episode)                        AS promo_id,
    f.product_id,
    f.episode                                                AS promo_no,
    f.start_date,
    GREATEST(f.last_promo_date, f.start_date)                AS last_promo_date,
    f.ended_on,
    GREATEST(f.last_promo_date, f.start_date) - f.start_date + 1 AS days_on_promo,
    CASE WHEN f.ended_on IS NULL THEN 'Yes' ELSE 'No' END    AS ongoing,
    -- 'Yes' = the promo was already running the first time this product was seen, so its real
    -- start date (and length) is unknown. Filter these out for promo-length analysis.
    CASE WHEN f.from_first_observation THEN 'Yes' ELSE 'No' END AS started_before_tracking,
    f.normal_price_at_start,
    f.lowest_promo_price,
    round(f.avg_discount_pct, 2)                             AS avg_discount_pct,
    round(f.max_discount_pct, 2)                             AS max_discount_pct,
    f.start_date - LAG(GREATEST(f.last_promo_date, f.start_date)) OVER (PARTITION BY f.product_id ORDER BY f.episode) AS days_since_previous_promo,
    to_char(f.start_date, 'DD Mon') || ' – ' || to_char(GREATEST(f.last_promo_date, f.start_date), 'DD Mon YYYY') AS promo_period,
    to_char(f.start_date, 'FMDay')                           AS start_weekday,
    EXTRACT(ISODOW FROM f.start_date)::int                   AS start_weekday_no,
    CASE WHEN f.max_discount_pct < 10 THEN '1. Under 10%'
         WHEN f.max_discount_pct < 20 THEN '2. 10–20%'
         WHEN f.max_discount_pct < 30 THEN '3. 20–30%'
         ELSE '4. 30%+' END                                  AS discount_band
FROM f;

CREATE VIEW v_scraper_runs AS
SELECT competitor, started_at, finished_at, status, items_seen, items_changed, error
FROM runs;

CREATE VIEW v_item_prices AS
SELECT n.*, CASE WHEN n.ours THEN 'Ours' WHEN n.in_our_groups THEN 'Rival in our categories' ELSE 'Other' END AS scope
FROM item_prices_now n;
CREATE VIEW v_position AS SELECT * FROM price_position;
CREATE VIEW v_actions AS
SELECT a.*, CASE a.priority WHEN 1 THEN '1 Act now' WHEN 2 THEN '2 This week' ELSE '3 Good to know' END AS priority_label
FROM actions a;

-- Read-only access for Power BI through Supabase's API (the "anon" key): reporting views only.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        -- Supabase grants its API roles access to new tables by default: take that away from the raw
        -- tables, so the public key can read the reporting views and nothing else.
        REVOKE ALL ON products, prices, runs, report_settings, v_daily_prices_live,
                      items, retailer_items, product_links, match_review, item_prices_now, price_position, actions FROM anon;
        GRANT SELECT ON v_products, v_daily_prices, v_price_periods, v_price_changes, v_promotions, v_scraper_runs,
                        v_items, v_match_review, v_item_prices, v_position, v_actions TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON products, prices, runs, report_settings, v_daily_prices_live,
                      items, retailer_items, product_links, match_review, item_prices_now, price_position, actions FROM authenticated;
    END IF;
END $$;
