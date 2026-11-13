"""Copy this file to scrapers/<competitor>.py (no leading underscore) and paste your code in.

The runner needs two things from the file: COMPETITOR and scrape().

scrape(targets) receives the ACTIVE rows for this competitor from config/targets.csv:
    [{"category": "Smartphones", "page_type": "category", "url": "https://..."}, ...]
so which pages get scraped is controlled from that file, not from code.
(If your code has its URLs built in, define scrape() with no argument instead; it still works.)

Whole catalogue (page_type = all): write discover_categories() returning
    [("Electronics > Small Appliances > Kettles", "https://.../kettles"), ...]   (lowest level only)
and a scrape_category(url, category, site_category, seen) like the one below, then:
    yield from crawl_all(COMPETITOR, discover_categories(), scrape_category, t["category"])
See foodplus.py for a complete example.
"""
from ._utils import crawl_all, get  # noqa: F401  (get: requests with retries + browser-like headers)

COMPETITOR = "Competitor Name"  # must match the competitor column in config/targets.csv


def scrape(targets):
    for t in targets:
        # ---- paste / call your existing scraping code here ----
        # html = get(t["url"]).text
        # if t["page_type"] == "category": parse the listing (and follow "next page" links)
        # else:                            parse a single product page
        rows = []
        # rows.append({
        #     "sku": "SAMSUNG-A15-128",       # required: stable id for the product on THIS site
        #     "name": "Samsung Galaxy A15 128GB",
        #     "normal_price": "KSh 18,999",   # required: the regular price (often the struck-through one on a sale)
        #     "promo_price": "KSh 16,499",    # optional: the discounted price; None / "" when there is no promo
        #     # Both accept numbers or text; they're parsed automatically.
        #     # A promo_price equal to or above normal_price is ignored (not a real promo).
        #     "currency": "KES",              # optional
        #     "in_stock": True,               # optional
        #     "url": "https://...",           # optional
        #     "category": t["category"],      # keep this: tags the item with your category
        #     "site_category": "Electronics > Small Appliances > Kettles",  # the site's own category path
        #     "group": None,                  # optional: better set in config/product_map.csv
        # })
        yield from rows
