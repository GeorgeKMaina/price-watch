"""Quickmart (quickmart.co.ke).

How the site works:
  * Prices and stock are per BRANCH. Category and search pages redirect to the homepage
    until a delivery location is set, exactly like a shopper typing an address.
    The scraper sets one fixed location (LOCATION below) at the start of each run, so it
    always sees the same branch's prices and the history stays comparable day to day.
  * On a promo the card shows the current price in .products-price-new and the
    struck-through normal price in del.products-price-old. Without a promo there's
    only .products-price-new.
        normal_price = products-price-old if present, else products-price-new
        promo_price  = products-price-new when products-price-old is present, else None
  * Category pages show 30 products per page; page N is <category-url>?page-N.

Politeness: one session per run, a pause between every request, and no workarounds if
the site refuses access. If Quickmart blocks the scraper, the run fails with a clear message
(see the README section "If a site blocks the scraper").

Pages come from config/targets.csv (competitor = Quickmart):
    page_type = category  ->  e.g. https://www.quickmart.co.ke/small-kitchen-home-appliance
    page_type = product   ->  e.g. https://www.quickmart.co.ke/colgate-max-fresh-spicy-130g-66
"""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from ._utils import HEADERS, crawl_all, parse_price

COMPETITOR = "Quickmart"
BASE = "https://www.quickmart.co.ke"

# The delivery location that decides which branch's prices you see. Change it to the
# area you compete in; keep it fixed afterwards so prices stay comparable over time.
#   Quickmart Kiambu (Thindigua, Kiambu Road) = branch 3: the biggest in sales and range.
#   (Until 17 Nov 2026 this was central Nairobi = branch 16, which doesn't stock e.g. Alpro.
#   Product ids are per branch, so a new branch starts a new price history.)
LOCATION = {
    "country": "KE",
    "state": "Kiambu County",
    "city": "Kiambu",
    "lat": "-1.2030",
    "lng": "36.8350",
    "address": "Thindigua, Kiambu, Kenya",
}
EXPECTED_BRANCH = "3"   # warn if Quickmart starts sending this location to another branch

MAX_PAGES = 60          # safety cap per category (30 products per page)
PAUSE_SECONDS = 2.0     # between requests


class Blocked(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(s: requests.Session, url: str) -> requests.Response:
    for attempt in range(1, 4):
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(PAUSE_SECONDS * attempt * 2)
            continue
        if r.status_code in (403, 429) or "cf-chl" in r.text[:5000] or "Just a moment..." in r.text[:2000]:
            raise Blocked(
                f"Quickmart refused the request (HTTP {r.status_code}). The site is blocking this IP "
                "address for now. The scraper doesn't try to get around blocks: see the README "
                "section 'If a site blocks the scraper'."
            )
        r.raise_for_status()
        time.sleep(PAUSE_SECONDS)
        return r
    raise RuntimeError("unreachable")


def _set_location(s: requests.Session) -> str:
    """Choose a branch the way a shopper does: by giving a delivery location. Returns the branch id."""
    _get(s, BASE + "/")
    r = s.post(
        BASE + "/operation/set-current-location",
        data={**LOCATION, "radius": "", "fOutMode": "json", "fIsAjax": "1"},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    if r.status_code in (403, 429):
        raise Blocked(f"Quickmart refused the location request (HTTP {r.status_code}).")
    ans = r.json()
    if not ans.get("status") or not ans.get("shopId"):
        raise RuntimeError(f"Quickmart didn't accept the delivery location: {ans}")
    time.sleep(PAUSE_SECONDS)
    return str(ans["shopId"])


def _prices(block) -> tuple[float | None, float | None]:
    new = block.select_one(".products-price-new") if block else None
    old = block.select_one(".products-price-old") if block else None
    new_v = parse_price(new.get_text()) if new else None
    old_v = parse_price(old.get_text()) if old else None
    if old_v and new_v and new_v < old_v:
        return old_v, new_v
    return new_v or old_v, None


def _stock(el) -> bool | None:
    q = el.select_one("[data-stock]") if el else None
    if q is None:
        return None
    try:
        return int(q["data-stock"]) > 0
    except ValueError:
        return None


IMAGE_CODE = re.compile(r"product_images_(\d+)\.")


def _item_code(el) -> str | None:
    """Quickmart's own item code (= ITEM_CODE in its sales files): the website stores each product's
    picture under it, e.g. .../product_images_400100.png for Brookside Dairy Best Milk 500ml."""
    m = IMAGE_CODE.search(str(el)) if el is not None else None
    return m.group(1) if m else None


def _card(card, category, branch):
    link = card.select_one("a.products-title")
    sid = card.select_one("input[name=selprod_id]")
    if not link or not sid:
        return None
    normal, promo = _prices(card.select_one(".products-price"))
    return {
        "retailer_code": _item_code(card),
        "sku": sid["value"],
        "name": link.get("title") or link.get_text(" ", strip=True),
        "normal_price": normal,
        "promo_price": promo,
        "currency": "KES",
        "in_stock": _stock(card),
        "url": BASE + link["href"] if link["href"].startswith("/") else link["href"],
        "category": category,
    }


def discover_categories(s) -> list[tuple[str, str]]:
    """The whole category tree from /category: [(site_category_path, url), ...], lowest level only.

    The page lists each main category ("Foods") with its sub-categories ("Flour", "Sugar"...).
    A main category without sub-categories is crawled itself.
    """
    soup = BeautifulSoup(_get(s, BASE + "/category").text, "html.parser")
    found: dict[str, str] = {}
    for block in soup.select(".categories-listing-item"):
        top = block.select_one(".categories-listing-title a[href]")
        if not top:
            continue
        top_name = top.get_text(" ", strip=True)
        subs = block.select(".categories-map a.categories-map-link[href]")
        for a in subs or [top]:
            name = a.get_text(" ", strip=True)
            path = top_name if a is top else f"{top_name} > {name}"
            found.setdefault(BASE + a["href"].split("?")[0].rstrip("/"), path)
    return [(path, url) for url, path in found.items()]


def scrape_category(s, url, category, branch, site_category=None, seen=None):
    url = url.split("?")[0].rstrip("/")
    seen = set() if seen is None else seen   # shared across categories in a full crawl: each product once
    for page in range(1, MAX_PAGES + 1):
        r = _get(s, url if page == 1 else f"{url}?page-{page}")
        if page == 1 and r.url.rstrip("/").endswith("/home"):
            raise RuntimeError(f"{url} redirected to the homepage: not a category URL, or no branch selected")
        soup = BeautifulSoup(r.text, "html.parser")
        new = 0
        for card in soup.select(".products.productInfoJs"):
            item = _card(card, category, branch)
            if item and item["sku"] not in seen:
                seen.add(item["sku"])
                item["site_category"] = site_category
                new += 1
                yield item
        if new == 0 or not soup.select_one(".pagination-item.next"):
            break


def scrape_product(s, url, category, branch):
    r = _get(s, url)
    soup = BeautifulSoup(r.text, "html.parser")
    main = soup.select_one(".product-description") or soup
    title = main.select_one("h1.product-title")
    form = main.select_one("form#frmBuyProduct") or soup.select_one("form#frmBuyProduct")
    sid = None
    if form:
        m = re.search(r"frmBuyProd-(\d+)", " ".join(form.get("class", [])))
        sid = m.group(1) if m else None
    normal, promo = _prices(main.select_one(".products-price"))
    yield {
        "retailer_code": _item_code(main.select_one("img[src*=product_images_]") or main),
        "sku": sid or url.rstrip("/").split("/")[-1],
        "name": title.get_text(" ", strip=True) if title else None,
        "normal_price": normal,
        "promo_price": promo,
        "currency": "KES",
        "in_stock": _stock(form),
        "url": url,
        "category": category,
    }


def scrape(targets):
    s = _session()
    branch = _set_location(s)
    print(f"    Quickmart branch for {LOCATION['address']}: {branch}")
    if EXPECTED_BRANCH and branch != EXPECTED_BRANCH:
        print(f"    WARNING: expected branch {EXPECTED_BRANCH} (Kiambu), got {branch}: prices may not be comparable")
    for t in targets:
        if t["page_type"] == "all":
            yield from crawl_all(
                COMPETITOR, discover_categories(s),
                lambda url, cat, path, seen: scrape_category(s, url, cat, branch, path, seen),
                t["category"])
        elif t["page_type"] == "product":
            yield from scrape_product(s, t["url"], t["category"], branch)
        else:
            yield from scrape_category(s, t["url"], t["category"], branch)
