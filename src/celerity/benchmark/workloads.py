"""Stable, read-only workload contracts shared by preflight and Locust."""

import hashlib
import json
import math
import re

WORKLOADS = {
    "catalogue": {"path": "/api/products?limit=20&offset=0", "fault": "n-plus-one"},
    "order-history": {"path": "/api/customers/1/orders?limit=20", "fault": "missing-index"},
    "shipping": {"path": "/api/checkout/quote?zone=national", "fault": "redundant-shipping"},
}


def response_digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_response(workload: str, payload: object) -> None:
    if workload == "catalogue":
        if not isinstance(payload, list) or len(payload) != 20:
            raise ValueError("Expected 20 catalogue products.")
        if [product["id"] for product in payload] != list(range(1, 21)):
            raise ValueError("Unexpected catalogue product IDs.")
        for product in payload:
            if (
                product["currency"] != "USD"
                or product["price_cents"] < 0
                or product["stock"] < 0
                or len(product["images"]) != 2
            ):
                raise ValueError("Invalid catalogue product fields.")
    elif workload == "order-history":
        if not isinstance(payload, list) or len(payload) != 20:
            raise ValueError("Expected 20 seeded customer orders.")
        previous = None
        for order in payload:
            if order["customer_id"] != 1 or not order["items"]:
                raise ValueError("Order belongs to the wrong customer or has no items.")
            subtotal = sum(item["quantity"] * item["unit_price_cents"] for item in order["items"])
            if (
                subtotal != order["subtotal_cents"]
                or order["total_cents"] != subtotal + order["shipping_cents"]
            ):
                raise ValueError("Order totals are inconsistent.")
            key = (order["created_at"], order["id"])
            if previous is not None and key > previous:
                raise ValueError("Orders are not newest first.")
            previous = key
    elif workload == "shipping":
        if payload != {"zone": "national", "shipping_cents": 500, "currency": "USD"}:
            raise ValueError("Unexpected shipping quote.")
    else:
        raise ValueError("Unknown workload.")


def read_measurement_headers(headers) -> dict:
    result = {
        "sql_count": int(headers["X-Celerity-Sql-Count"]),
        "sql_ms": float(headers["X-Celerity-Sql-Ms"]),
        "shipping_calls": int(headers["X-Celerity-Shipping-Calls"]),
        "shipping_ms": float(headers["X-Celerity-Shipping-Ms"]),
        "trace_id": headers["X-Celerity-Trace-Id"],
    }
    if not re.fullmatch(r"[0-9a-f]{32}", result["trace_id"]) or int(result["trace_id"], 16) == 0:
        raise ValueError("Missing or invalid trace ID.")
    for key in ("sql_count", "sql_ms", "shipping_calls", "shipping_ms"):
        if not math.isfinite(result[key]) or result[key] < 0:
            raise ValueError("Invalid request measurement.")
    return result
