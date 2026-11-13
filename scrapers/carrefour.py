"""Carrefour Kenya (carrefour.ke): importer for pages SAVED BY HAND in a browser.

Carrefour's site blocks automated visitors (it returns an empty page), so this file never
contacts carrefour.ke. Instead, someone opens a category or search page in their normal
browser and saves it (Ctrl+S) into manual/carrefour/<Category>/. Every run imports whatever
saved pages are there. See manual/carrefour/HOW_TO_SAVE.txt.

Saved-page formats:
    .mhtml / .mht   "Webpage, Single File" (Chrome / Edge). Preferred: the file itself records
                    the date it was saved and the page's address.
    .html / .htm    "Webpage, Complete". The save date comes from the file name if it has one
                    (e.g. "2026-11-14 Beverages.html"), else from the file's modified time.

How prices appear on a card:
    on promo:  "KES 159.00" followed by a struck-through "169.00"
    no promo:  "KES 180.00" only
        normal_price = struck-through price if present, else the shown price
        promo_price  = the shown price when a struck-through price is present, else None
SKU = the number after /p/ in the product link (e.g. /p/241813), the same on every page.

Re-importing the same saved file adds nothing (same product + same save time = already stored),
so files can stay in the folder.
"""
from __future__ import annotations

import email
import email.policy
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

from ._utils import parse_price

COMPETITOR = "Carrefour"
ALLOW_EMPTY = True     # no new saved pages today is fine, not a failure
BASE = "https://www.carrefour.ke"
FOLDER = Path(__file__).resolve().parent.parent / "manual" / "carrefour"
EXTENSIONS = {".mhtml", ".mht", ".html", ".htm"}
DATE_IN_NAME = re.compile(r"(20\d\d)[-_.](\d\d)[-_.](\d\d)")


def _read_mhtml(path: Path) -> tuple[str, datetime | None, str | None]:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    saved = None
    if msg["Date"]:
        try:
            saved = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            saved = None
    url = str(msg["Snapshot-Content-Location"] or "") or None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content(), saved, url
    raise ValueError(f"{path.name}: no HTML found inside the .mhtml file")


def _read(path: Path) -> tuple[str, datetime, str | None]:
    if path.suffix.lower() in (".mhtml", ".mht"):
        html, saved, url = _read_mhtml(path)
    else:
        html = path.read_text(encoding="utf-8", errors="replace")
        saved, url = None, None
        m = re.search(r"<!-- saved from url=\(\d+\)(\S+) -->", html[:500])
        if m:
            url = m.group(1)
    if saved is None:
        m = DATE_IN_NAME.search(path.name)
        if m:
            saved = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=timezone.utc)
        else:
            saved = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return html, saved.astimezone(timezone.utc), url


def _pid(href: str) -> str | None:
    m = re.search(r"/p/(\d+)", href or "")
    return m.group(1) if m else None


def _card_of(link):
    """Largest ancestor that still holds only this one product and shows a KES price."""
    pid = _pid(link.get("href"))
    best, el = None, link
    for _ in range(10):
        el = el.parent
        if el is None or el.name in ("body", "html"):
            break
        ids = {_pid(a.get("href")) for a in el.select('a[href*="/p/"]')}
        if ids != {pid}:
            break
        if el.find("span", string=lambda s: s and s.strip() == "KES"):
            best = el
    return best


def parse_page(html: str, category: str | None, saved_at: str):
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    for link in soup.select('a[href*="/p/"]'):
        pid = _pid(link.get("href"))
        if not pid or pid in seen:
            continue
        card = _card_of(link)
        if card is None:
            continue
        kes = card.find("span", string=lambda s: s and s.strip() == "KES")
        shown = kes.find_next_sibling()
        struck = kes.parent.select_one('[class*="line-through"]')
        shown_v = parse_price(shown.get_text()) if shown else None
        struck_v = parse_price(struck.get_text()) if struck else None
        if struck_v and shown_v and shown_v < struck_v:
            normal, promo = struck_v, shown_v
        else:
            normal, promo = shown_v or struck_v, None
        names = [a.get_text(" ", strip=True) for a in card.select('a[href*="/p/"]') if a.get_text(strip=True)]
        if not names or normal is None:
            continue
        seen.add(pid)
        text = card.get_text(" ", strip=True).lower()
        href = link["href"].split("?")[0]
        yield {
            "sku": pid,
            "name": names[0],
            "normal_price": normal,
            "promo_price": promo,
            "currency": "KES",
            "in_stock": "out of stock" not in text,
            "url": BASE + href if href.startswith("/") else href,
            "category": category,
            "scraped_at": saved_at,
        }


def saved_files():
    if not FOLDER.exists():
        return []
    return sorted(p for p in FOLDER.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS
                  and not any(part.startswith("_") for part in p.relative_to(FOLDER).parts))


def scrape():
    files = saved_files()
    print(f"    {len(files)} saved page(s) in manual/carrefour/")
    for path in files:
        rel = path.relative_to(FOLDER)
        category = rel.parts[0] if len(rel.parts) > 1 else None   # sub-folder name = your category
        html, saved, url = _read(path)
        saved_at = saved.replace(microsecond=0).isoformat()
        rows = list(parse_page(html, category, saved_at))
        print(f"    {rel}: {len(rows)} products, saved {saved_at[:16].replace('T', ' ')} UTC"
              + ("" if rows else "  <- no products found: was the page fully loaded before saving?"))
        yield from rows
