"""Deterministic synthetic data, inserted in one transaction and never overwritten."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func, insert, select, text

from celerity.models import Customer, Order, OrderItem, Product, ProductImage


def product_price(product_id: int) -> int:
    return 1000 + (product_id % 100) * 125


def seed_demo(
    engine: Engine, *, products: int = 2000, customers: int = 1000, orders: int = 30000
) -> dict[str, int]:
    if products < 1 or customers < 1 or orders < 0:
        raise ValueError("Use positive product/customer counts and a nonnegative order count.")
    with engine.begin() as connection:
        counts = {
            model.__tablename__: connection.scalar(select(func.count()).select_from(model))
            for model in (Product, Customer, Order)
        }
        if any(counts.values()):
            if not counts["products"] or not counts["customers"]:
                raise RuntimeError(
                    "Partial data exists; use a fresh lab database for initial seeding."
                )
            return counts
        connection.execute(
            insert(Product),
            [
                {
                    "id": i,
                    "name": f"Demo product {i:04}",
                    "price_cents": product_price(i),
                    "stock": 1000,
                }
                for i in range(1, products + 1)
            ],
        )
        connection.execute(
            insert(ProductImage),
            [
                {
                    "id": (i - 1) * 2 + j,
                    "product_id": i,
                    "url": f"https://images.example.test/products/{i}/{j}.jpg",
                }
                for i in range(1, products + 1)
                for j in (1, 2)
            ],
        )
        connection.execute(
            insert(Customer),
            [
                {"id": i, "name": f"Demo customer {i:04}", "email": f"customer{i}@example.test"}
                for i in range(1, customers + 1)
            ],
        )
        epoch = datetime(2025, 1, 1, tzinfo=UTC)
        for start in range(1, orders + 1, 1000):
            ids = range(start, min(start + 1000, orders + 1))
            connection.execute(
                insert(Order),
                [
                    {
                        "id": i,
                        "customer_id": (i - 1) % customers + 1,
                        "subtotal_cents": product_price((i - 1) % products + 1),
                        "shipping_cents": 500,
                        "total_cents": product_price((i - 1) % products + 1) + 500,
                        "status": "placed",
                        "created_at": epoch + timedelta(seconds=i),
                    }
                    for i in ids
                ],
            )
            connection.execute(
                insert(OrderItem),
                [
                    {
                        "id": i,
                        "order_id": i,
                        "product_id": (i - 1) % products + 1,
                        "quantity": 1,
                        "unit_price_cents": product_price((i - 1) % products + 1),
                    }
                    for i in ids
                ],
            )
        # Explicit seed IDs must not collide with the next normal POST request.
        for table in ("products", "product_images", "customers", "orders", "order_items"):
            connection.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                    f"EXISTS (SELECT 1 FROM {table}))"
                ),
                {"table": table},
            )
    return {"products": products, "customers": customers, "orders": orders}
