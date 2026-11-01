"""Every database test gets an isolated PostgreSQL schema, never a database reset."""

import os
import re
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from celerity.config import Settings
from celerity.database import Base, configure_order_index
from celerity.main import create_app
from celerity.seed import seed_demo


@pytest.fixture
def engine() -> Iterator[Engine]:
    url = os.environ.get("CELERITY_TEST_DATABASE_URL", Settings().database_url)
    admin = create_engine(url, pool_pre_ping=True)
    schema = f"test_celerity_{uuid.uuid4().hex[:12]}"
    assert re.fullmatch(r"test_celerity_[0-9a-f]{12}", schema)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(isolated)
        seed_demo(isolated, products=30, customers=10, orders=100)
        configure_order_index(isolated, missing=False)
        yield isolated
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def client_factory(engine):
    clients = []

    def build(*, n_plus_one=False, redundant_shipping=False, handler=None):
        calls = []

        def shipping(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if handler is not None:
                return handler(request)
            zone = request.url.params["zone"]
            prices = {"local": 250, "national": 500, "international": 1500}
            return httpx.Response(200, json={"zone": zone, "shipping_cents": prices[zone]})

        outbound = httpx.Client(
            base_url="http://shipping.test", transport=httpx.MockTransport(shipping)
        )
        settings = Settings(
            _env_file=None,
            enable_n_plus_one=n_plus_one,
            enable_missing_index=False,
            enable_redundant_shipping=redundant_shipping,
        )
        app = create_app(settings, engine=engine, shipping_client=outbound)
        client = TestClient(app)
        client.__enter__()
        clients.append((client, outbound))
        return client, calls

    yield build
    for client, outbound in reversed(clients):
        client.__exit__(None, None, None)
        outbound.close()
