import httpx
from sqlalchemy import event

from celerity.database import configure_order_index, order_index_present


def count_selects(engine, action):
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = action()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    return response.json(), statements


def test_n_plus_one_changes_work_not_results(client_factory, engine):
    healthy, _ = client_factory()
    faulty, _ = client_factory(n_plus_one=True)
    expected, efficient = count_selects(engine, lambda: healthy.get("/api/products?limit=20"))
    actual, redundant = count_selects(engine, lambda: faulty.get("/api/products?limit=20"))
    assert actual == expected
    assert len(efficient) == 2
    assert len(redundant) == 21


def test_index_can_be_removed_and_restored_without_changing_results(client_factory, engine):
    client, _ = client_factory()
    expected = client.get("/api/customers/1/orders").json()
    assert order_index_present(engine)
    configure_order_index(engine, missing=True)
    assert not order_index_present(engine)
    assert client.get("/api/customers/1/orders").json() == expected
    configure_order_index(engine, missing=False)
    assert order_index_present(engine)


def test_redundant_shipping_makes_three_real_tool_calls_for_same_quote(client_factory):
    healthy, normal_calls = client_factory()
    faulty, repeated_calls = client_factory(redundant_shipping=True)
    expected = healthy.get("/api/checkout/quote?zone=national")
    actual = faulty.get("/api/checkout/quote?zone=national")
    assert actual.json() == expected.json()
    assert len(normal_calls) == 1
    assert len(repeated_calls) == 3
    assert actual.headers["X-Celerity-Shipping-Calls"] == "3"


def test_fault_switches_do_not_enable_each_other(client_factory):
    client, _ = client_factory(n_plus_one=True)
    flags = client.get("/lab/scenario").json()
    assert flags["faults"] == {
        "n_plus_one": True,
        "missing_index": False,
        "redundant_shipping": False,
    }
    assert flags["orders_lookup_index_present"] is True
    assert flags["shipping_calls_per_quote"] == 1


def test_malformed_or_wrong_zone_shipping_response_is_rejected(client_factory):
    for payload in (
        {"zone": "local", "shipping_cents": 200},
        {"zone": "national", "shipping_cents": -1},
        {"wrong": "payload"},
    ):
        client, _ = client_factory(
            handler=lambda request, body=payload: httpx.Response(200, json=body)
        )
        assert client.get("/api/checkout/quote?zone=national").status_code == 503


def test_invalid_zone_never_calls_shipping(client_factory):
    client, calls = client_factory()
    assert client.get("/api/checkout/quote?zone=unknown").status_code == 422
    assert calls == []
