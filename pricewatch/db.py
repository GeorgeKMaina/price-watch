"""PostgreSQL storage (Supabase in production, any Postgres for testing).

Connection: the DATABASE_URL environment variable, e.g.
    postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
(Supabase: Project settings -> Database -> Connection string -> "Session pooler". The pooler
works from GitHub Actions; the "direct connection" host does not.)
For local runs, put the same line in a file named .env in the project folder (never commit it).

Storage rule: a price row is written only when the normal price, promo price or stock status
differs from the product's latest stored row. Every product seen in a run gets last_seen updated.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "sql" / "schema.sql"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def connect():
    _load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Put your Supabase connection string in a .env file in the project "
            "folder (see SUPABASE_SETUP.md), or set it as a GitHub secret for the Actions run."
        )
    # keepalives: notice a dead connection (PC went to sleep, network dropped) within ~2 minutes
    # instead of waiting forever; timeouts stop any single statement or lock wait from hanging.
    conn = psycopg2.connect(**connection_params(url), connect_timeout=30,
                            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=6)
    with conn.cursor() as cur:   # (Supabase's pooler ignores connect-time options, so set them here)
        cur.execute("SET statement_timeout = '15min'; SET lock_timeout = '2min'")
    conn.commit()
    conn.autocommit = False
    return conn


def connection_params(url: str) -> dict:
    """Split postgresql://user:password@host:port/dbname into parts.

    Done by hand rather than as a standard URL so the password may contain ANY characters,
    including @ : / # ? $ ! (the password is everything between the first ':' after the user
    name and the LAST '@'). Paste the password exactly as it is, without URL-encoding it.
    """
    from urllib.parse import unquote

    url = url.strip()
    m = re.match(r"^postgres(?:ql)?://", url)
    if not m:
        raise SystemExit("DATABASE_URL must start with postgresql://")
    rest = url[m.end():]
    at = rest.rfind("@")
    if at == -1:
        raise SystemExit("DATABASE_URL has no '@' between the password and the host")
    userinfo, hostpart = rest[:at], rest[at + 1:]
    user, _, password = userinfo.partition(":")
    hostport, _, dbname = hostpart.partition("/")
    dbname = dbname.split("?")[0] or "postgres"
    host, _, port = hostport.partition(":")
    return {"host": host, "port": int(port or 5432), "user": unquote(user),
            "password": password, "dbname": dbname, "sslmode": "require" if "supabase" in host else "prefer"}


def apply_schema(conn) -> None:
    """Create tables if missing and refresh the views. Parallel GitHub jobs take turns (advisory lock)."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(724001)")
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


# ------------------------------------------------------------------ runs
def start_run(conn, competitor: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO runs (competitor, started_at, status) VALUES (%s, %s, 'running') RETURNING id",
                    (competitor, now_utc()))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, seen: int = 0, changed: int = 0, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE runs SET finished_at = %s, status = %s, items_seen = %s, items_changed = %s, error = %s"
                    " WHERE id = %s", (now_utc(), status, seen, changed, error, run_id))
    conn.commit()


# ------------------------------------------------------------------ saving a whole run
def save_items(conn, run_id: int, competitor: str, items: list[dict], ts: datetime) -> int:
    """Upsert products, then write price rows only where something changed. Returns rows written.

    Items carrying their own "scraped_at" (hand-saved pages) are stored as-is at that time,
    skipping any already stored for the same product and time.
    """
    if not items:
        return 0
    # Live scrapes: one observation per SKU (the last one in the run wins).
    # Hand-saved pages: one observation per SKU per saved page (different dates are history).
    live_items = {it["sku"]: it for it in items if not it.get("scraped_at")}
    saved_items = {(it["sku"], it["scraped_at"]): it for it in items if it.get("scraped_at")}
    rows = list(live_items.values()) + list(saved_items.values())

    def when(it):
        t = it.get("scraped_at") or ts
        return datetime.fromisoformat(t) if isinstance(t, str) else t

    by_sku: dict[str, dict] = {}
    for it in sorted(rows, key=when):           # latest observation gives the name/url/labels
        first = by_sku.get(it["sku"], {}).get("_first", when(it))
        by_sku[it["sku"]] = {**it, "_first": first, "_last": when(it)}
    with conn.cursor() as cur:
        product_rows = [(
            competitor, it["sku"], it["name"], it.get("url"), it.get("currency"),
            it.get("site_category"), it.get("category"),
            it.get("my_category"), it.get("product_group"), bool(it.get("key_item")),
            it["_first"], it["_last"], it.get("retailer_code"), it.get("barcode"),
        ) for it in by_sku.values()]
        ids = psycopg2.extras.execute_values(cur, """
            INSERT INTO products (competitor, sku, name, url, currency, site_category, target_category,
                                  my_category, product_group, key_item, first_seen, last_seen,
                                  retailer_code, barcode)
            VALUES %s
            ON CONFLICT (competitor, sku) DO UPDATE SET
                retailer_code   = COALESCE(EXCLUDED.retailer_code, products.retailer_code),
                barcode         = COALESCE(EXCLUDED.barcode, products.barcode),
                name            = EXCLUDED.name,
                url             = COALESCE(EXCLUDED.url, products.url),
                currency        = COALESCE(EXCLUDED.currency, products.currency),
                site_category   = COALESCE(EXCLUDED.site_category, products.site_category),
                target_category = COALESCE(EXCLUDED.target_category, products.target_category),
                my_category     = EXCLUDED.my_category,
                product_group   = EXCLUDED.product_group,
                key_item        = EXCLUDED.key_item,
                first_seen      = LEAST(products.first_seen, EXCLUDED.first_seen),
                last_seen       = GREATEST(products.last_seen, EXCLUDED.last_seen)
            RETURNING sku, id
        """, product_rows, page_size=1000, fetch=True)
        pid = dict(ids)

        # latest stored observation per product of this competitor
        cur.execute("""
            SELECT DISTINCT ON (x.product_id) x.product_id, x.normal_price, x.promo_price, x.in_stock
            FROM prices x JOIN products p ON p.id = x.product_id
            WHERE p.competitor = %s
            ORDER BY x.product_id, x.observed_at DESC, x.id DESC
        """, (competitor,))
        latest = {r[0]: (float(r[1]), None if r[2] is None else float(r[2]), r[3]) for r in cur.fetchall()}

        live, saved_pages = [], []
        for it in rows:
            product_id = pid[it["sku"]]
            stock = None if it.get("in_stock") is None else bool(it["in_stock"])
            new = (round(float(it["normal_price"]), 2),
                   None if it.get("promo_price") is None else round(float(it["promo_price"]), 2), stock)
            if it.get("scraped_at"):
                saved_pages.append((product_id, run_id, it["scraped_at"], *new))
            elif latest.get(product_id) != new:
                live.append((product_id, run_id, ts, *new))

        if live:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO prices (product_id, run_id, observed_at, normal_price, promo_price, in_stock) VALUES %s
            """, live, page_size=1000)
        written = len(live)
        if saved_pages:
            res = psycopg2.extras.execute_values(cur, """
                INSERT INTO prices (product_id, run_id, observed_at, normal_price, promo_price, in_stock)
                SELECT v.product_id, v.run_id, v.observed_at::timestamptz, v.normal_price, v.promo_price, v.in_stock
                FROM (VALUES %s) AS v(product_id, run_id, observed_at, normal_price, promo_price, in_stock)
                WHERE NOT EXISTS (SELECT 1 FROM prices x WHERE x.product_id = v.product_id
                                  AND x.observed_at = v.observed_at::timestamptz)
                RETURNING 1
            """, saved_pages, page_size=1000, fetch=True)
            written += len(res)
    conn.commit()
    return written


def relabel(conn, rules, apply_map) -> int:
    """Re-apply config/product_map.csv to every product (category, group, key item)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, competitor, sku, name FROM products")
        updates = []
        for pid, comp, sku, name in cur.fetchall():
            lab = apply_map(rules, comp, sku, name)
            updates.append((lab["my_category"], lab["product_group"], lab["key_item"], pid))
        psycopg2.extras.execute_batch(cur, "UPDATE products SET my_category = %s, product_group = %s, key_item = %s"
                                           " WHERE id = %s", updates, page_size=1000)
    conn.commit()
    return len(updates)
