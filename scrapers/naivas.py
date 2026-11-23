"""Naivas (naivas.online).

Naivas rebuilt its website after 16 Nov 2026. Category pages now show only the first 20
products and load the rest in the background as you scroll, from the catalogue source the
website itself uses (app.naivas.online/api/v1, "web" channel). This scraper reads that same
source - with the owner's explicit approval - 250 products per request, with a pause between
requests. The plain listing stops at 10,000 and shuffles its order, so the catalogue is read
department by department (each department listing includes all its sub-categories).

Each product comes with:
    product_id      Naivas' internal number (also the number used by the old site, so the
                    price history continues)                              -> sku
    sku             Naivas item code, e.g. N045994 or 20000988 (= ITEMLOOKUPCODE in Naivas'
                    sales files)                                          -> retailer_code
    product_number  barcode                                               -> barcode
    price / special_price (+ special_price_from / _to)                   -> normal / promo price
    category (+ ancestors)                                                -> site_category

config/targets.csv: use page_type "all" for Naivas (single category / product rows are ignored
for Naivas now: the full catalogue is only ~40 requests).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from ._utils import HEADERS

COMPETITOR = "Naivas"
SITE = "https://www.naivas.online"
API = "https://app.naivas.online/api/v1"
PER_PAGE = 250
PAUSE_SECONDS = 3.0
MAX_PAGES = 60             # per department per pass (a department of 15,000 products)
MAX_PASSES = 4             # re-read a department until all its listed products are collected


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({**HEADERS, "Accept": "application/json", "X-Channel": "web"})
    return s


def _get(s: requests.Session, params: dict) -> dict:
    last = None
    for attempt in range(1, 6):
        try:
            r = s.get(f"{API}/products", params=params, timeout=60)
            if r.status_code in (403, 429):
                raise RuntimeError(f"Naivas refused the request (HTTP {r.status_code})")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(PAUSE_SECONDS * attempt * 2)
    raise last


def _when(v):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _promo(p: dict) -> float | None:
    """special_price only counts while its date window is running."""
    sp = p.get("special_price")
    if sp in (None, "", 0):
        return None
    now = datetime.now(timezone.utc)
    start, end = _when(p.get("special_price_from")), _when(p.get("special_price_to"))
    if (start and now < start) or (end and now > end):
        return None
    return float(sp)


def _category(p: dict) -> str | None:
    c = p.get("category") or {}
    names = [a.get("name") for a in (c.get("ancestors") or []) if a.get("name")]
    if c.get("name"):
        names.append(c["name"])
    return " > ".join(names) or None


def _in_stock(p: dict):
    if p.get("is_active") is False:
        return False
    if p.get("in_stock") is not None:
        return bool(p["in_stock"])
    q = p.get("inventory_source_quantity", p.get("qty"))
    return None if q is None else float(q) > 0


def _row(p: dict, category):
    return {
        "sku": str(p.get("product_id") or p.get("id")),
        "name": p.get("name"),
        "normal_price": p.get("price"),
        "promo_price": _promo(p),
        "currency": "KES",
        "in_stock": _in_stock(p),
        "url": f"{SITE}/{p['url_key']}" if p.get("url_key") else None,
        "category": category,
        "site_category": _category(p),
        "retailer_code": (str(p["sku"]).strip().upper() if p.get("sku") else None),
        "barcode": (str(p["product_number"]).strip() if p.get("product_number") else None),
    }


def _departments(s: requests.Session) -> list[str]:
    """Top-level category slugs (their listing includes everything beneath them). Promos last:
    it's a collection of items that also sit in a real department."""
    for attempt in range(1, 6):
        try:
            r = s.get(f"{API}/categories", params={"per_page": 1000}, timeout=60)
            r.raise_for_status()
            data = r.json().get("data", [])
            break
        except (requests.RequestException, ValueError):
            if attempt == 5:
                raise
            time.sleep(PAUSE_SECONDS * attempt * 2)
    tops = [c["slug"] for c in data if c.get("url_path") and "/" not in c["url_path"]]
    time.sleep(PAUSE_SECONDS)
    return sorted(tops, key=lambda x: x == "promos")


def scrape_all(category: str | None = None):
    """Read department by department. The source returns pages in a shifting order, so each
    department is re-read (up to MAX_PASSES times) until the number of different products
    collected matches the total the source reports for it."""
    s = _session()
    seen: set = set()
    shortfall = 0
    for dept in _departments(s):
        got: set = set()
        total = None
        for _pass in range(MAX_PASSES):
            page, last_page = 1, 1
            while page <= min(last_page, MAX_PAGES):
                j = _get(s, {"category": dept, "per_page": PER_PAGE, "page": page})
                meta = j.get("meta") or {}
                last_page = int(meta.get("last_page") or last_page)
                total = int(meta.get("total") or 0)
                for p in j.get("data") or []:
                    pid = p.get("product_id") or p.get("id")
                    if not pid or p.get("price") in (None, ""):
                        continue
                    got.add(pid)
                    if pid not in seen:
                        seen.add(pid)
                        yield _row(p, category)
                page += 1
                time.sleep(PAUSE_SECONDS)
            if total is not None and len(got) >= total:
                break
        if total and len(got) < total:
            shortfall += total - len(got)
        print(f"    {dept}: {len(got)} of {total} | catalogue so far {len(seen)}", flush=True)
    if shortfall:
        print(f"    note: {shortfall} listed products could not be collected after {MAX_PASSES} passes")


def scrape(targets):
    if not any(t["page_type"] == "all" for t in targets):
        print("    note: Naivas now only supports page_type 'all' (full catalogue, ~40 requests)")
    yield from scrape_all(next((t["category"] for t in targets if t["page_type"] == "all"), None))
