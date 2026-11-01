"""Phase 1 shopping API. Run init-db before starting the app."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from celerity.config import Settings
from celerity.database import make_engine, make_sessions, order_index_present
from celerity.models import Customer, Order, OrderItem, Product
from celerity.schemas import (
    CustomerIn,
    CustomerOut,
    OrderIn,
    OrderOut,
    ProductOut,
    QuoteOut,
    Zone,
)
from celerity.shipping import ShippingGateway, ShippingUnavailable


def customer_orders_query(customer_id: int, limit: int = 20):
    return (
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
    )


def product_response(product: Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        name=product.name,
        price_cents=product.price_cents,
        stock=product.stock,
        images=[image.url for image in product.images],
    )


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    shipping_client: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or Settings()
    owns_engine = engine is None
    engine = engine if engine is not None else make_engine(settings.database_url)
    sessions = make_sessions(engine)
    owns_client = shipping_client is None
    client = (
        shipping_client
        if shipping_client is not None
        else httpx.Client(base_url=settings.shipping_url, timeout=3.0, trust_env=False)
    )
    shipping = ShippingGateway(client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owns_client:
                client.close()
            if owns_engine:
                engine.dispose()

    application = FastAPI(
        title="Celerity Shopping Lab",
        description=(
            "Phase 1 target application for Celerity's agentic performance engineer. "
            "Synthetic local data; intentionally switchable performance faults."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.engine = engine
    application.state.settings = settings

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    Db = Annotated[Session, Depends(get_session)]
    PageLimit = Annotated[int, Query(ge=1, le=100)]
    Offset = Annotated[int, Query(ge=0)]

    @application.get("/", tags=["Lab"])
    def root() -> dict[str, str | int]:
        return {"project": "Celerity", "phase": 1, "docs": "/docs", "scenario": "/lab/scenario"}

    @application.get("/health", tags=["Lab"])
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Database is unavailable.") from exc
        return {"status": "ok", "database": "postgresql"}

    @application.get("/lab/scenario", tags=["Lab"])
    def scenario() -> dict:
        return {
            "faults": settings.faults(),
            "orders_lookup_index_present": order_index_present(engine),
            "shipping_calls_per_quote": settings.shipping_calls,
            "note": "Local teaching lab. Change a scenario using the start script and restart.",
        }

    @application.get("/api/products", response_model=list[ProductOut], tags=["Products"])
    def list_products(db: Db, limit: PageLimit = 20, offset: Offset = 0):
        query = select(Product).order_by(Product.id).limit(limit).offset(offset)
        if not settings.enable_n_plus_one:
            query = query.options(selectinload(Product.images))
        return [product_response(product) for product in db.scalars(query)]

    @application.get("/api/products/{product_id}", response_model=ProductOut, tags=["Products"])
    def get_product(product_id: int, db: Db):
        product = db.scalar(
            select(Product).where(Product.id == product_id).options(selectinload(Product.images))
        )
        if product is None:
            raise HTTPException(404, "Product not found.")
        return product_response(product)

    @application.get("/api/customers", response_model=list[CustomerOut], tags=["Customers"])
    def list_customers(db: Db, limit: PageLimit = 20, offset: Offset = 0):
        return list(db.scalars(select(Customer).order_by(Customer.id).limit(limit).offset(offset)))

    @application.post(
        "/api/customers", response_model=CustomerOut, status_code=201, tags=["Customers"]
    )
    def create_customer(payload: CustomerIn, db: Db):
        customer = Customer(**payload.model_dump())
        db.add(customer)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "A customer with this email already exists.") from exc
        return customer

    @application.get(
        "/api/customers/{customer_id}/orders", response_model=list[OrderOut], tags=["Orders"]
    )
    def list_customer_orders(customer_id: int, db: Db, limit: PageLimit = 20):
        if db.get(Customer, customer_id) is None:
            raise HTTPException(404, "Customer not found.")
        return list(
            db.scalars(customer_orders_query(customer_id, limit).options(selectinload(Order.items)))
        )

    @application.get("/api/orders/{order_id}", response_model=OrderOut, tags=["Orders"])
    def get_order(order_id: int, db: Db):
        order = db.scalar(
            select(Order).where(Order.id == order_id).options(selectinload(Order.items))
        )
        if order is None:
            raise HTTPException(404, "Order not found.")
        return order

    @application.get("/api/checkout/quote", response_model=QuoteOut, tags=["Checkout"])
    def checkout_quote(response: Response, zone: Zone = "national"):
        try:
            quote = shipping.quote(zone, calls=settings.shipping_calls)
        except ShippingUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        response.headers["X-Celerity-Shipping-Calls"] = str(settings.shipping_calls)
        return quote

    @application.post("/api/orders", response_model=OrderOut, status_code=201, tags=["Orders"])
    def create_order(payload: OrderIn, response: Response, db: Db):
        # Validate cheap local inputs before making any downstream request.
        if db.get(Customer, payload.customer_id) is None:
            raise HTTPException(404, "Customer not found.")
        requested = {item.product_id: item.quantity for item in payload.items}
        available = dict(
            db.execute(select(Product.id, Product.stock).where(Product.id.in_(requested))).all()
        )
        if len(available) != len(requested):
            raise HTTPException(404, "One or more products were not found.")
        if any(available[product_id] < quantity for product_id, quantity in requested.items()):
            raise HTTPException(409, "Insufficient stock; no items were reserved.")
        # Fetch the quote before locking stock rows, so slow HTTP does not hold row locks.
        try:
            quote = shipping.quote(payload.shipping_zone, calls=settings.shipping_calls)
        except ShippingUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        # Sorted row locks prevent overselling and reduce deadlock risk for overlapping carts.
        products = list(
            db.scalars(
                select(Product)
                .where(Product.id.in_(requested))
                .order_by(Product.id)
                .with_for_update()
            )
        )
        if len(products) != len(requested):
            raise HTTPException(404, "One or more products were not found.")
        # Another buyer may have purchased while we were waiting for shipping.
        if any(product.stock < requested[product.id] for product in products):
            raise HTTPException(409, "Insufficient stock; no items were reserved.")
        subtotal = sum(product.price_cents * requested[product.id] for product in products)
        order = Order(
            customer_id=payload.customer_id,
            subtotal_cents=subtotal,
            shipping_cents=quote.shipping_cents,
            total_cents=subtotal + quote.shipping_cents,
            items=[
                OrderItem(
                    product_id=product.id,
                    quantity=requested[product.id],
                    unit_price_cents=product.price_cents,
                )
                for product in products
            ],
        )
        for product in products:
            product.stock -= requested[product.id]
        db.add(order)
        db.commit()
        db.refresh(order)
        # Explicitly load items for serialization; unrelated endpoints cannot lazy-load them.
        db.refresh(order, attribute_names=["items"])
        response.headers["X-Celerity-Shipping-Calls"] = str(settings.shipping_calls)
        return order

    return application
