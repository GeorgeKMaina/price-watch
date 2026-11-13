# Power BI setup

Power BI reads the reporting views in your Supabase database, which the daily GitHub run fills.
Plan on about an hour the first time.

```
GitHub Actions (daily, full catalogues) ─► Supabase database ─► Power BI (scheduled refresh)
```

**Do the first run before building the report** (see [SUPABASE_SETUP.md](../SUPABASE_SETUP.md)). After one
day you have every product and its current price. Price changes and promos start appearing from the second day.

The views Power BI reads:

| View | Rows | What it is |
|---|---|---|
| `v_products` | ~30,000+ | every product at every competitor, with the site's category levels and your labels |
| `v_daily_prices` | products × 21 days | the price of every product on every day of the last 21 days (daily grid, heatmaps) |
| `v_price_periods` | ~1 per product + 1 per change | the **full** price history in compact form, for long-range evolution charts |
| `v_price_changes` | grows daily | every normal-price increase / drop and promo start / change / end, full history |
| `v_promotions` | grows daily | one row per promo episode, full history |
| `v_scraper_runs` | 4 a day | each crawl's status, products seen and errors |

Files in this folder:
- `queries.pq`: the Power Query (M) code, one block per query
- `measures.dax`: the Date table and all the measures

---

## 1. Connect to the data (Power Query)

Pick **one** connection option. Both read the same views.

**Option A: PostgreSQL connector (simplest)**
1. Open **Power BI Desktop → Blank report**, then **Home → Transform data**.
2. **Home → Manage Parameters → New**: `SupabaseHost` (Text) = your pooler host and port, e.g.
   `aws-0-eu-central-1.pooler.supabase.com:5432` (see SUPABASE_SETUP.md step 6).
3. Add the option A `fnView` block from `queries.pq` (**New Source → Blank Query → Advanced Editor**, paste,
   then rename the query to `fnView`).
4. Add the six table queries (`Products`, `PricePeriods`, `DailyPrices`, `PriceChanges`, `Promotions`, `ScraperRuns`) the same way.
5. When asked for credentials, choose **Database**: user `postgres.<your-project-ref>`, and your database password.
   If it asks about encryption, keep it on.
6. **Close & Apply.**
7. After publishing (section 5), if the Power BI Service says scheduled refresh needs a **gateway** for this
   source, switch to option B. You only need to replace `fnView` and add two parameters; nothing else changes.

**Option B: Supabase web API (refreshes in the Power BI Service with no gateway)**
1. In Supabase, set **Project settings → API → Max rows** to `100000`.
2. Create parameters `SupabaseUrl` (e.g. `https://abcdefghijkl.supabase.co`) and `SupabaseKey` (the **anon public**
   key). That key can only *read* the reporting views.
3. Add the option B `fnView` block and the six table queries from `queries.pq`.
4. When asked for credentials for the supabase.co address, choose **Anonymous**: the key travels in the request
   headers. In the Service, set the same data source to **Anonymous** too, and tick **Skip test connection**.
5. **Close & Apply.**

## 2. Model

In **Model view**, create these relationships (all single direction, one-to-many):

| From (one side) | To (many side) | Note |
|---|---|---|
| `Products[product_id]` | `DailyPrices[product_id]` | |
| `Products[product_id]` | `PriceChanges[product_id]` | |
| `Products[product_id]` | `Promotions[product_id]` | |
| `Products[product_id]` | `PricePeriods[product_id]` | no relationship to Date: the "… On Date" measures handle dates |
| `Date[Date]` | `DailyPrices[date]` | |
| `Date[Date]` | `PriceChanges[date]` | |
| `Date[Date]` | `Promotions[start_date]` | the date slicer picks promos that *started* in the period |

Then:
1. **Modeling → New table**: paste the `Date` block from `measures.dax`. Select the new table, then
   **Table tools → Mark as date table → Date**. Set `Month` to sort by `Month Sort`, and `Weekday` by `Weekday No`.
2. **Home → Enter data**: create an empty table named `Measures`, then **Load**.
3. Select `Measures`, then **Modeling → New measure** for each block in `measures.dax`.
4. Formatting:
   - Every measure ending in `%`: Percentage.
   - Price measures and markers: Whole number with thousands separator.
   - `PriceChanges[change_pct]` is already a percent figure (e.g. 4.5 means 4.5%), so leave it as Decimal.
5. Sort-by-column settings: `PriceChanges[change_label]` by `change_order`, and `Promotions[start_weekday]` by `start_weekday_no`.
6. Hide the helper columns from report view: `product_id`, `promo_id`, `change_order`, `Month Sort`, `Weekday No`, `start_weekday_no`.

## 3. Slicers on every page (including the product search)

Build this row once on the first page, copy it to the others, then use **View → Sync slicers**
to tick every page, so a choice made on one page carries across all of them.

| Slicer | Field | Settings |
|---|---|---|
| **Search** | `Products[search_text]` (or `Products[name]`) | see below |
| Site category | `Products[site_category_1]`, `site_category_2`, `site_category_3` | one **hierarchy slicer**: drag all three into one slicer and expand level by level. Each competitor's *own* categories, for exploring any part of the catalogue, including ones you don't sell in |
| Category | `Products[category]` | Dropdown, multi-select. *Your* category: from `product_map.csv` if set, otherwise the site's top-level category |
| Competitor | `Products[competitor]` | Tile buttons |
| Item | `Products[item]` | Dropdown with search; the matched item across competitors |
| Key items only | `Products[key_item]` | Tile, pick "Yes" |
| Dates | `Date[Date]` | Between, or Relative date: Last 30 days |

**Type-to-search product box**
- **Option A (works everywhere, including the Service):** add a **Slicer** with `Products[search_text]`.
  Set **Slicer settings → Style: Dropdown**, then use the visual's **"…" menu → Search**.
  Users type part of a name, such as `a15`, `galaxy`, `hp 250` or `smartphones`. The list narrows as they type,
  and they tick what they want. Matching is "contains", not case-sensitive.
  Under **Selection**, turn **Multi-select with Ctrl** off so ticking several items is easy.
- **Option B (free text):** if your Power BI has the **Text slicer** visual (on older versions, turn it on under
  **File → Options → Preview features**), add it with `Products[search_text]`. Users type `a15`, press Enter,
  and every matching product is selected. Check that it works in the Service for your tenant first.

`search_text` combines the name, competitor, site category, your category, SKU and group, so "naivas kettle"
or "small appliances" works.

**With 30,000+ products**, start every visual filtered (a category, a competitor, key items, or a search).
Tables of 30,000 rows are slow to scroll and don't tell you much. The **Category explorer** (page 0 below)
is the place to start when looking at a category you don't sell in yet.

---

## 4. Report pages

### Page 0: Category explorer (the whole market, before you pick a slice)
For looking into categories, including ones you don't play in yet.
- **Matrix "Market map"**: Rows = `Products[site_category_1]` → `site_category_2` → `site_category_3`
  (expandable); Columns = `Products[competitor]`; Values = `Products Tracked`.
  This shows who carries how much of each category.
- Add `Products On Promo Now` and `Share On Promo %` as extra values, or on a second copy of the matrix.
  This shows where the discounting is happening.
- **Table "Price ladder"** for the category picked in the slicer: `Products[competitor]`, `Products[name]`,
  `Latest Price Paid`, `Latest Normal Price`, `Promo Discount %`, sorted by price. This gives the entry,
  mid and premium price points in a category you're thinking of entering.
- **Card**: `Delisted Products` (no longer on the competitor's site for more than 3 days).

Category names differ between competitors (Naivas "Kitchen Appliances" vs Foodplus "Small Appliances").
To compare across competitors, map them to your own names in `config/product_map.csv`
(e.g. `name_contains = kettle` → `category = Kettles`), and use the **Category** slicer.

### Page 1: Alerts (what changed)
- **Cards:** `Normal Price Increases`, `Normal Price Drops`, `Promos Started`, `Promos Ended`, `Products On Promo Now`, `New Listings`.
- **Table "Price changes".** Columns: `PriceChanges[date]`, `Products[competitor]`, `Products[category]`, `Products[name]`,
  `PriceChanges[change_label]`, `from_price`, `to_price`, `change_pct`. Sort by date, descending.
  - Conditional formatting: on `change_pct` → **Icons** (▲ red when > 0, ▼ green when < 0);
    on `change_label` → **Font color** rules (increase red, drop green, promo started purple).
- **Slicer** on `PriceChanges[change_label]`, so users can show only increases, only promos, and so on.
- **Stacked column chart:** X = `Date[Week Start]`, Y = `All Price Changes`, Legend = `PriceChanges[change_label]`.

### Page 2: Price evolution (with markers for drops and promos)
The main chart: one panel per competitor, with dots on the days a price dropped, rose or a promo started.

1. Add a **Line chart**.
   - **X-axis:** `Date[Date]` (set the type to Continuous)
   - **Y-axis**, in this order: `Latest Normal Price`, `Latest Promo Price`, `Drop Marker`, `Increase Marker`, `Promo Start Marker`
   - **Small multiples:** `Products[competitor]`
2. Format pane → **Lines**, per series:
   - `Latest Normal Price`: width 2, solid, dark blue
   - `Latest Promo Price`: width 2, **dashed**, purple. Its gaps are the days with no promo.
   - The three markers: **width 0**, so no line shows
3. Format pane → **Markers**: turn on, then per series:
   - `Drop Marker`: ▼ triangle down, green, size 8
   - `Increase Marker`: ▲ triangle, red, size 8
   - `Promo Start Marker`: ● circle, purple, size 8
   - normal and promo price lines: markers off
4. Format pane → **Legend** on, at the top. Rename the series in the visual's field well
   (double-click), e.g. "Normal price", "Promo price", "Price drop", "Price increase", "Promo started".
5. **Tooltips:** add `Promo Discount %` to the tooltip well.

Use it with the **Item** or **Search** slicer set to one item: you get that item at every competitor, side by side,
with every drop and promo marked. Under it, add a **table** for the same selection: `PriceChanges[date]`,
`Products[competitor]`, `change_label`, `from_price`, `to_price`, `change_pct`.

> The markers rely on a single item being selected. With many items selected, each point is an average,
> so add a small text box: "Pick one item to see its price evolution".

**Long-range evolution (months, the full history):** the chart above uses `DailyPrices`, so it covers the last
21 days. For longer periods, make a copy of it and swap in `Normal Price On Date`, `Promo Price On Date` and
`Price Paid On Date` (from `PricePeriods`), with X = `Date[Date]` or `Date[Week Start]` and the date slicer
set to the range you want. This works for any date since tracking began.

A second, simpler chart for comparing categories: **Line chart** with X = `Date[Week Start]`,
Y = `Avg Normal Price Change % (period)`, Legend = `Products[category]`. Each point is the average change
within that week, so spikes show the weeks a category moved.

### Page 3: Daily prices (last 10 days, Category > Item > Retailer)
This is your spreadsheet layout, kept up to date automatically:

```
                              Thu 12 Nov  Wed 11 Nov  Tue 10 Nov  ...  (newest first)
▾ Smartphones
   ▾ Samsung Galaxy A15
        Jumia                   16,499     18,999 ▼    19,499      <- purple = on promo
        Kilimall                18,110     18,110      18,420
        Avechi                  18,780 ▲   18,500      18,500      <- green text = cheapest that day
   ▸ Xiaomi Redmi 13C          (collapsed)
▸ Laptops
```

**Build it**
1. Set `Date[Day Label]` → **Sort by column → Days Ago**, so the newest day is on the left.
2. Add a **Matrix** visual:
   - **Rows:** `Products[category]`, `Products[item]`, `Products[competitor]`
   - **Columns:** `Date[Day Label]`
   - **Values:** `Daily Price`
3. **Show the last 10 days:** add a slicer on `Date[Days Ago]`, style **Less than or equal to**, and set it to `9`.
   Users can drag it to 6 (7 days) or 29 (30 days). Then **Format → Edit interactions**: stop the page's other date
   slicer from filtering this matrix, so the two don't fight. Or simply don't sync the main date slicer to this page.
   To fix it at 10 days with no slicer, use a visual-level filter instead: `Days Ago` is less than or equal to 9.
4. Format the matrix:
   - **Layout:** stepped. **Row subtotals:** off. **Column subtotals:** off.
   - **Row headers → +/- icons:** on, so users can expand and collapse categories and items.
     Right-click a row → **Collapse → Collapse all** to start the page with categories closed.
   - **Values:** font size 9–10, so 10 days fit on screen.
5. Conditional formatting on `Daily Price` (Values well → field menu → **Conditional formatting**):
   - **Background color** → Format style **Field value** → based on `Daily Cell Colour`.
     Promo days turn light purple, and promo start days darker purple.
   - **Font color** → **Field value** → based on `Daily Font Colour`. The cheapest retailer each day shows green.
   - **Icons** → Format style **Rules** → based on `Daily Change Direction`:
     `= 1` → ▲ red, `= -1` → ▼ green. Icon position: right of the data. No icon on unchanged days.
6. **Tooltips:** add `Daily Price Detail` to the tooltip well. Hovering a cell shows
   "Normal 18,999 · Promo 16,499 (−13%)".

**Handling many SKUs** (from most to least effective)
- **Start collapsed:** categories closed, so the page is a short list, and users expand what they need.
- **Key items only:** the `Products[key_item]` slicer set to "Yes" shows just the SKUs flagged in `product_map.csv`.
  This is usually the main view.
- **Search box + category slicer** (the synced slicers): type `a15` or pick "Smartphones".
- **"Changed only" toggle:** add `Changes In View` as a **visual-level filter**, set to `is greater than 0`.
  The matrix then hides every retailer row where nothing happened in the days shown. Keep a copy of the page
  without this filter for the full view, or put the two on a bookmark toggle.
- **Matched items:** `item` is the `product_group` from `product_map.csv`, so the same phone at 4 retailers sits under
  one item, as in your sheet. Items with no group appear under their own name with a single retailer row.
- **Export:** the matrix's **"…" → Export data** gives the same grid as an Excel file whenever someone asks for it.

### Page 4: Promotions (how often, when, how deep)
- **Cards:** `Times On Promo`, `Promo Days`, `Avg Promo Length (days)`, `Avg Promo Discount %`, `Deepest Discount %`, `Promos Running Now`.
- **Table "Promo history per item".** One row per item per competitor: `Products[competitor]`, `Products[name]`, `Times On Promo`,
  `Promo Days`, `Avg Promo Length (days)`, `Avg Promo Discount %`, `Deepest Discount %`, `Last Promo Start`,
  `Days Since Last Promo`, `Avg Days Between Promos`. Sort by `Times On Promo`, descending.
  Add data bars on `Times On Promo` and `Avg Promo Discount %`.
- **Table "Promo dates".** One row per promo episode: `Products[competitor]`, `Products[name]`, `Promotions[promo_no]`,
  `Promotions[promo_period]`, `Promotions[days_on_promo]`, `Promotions[normal_price_at_start]`,
  `Promotions[lowest_promo_price]`, `Promotions[max_discount_pct]`, `Promotions[ongoing]`. Sort by `start_date`, descending.
- **Promo calendar (matrix heatmap).** A visual timeline of when each item was on promo:
  Rows = `Products[competitor]`, `Products[name]`; Columns = `Date[Week Start]` (or `Date[Date]` for a short period);
  Values = `Share On Promo %`. Under **Cell elements**, set the background colour from white (0%) to purple (100%), and
  turn off the number text so only the colour shows.
- **Column chart "Which weekday do promos start?"**: X = `Promotions[start_weekday]`, Y = `Times On Promo`, Legend = `Products[competitor]`.
- **Column chart "How deep are the discounts?"**: X = `Promotions[discount_band]`, Y = `Times On Promo`, Legend = `Products[competitor]`.

### Page 5: Insights (who's aggressive, what's moving)
- **Line chart "Promo intensity"**: X = `Date[Week Start]`, Y = `Share On Promo %`, Legend = `Products[competitor]`.
  This shows which competitor discounts most, and when promo waves happen (pay day, holidays).
- **Clustered bar chart "Price behaviour by competitor"**: Y = `Products[competitor]`,
  X = `Avg Normal Price Change % (period)`, `Share On Promo %` and `Out Of Stock %`.
- **Matrix "Category × competitor"**: Rows = `Products[category]`, Columns = `Products[competitor]`,
  Values = `Avg Normal Price Change % (period)`. Colour the cells with a diverging scale, green below 0 and red above.
  This shows at a glance which competitor is raising prices in which category.
- **Table "Biggest movers"**: `Products[competitor]`, `Products[name]`, `Normal Price Change % (period)`, `Price Moves Per Product`.
  Use the visual's **Top N** filter: top 10 by absolute change.
- **Table "Who's cheapest"**: Rows = `Products[item]`; Values = `Cheapest Competitor`, `Cheapest In Group`.
  This needs `product_group` set in `config/product_map.csv`.
- **Cards:** `Avg Increase %`, `Avg Drop %`, `Price Moves Per Product`.

### Page 6: Latest prices
- Table: `Products[competitor]`, `Products[category]`, `Products[name]`, `Latest Normal Price`, `Latest Promo Price`,
  `Promo Discount %`, `Latest Price Paid`, `Vs Cheapest %`, `Normal Price Change % (period)`.
  Colour `Vs Cheapest %` from green (0%) to red. Set `Products[url]` to Data category **Web URL** and add it
  so users can click through to the product page.

### Page 7: Scraper health
- Table of `ScraperRuns`: `started_at`, `competitor`, `status`, `items_seen`, `items_changed`, `error`. Sort by `started_at`, descending.
- Cards: `Failed Runs` (with the date slicer set to Today) and `Products Seen Last Run`.
  - **failed:** nothing was collected (the site is down, blocking, or changed its layout).
  - **partial:** the crawl stopped part-way; prices collected up to then were saved.
  - A sudden drop in `items_seen` for a competitor usually means some categories failed. The Actions log lists them.

---

## 5. Publish and refresh daily

1. **Home → Publish** to a workspace in the Power BI Service.
2. In the Service, open the semantic model's **Settings**:
   - **Data source credentials**: option A: Database (user and password); option B: Anonymous with
     **Skip test connection** ticked. Privacy level: **Organizational**.
   - **Scheduled refresh**: on, daily at **09:00 (UTC+03:00 Nairobi)**. The crawl starts at 06:00 Nairobi
     and takes up to about 2 hours with three full catalogues, and GitHub sometimes starts it late.
     One refresh a day keeps you well inside Supabase's free data-transfer allowance.
3. Share the report or create an App. Sharing with other people needs Power BI Pro (or Premium/Fabric capacity).

When you edit `config/product_map.csv` (new categories, groups, key items), GitHub relabels every product in the
database within about a minute. Refresh Power BI to see the new labels; the whole history is relabelled.

## Troubleshooting

- **"The column X was not found":** a view's column was renamed. Re-open the Power Query step that failed.
- **Option A asks for a gateway in the Service:** switch to option B (section 1).
- **Option B returns only 1,000 rows per table:** Supabase's **Max rows** is still 1000. Set it to 100000
  (Project settings → API), or lower `PageSize` in `fnView` to match.
- **Can't connect at all:** check you used the **Session pooler** host, not `db.<ref>.supabase.co`, and that
  the project isn't paused (Supabase dashboard → Restore).
- **Markers show on every day:** the marker line width isn't 0, or the marker measure is missing `IF ( ... > 0, ... )`.
- **Lots of products in "Uncategorised":** they came from a single-category or product row in `targets.csv`
  with no category and no matching rule. Full-catalogue (`all`) rows always carry the site's categories.
- **Data looks a day old:** check the Actions tab. The crawl may still be running, or may have started late.
  Then refresh again.
- **Numbers come in as text, or 1,299 becomes 1.299:** the queries force the `en-US` culture. Keep that argument
  on `Table.TransformColumnTypes`.
