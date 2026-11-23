"""Run scrapers and store the results in the database (Supabase / PostgreSQL).

    python -m pricewatch.run                       # all scrapers
    python -m pricewatch.run --only naivas         # one scraper (module name)
    python -m pricewatch.run --only naivas --max-categories 3     # quick test of a full-catalogue crawl

A failing scraper is logged in the `runs` table and does not stop the others. If a long crawl
breaks part-way (site blocks, network drops), everything collected up to then is still saved
and the run is marked "partial". Exit code is 1 if any scraper failed or was partial, so
GitHub Actions marks the run red.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import pkgutil
import sys
import traceback

import scrapers
from scrapers._utils import parse_price

from . import config, db


def discover(only: list[str] | None = None):
    """Yield (module_name, module) for each scraper module (files not starting with _)."""
    for info in sorted(pkgutil.iter_modules(scrapers.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        if only and info.name not in only:
            continue
        module = importlib.import_module(f"scrapers.{info.name}")
        if not hasattr(module, "scrape"):
            print(f"  ! scrapers/{info.name}.py has no scrape() function, skipping")
            continue
        yield info.name, module


def normalize(raw: dict) -> dict:
    """Validate one scraped item and coerce types. Raises ValueError on bad data."""
    # "price" is accepted as an alias for "normal_price"
    if raw.get("normal_price") in (None, "") and raw.get("price") not in (None, ""):
        raw = {**raw, "normal_price": raw["price"]}
    missing = [k for k in ("sku", "name", "normal_price") if raw.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing {missing} in {raw!r}")
    normal = parse_price(raw["normal_price"])
    if normal is None or normal <= 0:
        raise ValueError(f"could not parse normal_price {raw['normal_price']!r}")
    promo = parse_price(raw.get("promo_price"))
    if promo is not None and (promo <= 0 or promo >= normal):
        promo = None  # sites often repeat the normal price in the promo slot: not a real promo
    in_stock = raw.get("in_stock")
    return {
        "sku": str(raw["sku"]).strip(),
        "name": " ".join(str(raw["name"]).split()),
        "normal_price": normal,
        "promo_price": promo,
        "currency": raw.get("currency"),
        "url": raw.get("url"),
        "category": raw.get("category") or None,           # from targets.csv / Carrefour folder
        "site_category": raw.get("site_category") or None, # the competitor's own category path
        "in_stock": None if in_stock is None else bool(in_stock),
        "scraped_at": raw.get("scraped_at"),               # only set by importers of hand-saved pages
        "retailer_code": raw.get("retailer_code") or None, # retailer's own item code, when the site shows it
        "barcode": raw.get("barcode") or None,
    }


def call_scrape(module, competitor: str):
    """Call scrape(targets) if the scraper takes an argument, else scrape()."""
    targets = config.targets_for(competitor)
    takes_targets = len(inspect.signature(module.scrape).parameters) > 0
    if takes_targets:
        if not targets:
            raise RuntimeError(f"no active rows for '{competitor}' in config/targets.csv")
        kinds = ", ".join(sorted({t["page_type"] for t in targets}))
        print(f"    {len(targets)} target row(s) from config/targets.csv ({kinds})")
        yield from module.scrape(targets)
        return
    if targets:
        print(f"    note: {len(targets)} row(s) in targets.csv, but scrape() takes no argument, so they're ignored")
    yield from module.scrape()


def _alive(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.commit()
        return True
    except Exception:  # noqa: BLE001
        return False


def _fresh(conn):
    """A long crawl leaves the database connection idle for 30+ minutes, and Supabase may close it.
    Check it before saving; reconnect if it was dropped, so the crawl's results are never lost."""
    if _alive(conn):
        return conn
    print("    database connection was closed while crawling: reconnecting before saving", flush=True)
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return db.connect()


def run_one(conn, name: str, module, rules):
    """Returns (ok, conn): conn may be a new connection if the old one was dropped."""
    competitor = getattr(module, "COMPETITOR", name)
    run_id = db.start_run(conn, competitor)
    ts = db.now_utc()
    items, skipped, error = [], 0, None
    allow_empty = getattr(module, "ALLOW_EMPTY", False)   # e.g. no new hand-saved pages today
    try:
        for raw in call_scrape(module, competitor):
            try:
                item = normalize(raw)
            except ValueError as e:
                skipped += 1
                if skipped <= 5:
                    print(f"    skipped item: {e}")
                continue
            item.update(config.apply_map(rules, competitor, item["sku"], item["name"]))
            items.append(item)
            if len(items) % 2000 == 0:
                print(f"    ... {len(items)} items so far")
    except Exception as e:  # noqa: BLE001 - keep what we have, record the failure
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    conn = _fresh(conn)
    try:
        if not items and not allow_empty and error is None:
            error = "RuntimeError: scraper returned no valid items (site layout changed or blocked?)"
        written = db.save_items(conn, run_id, competitor, items, ts) if items else 0
        status = "ok" if error is None else ("partial" if items else "failed")
        db.finish_run(conn, run_id, status, len({i["sku"] for i in items}), written, error[:2000] if error else None)
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn = _fresh(conn)
        status, error = "failed", f"{type(e).__name__} while saving: {e}"
        traceback.print_exc()
        db.finish_run(conn, run_id, status, 0, 0, error[:2000])
        written = 0

    seen = len({i["sku"] for i in items})
    msg = f"{competitor}: {seen} products seen, {written} price rows written (changes)"
    if skipped:
        msg += f", {skipped} skipped"
    if status == "ok":
        print(f"  ok   {msg}")
    else:
        print(f"  {status.upper()} {msg} | {error}")
    return status == "ok", conn


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="scraper module names to run")
    parser.add_argument("--max-categories", type=int, help="full-catalogue crawls: stop after N categories (testing)")
    args = parser.parse_args(argv)
    if args.max_categories:
        os.environ["PRICEWATCH_MAX_CATEGORIES"] = str(args.max_categories)

    # Watchdog: a run that is still going after 3 hours is stuck (e.g. the PC slept mid-run).
    # End it so the next scheduled run isn't blocked. Prices saved earlier stay saved.
    import threading
    def _give_up():
        print("Watchdog: run exceeded 3 hours, stopping.", flush=True)
        os._exit(2)
    timer = threading.Timer(3 * 3600, _give_up)
    timer.daemon = True
    timer.start()

    conn = db.connect()
    db.apply_schema(conn)          # creates tables on first use, refreshes views
    rules = config.load_map()
    found = list(discover(args.only))
    if not found:
        print("No scrapers found in scrapers/.")
        return 1
    print(f"Running {len(found)} scraper(s)")
    results = []
    for name, module in found:
        ok, conn = run_one(conn, name, module, rules)
        results.append(ok)
    conn.close()
    failed = results.count(False)
    print(f"Done: {len(results) - failed} ok, {failed} failed or partial")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
