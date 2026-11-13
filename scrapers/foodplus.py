"""Foodplus (foodplus.co.ke), a Magento 2 shop.

How prices appear on the site:
    on promo:   "Special Price 1,695.00 KSh"  +  "Regular Price 2,420.00 KSh"
    no promo:   one price only
Each price sits in a tag with data-price-type ("finalPrice" or "oldPrice") and a clean
data-price-amount, so no text parsing is needed:
    normal_price = oldPrice if present, else finalPrice
    promo_price  = finalPrice when an oldPrice is present, else None

Pages come from config/targets.csv (competitor = Foodplus):
    page_type = category  ->  a listing such as .../small-appliances/kettles.html (all pages are followed)
    page_type = product   ->  a single product page such as .../ramtons-rm-226-corded-kettle-1-7l.html
"""
from __future__ import annotations

import time
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from bs4 import BeautifulSoup

import re

from ._utils import crawl_all, get

COMPETITOR = "Foodplus"
MAX_PAGES = 50          # safety cap per category (the site shows 60 products per page)
PAUSE_SECONDS = 1.0     # be polite between page requests


def _with_params(url: str, **params) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update({k: str(v) for k, v in params.items()})
    return urlunsplit(parts._replace(query=urlencode(query)))


def _prices(box) -> tuple[float | None, float | None]:
    """(normal, promo) from a Magento .price-box element."""
    if box is None:
        return None, None
    final = box.select_one('[data-price-type="finalPrice"]')
    old = box.select_one('[data-price-type="oldPrice"]')
    final_v = float(final["data-price-amount"]) if final and final.get("data-price-amount") else None
    old_v = float(old["data-price-amount"]) if old and old.get("data-price-amount") else None
    if old_v and final_v and final_v < old_v:
        return old_v, final_v
    return final_v or old_v, None


BASE = "https://foodplus.co.ke"
_CAT_URL = re.compile(r"^https://foodplus\.co\.ke/([a-z0-9-]+(?:/[a-z0-9-]+)*)\.html$")


def discover_categories() -> list[tuple[str, str]]:
    """The whole category tree from the site menu: [(site_category_path, url), ...], lowest level only.

    Menu links map directly onto the URL path (electronics/small-appliances/kettles.html), so the
    path's names come from each level's link text: "Electronics > Small Appliances > Kettles".
    """
    soup = BeautifulSoup(get(BASE + "/").text, "html.parser")
    names: dict[str, str] = {}
    for a in soup.select("nav a[href], .navigation a[href]"):
        m = _CAT_URL.match(a["href"].split("?")[0])
        text = a.get_text(" ", strip=True)
        if m and text and m.group(1) not in names:
            names[m.group(1)] = text
    paths = sorted(names)
    leaves = [p for p in paths if not any(q.startswith(p + "/") for q in paths)]

    def label(path: str) -> str:
        parts = path.split("/")
        return " > ".join(names.get("/".join(parts[:i]), parts[i - 1].replace("-", " ").title())
                          for i in range(1, len(parts) + 1))

    return [(label(p), f"{BASE}/{p}.html") for p in leaves]


def scrape_category(url: str, category: str | None, site_category: str | None = None, seen: set | None = None):
    seen = set() if seen is None else seen   # shared across categories in a full crawl: each product once
    for page in range(1, MAX_PAGES + 1):
        soup = BeautifulSoup(get(_with_params(url, p=page)).text, "html.parser")
        cards = soup.select("li.product-item")
        new = 0
        for card in cards:
            form = card.select_one("[data-product-sku]")
            link = card.select_one("a.product-item-link")
            if not link:
                continue
            sku = form["data-product-sku"] if form else card.select_one("[data-product-id]")["data-product-id"]
            if sku in seen:
                continue  # Magento repeats the last page when you ask past the end
            seen.add(sku)
            new += 1
            normal, promo = _prices(card.select_one(".price-box"))
            yield {
                "sku": sku,
                "name": link.get_text(" ", strip=True),
                "normal_price": normal,
                "promo_price": promo,
                "currency": "KES",
                # out-of-stock cards show a "stock unavailable" label instead of the Add button
                "in_stock": card.select_one(".action.tocart") is not None,
                "url": link.get("href"),
                "category": category,
                "site_category": site_category,
            }
        # stop when a page adds nothing new or there's no "next page" link
        if new == 0 or not soup.select_one(".pages-item-next, a.next"):
            break
        time.sleep(PAUSE_SECONDS)


def scrape_product(url: str, category: str | None):
    soup = BeautifulSoup(get(url).text, "html.parser")
    main = soup.select_one(".product-info-main") or soup
    sku = main.select_one('[itemprop="sku"]')
    name = main.select_one("h1")
    normal, promo = _prices(main.select_one(".price-box"))
    stock = main.select_one(".stock")
    yield {
        "sku": sku.get_text(strip=True) if sku else url.rstrip("/").split("/")[-1],
        "name": name.get_text(" ", strip=True) if name else None,
        "normal_price": normal,
        "promo_price": promo,
        "currency": "KES",
        "in_stock": ("available" in stock.get("class", []) and "unavailable" not in stock.get("class", [])) if stock else None,
        "url": url,
        "category": category,
    }


def scrape(targets):
    for t in targets:
        if t["page_type"] == "all":
            yield from crawl_all(COMPETITOR, discover_categories(), scrape_category, t["category"])
        elif t["page_type"] == "product":
            yield from scrape_product(t["url"], t["category"])
        else:
            yield from scrape_category(t["url"], t["category"])
        time.sleep(PAUSE_SECONDS)
