"""Drop one .py file per competitor in this folder.

Each file needs:
    COMPETITOR = "Display name"        (matches the competitor column in config/targets.csv)
    def scrape(targets):  -> list (or generator) of dicts with keys
        (targets = active rows from config/targets.csv; scrape() with no argument also works)
        sku           (required) unique id of the product on that site
        name          (required)
        normal_price  (required) number or text like "KSh 1,299.00"
        promo_price   (optional) discounted price, None when no promo
        url, currency, in_stock, category, site_category   (optional)

Files starting with "_" are ignored (see _template.py).
"""
