"""Request-scoped measurements and explicit OpenTelemetry instrumentation.

Each app owns its provider and listeners, without global monkey patches. Multiple
apps can share an engine in tests without counting one another's SQL twice.
"""

import json
import os
import re
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import Engine, event
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from celerity import __version__
from celerity.config import Settings


class JsonlSpanExporter(SpanExporter):
    """One JSON object per completed span, including parent IDs and durations."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8")
        self._lock = Lock()
        self.count = 0
        self.failed_batches = 0
        self._closed = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = []
        for span in spans:
            rows.append(
                json.dumps(
                    {
                        "trace_id": f"{span.context.trace_id:032x}",
                        "span_id": f"{span.context.span_id:016x}",
                        "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                        "name": span.name,
                        "kind": span.kind.name,
                        "service": span.resource.attributes.get("service.name"),
                        "start_ns": span.start_time,
                        "end_ns": span.end_time,
                        "duration_ms": (span.end_time - span.start_time) / 1_000_000,
                        "status": span.status.status_code.name,
                        "attributes": dict(span.attributes or {}),
                    }
                )
            )
        with self._lock:
            if self._closed:
                return SpanExportResult.FAILURE
            try:
                self._file.write("\n".join(rows) + ("\n" if rows else ""))
                self._file.flush()
                self.count += len(rows)
            except OSError:
                self.failed_batches += 1
                return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.close()
                self._closed = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        with self._lock:
            if not self._closed:
                self._file.flush()
        return True


@dataclass
class RequestMeasurement:
    runtime: "TelemetryRuntime"
    run_id: str
    sql_count: int = 0
    sql_ms: float = 0.0
    shipping_count: int = 0
    shipping_ms: float = 0.0


current_measurement: ContextVar[RequestMeasurement | None] = ContextVar(
    "celerity_request_measurement", default=None
)
propagator = TraceContextTextMapPropagator()


class TelemetryRuntime:
    def __init__(self, settings: Settings, service: str, exporter: SpanExporter | None = None):
        self.identity = uuid.uuid4().hex
        self.service = service
        self.ratio = settings.telemetry_sample_ratio
        self.provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": service,
                    "service.version": __version__,
                    "service.instance.id": f"{os.getpid()}-{self.identity[:8]}",
                    "deployment.environment.name": "local-lab",
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(self.ratio)),
        )
        self.json_exporter = None
        if exporter is not None:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            path = settings.telemetry_dir / f"{service}-{os.getpid()}-{self.identity[:8]}.jsonl"
            self.json_exporter = JsonlSpanExporter(path)
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    self.json_exporter,
                    schedule_delay_millis=500,
                    max_queue_size=8192,
                    max_export_batch_size=256,
                )
            )
        if settings.otlp_endpoint:
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=settings.otlp_endpoint, timeout=3),
                    schedule_delay_millis=500,
                )
            )
        self.tracer = self.provider.get_tracer("celerity.observation", __version__)

    def describe(self) -> dict:
        return {
            "enabled": True,
            "service": self.service,
            "sample_ratio": self.ratio,
            "span_file": str(self.json_exporter.path) if self.json_exporter else None,
            "exported_spans": self.json_exporter.count if self.json_exporter else None,
            "failed_export_batches": self.json_exporter.failed_batches
            if self.json_exporter
            else None,
        }

    def shutdown(self) -> None:
        self.provider.force_flush(timeout_millis=5000)
        self.provider.shutdown()

    def observe_engine(self, engine: Engine):
        def before(_connection, _cursor, statement, _parameters, context, _many):
            measurement = current_measurement.get()
            if measurement is None or measurement.runtime is not self:
                return
            operation = statement.split(None, 1)[0].upper() if statement.strip() else "UNKNOWN"
            span = self.tracer.start_span(
                f"SQL {operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "db.system.name": "postgresql",
                    "db.operation.name": operation,
                    # Bound SQL contains placeholders, not the bound parameter values.
                    "db.query.text": statement,
                    "celerity.run_id": measurement.run_id,
                },
            )
            context._celerity_measurement = (measurement, time.perf_counter(), span)

        def finish(context, error=None):
            entry = getattr(context, "_celerity_measurement", None)
            if entry is None:
                return
            if entry[0].runtime is not self:
                return
            del context._celerity_measurement
            measurement, started, span = entry
            measurement.sql_count += 1
            measurement.sql_ms += (time.perf_counter() - started) * 1000
            if error is not None:
                span.set_status(Status(StatusCode.ERROR, "Database operation failed"))
                span.set_attribute("error.type", type(error).__name__)
            span.end()

        def after(_connection, _cursor, _statement, _parameters, context, _many):
            finish(context)

        def on_error(exception_context):
            if exception_context.execution_context is not None:
                finish(exception_context.execution_context, exception_context.original_exception)

        listeners = [
            ("before_cursor_execute", before),
            ("after_cursor_execute", after),
            ("handle_error", on_error),
        ]
        for name, callback in listeners:
            event.listen(engine, name, callback)

        def detach():
            for name, callback in listeners:
                event.remove(engine, name, callback)

        return detach


class ObservationMiddleware:
    """Pure ASGI middleware; its context follows FastAPI's sync worker threads."""

    def __init__(self, app: ASGIApp, runtime: TelemetryRuntime):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path.startswith("/api/") or path == "/quote"):
            await self.app(scope, receive, send)
            return
        carrier = {
            key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]
        }
        run_id = carrier.get("x-celerity-run-id", "manual")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            run_id = "manual"
        measurement = RequestMeasurement(self.runtime, run_id)
        token = current_measurement.set(measurement)
        status_code = 500
        method = scope["method"]
        try:
            with self.runtime.tracer.start_as_current_span(
                f"{method} request",
                kind=SpanKind.SERVER,
                context=propagator.extract(carrier),
                record_exception=False,
                set_status_on_exception=False,
                attributes={"http.request.method": method, "celerity.run_id": run_id},
            ) as span:

                async def measured_send(message: Message):
                    nonlocal status_code
                    if message["type"] == "http.response.start":
                        status_code = message["status"]
                        context = span.get_span_context()
                        headers = list(message.get("headers", []))
                        values = {
                            "x-celerity-trace-id": f"{context.trace_id:032x}",
                            "x-celerity-sql-count": str(measurement.sql_count),
                            "x-celerity-sql-ms": f"{measurement.sql_ms:.3f}",
                            "x-celerity-shipping-calls": str(measurement.shipping_count),
                            "x-celerity-shipping-ms": f"{measurement.shipping_ms:.3f}",
                        }
                        names = {name.encode("ascii") for name in values}
                        headers = [
                            (key, value) for key, value in headers if key.lower() not in names
                        ]
                        headers.extend(
                            (key.encode("ascii"), value.encode("ascii"))
                            for key, value in values.items()
                        )
                        message = {**message, "headers": headers}
                    await send(message)

                try:
                    await self.app(scope, receive, measured_send)
                except Exception as exc:
                    span.set_attribute("error.type", type(exc).__name__)
                    raise
                finally:
                    route = getattr(scope.get("route"), "path", "unmatched")
                    span.update_name(f"{method} {route}")
                    span.set_attributes(
                        {
                            "http.route": route,
                            "http.response.status_code": status_code,
                            "celerity.sql.count": measurement.sql_count,
                            "celerity.sql.duration_ms": measurement.sql_ms,
                            "celerity.shipping.calls": measurement.shipping_count,
                            "celerity.shipping.duration_ms": measurement.shipping_ms,
                        }
                    )
                    if status_code >= 500:
                        span.set_status(Status(StatusCode.ERROR))
        finally:
            current_measurement.reset(token)


@contextmanager
def shipping_request(zone: str) -> Iterator[dict[str, str]]:
    measurement = current_measurement.get()
    if measurement is None:
        yield {}
        return
    started = time.perf_counter()
    measurement.shipping_count += 1
    try:
        with measurement.runtime.tracer.start_as_current_span(
            "GET shipping /quote",
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
            attributes={
                "http.request.method": "GET",
                "url.path": "/quote",
                "peer.service": "celerity-shipping",
                "celerity.shipping.zone": zone,
                "celerity.run_id": measurement.run_id,
            },
        ) as span:
            headers = {"x-celerity-run-id": measurement.run_id}
            propagator.inject(headers)
            try:
                yield headers
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, "Shipping request failed"))
                span.set_attribute("error.type", type(exc).__name__)
                raise
    finally:
        measurement.shipping_ms += (time.perf_counter() - started) * 1000
