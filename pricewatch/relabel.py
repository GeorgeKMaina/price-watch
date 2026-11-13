"""Re-apply config/product_map.csv to every product in the database.

    python -m pricewatch.relabel

Runs automatically on GitHub whenever config/product_map.csv is changed, so new categories,
product groups and key items show up in Power BI on its next refresh, for the whole history.
"""
from . import config, db


def main() -> None:
    conn = db.connect()
    db.apply_schema(conn)
    n = db.relabel(conn, config.load_map(), config.apply_map)
    conn.close()
    print(f"Relabelled {n} products from config/product_map.csv")


if __name__ == "__main__":
    main()
