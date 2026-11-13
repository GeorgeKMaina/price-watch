# Supabase setup (free database)

The scrapers write into a free Supabase PostgreSQL database, and Power BI reads from it.
Plan on about 15 minutes. You need a GitHub account (you can sign in to Supabase with it).

## 1. Create the project

1. Go to [supabase.com](https://supabase.com), then **Start your project**, and sign in with GitHub.
2. **New project**:
   - **Name:** `price-watch`
   - **Database password:** click **Generate a password**, then **save it somewhere safe** (a password manager).
     You need it in steps 2 and 4.
   - **Region:** the closest one to Kenya (e.g. *Frankfurt* or *London*, or *South Africa* if listed).
   - **Plan:** Free.
3. Wait a minute or two while it sets up.

## 2. Copy the connection string

1. In the project, click **Connect** (top bar).
2. Choose **Session pooler** (not "Direct connection"). The direct host doesn't work from GitHub Actions.
3. Copy the URI. It looks like:
   ```
   postgresql://postgres.abcdefghijkl:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
4. Replace `[YOUR-PASSWORD]` with the database password from step 1. This full line is your **DATABASE_URL**.
   Treat it like a password: never paste it into a file you commit or share.

## 3. Give it to GitHub (for the daily runs)

1. Open your repo on github.com, then **Settings → Secrets and variables → Actions → New repository secret**.
2. **Name:** `DATABASE_URL`. **Secret:** paste the line from step 2. Then **Add secret**.

GitHub never shows it again, not even in logs, and nobody can read it from the repo, even when the repo is public.

## 4. Use it on your PC (notebook / Anaconda Prompt), optional

1. In the project folder, create a file named `.env` (it's already in `.gitignore`, so it won't be uploaded):
   ```
   DATABASE_URL=postgresql://postgres.abcdefghijkl:YOUR-PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
2. In the Anaconda Prompt, inside the project folder:
   ```bash
   pip install psycopg2-binary
   ```
   ```bash
   python -m pricewatch.setup_db
   ```
   It should print `Database ready (0 products stored).` That creates all the tables and views.
   (The daily GitHub run would also create them on its first run.)

## 5. First run

On GitHub: **Actions → Scrape prices → Run workflow**. The full crawls take about 30–60 minutes, with the
three sites running side by side. Then, in Supabase, open **Table Editor → products** to see them arrive.

## 6. Settings for Power BI

- **For the web-API connection (option B in `powerbi/queries.pq`):** in **Project settings → API**, set
  **Max rows** to `100000`, and copy the **Project URL** and the **anon public** key.
  The anon key can only read the reporting views (`v_products`, `v_daily_prices`, `v_price_changes`,
  `v_promotions`, `v_scraper_runs`). The tables themselves are locked, and nothing can be changed with it.
- **For the PostgreSQL connection (option A):** use the pooler host and port from step 2
  (e.g. `aws-0-eu-central-1.pooler.supabase.com:5432`), user `postgres.abcdefghijkl`, and the DB password.

## Free-plan limits to know (check the current numbers on supabase.com/pricing)

- **Database size: 500 MB.** Measured on a full test crawl of all three sites (34,000 products): **27 MB** on day one.
  Prices are stored only when they change, so it grows by roughly 3–10 MB a month, depending on how often
  competitors change prices. The day-by-day table adds about 70 MB at 21 days. To check usage: **Project settings → Usage**.
- **Inactive projects are paused after about a week without activity.** The daily run keeps it active.
  If it ever pauses, click **Restore** in the dashboard.
- **Data transfer (egress), a few GB a month.** Each Power BI refresh downloads the reporting views, roughly
  50–100 MB with the full catalogues, mostly the day-by-day table. One refresh a day fits; avoid hourly refreshes.
  The day-by-day table covers the last **21 days** by default. Longer trends come from `v_price_periods`, which
  stays small. To change the number of days, run this in the **SQL Editor**:
  ```sql
  UPDATE report_settings SET daily_days = 30;
  ```
  The next daily run applies it. More days means bigger refreshes.
