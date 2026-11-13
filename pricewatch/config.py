"""The two files you maintain in config/ (edit in Excel or on GitHub; changes apply on the next run).

config/targets.csv       WHAT to scrape
    competitor   must match the scraper's COMPETITOR name (case doesn't matter)
    category     YOUR category name for everything found on that page, e.g. Smartphones
    page_type    all | category | product
                   all      = the WHOLE catalogue: the scraper finds every category on the site itself
                              and stores the site's own category path with each product (url = homepage)
                   category = one listing page (all its pages are followed)
                   product  = one single product page
    url          the page to scrape
    active       yes | no             (no = skip it, keep the row for later)
    notes        anything

config/product_map.csv   HOW to label items (applied when exporting, so it also relabels history)
    competitor     blank = every competitor
    sku            exact SKU to match            } use one or both;
    name_contains  words that must all appear     } matching ignores case
    category       overrides the category from targets.csv
    product_group  same value at every competitor = same item, compared side by side
    key_item       yes | no  - flag the items you care about most
    notes          anything
Rows are applied top to bottom; a later matching row overrides the earlier one's non-blank fields.
"""
from __future__ import annotations

import csv
from pathlib import Path

from pathlib import Path as _P

ROOT = _P(__file__).resolve().parent.parent

CONFIG = ROOT / "config"
YES = {"yes", "y", "true", "1", "x"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # utf-8-sig: Excel adds a BOM when saving as "CSV UTF-8"
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def targets_for(competitor: str) -> list[dict]:
    """Active rows of targets.csv for one competitor."""
    key = competitor.strip().lower()
    return [
        {"category": r.get("category") or None, "page_type": (r.get("page_type") or "category").lower(), "url": r["url"]}
        for r in _rows(CONFIG / "targets.csv")
        if r.get("competitor", "").lower() == key and r.get("url") and r.get("active", "yes").lower() in YES
    ]


def load_map() -> list[dict]:
    rules = []
    for r in _rows(CONFIG / "product_map.csv"):
        if not (r.get("sku") or r.get("name_contains")):
            continue  # a rule must say which items it matches
        rules.append({
            "competitor": r.get("competitor", "").lower(),
            "sku": r.get("sku", "").lower(),
            "words": r.get("name_contains", "").lower().split(),
            "category": r.get("category") or None,
            "product_group": r.get("product_group") or None,
            "key_item": (r.get("key_item", "").lower() in YES) if r.get("key_item") else None,
        })
    return rules


def apply_map(rules: list[dict], competitor: str, sku: str, name: str) -> dict:
    """Labels from product_map.csv for one product: my_category, product_group, key_item."""
    out = {"my_category": None, "product_group": None, "key_item": False}
    comp, sku_l, name_l = competitor.lower(), str(sku).lower(), name.lower()
    for r in rules:
        if r["competitor"] and r["competitor"] != comp:
            continue
        if r["sku"] and r["sku"] != sku_l:
            continue
        if r["words"] and not all(w in name_l for w in r["words"]):
            continue
        if r["category"] is not None:
            out["my_category"] = r["category"]
        if r["product_group"] is not None:
            out["product_group"] = r["product_group"]
        if r["key_item"] is not None:
            out["key_item"] = r["key_item"]
    return out
