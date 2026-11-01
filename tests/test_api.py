from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
from sqlalchemy import func, select, update

from celerity.database import make_sessions
from celerity.models import Order, Product


def test_health_and_catalogue(client_factory):
    client, _ = client_factory()
    assert client.get("/health").json() == {"status": "ok", "database": "postgresql"}
    response = client.get("/api/products", params={"limit": 5, "offset": 2})
    assert response.status_code == 200
    products = response.json()
    assert [product["id"] for product in products] == [3, 4, 5, 6, 7]
    assert all(len(product["images"]) == 2 for product in products)
    assert products[0]["price_cents"] == 1375
    assert client.get("/api/products/3").json() == products[0]
    assert client.get("/api/products/99999").status_code == 404


def test_pagination_is_bounded(client_factory):
    client, _ = client_factory()
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get("/api/products", params=params).status_code == 422


def test_customer_creation_normalizes_email_and_handles_duplicates(client_factory):
    client, _ = client_factory()
    payload = {"name": "  New customer  ", "email": " NEW@example.test "}
    created = client.post("/api/customers", json=payload)
    assert created.status_code == 201
    assert created.json() == {"id": 11, "name": "New customer", "email": "new@example.test"}
    assert client.post("/api/customers", json=payload).status_code == 409
    assert client.post("/api/customers", json={"name": " ", "email": "invalid"}).status_code == 422


def test_orders_are_filtered_and_sorted(client_factory):
    client, _ = client_factory()
    orders = client.get("/api/customers/1/orders", params={"limit": 3}).json()
    assert [order["id"] for order in orders] == [91, 81, 71]
    assert all(order["customer_id"] == 1 for order in orders)
    assert client.get("/api/customers/99999/orders").status_code == 404
    assert client.get("/api/orders/91").json() == orders[0]
    assert client.get("/api/orders/99999").status_code == 404


def test_create_order_uses_server_prices_and_reduces_stock(client_factory, engine):
    client, calls = client_factory()
    payload = {
        "customer_id": 1,
        "items": [{"product_id": 1, "quantity": 2}],
        "shipping_zone": "local",
    }
    result = client.post("/api/orders", json=payload)
    assert result.status_code == 201
    order = result.json()
    assert order["id"] == 101  # Seeded IDs did not break the PostgreSQL sequence.
    assert order["subtotal_cents"] == 2250
    assert order["shipping_cents"] == 250
    assert order["total_cents"] == 2500
    assert order["items"] == [{"product_id": 1, "quantity": 2, "unit_price_cents": 1125}]
    assert len(calls) == 1
    with make_sessions(engine)() as session:
        assert session.get(Product, 1).stock == 998


def test_invalid_cart_and_missing_entities(client_factory):
    client, calls = client_factory()
    for items in ([], [{"product_id": 1, "quantity": 0}], [{"product_id": 1, "quantity": 1}] * 2):
        assert (
            client.post("/api/orders", json={"customer_id": 1, "items": items}).status_code == 422
        )
    for customer, product in ((99999, 1), (1, 99999)):
        assert (
            client.post(
                "/api/orders",
                json={"customer_id": customer, "items": [{"product_id": product, "quantity": 1}]},
            ).status_code
            == 404
        )
    assert calls == []


def test_failed_cart_is_atomic(client_factory, engine):
    client, calls = client_factory()
    with engine.begin() as connection:
        connection.execute(update(Product).where(Product.id == 2).values(stock=0))
    result = client.post(
        "/api/orders",
        json={
            "customer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 2},
                {"product_id": 2, "quantity": 1},
            ],
        },
    )
    assert result.status_code == 409
    with make_sessions(engine)() as session:
        assert session.get(Product, 1).stock == 1000
        assert session.scalar(select(func.count()).select_from(Order)) == 100
    assert calls == []


def test_shipping_failure_does_not_create_order_or_reserve_stock(client_factory, engine):
    def timeout(request):
        raise httpx.ReadTimeout("simulated", request=request)

    client, _ = client_factory(handler=timeout)
    result = client.post(
        "/api/orders",
        json={
            "customer_id": 1,
            "items": [{"product_id": 1, "quantity": 2}],
        },
    )
    assert result.status_code == 503
    with make_sessions(engine)() as session:
        assert session.get(Product, 1).stock == 1000
        assert session.scalar(select(func.count()).select_from(Order)) == 100


def test_concurrent_orders_do_not_oversell(client_factory, engine):
    client, _ = client_factory()
    with engine.begin() as connection:
        connection.execute(update(Product).where(Product.id == 1).values(stock=1))

    start_together = Barrier(2)

    def order(_):
        start_together.wait(timeout=5)
        return client.post(
            "/api/orders",
            json={
                "customer_id": 1,
                "items": [{"product_id": 1, "quantity": 1}],
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(order, range(2)))
    assert sorted(statuses) == [201, 409]
    with make_sessions(engine)() as session:
        assert session.get(Product, 1).stock == 0
        assert session.scalar(select(func.count()).select_from(Order)) == 101
