"""Create the tables and reporting views in the database (first-time setup; safe to re-run).

    python -m pricewatch.setup_db
"""
from . import db


def main() -> None:
    conn = db.connect()
    db.apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM products")
        n = cur.fetchone()[0]
    conn.close()
    print(f"Database ready ({n} products stored).")


if __name__ == "__main__":
    main()
