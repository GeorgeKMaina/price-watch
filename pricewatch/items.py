"""Build the ITEM MASTER from the retailers' monthly files and store it in the database.

    python -m pricewatch.items            # scan the folders in config/settings.json
    python -m pricewatch.items --rebuild  # ignore the cache and re-read every file

What it does
------------
* Scans every .xlsx in the folders listed in config/settings.json ("retailer_files_folders").
* Recognises each sheet's retailer from its column headings (Majid/Carrefour, Naivas,
  Chandarana/Foodplus, Quickmart) - file names don't matter.
* Keeps ONLY item details: retailer item code, barcode, item name, supplier, category, brand.
  Sales quantities and values are never read into the database.
* Writes two tables:
    retailer_items : one row per retailer item code (each retailer's own code, name, supplier...)
    items          : one row per product, keyed by BARCODE (the same at every retailer), with ONE
                     uniform name, brand, supplier and category. Items without a barcode are keyed
                     by retailer + code.
* Uniform names can be overridden in config/item_names.csv (item_key or barcode, uniform_name).

Big files are only re-read when they change: a cache of what was extracted lives in
data/items_cache/ (never uploaded anywhere).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2.extras
from openpyxl import load_workbook

from . import db

ROOT = db.ROOT
CACHE = ROOT / "data" / "items_cache"
SETTINGS = ROOT / "config" / "settings.json"
NAME_OVERRIDES = ROOT / "config" / "item_names.csv"

# Column layout of each retailer's file (recognised by these headings). Defaults only: the copy in
# config/settings.json ("retailer_file_columns") is the one used, so headings can be changed there.
LAYOUTS = {
    "Carrefour": {"code": "Item Code", "barcode": "Item Bar Code", "name": "Item Name", "supplier": "SupplierName",
                  "brand": "Brand Name", "category": ["Section Name", "Family name", "Sub Family Name"],
                  "month": ["EoM", "EOM"]},
    "Naivas": {"code": "ITEMLOOKUPCODE", "barcode": "BARCODE", "name": "DESCRIPTION", "supplier": "SUPPLIERNAME",
               "brand": None, "category": ["CATEGORY"], "month": ["EOM"]},
    "Foodplus": {"code": "ITEM_CD", "barcode": "Barcode", "name": "Item Desc", "supplier": "VendorName",
                 "brand": None, "category": ["Department"], "month": ["Month"]},
    "Quickmart": {"code": "ITEM_CODE", "barcode": "BARCODE", "name": "ITEM_NAME", "supplier": "SUPPLIER",
                  "brand": None, "category": ["DEPARTMENT", "SUB DEPARTMENT", "MICRO DEPARTMENT"], "month": ["EOM"]},
}
# Which retailer's wording becomes the uniform name / category when several have the item
NAME_PRIORITY = ["Naivas", "Carrefour", "Foodplus", "Quickmart"]
CATEGORY_PRIORITY = ["Carrefour", "Quickmart", "Naivas", "Foodplus"]


def settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def norm_code(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    s = re.sub(r"\.0+$", "", s)
    return s.upper() or None


def norm_barcode(v) -> str | None:
    s = norm_code(v)
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?E\+\d+", s):          # scientific notation that slipped through
        s = str(int(float(s)))
    s = re.sub(r"\D", "", s).lstrip("0")
    return s if len(s) >= 6 else None                   # too short to be a real barcode


def clean_text(v) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def detect(header: list) -> tuple[str, dict] | None:
    h = {str(x).strip() for x in header if x is not None}
    for retailer, lay in (settings().get("retailer_file_columns") or LAYOUTS).items():
        if {lay["code"], lay["barcode"], lay["name"], lay["supplier"]} <= h:
            return retailer, lay
    return None


def read_sheet(ws) -> tuple[str, list[dict]] | None:
    rows = ws.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        return None
    found = detect(list(header))
    if not found:
        return None
    retailer, lay = found
    idx = {str(c).strip(): i for i, c in enumerate(header) if c is not None}
    def col(name):
        return idx.get(name)
    ci = {k: col(lay[k]) for k in ("code", "barcode", "name", "supplier")}
    bi = col(lay["brand"]) if lay["brand"] else None
    cats = [col(c) for c in lay["category"]]
    mi = next((col(m) for m in lay["month"] if col(m) is not None), None)
    items: dict[str, dict] = {}
    for r in rows:
        code = norm_code(r[ci["code"]]) if ci["code"] < len(r) else None
        if not code:
            continue
        month = r[mi] if mi is not None and mi < len(r) else None
        month = month.date().isoformat() if hasattr(month, "date") else (str(month)[:10] if month else None)
        prev = items.get(code)
        if prev and (prev["last_month"] or "") >= (month or ""):
            continue                                        # keep the most recent description
        items[code] = {
            "retailer": retailer, "retailer_code": code,
            "barcode": norm_barcode(r[ci["barcode"]]),
            "retailer_name": clean_text(r[ci["name"]]),
            "supplier": clean_text(r[ci["supplier"]]),
            "brand": clean_text(r[bi]) if bi is not None else None,
            "category": [clean_text(r[c]) if c is not None and c < len(r) else None for c in cats],
            "last_month": month,
        }
    return retailer, list(items.values())


def file_items(path: Path, rebuild: bool) -> list[dict]:
    """Items from one workbook, from cache if the file hasn't changed."""
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    cache = CACHE / f"{key}.json"
    if cache.exists() and not rebuild:
        return json.loads(cache.read_text(encoding="utf-8"))
    print(f"    reading {path.name} ...", flush=True)
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        res = read_sheet(ws)
        if res:
            retailer, items = res
            print(f"      [{ws.title}] {retailer}: {len(items)} item codes", flush=True)
            out.extend(items)
    wb.close()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def tidy_name(name: str, retailer: str) -> str:
    s = name
    if retailer == "Quickmart":
        s = re.sub(r"^[A-Z]{2}-", "", s)                    # department prefix: FD-, PC-, HC- ...
    s = s.title()
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(Kg|G|Gm|Gms|Ml|L|Ltr|Ltrs|Litre|Pcs|Pc|S)\b",
               lambda m: m.group(1) + m.group(2).lower(), s)
    return s.replace("'S ", "'s ")


MERGES_CSV = ROOT / "data" / "item_merges.csv"
NO_MERGE = ROOT / "config" / "item_no_merge.csv"
MERGE_SCORE = 0.80


def merge_by_name(by_key: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Same product, different barcodes: retailers don't always use the same barcode (Quickmart's
    files often carry the older import barcode, e.g. Weetabix Banana 500G/460G). Items from
    DIFFERENT retailers are merged when brand, words and pack size match clearly (score >= 0.80
    and clearly better than the next candidate, both ways). Every merge is listed in
    data/item_merges.csv; to undo one, put its two item_keys in config/item_no_merge.csv."""
    from .link import Index, parse_name, score   # same name + pack-size logic as the website matching

    blocked = set()
    if NO_MERGE.exists():
        with NO_MERGE.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                a, b = (r.get("item_key_1") or "").strip(), (r.get("item_key_2") or "").strip()
                if a and b:
                    blocked.add(frozenset((a, b)))

    def rep(group):
        by_ret = {g["retailer"]: g for g in group}
        src = next((by_ret[r] for r in NAME_PRIORITY if r in by_ret and by_ret[r]["retailer_name"]), group[0])
        words, _, brand = parse_name(src["retailer_name"])
        sizes = set().union(*(parse_name(g["retailer_name"])[1] for g in group if g["retailer_name"]))
        return words, sizes, brand

    keys = list(by_key)
    parsed = {k: rep(by_key[k]) for k in keys}
    rets = {k: {g["retailer"] for g in by_key[k]} for k in keys}
    idx = Index([(k, k, " ", "") for k in keys])
    idx.p = {k: (k, "", "", parsed[k]) for k in keys}          # use the group's own parsed name
    idx.post = defaultdict(set)
    for k in keys:
        for w in parsed[k][0]:
            idx.post[w].add(k)

    def best(k):
        out = []
        for sc, c in idx.candidates(parsed[k], k=8):
            if c != k and not (rets[k] & rets[c]) and parsed[k][2] == parsed[c][2] \
                    and frozenset((k, c)) not in blocked:
                out.append((sc, c))
        return out

    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    merges = []
    for k in keys:
        c1 = best(k)
        if not c1 or c1[0][0] < MERGE_SCORE or (len(c1) > 1 and c1[0][0] - c1[1][0] < 0.1):
            continue
        sc, c = c1[0]
        back = best(c)
        if not back or back[0][1] != k or (len(back) > 1 and back[0][0] - back[1][0] < 0.1):
            continue                                            # must be each other's clear best match
        a, b = find(k), find(c)
        if a == b:
            continue
        parent[b] = a
        merges.append((k, c, sc))

    merged: dict[str, list[dict]] = defaultdict(list)
    for k in keys:
        merged[find(k)].extend(by_key[k])
    # the kept key: the one shared by most retailers (a barcode key when there is one)
    out = {}
    for root, group in merged.items():
        members = {g["item_key"] for g in group}
        keep = max(members, key=lambda m: (len({g["retailer"] for g in group if g["item_key"] == m}), m.startswith("B")))
        for g in group:
            g["item_key"] = keep
        out[keep] = group

    MERGES_CSV.parent.mkdir(parents=True, exist_ok=True)
    names = {k: " / ".join(sorted({f"{g['retailer']}: {g['retailer_name']}" for g in by_key[k]})) for k in keys}
    with MERGES_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["item_key_1", "item_key_2", "score", "names_1", "names_2"])
        for a, b, sc in merges:
            w.writerow([a, b, sc, names[a], names[b]])
    print(f"  merged {len(merges)} items that differ only in barcode (list: {MERGES_CSV.relative_to(ROOT)})")
    return out


def build(rebuild: bool = False):
    folders = [Path(f) for f in settings().get("retailer_files_folders", [])]
    files = []
    for f in folders:
        if not f.exists():
            print(f"  ! folder not found: {f}")
            continue
        files += [p for p in f.rglob("*.xlsx") if not p.name.startswith("~$")]
    print(f"Scanning {len(files)} workbook(s) in {len(folders)} folder(s)")
    rows: dict[tuple, dict] = {}
    for p in sorted(files):
        try:
            for it in file_items(p, rebuild):
                k = (it["retailer"], it["retailer_code"])
                if k not in rows or (it["last_month"] or "") >= (rows[k]["last_month"] or ""):
                    rows[k] = it
        except Exception as e:  # noqa: BLE001
            print(f"  ! {p.name}: {type(e).__name__}: {e}")
    retailer_items = list(rows.values())
    for it in retailer_items:
        it["item_key"] = ("B" + it["barcode"]) if it["barcode"] else f"{it['retailer'][:2].upper()}-{it['retailer_code']}"

    # ---- one row per product (item_key) -------------------------------------------------
    overrides = {}
    if NAME_OVERRIDES.exists():
        with NAME_OVERRIDES.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                k = (r.get("item_key") or "").strip() or ("B" + (norm_barcode(r.get("barcode")) or ""))
                if k and r.get("uniform_name"):
                    overrides[k] = r["uniform_name"].strip()
    by_key: dict[str, list[dict]] = defaultdict(list)
    for it in retailer_items:
        by_key[it["item_key"]].append(it)
    by_key = merge_by_name(by_key)
    ours_patterns = [p.upper() for p in settings().get("our_suppliers_contain", []) if p]
    items = []
    for key, group in by_key.items():
        by_ret = {g["retailer"]: g for g in group}
        name_src = next((by_ret[r] for r in NAME_PRIORITY if r in by_ret and by_ret[r]["retailer_name"]), group[0])
        cat_src = next((by_ret[r] for r in CATEGORY_PRIORITY if r in by_ret and any(by_ret[r]["category"])), group[0])
        cats = [c for c in cat_src["category"] if c] + [None, None, None]
        brand = next((g["brand"] for g in group if g["brand"]), None)
        suppliers = Counter(g["supplier"] for g in group if g["supplier"])
        uniform = overrides.get(key) or tidy_name(name_src["retailer_name"] or key, name_src["retailer"])
        size, unit = pack_size(uniform, [g["retailer_name"] for g in group])
        top_supplier = suppliers.most_common(1)[0][0] if suppliers else None
        items.append({
            "item_key": key, "barcode": group[0]["barcode"],
            "uniform_name": uniform,
            "brand": brand.title() if brand else None,
            "supplier": top_supplier,
            "all_suppliers": " | ".join(sorted(suppliers)),
            "category_1": cats[0], "category_2": cats[1], "category_3": cats[2],
            "retailers_in_files": ", ".join(sorted(by_ret)),
            "supplier_group": supplier_group(top_supplier),
            "ours": any(p in s.upper() for s in suppliers for p in ours_patterns),
            "pack_size": size, "pack_unit": unit,
        })
    return retailer_items, items


_SUPPLIER_NOISE = {"LIMITED", "LTD", "LTD.", "LIMITE", "LIMIT", "L", "HQ", "CO", "CO.", "COMPANY", "PLC", "KENYA", "K",
                   "KE", "EA", "(EA)", "E.A", "E.A.", "(E.A)", "(E.A.)", "EAST", "AFRICA", "INTERNATIONAL", "INDUSTRIES",
                   "DISTRIBUTORS", "DISTRIBUTION", "TRADING", "GENERAL", "&", "AND"}


def supplier_group(name: str | None) -> str | None:
    """One readable name per supplier, whatever each retailer's spelling:
    'HEALTHY U TWO THOUSAND LTD-CONSIGNMENT' and '... LIMITED' -> 'Healthy U Two Thousand'."""
    if not name:
        return None
    s = re.split(r"\s+-|-\s*CONSIGN|\(CONSIGN|-CONS", name.upper())[0]
    words = [w for w in re.split(r"\s+", s.replace(",", " ")) if w]
    while len(words) > 1 and words[-1].strip(".()") in {w.strip(".()") for w in _SUPPLIER_NOISE}:
        words.pop()
    return " ".join(words).title() if words else name.title()


def pack_size(uniform: str, names: list) -> tuple[float | None, str | None]:
    """Pack size for price-per-kg / per-litre comparisons: grams, millilitres, or pieces."""
    from .link import parse_name
    for n in [uniform] + [x for x in names if x]:
        sizes = parse_name(n)[1]
        for unit in ("g", "ml"):
            v = [val for kind, val in sizes if kind == unit and val > 0]
            if v:
                return min(v), unit
    for n in [uniform] + [x for x in names if x]:
        v = [val for kind, val in parse_name(n)[1] if kind == "pc" and val > 1]
        if v:
            return min(v), "pc"
    return None, None


WATCHLIST = ROOT / "config" / "watchlist.csv"


def apply_watchlist(items) -> int:
    """Mark items as followed. config/watchlist.csv rows (any column may be blank):
    supplier_contains / brand_contains / name_contains: text that must appear (ignoring case);
    barcode: one exact product. A row's filled-in columns must all match. No rows = follow everything."""
    rules = []
    if WATCHLIST.exists():
        with WATCHLIST.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k: (v or "").strip() for k, v in r.items() if k}
                if any(r.get(c) for c in ("supplier_contains", "brand_contains", "name_contains", "barcode")):
                    rules.append(r)
    for i in items:
        if not rules:
            i["watched"] = True
            continue
        def ok(r):
            if r.get("supplier_contains") and r["supplier_contains"].lower() not in (i["all_suppliers"] or "").lower():
                return False
            if r.get("brand_contains") and r["brand_contains"].lower() not in ((i["brand"] or "") + " " + i["uniform_name"]).lower():
                return False
            if r.get("name_contains") and r["name_contains"].lower() not in i["uniform_name"].lower():
                return False
            if r.get("barcode") and norm_barcode(r["barcode"]) != i["barcode"]:
                return False
            return True
        i["watched"] = any(ok(r) for r in rules)
    return sum(i["watched"] for i in items)


def save(retailer_items, items) -> None:
    conn = db.connect()
    db.apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE retailer_items, items")
        psycopg2.extras.execute_values(cur, """
            INSERT INTO items (item_key, barcode, uniform_name, brand, supplier, all_suppliers,
                               category_1, category_2, category_3, retailers_in_files, watched,
                               supplier_group, ours, pack_size, pack_unit) VALUES %s""",
            [(i["item_key"], i["barcode"], i["uniform_name"], i["brand"], i["supplier"], i["all_suppliers"],
              i["category_1"], i["category_2"], i["category_3"], i["retailers_in_files"], i["watched"],
              i["supplier_group"], i["ours"], i["pack_size"], i["pack_unit"]) for i in items],
            page_size=2000)
        psycopg2.extras.execute_values(cur, """
            INSERT INTO retailer_items (retailer, retailer_code, item_key, barcode, retailer_name, supplier, brand,
                                        category_1, category_2, category_3, last_month) VALUES %s""",
            [(r["retailer"], r["retailer_code"], r["item_key"], r["barcode"], r["retailer_name"], r["supplier"], r["brand"],
              *(r["category"] + [None, None, None])[:3], r["last_month"]) for r in retailer_items], page_size=2000)
        # website links follow their file item when items get merged
        cur.execute("""UPDATE product_links l SET item_key = r.item_key FROM retailer_items r
                       WHERE r.retailer = l.retailer AND r.retailer_code = l.retailer_code AND l.item_key <> r.item_key""")
    conn.commit()
    conn.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="re-read every file, ignoring the cache")
    args = ap.parse_args(argv)
    retailer_items, items = build(args.rebuild)
    per = Counter(r["retailer"] for r in retailer_items)
    shared = sum(1 for i in items if "," in i["retailers_in_files"])
    print(f"Item master: {len(items)} products ({shared} sold by 2+ retailers) | item codes per retailer: {dict(per)}")
    print(f"Followed (config/watchlist.csv): {apply_watchlist(items)} of {len(items)}")
    print(f"Ours (settings.json our_suppliers_contain): {sum(i['ours'] for i in items)} items | "
          f"with a pack size: {sum(1 for i in items if i['pack_size'])}")
    save(retailer_items, items)
    print("Saved to the database (tables items, retailer_items).")


if __name__ == "__main__":
    main()
