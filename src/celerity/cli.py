"""Explicit database preparation; starting the API never silently edits its schema."""

import argparse
import json

from celerity.config import Settings
from celerity.database import Base, configure_order_index, make_engine
from celerity.seed import seed_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Celerity Phase 1 lab commands")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init-db", help="Create tables, seed if empty, and apply index setting"
    )
    init.add_argument("--products", type=int, default=2000)
    init.add_argument("--customers", type=int, default=1000)
    init.add_argument("--orders", type=int, default=30000)
    arguments = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        Base.metadata.create_all(engine)
        counts = seed_demo(
            engine,
            products=arguments.products,
            customers=arguments.customers,
            orders=arguments.orders,
        )
        configure_order_index(engine, missing=settings.enable_missing_index)
        print(json.dumps({"rows": counts, "faults": settings.faults()}, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
