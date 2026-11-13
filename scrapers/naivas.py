"""Naivas (naivas.online), a Laravel Livewire shop.

How prices appear on the site:
    on promo:   green "KES 7,525"  +  red struck-through "KES 12,495"  +  "Save KES 4,970"
    no promo:   green price only
        normal_price = struck-through price if present, else the green price
        promo_price  = the green price when a struck-through price is present, else None

IDs: each product has an internal product_id (e.g. 40740). Category cards carry it in a hidden
<input name="product_id">, and product pages carry it in the image path /product/40740/, so both
page types give the same SKU and the price history stays continuous.

Category pages show 15 products per page; page N is <category-url>?page=N.
(The "over 18" pop-up in the HTML is only shown in the browser; it doesn't affect the data.)

Pages come from config/targets.csv (competitor = Naivas):
    page_type = category  ->  e.g. https://www.naivas.online/kitchen-appliances/electric-kettles-air-friers
    page_type = product   ->  e.g. https://www.naivas.online/mika-air-frier-5ltr-mafdw2105
"""
from __future__ import annotations

import html
import json
import re
import time

from bs4 import BeautifulSoup

from ._utils import crawl_all, get, parse_price

COMPETITOR = "Naivas"
BASE = "https://www.naivas.online"
MAX_PAGES = 80          # safety cap per category (15 products per page)
PAUSE_SECONDS = 2.0     # be polite between page requests


def _prices(block) -> tuple[float | None, float | None]:
    if block is None:
        return None, None
    current = block.select_one("span.text-naivas-green")
    old = block.select_one("span.line-through")
    cur_v = parse_price(current.get_text()) if current else None
    old_v = parse_price(old.get_text()) if old else None
    if old_v and cur_v and cur_v < old_v:
        return old_v, cur_v
    return cur_v or old_v, None


def _card_container(inp):
    """Walk up from the hidden product_id input to the element holding the whole card."""
    el = inp
    for _ in range(12):
        el = el.parent
        if el is None:
            return None
        if el.select_one(".product-price") and el.select_one("a[title]"):
            return el
    return None


def discover_categories(home: str = BASE + "/") -> list[tuple[str, str]]:
    """The whole category tree from the site menu: [(site_category_path, url), ...], lowest level only.

    Menu structure: department (top bar) > category > sub-category. A category without
    sub-categories is crawled itself.
    """
    soup = BeautifulSoup(get(home).text, "html.parser")
    found: dict[str, str] = {}
    for masonry in soup.select("ul.masonry"):
        panel = masonry.find_parent("div", class_="absolute")
        dept = None
        if panel is not None:
            label = panel.find_previous_sibling(["button", "a", "span", "div"])
            dept = label.get_text(" ", strip=True) if label else None
            if dept and (len(dept) > 40 or not dept.strip()):
                dept = None
        # each category is an <a> directly in the list, wrapping its own list of sub-category links
        for item in masonry.find_all(["a", "li"], recursive=False):
            top = item if item.name == "a" else item.find("a", href=True)
            if not top or not top.get("href"):
                continue
            top_url = _abs(top["href"])
            top_name = next((s.strip() for s in top.strings if s.strip()), None)
            subs = [a for a in item.select("ul li a[href]") if a is not top]
            if subs:
                for a in subs:
                    path = " > ".join(x for x in (dept, top_name, a.get_text(" ", strip=True)) if x)
                    found.setdefault(_abs(a["href"]), path)
            elif top_name:
                found.setdefault(top_url, " > ".join(x for x in (dept, top_name) if x))
    return [(path, url) for url, path in found.items()]


def _abs(href: str) -> str:
    href = href.split("?")[0].rstrip("/")
    return href if href.startswith("http") else BASE + href


def scrape_category(url: str, category: str | None, site_category: str | None = None, seen: set | None = None):
    url = url.split("?")[0].rstrip("/")
    seen = set() if seen is None else seen   # shared across categories in a full crawl: each product once
    for page in range(1, MAX_PAGES + 1):
        soup = BeautifulSoup(get(url if page == 1 else f"{url}?page={page}").text, "html.parser")
        new = 0
        for inp in soup.select("input[name=product_id]"):
            pid = inp.get("value")
            if not pid or pid in seen:
                continue
            card = _card_container(inp)
            if card is None:
                continue
            link = card.select_one("a[title][href]")
            normal, promo = _prices(card.select_one(".product-price"))
            text = card.get_text(" ", strip=True).lower()
            seen.add(pid)
            new += 1
            yield {
                "sku": pid,
                "name": link["title"],
                "normal_price": normal,
                "promo_price": promo,
                "currency": "KES",
                "in_stock": not ("out of stock" in text or "unavailable" in text),
                "url": link["href"],
                "category": category,
                "site_category": site_category,
            }
        if new == 0:
            break  # past the last page (Naivas returns an empty listing), or all already seen
        time.sleep(PAUSE_SECONDS)


def scrape_product(url: str, category: str | None):
    soup = BeautifulSoup(get(url).text, "html.parser")
    data = {}
    for sc in soup.select("script[type='application/ld+json']"):
        try:
            d = json.loads(html.unescape(sc.get_text()))
        except ValueError:
            continue
        if d.get("@type") == "Product":
            data = d
            break
    offer = data.get("offers") or {}
    details = soup.select_one("div.details")
    normal, promo = _prices(details)
    if normal is None:  # fall back to the structured data (current price only)
        normal = parse_price(offer.get("price"))
    image = (soup.select_one("meta[property='og:image']") or {}).get("content", "")
    m = re.search(r"/product/(\d+)/", image)
    title = soup.select_one("meta[property='og:title']")
    availability = offer.get("availability", "")
    yield {
        "sku": m.group(1) if m else (data.get("sku") or url.rstrip("/").split("/")[-1]),
        "name": data.get("name") or (title.get("content") if title else None),
        "normal_price": normal,
        "promo_price": promo,
        "currency": offer.get("priceCurrency") or "KES",
        "in_stock": ("InStock" in availability) if availability else None,
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
