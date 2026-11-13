"""Rebuild the stored day-by-day table (v_daily_prices) that Power BI reads.

    python -m pricewatch.refresh            # after the daily crawl (the GitHub workflow does this)
    python -m pricewatch.refresh --rebuild  # after changing its definition in sql/schema.sql

The first refresh builds it; later ones swap in the new data without blocking Power BI.
"""
import sys
import time

from . import db


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    conn = db.connect()
    if "--rebuild" in argv:
        with conn.cursor() as cur:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS v_daily_prices CASCADE")
        conn.commit()
    db.apply_schema(conn)
    t = time.time()
    with conn.cursor() as cur:
        cur.execute("SELECT relispopulated FROM pg_class WHERE relname = 'v_daily_prices'")
        populated = cur.fetchone()[0]
        cur.execute("SET statement_timeout = '15min'")
        cur.execute(f"REFRESH MATERIALIZED VIEW {'CONCURRENTLY ' if populated else ''}v_daily_prices")
        cur.execute("SELECT COUNT(*) FROM v_daily_prices")
        n = cur.fetchone()[0]
    conn.commit()
    conn.close()
    print(f"v_daily_prices refreshed: {n} rows in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
