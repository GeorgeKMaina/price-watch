# Competitor Price Watch

Every day this project crawls the **whole catalogue** of each competitor: every product, its normal price, its
promo price, its stock status, and the competitor's own category path, such as
"Electronics > Small Appliances > Kettles". Everything goes into a free Supabase database, and Power BI reads it.
You choose in Power BI which categories, competitors or items to look at, including categories you don't sell in yet.

```
GitHub Actions (daily, one job per competitor) ─► Supabase PostgreSQL (free) ─► Power BI (daily refresh)
        ▲                                              ▲
 config/*.csv (you edit)              manual/carrefour/ (saved pages, imported on upload)
```

| Competitor | How | Coverage |
|---|---|---|
| Foodplus | full crawl, ~548 categories | whole catalogue |
| Naivas | full crawl, ~231 categories | whole catalogue |
| Quickmart | full crawl, ~70 categories, one fixed branch | whole catalogue for that branch |
| Carrefour | pages saved by hand (the site blocks automated tools) | the categories your team saves |

```
config/
  targets.csv        WHAT to crawl ("all" = whole catalogue, or single categories / products)
  product_map.csv    YOUR labels: category, matched item across competitors, key items
scrapers/            one file per competitor (foodplus, naivas, quickmart, carrefour importer)
  _template.py       copy this to add a competitor
  _utils.py          shared helpers: get() with retries, parse_price(), crawl_all()
pricewatch/
  run.py             runs the scrapers and saves to the database
  db.py              database code (change-only price storage)
  setup_db.py        creates the tables and views (first time)
  refresh.py         rebuilds the day-by-day table Power BI reads (after each crawl)
  relabel.py         re-applies product_map.csv to every product
  export.py          views to CSV, for Excel or a backup
sql/schema.sql       tables + the reporting views Power BI uses
manual/carrefour/    saved Carrefour pages (see HOW_TO_SAVE.txt)
notebooks/price_watch.ipynb   try scrapers, browse category trees, query the database
powerbi/             SETUP.md (build guide), queries.pq (connection), measures.dax
SUPABASE_SETUP.md    create the free database (15 minutes)
.github/workflows/scrape.yml  the daily schedule
```

## Getting started

1. **Create the database:** follow [SUPABASE_SETUP.md](SUPABASE_SETUP.md). This gives you `DATABASE_URL`.
2. **Put the project on GitHub** (below), and add `DATABASE_URL` as a repository secret.
3. **Run it once:** **Actions → Scrape prices → Run workflow**. It takes about 30–90 minutes, with the three sites running side by side.
4. **Build the report:** follow [powerbi/SETUP.md](powerbi/SETUP.md).

From the second day on, price changes and promos start appearing.

## Put it on GitHub

1. Create a new repository on github.com, e.g. `price-watch`.
   **Public is recommended:** GitHub Actions minutes are unlimited for public repos, but private repos get
   2,000 free minutes a month, and three full daily crawls need about 2,700. The repo only holds code: all the
   price data is in your private Supabase database, and the connection string is a hidden secret.
2. Upload this folder:
   - **Without git:** on the repo page, click **Add file → Upload files**, drag in *the contents* of this folder
     (including the `.github`, `config` and `manual` folders), and commit. If the browser skips `.github`, create
     `.github/workflows/scrape.yml` by hand with **Add file → Create new file**, and paste the contents in.
   - **With git:**
     ```bash
     git init && git add . && git commit -m "Initial commit"
     git branch -M main
     git remote add origin https://github.com/<you>/price-watch.git
     git push -u origin main
     ```
3. **Settings → Secrets and variables → Actions → New repository secret:** `DATABASE_URL`.
4. The schedule runs daily at 03:00 UTC (06:00 Nairobi). Change the `cron` line in the workflow if needed.

## What gets stored, and how

- **Every product the competitor lists,** with the site's category path, split into levels 1/2/3 for Power BI.
- **A price row only when something changes:** normal price, promo price or stock. A product seen again at the
  same price only gets its `last_seen` date updated. This keeps 30,000+ products within the free 500 MB for a
  long time. The views rebuild the daily picture from it.
- **What counts as a change** (each product compared with its previous observation):

  | Change | Meaning |
  |---|---|
  | Normal price increase | normal → normal, up |
  | Normal price drop | normal → normal, down |
  | Promo started | normal → promo |
  | Promo price changed | promo → different promo |
  | Promo ended | promo → normal |

- **Promo episodes** (`v_promotions`): start, end, length, lowest price and deepest discount. Promos already
  running on the first day a product was seen are flagged `started_before_tracking = Yes`, because their real
  start is unknown.
- **Products that disappear** from a site keep their history; their `last_seen` date stops moving.

## Choose what to track: `config/`

Edit in Excel (save as **CSV UTF-8**) or on GitHub (open the file, then click the pencil icon).

**`config/targets.csv`**: what to crawl. Out of the box: one `all` row per competitor (the whole catalogue).

| competitor | category | page_type | url | active |
|---|---|---|---|---|
| Naivas | | all | https://www.naivas.online/ | yes |
| Naivas | Kettles | category | https://www.naivas.online/kitchen-appliances/electric-kettles-air-friers | no |

- `all`: the scraper finds every category itself. `category`: one listing (all its pages).
  `product`: one product page.
- `category` (optional) is your label for everything from that row.
- `active = no` keeps the row but skips it.

**`config/product_map.csv`**: your labels on top of the site's categories.

| competitor | sku | name_contains | category | product_group | key_item |
|---|---|---|---|---|---|
| | | kettle | Kettles | | no |
| | | ramtons 324 | Kettles | Ramtons RM/324 kettle 1.5L | yes |

- A rule matches by exact `sku` or by `name_contains` (all the words, any order, ignoring case). A blank
  `competitor` means every competitor.
- `category`: *your* category. Use it to line up different sites' names: Naivas "Kitchen Appliances" and
  Foodplus "Small Appliances" can both become "Kettles".
- `product_group`: the same item at every competitor. Power BI compares them side by side.
- `key_item = yes`: the items you care about most.
- Saving the file on GitHub relabels **every product, including history**, within about a minute.

## Your item list (from the retailers' monthly files)

The dashboard's item pages are built from the files retailers share with you, not from website
names. Each product gets **one uniform name** across retailers, keyed by barcode.

- **Where the files are:** `config/settings.json`, `retailer_files_folders`. Change the folders there.
  `python -m pricewatch.items` reads them. Only item details are read; sales figures are never saved.
- **Which items you follow:** `config/watchlist.csv` (supplier, brand or name text, or a barcode).
  An empty file means you follow every item.
- **Matching to the websites:** `python -m pricewatch.link`.
  - Foodplus and Carrefour match by item code.
  - Naivas matches by item code (ITEMLOOKUPCODE) or barcode.
  - Quickmart shows no codes online, so it is matched by name and pack size.
- **Matches that are unsure** are listed on the Power BI page **Matching to check**, and in
  `data/needs_matching.csv`. To confirm one, add a row to `config/confirmed_matches.csv`:
  `retailer,retailer_code,web_sku`. Use `web_sku = none` if the item isn't sold online.
- **Daily run:** `run_quickmart.bat` (PC, 10:00) runs Quickmart, then items, then link, then refresh.
  By then GitHub's 06:00 run has already collected Foodplus and Naivas.

Naivas: since its new website (Nov 2026), its full catalogue is read department by department
from the source its own website uses. The owner approved this. It covers about 11,900 products.

## Adding a competitor

Copy `scrapers/_template.py` to `scrapers/<name>.py`. A scraper yields one dict per product:

```python
{"sku": "...", "name": "...", "normal_price": "KSh 18,999", "promo_price": "KSh 16,499",  # or None
 "in_stock": True, "url": "...", "currency": "KES",
 "site_category": "Electronics > Small Appliances > Kettles", "category": t["category"]}
```

For a full catalogue, write `discover_categories()` (returns `[(path, url), ...]`) and let
`crawl_all()` in `_utils.py` do the rest. `foodplus.py` is the simplest example. Use section 6 of the notebook to
see how a new site shows its prices.

## Carrefour: saved pages instead of scraping

Carrefour Kenya's site blocks automated tools (it returns an empty page), and this project doesn't try to get
around that. Someone opens a Carrefour category in their normal browser, saves it with Ctrl+S as **Webpage,
Single File (.mhtml)** into `manual/carrefour/<Category>/`, and uploads it to the repo. It's imported within a
minute, under the date it was saved. The steps are in [manual/carrefour/HOW_TO_SAVE.txt](manual/carrefour/HOW_TO_SAVE.txt).
Importing the same file twice adds nothing.

## Run locally (optional)

Put `DATABASE_URL=...` in a `.env` file in this folder (see SUPABASE_SETUP.md), then in the Anaconda Prompt:

```bash
pip install psycopg2-binary
```
```bash
python -m pricewatch.run --only naivas --max-categories 3
```

That command crawls just the first 3 Naivas categories: a one-minute test. Other commands:
`python -m pricewatch.refresh` (rebuild the daily table), `python -m pricewatch.relabel`,
`python -m pricewatch.export` (views to CSV in `exports/`). The notebook
[notebooks/price_watch.ipynb](notebooks/price_watch.ipynb) does the same interactively.

## If a site blocks the scraper

The scrapers don't try to get around blocks: no rotating IP addresses or proxies, no disguising. A block is the
site saying no. Instead:

1. **Go slower:** raise `PAUSE_SECONDS` in that site's scraper (e.g. 2 → 4). A full crawl then takes longer but
   asks less of the site.
2. **Run that site from your own connection:** GitHub's servers are shared with many other users, so some sites
   block them on sight. Run `python -m pricewatch.run --only quickmart` on an office PC (Windows Task Scheduler
   can run it daily). It writes to the same database, and the other competitors keep running on GitHub.
3. **Wait it out:** most blocks lift within a day. Whatever was collected before the block is saved, and the run is
   marked `partial` in Power BI's Scraper health page.
4. **Ask for the data,** or use a provider that licenses it.

**Quickmart branches:** Quickmart prices and product IDs are per branch. `LOCATION` in `scrapers/quickmart.py`
picks the branch (Nairobi CBD by default). Set it to the area you compete in, then keep it fixed.

## Things to know

- **Crawl load:** a full daily crawl is about 800 pages on Foodplus, 750 on Naivas and 950 on Quickmart, at one
  page every 1–2 seconds per site. That's polite, but it's real traffic. Crawl daily, not hourly.
- **A product listed in several categories** is stored once, under the first real category it's found in. Promotion
  collections such as "Deals" or "Special Offers" are crawled last, so they never become a product's category.
- **Supabase free plan:** 500 MB, about a week of inactivity before pausing (the daily run prevents it), and a
  data-transfer allowance (one Power BI refresh a day fits). See SUPABASE_SETUP.md.
- **Be fair:** stay within each site's terms and robots.txt. All three crawled sites allow crawling in robots.txt.
