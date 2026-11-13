"""Export the reporting views to CSV files (for Excel, or as a backup).

    python -m pricewatch.export                 # all views -> exports/
    python -m pricewatch.export v_products      # just one view

Power BI doesn't need this: it reads the views straight from the database.
"""
import csv
import sys

from . import db

VIEWS = ["v_products", "v_daily_prices", "v_price_changes", "v_promotions", "v_scraper_runs"]
OUT = db.ROOT / "exports"


def main(argv=None) -> None:
    views = (argv if argv is not None else sys.argv[1:]) or VIEWS
    OUT.mkdir(exist_ok=True)
    conn = db.connect()
    for v in views:
        with conn.cursor(name=f"exp_{v}") as cur:   # server-side cursor: streams large views
            cur.itersize = 5000
            cur.execute(f"SELECT * FROM {v}")
            path = OUT / f"{v[2:]}.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                header_written, n = False, 0
                for row in cur:
                    if not header_written:
                        w.writerow([d[0] for d in cur.description])
                        header_written = True
                    w.writerow(row)
                    n += 1
        print(f"{path.relative_to(db.ROOT)}: {n} rows")
    conn.close()


if __name__ == "__main__":
    main()
