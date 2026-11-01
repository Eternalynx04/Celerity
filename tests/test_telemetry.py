import json

import httpx
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text

from celerity.config import Settings
from celerity.main import create_app
from celerity.telemetry import TelemetryRuntime


def instrumented_app(engine, tmp_path, *, n_plus_one=False, shipping_handler=None):
    settings = Settings(
        _env_file=None,
        telemetry_dir=tmp_path,
        telemetry_enabled=True,
        telemetry_sample_ratio=1.0,
        otlp_endpoint="",
        enable_n_plus_one=n_plus_one,
    )
    exporter = InMemorySpanExporter()
    runtime = TelemetryRuntime(settings, "celerity-test", exporter)
    requests = []

    def handler(request):
        requests.append(request)
        if shipping_handler:
            return shipping_handler(request)
        return httpx.Response(200, json={"zone": "national", "shipping_cents": 500})

    outbound = httpx.Client(base_url="http://shipping.test", transport=httpx.MockTransport(handler))
    app = create_app(settings, engine=engine, shipping_client=outbound, telemetry=runtime)
    return app, exporter, runtime, outbound, requests


def test_sql_spans_and_headers_match_request(engine, tmp_path):
    app, exporter, runtime, outbound, _ = instrumented_app(engine, tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/products?limit=20", headers={"X-Celerity-Run-Id": "test-run"}
            )
            assert response.status_code == 200
            assert response.headers["X-Celerity-Sql-Count"] == "2"
            trace_id = response.headers["X-Celerity-Trace-Id"]
        spans = exporter.get_finished_spans()
        server = next(span for span in spans if span.kind.name == "SERVER")
        sql = [span for span in spans if span.name.startswith("SQL ")]
        assert server.attributes["celerity.sql.count"] == 2
        assert server.name == "GET /api/products"
        assert f"{server.context.trace_id:032x}" == trace_id
        assert len(sql) == 2
        assert all(span.parent.span_id == server.context.span_id for span in sql)
        assert all(span.attributes["celerity.run_id"] == "test-run" for span in spans)
    finally:
        runtime.shutdown()
        outbound.close()


def test_outbound_span_propagates_its_context(engine, tmp_path):
    app, exporter, runtime, outbound, requests = instrumented_app(engine, tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get("/api/checkout/quote")
            assert response.headers["X-Celerity-Shipping-Calls"] == "1"
        spans = exporter.get_finished_spans()
        server = next(span for span in spans if span.kind.name == "SERVER")
        call = next(span for span in spans if span.kind.name == "CLIENT")
        assert call.parent.span_id == server.context.span_id
        assert requests[0].headers["traceparent"].split("-")[2] == f"{call.context.span_id:016x}"
        assert f"{call.context.trace_id:032x}" == response.headers["X-Celerity-Trace-Id"]
    finally:
        runtime.shutdown()
        outbound.close()


def test_multiple_app_instances_do_not_double_count_sql(engine, tmp_path):
    first = instrumented_app(engine, tmp_path / "first")
    second = instrumented_app(engine, tmp_path / "second", n_plus_one=True)
    try:
        with TestClient(first[0]) as a, TestClient(second[0]) as b:
            response_a = a.get("/api/products?limit=20")
            response_b = b.get("/api/products?limit=20")
            assert response_a.headers["X-Celerity-Sql-Count"] == "2"
            assert response_b.headers["X-Celerity-Sql-Count"] == "21"
            assert (
                response_a.headers["X-Celerity-Trace-Id"]
                != response_b.headers["X-Celerity-Trace-Id"]
            )
        assert len([s for s in first[1].get_finished_spans() if s.name.startswith("SQL ")]) == 2
        assert len([s for s in second[1].get_finished_spans() if s.name.startswith("SQL ")]) == 21
    finally:
        for item in (first, second):
            item[2].shutdown()
            item[3].close()


def test_bound_values_and_request_bodies_are_not_exported(engine, tmp_path):
    app, exporter, runtime, outbound, _ = instrumented_app(engine, tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/customers",
                json={
                    "name": "Private sample name",
                    "email": "private-sample@example.test",
                },
            )
            assert response.status_code == 201
        attributes = json.dumps([dict(span.attributes) for span in exporter.get_finished_spans()])
        assert "private-sample@example.test" not in attributes
        assert "Private sample name" not in attributes
    finally:
        runtime.shutdown()
        outbound.close()


def test_jsonl_exporter_writes_valid_correlated_spans(engine, tmp_path):
    settings = Settings(
        _env_file=None,
        telemetry_dir=tmp_path,
        otlp_endpoint="",
        telemetry_enabled=True,
        telemetry_sample_ratio=1.0,
        enable_n_plus_one=False,
    )
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        response = client.get("/api/products?limit=2")
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert all(row["trace_id"] == response.headers["X-Celerity-Trace-Id"] for row in rows)
    assert all(row["duration_ms"] >= 0 for row in rows)


def test_disabled_tracing_preserves_api(client_factory):
    client, _ = client_factory()
    assert client.get("/lab/telemetry").json() == {"enabled": False}
    response = client.get("/api/products?limit=2")
    assert response.status_code == 200
    assert "X-Celerity-Trace-Id" not in response.headers


def test_failed_shipping_is_traced_without_exporting_exception_message(engine, tmp_path):
    def fail(request):
        raise httpx.ReadTimeout("private diagnostic detail", request=request)

    app, exporter, runtime, outbound, _ = instrumented_app(engine, tmp_path, shipping_handler=fail)
    try:
        with TestClient(app) as client:
            response = client.get("/api/checkout/quote")
            assert response.status_code == 503
            assert response.headers["X-Celerity-Shipping-Calls"] == "1"
        spans = exporter.get_finished_spans()
        assert all(span.status.status_code.name == "ERROR" for span in spans)
        serialized = json.dumps(
            [
                {"attributes": dict(span.attributes), "description": span.status.description}
                for span in spans
            ]
        )
        assert "private diagnostic detail" not in serialized
        assert all(not span.events for span in spans)
    finally:
        runtime.shutdown()
        outbound.close()


def test_database_errors_finish_their_spans(engine, tmp_path):
    app, exporter, runtime, outbound, _ = instrumented_app(engine, tmp_path)

    @app.get("/api/test-database-error")
    def broken_query():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 / 0"))

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/api/test-database-error").status_code == 500
        spans = exporter.get_finished_spans()
        sql = next(span for span in spans if span.name.startswith("SQL "))
        assert sql.status.status_code.name == "ERROR"
        server = next(span for span in spans if span.kind.name == "SERVER")
        assert server.attributes["celerity.sql.count"] == 1
        assert server.status.status_code.name == "ERROR"
    finally:
        runtime.shutdown()
        outbound.close()
