-- Decision tables for Power BI. Run after every crawl by `python -m pricewatch.refresh`.
-- "Ours" = items whose supplier matches config/settings.json our_suppliers_contain (items.ours).
-- Rivals = other items in the same narrowest category (compare_group), at the same retailer,
-- with the same kind of pack (g / ml / pieces) and a similar size (half to double ours).

-- 1) Today's price of each item at each retailer -------------------------------------------
DELETE FROM item_prices_now;
WITH last AS (
    SELECT DISTINCT ON (x.product_id) x.product_id, x.normal_price, x.promo_price, x.in_stock
    FROM prices x JOIN product_links l ON l.product_id = x.product_id
    ORDER BY x.product_id, x.observed_at DESC, x.id DESC
),
ago AS (   -- the price 7 days ago (or the first price seen, for products tracked for less than 7 days)
    SELECT DISTINCT ON (x.product_id) x.product_id, x.normal_price
    FROM prices x JOIN product_links l ON l.product_id = x.product_id
    ORDER BY x.product_id, (x.observed_at <= now() - interval '7 days') DESC,
             CASE WHEN x.observed_at <= now() - interval '7 days' THEN x.observed_at END DESC NULLS LAST,
             x.observed_at, x.id
),
w AS (
    SELECT l.item_key, p.competitor AS retailer, p.id AS product_id, p.name, p.url,
           last.normal_price, last.promo_price, COALESCE(last.promo_price, last.normal_price) AS paid,
           last.in_stock, ago.normal_price AS ago
    FROM product_links l
    JOIN products p ON p.id = l.product_id
    JOIN last ON last.product_id = p.id
    LEFT JOIN ago ON ago.product_id = p.id
    WHERE p.last_seen > now() - interval '3 days'
),
one AS (   -- an item linked to two listings at one retailer: keep the cheaper
    SELECT DISTINCT ON (item_key, retailer) * FROM w ORDER BY item_key, retailer, paid
)
INSERT INTO item_prices_now (item_key, retailer, product_id, web_name, url, normal_price, promo_price, price_paid,
                             discount_pct, in_stock, normal_7d_ago, change_7d_pct, per_unit, unit_label,
                             compare_group, ours, in_our_groups)
SELECT o.item_key, o.retailer, o.product_id, o.name, o.url, o.normal_price, o.promo_price, o.paid,
       CASE WHEN o.promo_price IS NOT NULL AND o.normal_price > 0
            THEN round((o.normal_price - o.promo_price) * 100 / o.normal_price, 1) END,
       o.in_stock, o.ago,
       CASE WHEN o.ago > 0 AND o.ago <> o.normal_price THEN round((o.normal_price - o.ago) * 100 / o.ago, 1) END,
       CASE WHEN i.pack_size > 0 THEN round(o.paid / i.pack_size * CASE WHEN i.pack_unit IN ('g', 'ml') THEN 1000 ELSE 1 END, 1) END,
       CASE i.pack_unit WHEN 'g' THEN 'per kg' WHEN 'ml' THEN 'per litre' WHEN 'pc' THEN 'per piece' END,
       COALESCE(i.category_3, i.category_2, i.category_1, 'Uncategorised'),
       i.ours, FALSE
FROM one o JOIN items i ON i.item_key = o.item_key;

UPDATE item_prices_now r SET in_our_groups = TRUE
WHERE NOT r.ours AND EXISTS (SELECT 1 FROM item_prices_now o
                             WHERE o.ours AND o.retailer = r.retailer AND o.compare_group = r.compare_group);

-- 2) Our items vs their cheapest rival ------------------------------------------------------
DELETE FROM price_position;
WITH ip AS (
    SELECT n.*, i.pack_size, i.pack_unit, i.uniform_name, i.supplier_group
    FROM item_prices_now n JOIN items i ON i.item_key = n.item_key
    WHERE n.per_unit IS NOT NULL
),
pairs AS (
    SELECT o.item_key, o.retailer, o.compare_group, o.price_paid AS our_price, o.per_unit AS our_per_unit, o.unit_label,
           r.item_key AS rival_item_key, r.uniform_name AS rival_name, r.supplier_group AS rival_supplier,
           r.price_paid AS rival_price, r.per_unit AS rival_per_unit,
           ROW_NUMBER() OVER (PARTITION BY o.item_key, o.retailer ORDER BY r.per_unit, r.item_key) AS rn,
           COUNT(*) OVER (PARTITION BY o.item_key, o.retailer) AS rivals,
           SUM((r.per_unit < o.per_unit)::int) OVER (PARTITION BY o.item_key, o.retailer) AS cheaper_rivals
    FROM ip o
    JOIN ip r ON r.retailer = o.retailer AND r.compare_group = o.compare_group AND r.pack_unit = o.pack_unit
             AND NOT r.ours AND r.pack_size BETWEEN o.pack_size * 0.5 AND o.pack_size * 2
    WHERE o.ours
)
INSERT INTO price_position (item_key, retailer, compare_group, our_price, our_per_unit, unit_label, rival_item_key,
                            rival_name, rival_supplier, rival_price, rival_per_unit, gap_pct, rivals, cheaper_rivals,
                            rival_median_per_unit, gap_vs_median_pct)
SELECT p.item_key, p.retailer, p.compare_group, p.our_price, p.our_per_unit, p.unit_label, p.rival_item_key, p.rival_name,
       p.rival_supplier, p.rival_price, p.rival_per_unit,
       round((p.our_per_unit - p.rival_per_unit) * 100 / NULLIF(p.rival_per_unit, 0), 1), p.rivals, p.cheaper_rivals,
       round(m.med, 1), round((p.our_per_unit - m.med) * 100 / NULLIF(m.med, 0), 1)
FROM pairs p
JOIN (SELECT item_key, retailer, percentile_cont(0.5) WITHIN GROUP (ORDER BY rival_per_unit)::numeric AS med
      FROM pairs GROUP BY 1, 2) m USING (item_key, retailer)
WHERE p.rn = 1;

-- 3) The action list --------------------------------------------------------------------------
DELETE FROM actions;

-- a) the same item of ours has a different normal price at different retailers
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail, metric_pct)
SELECT CASE WHEN spread >= 15 THEN 1 ELSE 2 END, 'Price differs between retailers', hi_retailer, grp, item_key, name, sup,
       format('%s: normal price %s%% higher at %s than at %s', name, round(spread)::int, hi_retailer, lo_retailer),
       prices_txt, spread
FROM (
    SELECT n.item_key, i.uniform_name AS name, i.supplier_group AS sup, n.compare_group AS grp,
           round((max(n.normal_price) - min(n.normal_price)) * 100 / NULLIF(min(n.normal_price), 0), 1) AS spread,
           (array_agg(n.retailer ORDER BY n.normal_price DESC))[1] AS hi_retailer,
           (array_agg(n.retailer ORDER BY n.normal_price))[1] AS lo_retailer,
           string_agg(n.retailer || ' ' || to_char(n.normal_price, 'FM999,999,990')
                      || CASE WHEN n.promo_price IS NOT NULL THEN ' (promo ' || to_char(n.promo_price, 'FM999,999,990') || ')' ELSE '' END,
                      ' · ' ORDER BY n.normal_price) AS prices_txt
    FROM item_prices_now n JOIN items i ON i.item_key = n.item_key
    WHERE n.ours
    GROUP BY 1, 2, 3, 4
    HAVING count(*) >= 2
) g
WHERE spread >= 5;

-- (How we sit vs rivals is a standing position, not news: it's on the "Price vs rivals" page,
--  not in this list. Only what changed, or what's out of line, goes here.)

-- b) we're cheaper per kg / litre / piece than every similar rival by a wide margin (room on price?)
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail, metric_pct)
SELECT 3, 'Cheapest by far', p.retailer, p.compare_group, p.item_key, i.uniform_name, i.supplier_group,
       format('%s at %s: %s%% cheaper %s than the cheapest similar rival', i.uniform_name, p.retailer,
              round(-p.gap_pct)::int, p.unit_label),
       format('Ours %s (%s %s). Cheapest of %s rivals: %s by %s at %s (%s %s).',
              to_char(p.our_price, 'FM999,999,990'), to_char(p.our_per_unit, 'FM999,999,990'), p.unit_label, p.rivals,
              p.rival_name, p.rival_supplier, to_char(p.rival_price, 'FM999,999,990'),
              to_char(p.rival_per_unit, 'FM999,999,990'), p.unit_label),
       p.gap_pct
FROM price_position p JOIN items i ON i.item_key = p.item_key
WHERE p.cheaper_rivals = 0 AND p.gap_pct <= -25 AND p.rivals >= 2;

-- d) rivals started promos in the last 7 days, in categories where we're listed at that retailer
WITH s AS (SELECT (now() AT TIME ZONE timezone)::date AS today FROM report_settings),
starts AS (
    SELECT c.product_id, max(c.date) AS started
    FROM v_price_changes c CROSS JOIN s
    WHERE c.change_type = 'promo_start' AND c.date >= s.today - 7
    GROUP BY 1
),
rp AS (
    SELECT n.retailer, n.compare_group, i.supplier_group AS sup, i.uniform_name, n.normal_price, n.promo_price,
           n.discount_pct, st.started
    FROM item_prices_now n
    JOIN starts st ON st.product_id = n.product_id
    JOIN items i ON i.item_key = n.item_key
    WHERE NOT n.ours AND n.in_our_groups AND n.promo_price IS NOT NULL
)
INSERT INTO actions (priority, action_type, retailer, category, supplier, headline, detail, metric_pct, since)
SELECT CASE WHEN max(discount_pct) >= 20 OR count(*) >= 3 THEN 1 ELSE 2 END, 'Rival promo', retailer, compare_group, sup,
       format('%s: %s new promo%s in %s at %s, up to %s%% off', sup, count(*), CASE WHEN count(*) > 1 THEN 's' ELSE '' END,
              compare_group, retailer, round(max(discount_pct))::int),
       string_agg(format('%s %s→%s', uniform_name, to_char(normal_price, 'FM999,999,990'), to_char(promo_price, 'FM999,999,990')),
                  ' · ' ORDER BY discount_pct DESC),
       max(discount_pct), min(started)
FROM rp GROUP BY retailer, compare_group, sup;

-- e) the normal price of one of our items is different from 7 days ago (net: up-then-back isn't news)
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail, metric_pct)
SELECT CASE WHEN abs(n.change_7d_pct) >= 10 THEN 1 ELSE 2 END,
       CASE WHEN n.change_7d_pct > 0 THEN 'Our price went up' ELSE 'Our price went down' END,
       n.retailer, n.compare_group, n.item_key, i.uniform_name, i.supplier_group,
       format('%s at %s: normal price %s → %s (%s%s%%) in 7 days', i.uniform_name, n.retailer,
              to_char(n.normal_7d_ago, 'FM999,999,990'), to_char(n.normal_price, 'FM999,999,990'),
              CASE WHEN n.change_7d_pct > 0 THEN '+' ELSE '' END, n.change_7d_pct),
       'Check it matches your price list for this retailer.', n.change_7d_pct
FROM item_prices_now n JOIN items i ON i.item_key = n.item_key
WHERE n.ours AND n.change_7d_pct IS NOT NULL;

-- e2) a similar rival's normal price moved 5%+ in 7 days, in a category where we're listed there
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail, metric_pct)
SELECT CASE WHEN n.change_7d_pct <= -10 THEN 1 ELSE 2 END,
       CASE WHEN n.change_7d_pct > 0 THEN 'Rival price went up' ELSE 'Rival price went down' END,
       n.retailer, n.compare_group, n.item_key, i.uniform_name, i.supplier_group,
       format('%s (%s) at %s: normal price %s → %s (%s%s%%)', i.uniform_name, i.supplier_group, n.retailer,
              to_char(n.normal_7d_ago, 'FM999,999,990'), to_char(n.normal_price, 'FM999,999,990'),
              CASE WHEN n.change_7d_pct > 0 THEN '+' ELSE '' END, n.change_7d_pct),
       'Rival in ' || n.compare_group || ', where you are listed at ' || n.retailer || '.', n.change_7d_pct
FROM item_prices_now n JOIN items i ON i.item_key = n.item_key
WHERE n.in_our_groups AND abs(n.change_7d_pct) >= 5;

-- f) our item shows out of stock on the retailer's website
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail)
SELECT 2, 'Out of stock online', n.retailer, n.compare_group, n.item_key, i.uniform_name, i.supplier_group,
       format('%s: out of stock on %s''s website', i.uniform_name, n.retailer),
       'Shoppers can''t buy it online. Check the order / delivery to this retailer.'
FROM item_prices_now n JOIN items i ON i.item_key = n.item_key
WHERE n.ours AND n.in_stock = FALSE;

-- g) one of our promos started in the last 7 days (good to know)
WITH s AS (SELECT (now() AT TIME ZONE timezone)::date AS today FROM report_settings)
INSERT INTO actions (priority, action_type, retailer, category, item_key, item_name, supplier, headline, detail, metric_pct, since)
SELECT 3, 'Our promo started', p.competitor, n.compare_group, i.item_key, i.uniform_name, i.supplier_group,
       format('%s at %s: promo %s → %s (−%s%%)', i.uniform_name, p.competitor,
              to_char(c.from_price, 'FM999,999,990'), to_char(c.to_price, 'FM999,999,990'), round(-c.change_pct)::int),
       'Started ' || to_char(c.date, 'DD Mon') || '.', -c.change_pct, c.date
FROM v_price_changes c CROSS JOIN s
JOIN product_links l ON l.product_id = c.product_id
JOIN products p ON p.id = c.product_id
JOIN items i ON i.item_key = l.item_key AND i.ours
LEFT JOIN item_prices_now n ON n.item_key = i.item_key AND n.retailer = p.competitor
WHERE c.change_type = 'promo_start' AND c.date >= s.today - 7;
