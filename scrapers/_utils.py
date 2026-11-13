"""Shared helpers for scrapers."""
from __future__ import annotations

import re
import time

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def get(url: str, retries: int = 3, delay: float = 2.0, **kwargs) -> requests.Response:
    """GET with retries and a polite pause between attempts."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last = e
            time.sleep(delay * attempt)
    raise last


def crawl_all(competitor: str, categories, scrape_category, category=None, max_failures_in_a_row: int = 5):
    """Full-catalogue crawl: scrape every (site_category_path, url) once, each product only once.

    A category that errors is logged and skipped; several failures in a row stop the crawl
    (that usually means the site is blocking), and the runner saves what was collected.
    Set PRICEWATCH_MAX_CATEGORIES (or run.py --max-categories N) to try just the first N.
    """
    import os
    # promotion collections ("Fresh Deals", "Special Offers"...) last, so each product keeps its REAL category
    promo_like = re.compile(r"(?i)\b(deals?|offers?|promos?|promotions?|combo|clearance|exclusives?|special|private label)\b")
    categories = sorted(categories, key=lambda c: bool(promo_like.search(c[0])))
    limit = int(os.environ.get("PRICEWATCH_MAX_CATEGORIES") or 0)
    if limit:
        categories = categories[:limit]
    print(f"    {competitor}: {len(categories)} categories to crawl")
    seen: set[str] = set()
    failures = 0
    for i, (path, url) in enumerate(categories, 1):
        before = len(seen)
        try:
            for item in scrape_category(url, category, path, seen):
                yield item
            failures = 0
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"    ! {path} ({url}): {type(e).__name__}: {e}")
            if failures >= max_failures_in_a_row:
                raise RuntimeError(f"{failures} categories failed in a row; stopping the crawl ({e})") from e
        if i % 25 == 0 or i == len(categories):
            print(f"    ... {i}/{len(categories)} categories, {len(seen)} products (+{len(seen) - before} in last)")


def parse_price(value) -> float | None:
    """'KSh 1,299.00' -> 1299.0, '£51.77' -> 51.77, '1.299,00 €' -> 1299.0, 42 -> 42.0"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # first number in the text, so "KSh. 1,299" or "Was 1,500 now 1,299" behave
    match = re.search(r"\d[\d.,]*", str(value))
    if not match:
        return None
    text = match.group().strip(".,")
    if "," in text and "." in text:
        # whichever separator comes last is the decimal point
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # "1,299" (thousands) vs "12,50" (decimal)
        head, _, tail = text.rpartition(",")
        text = text.replace(",", "") if len(tail) == 3 else f"{head.replace(',', '')}.{tail}"
    try:
        return float(text)
    except ValueError:
        return None
