"""Prove all three faults using real PostgreSQL and a running shipping stub.

This is a mechanics check, not the Phase 2 performance benchmark. It temporarily
changes the lab's lookup index and restores its original presence in finally.
Run while the lab is idle, never against an unrelated or production database.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text

from celerity.config import Settings
from celerity.database import configure_order_index, make_engine, order_index_present
from celerity.main import create_app, customer_orders_query
from celerity.models import Order


def collect_nodes(plan: dict) -> list[dict]:
    return [plan, *(node for child in plan.get("Plans", []) for node in collect_nodes(child))]


def main() -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    report = {"checked_at": datetime.now(UTC).isoformat(), "checks": {}}
    original_index = order_index_present(engine)
    healthy = settings.model_copy(
        update={
            "enable_n_plus_one": False,
            "enable_missing_index": False,
            "enable_redundant_shipping": False,
        }
    )
    try:
        with engine.connect() as connection:
            report["postgresql"] = connection.scalar(text("SHOW server_version"))
            report["seeded_orders"] = connection.scalar(select(func.count()).select_from(Order))
        if report["seeded_orders"] < 20000:
            raise RuntimeError("Use the default seeded dataset (30,000 orders) for the plan check.")

        catalogue = []
        for broken in (False, True):
            statements = []

            def record(_conn, _cursor, statement, _params, _context, _many, captured=statements):
                if statement.lstrip().upper().startswith("SELECT"):
                    captured.append(statement)

            app = create_app(
                healthy.model_copy(update={"enable_n_plus_one": broken}), engine=engine
            )
            with TestClient(app) as client:
                event.listen(engine, "before_cursor_execute", record)
                try:
                    response = client.get("/api/products?limit=20")
                finally:
                    event.remove(engine, "before_cursor_execute", record)
                response.raise_for_status()
                catalogue.append((response.json(), len(statements)))
        assert catalogue[0][0] == catalogue[1][0]
        assert (catalogue[0][1], catalogue[1][1]) == (2, 21)
        report["checks"]["n_plus_one"] = {
            "healthy_selects": catalogue[0][1],
            "faulty_selects": catalogue[1][1],
            "same_response": True,
        }

        lookup_sql = str(
            customer_orders_query(1).compile(
                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
            )
        )
        plans = []
        orders = []
        for missing in (False, True):
            configure_order_index(engine, missing=missing)
            with engine.connect() as connection:
                explanation = connection.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + lookup_sql)
                ).scalar_one()[0]
            nodes = collect_nodes(explanation["Plan"])
            plans.append(
                {
                    "index_present": order_index_present(engine),
                    "node_types": [node["Node Type"] for node in nodes],
                    "indexes_used": [node["Index Name"] for node in nodes if "Index Name" in node],
                    "execution_ms": explanation["Execution Time"],
                }
            )
            with TestClient(create_app(healthy, engine=engine)) as client:
                result = client.get("/api/customers/1/orders")
                result.raise_for_status()
                orders.append(result.json())
        assert orders[0] == orders[1]
        assert plans[0]["indexes_used"] == ["ix_orders_customer_created"]
        assert "Seq Scan" in plans[1]["node_types"]
        report["checks"]["missing_index"] = {
            "healthy": plans[0],
            "faulty": plans[1],
            "same_response": True,
            "timing_note": "Single runs explain mechanics, not statistically established speedups.",
        }

        quotes = []
        for broken in (False, True):
            requests = []
            with httpx.Client(
                base_url=settings.shipping_url,
                timeout=3.0,
                trust_env=False,
                event_hooks={"request": [requests.append]},
            ) as outbound:
                app = create_app(
                    healthy.model_copy(update={"enable_redundant_shipping": broken}),
                    engine=engine,
                    shipping_client=outbound,
                )
                with TestClient(app) as client:
                    start = time.perf_counter()
                    result = client.get("/api/checkout/quote?zone=national")
                    elapsed = (time.perf_counter() - start) * 1000
                    result.raise_for_status()
                    quotes.append((result.json(), len(requests), elapsed))
        assert quotes[0][0] == quotes[1][0]
        assert (quotes[0][1], quotes[1][1]) == (1, 3)
        report["checks"]["redundant_shipping"] = {
            "healthy_calls": quotes[0][1],
            "faulty_calls": quotes[1][1],
            "healthy_elapsed_ms": round(quotes[0][2], 2),
            "faulty_elapsed_ms": round(quotes[1][2], 2),
            "same_response": True,
        }
        report["result"] = "PASS"
    finally:
        configure_order_index(engine, missing=not original_index)
        engine.dispose()
    output = Path("var")
    output.mkdir(exist_ok=True)
    (output / "phase1-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
