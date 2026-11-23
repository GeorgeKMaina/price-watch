"""Link website products to the item master (run after pricewatch.items and the crawls).

    python -m pricewatch.link                  # all retailers
    python -m pricewatch.link --naivas-pages 300   # max Naivas product pages to read this run

How each retailer is linked
---------------------------
Foodplus  : website code = Chandarana ITEM_CD          -> exact code match
Carrefour : website product id = Majid Item Code        -> exact code match
Naivas    : the item code (ITEMLOOKUPCODE) is shown on each PRODUCT page (not on category pages).
            The likely page for each Naivas file item is found by name + pack size, opened once,
            and its code read; codes never change, so each page is read only once (a few hundred
            per run, politely).
Quickmart : no code or barcode on the website -> matched by name + pack size:
              same pack size + same brand + most words in common  -> linked automatically
              anything less certain -> listed in the review (database view v_match_review and
              data/needs_matching.csv) with the top 3 website candidates.
Confirm or reject a match in config/confirmed_matches.csv:
    retailer,retailer_code,web_sku,notes
    Quickmart,220298,432609,          <- this file item IS website product 432609
    Quickmart,220333,none,            <- not sold online, stop suggesting
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import time
from collections import defaultdict

import psycopg2.extras
from bs4 import BeautifulSoup

from scrapers._utils import get

from . import db

ROOT = db.ROOT
CONFIRMED = ROOT / "config" / "confirmed_matches.csv"
REVIEW_CSV = ROOT / "data" / "needs_matching.csv"
AUTO_SCORE = 0.80      # name-match score needed to link without review
REVIEW_MIN = 0.35      # below this a candidate isn't worth suggesting

UNIT = {"KG": ("g", 1000), "KGS": ("g", 1000), "G": ("g", 1), "GM": ("g", 1), "GMS": ("g", 1), "GR": ("g", 1),
        "GRM": ("g", 1), "GRMS": ("g", 1), "ML": ("ml", 1), "MLS": ("ml", 1), "CL": ("ml", 10), "L": ("ml", 1000),
        "LT": ("ml", 1000), "LTR": ("ml", 1000), "LTRS": ("ml", 1000), "LITRE": ("ml", 1000), "LITRES": ("ml", 1000),
        "LITER": ("ml", 1000), "PCS": ("pc", 1), "PC": ("pc", 1), "PK": ("pc", 1), "PACK": ("pc", 1), "S": ("pc", 1)}
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(UNIT, key=len, reverse=True)) + r")\b")
# counts: "50'S", "100S", "20 BAGS", "6 PACK", "X6"
COUNT_RE = re.compile(r"\b(\d+)\s*(?:'S\b|S\b|BAGS?\b|TEA ?BAGS\b|SACHETS?\b|TABS?\b|TABLETS?\b|ROLLS?\b|PIECES?\b|PACK\b|PK\b|PCS?\b)"
                      r"|\bX\s*(\d+)\b|\b(\d+)\s*X\b")
NUMWORD = {"SIX": "6", "TWELVE": "12", "TWO": "2", "THREE": "3", "FOUR": "4", "TEN": "10", "TWENTY": "20"}
STOP = {"AND", "WITH", "THE", "OF", "FOR", "IN", "ON", "TO", "A", "NEW", "PACK", "OFFER", "PROMO", "FREE", "QSOKO",
        "FLAVOUR", "FLAVOURED", "FLAVOR", "FLV", "CEREAL", "CEREALS", "BREAKFAST", "BFAST", "UNTAGGED", "BAGS",
        "BAG", "PCS", "PIECES", "PIECE", "SACHET", "SACHETS", "GRAIN", "WHOLEGRAIN", "VALUE"}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("IES"):
        return w[:-3] + "Y"                                # BERRIES -> BERRY
    if len(w) > 3 and w.endswith("S") and not w.endswith("SS"):
        return w[:-1]                                      # OATS -> OAT, FLAKES -> FLAKE
    return w


ABBREV = {"UNSWTNED": "UNSWEETENED", "UNSWTND": "UNSWEETENED", "UNSWT": "UNSWEETENED", "UNSWEET": "UNSWEETENED",
          "ORG": "ORGANIC", "CHOC": "CHOCOLATE", "CHOCO": "CHOCOLATE", "VAN": "VANILLA", "STRAWB": "STRAWBERRY",
          "ORIG": "ORIGINAL", "COCO": "COCONUT", "ALMD": "ALMOND", "BRWN": "BROWN", "WHT": "WHITE"}
# supplier tags some retailers put in front of names (Quickmart: "HUL ALPRO ..." = Healthy U Ltd)
NAME_TAGS = {"HUL"}


def parse_name(name: str):
    s = (name or "").upper().replace("&", " AND ").replace("B/", " ").replace("B.", " ")
    s = re.sub(r"^[A-Z]{2}\s*-\s*", "", s)                 # Quickmart department prefix FD-, PC-, HC- ...
    s = re.sub(r"#\s*\S*|\bART\s*\d{4,}\b", " ", s)         # supplier article numbers: #80005329, ART152156
    s = " ".join(w for w in s.split() if w not in NAME_TAGS)
    s = re.sub(r"\b[A-Z]+\b", lambda m: ABBREV.get(m.group(0), m.group(0)), s)
    for word, digit in NUMWORD.items():
        s = re.sub(rf"\b{word}\b", digit, s)
    sizes = set()
    for num, unit in SIZE_RE.findall(s):
        kind, mult = UNIT[unit]
        v = float(num.replace(",", ".")) * mult
        sizes.add((kind, round(v, 1)))
    s2 = SIZE_RE.sub(" ", s)
    for m in COUNT_RE.finditer(s2):
        n = next(g for g in m.groups() if g)
        if int(n) > 1:
            sizes.add(("pc", float(n)))
    s2 = COUNT_RE.sub(" ", s2)
    words = [w.replace("'", "") for w in re.findall(r"[A-Z][A-Z']+", s2)]
    words = [_stem(w) for w in words if w not in STOP and len(w) > 1]
    words = [w for w in words if w not in STOP]
    brand = words[0] if words else None
    return set(words), sizes, brand


def score(a, b) -> float:
    (wa, sa, ba), (wb, sb, bb) = a, b
    if not wa or not wb:
        return 0.0
    if sa and sb:
        for kind in {k for k, _ in sa} & {k for k, _ in sb}:
            if not ({v for k, v in sa if k == kind} & {v for k, v in sb if k == kind}):
                return 0.0                                 # different pack size or count: never the same item
        if not (sa & sb):
            return 0.0                                     # sizes in different units (100G vs 50 bags)
    s = len(wa & wb) / len(wa | wb)
    if ba and ba not in wb and (not bb or bb not in wa):
        s *= 0.5                                           # brand word missing on one side
    if not sa or not sb:
        s *= 0.85                                          # can't confirm the pack size
    return round(s, 3)


class Index:
    """Token index over website products of one retailer, for fast candidate lookup."""
    def __init__(self, products):
        self.p = {pid: (sku, name, url, parse_name(name)) for pid, sku, name, url in products}
        self.post = defaultdict(set)
        for pid, (_, _, _, parsed) in self.p.items():
            for w in parsed[0]:
                self.post[w].add(pid)
        self.n = max(1, len(self.p))

    def candidates(self, parsed, k=3):
        words = sorted(parsed[0], key=lambda w: len(self.post.get(w, ())))
        cand = set()
        for w in words[:3]:
            cand |= self.post.get(w, set())
        scored = sorted(((score(parsed, self.p[c][3]), c) for c in cand), reverse=True)
        return [(s, c) for s, c in scored[:k] if s > 0]


def load(cur, retailer):
    cur.execute("SELECT retailer_code, item_key, retailer_name FROM retailer_items WHERE retailer = %s", (retailer,))
    file_items = cur.fetchall()
    cur.execute("SELECT id, sku, name, url, retailer_code, barcode FROM products WHERE competitor = %s", (retailer,))
    rows = cur.fetchall()
    web = [(pid, sku, name, url, rc) for pid, sku, name, url, rc, _ in rows]
    load.barcodes = {pid: bc for pid, _, _, _, _, bc in rows if bc}
    cur.execute("SELECT retailer_code, barcode FROM retailer_items WHERE retailer = %s AND barcode IS NOT NULL", (retailer,))
    load.file_barcodes = dict(cur.fetchall())
    cur.execute("SELECT product_id, retailer_code FROM product_links WHERE retailer = %s", (retailer,))
    linked = dict(cur.fetchall())
    return file_items, web, linked


def confirmed() -> dict:
    out = {}
    if CONFIRMED.exists():
        with CONFIRMED.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("retailer") and r.get("retailer_code"):
                    out[(r["retailer"].strip(), r["retailer_code"].strip().upper())] = (r.get("web_sku") or "").strip()
    return out


def naivas_page_code(url: str) -> str | None:
    soup = BeautifulSoup(get(url).text, "html.parser")
    for sc in soup.select("script[type='application/ld+json']"):
        try:
            d = json.loads(html.unescape(sc.get_text()))
        except ValueError:
            continue
        if d.get("@type") == "Product" and d.get("sku"):
            return str(d["sku"]).strip().upper()
    return None


def run(naivas_pages: int = 300) -> None:
    conn = db.connect()
    db.apply_schema(conn)
    cur = conn.cursor()
    conf = confirmed()
    links, review = [], []
    summary = {}

    for retailer in ("Foodplus", "Carrefour", "Naivas", "Quickmart"):
        # a website product that now shows the retailer's item code: its exact code beats an earlier name match
        cur.execute("""DELETE FROM product_links l USING products p
                       WHERE p.id = l.product_id AND l.retailer = %s AND l.method = 'name'
                         AND p.retailer_code IS NOT NULL AND p.retailer_code <> l.retailer_code""", (retailer,))
        if cur.rowcount:
            print(f"  {retailer}: {cur.rowcount} name matches replaced by the item code the website shows")
        file_items, web, already = load(cur, retailer)
        if not file_items or not web:
            summary[retailer] = f"{len(file_items)} file items, {len(web)} website products: nothing to link"
            continue
        by_code_file = {code: (key, name) for code, key, name in file_items}
        web_by_sku = {str(sku).upper(): pid for pid, sku, _, _, _ in web}
        used = set(already)
        n_code = n_conf = n_name = 0

        def add(pid, code, key, method, sc=None):
            nonlocal n_code, n_conf, n_name
            if pid in used:
                return
            used.add(pid)
            links.append((pid, retailer, code, key, method, sc))
            n_code += method in ("code", "barcode"); n_conf += method == "confirmed"; n_name += method == "name"

        # 1) confirmed by you
        for (r, code), web_sku in conf.items():
            if r == retailer and code in by_code_file and web_sku and web_sku.lower() != "none":
                pid = web_by_sku.get(web_sku.upper())
                if pid:
                    add(pid, code, by_code_file[code][0], "confirmed")

        # 2) exact codes
        if retailer in ("Foodplus", "Carrefour"):
            for code, (key, _) in by_code_file.items():
                if code in web_by_sku:
                    add(web_by_sku[code], code, key, "code")
        if retailer in ("Naivas", "Quickmart"):   # item code shown by the API (Naivas) / picture name (Quickmart)
            web_by_rcode = {str(rc).upper(): pid for pid, _, _, _, rc in web if rc}
            for code, (key, _) in by_code_file.items():
                if code in web_by_rcode:
                    add(web_by_rcode[code], code, key, "code")
        # any retailer whose website gives barcodes (Naivas): barcode = same product
        if load.barcodes:
            norm = lambda b: "".join(ch for ch in str(b) if ch.isdigit()).lstrip("0")
            web_by_bc = {norm(bc): pid for pid, bc in load.barcodes.items()}
            for code, (key, _) in by_code_file.items():
                bc = load.file_barcodes.get(code)
                if bc and norm(bc) in web_by_bc:
                    add(web_by_bc[norm(bc)], code, key, "barcode")

        linked_codes = {l[2] for l in links if l[1] == retailer} | {
            c for pid, c in already.items()}
        todo = [(code, key, name) for code, (key, name) in by_code_file.items()
                if code not in linked_codes and conf.get((retailer, code), "").lower() != "none"]

        # 3) Naivas: find the likely page by name, read its code (once), link on exact code
        if retailer == "Naivas" and todo and naivas_pages > 0:
            idx = Index([(pid, sku, name, url) for pid, sku, name, url, rc in web if not rc and pid not in used])
            urls = {pid: url for pid, _, _, url, _ in web}
            fetched = 0
            for code, key, name in todo:
                if fetched >= naivas_pages:
                    break
                for sc, pid in idx.candidates(parse_name(name), k=2):
                    if sc < 0.45 or fetched >= naivas_pages:
                        continue
                    try:
                        page_code = naivas_page_code(urls[pid])
                    except Exception:  # noqa: BLE001
                        page_code = None
                    fetched += 1
                    time.sleep(1.5)
                    if page_code:
                        cur.execute("UPDATE products SET retailer_code = %s WHERE id = %s", (page_code, pid))
                        conn.commit()
                        if page_code in by_code_file and pid not in used:
                            add(pid, page_code, by_code_file[page_code][0], "code")
                            if page_code == code:
                                break
            summary["Naivas pages read"] = fetched
            linked_codes = {l[2] for l in links if l[1] == retailer} | set(already.values())
            todo = [t for t in todo if t[0] not in linked_codes]

        # 4) Quickmart (and anything Naivas couldn't confirm): name + pack size
        if retailer in ("Quickmart", "Naivas") and todo:
            # Naivas: only website products without an item code (one with a code is a different item)
            idx = Index([(pid, sku, name, url) for pid, sku, name, url, rc in web if pid not in used and not rc])
            for code, key, name in todo:
                cands = idx.candidates(parse_name(name), k=3)
                if not cands:
                    continue
                best, pid = cands[0]
                second = cands[1][0] if len(cands) > 1 else 0
                if best >= AUTO_SCORE and best - second >= 0.1 and pid not in used:
                    add(pid, code, key, "name", best)
                    continue
                for rank, (sc, cpid) in enumerate(cands, 1):
                    if sc >= REVIEW_MIN:
                        sku, wname, url, _ = idx.p[cpid]
                        review.append((retailer, code, key, name, rank, cpid, sku, wname, url, sc))

        summary[retailer] = (f"{len(by_code_file)} file items | linked now: {n_code} by code, {n_name} by name, "
                             f"{n_conf} confirmed | previously linked: {len(already)}")

    if links:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO product_links (product_id, retailer, retailer_code, item_key, method, score) VALUES %s
            ON CONFLICT (product_id) DO UPDATE SET retailer_code = EXCLUDED.retailer_code, item_key = EXCLUDED.item_key,
                method = EXCLUDED.method, score = EXCLUDED.score, linked_at = now()""", links, page_size=1000)
    cur.execute("TRUNCATE match_review")
    if review:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO match_review (retailer, retailer_code, item_key, retailer_name, rank, product_id, web_sku,
                                      web_name, web_url, score) VALUES %s ON CONFLICT DO NOTHING""", review, page_size=1000)
    conn.commit()
    conn.close()

    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["retailer", "retailer_code", "file_item_name", "rank", "web_sku", "web_name", "score", "web_url"])
        for r in sorted(review, key=lambda x: (x[0], x[1], x[4])):
            w.writerow([r[0], r[1], r[3], r[4], r[6], r[7], r[9], r[8]])
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"Links written: {len(links)} | items to review: {len({(r[0], r[1]) for r in review})} "
          f"(see {REVIEW_CSV.relative_to(ROOT)})")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--naivas-pages", type=int, default=0,
                    help="(old site only) max Naivas product pages to read for codes; the new catalogue source "
                         "already gives every code, so this is 0 by default")
    run(ap.parse_args(argv).naivas_pages)


if __name__ == "__main__":
    main()
