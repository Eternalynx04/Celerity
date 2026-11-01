"""Deterministic local service, with the same trace context as the caller."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from celerity.config import Settings
from celerity.schemas import QuoteOut, Zone
from celerity.telemetry import ObservationMiddleware, TelemetryRuntime

PRICES = {"local": 250, "national": 500, "international": 1500}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    runtime = (
        TelemetryRuntime(settings, "celerity-shipping") if settings.telemetry_enabled else None
    )

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            if runtime is not None:
                runtime.shutdown()

    app = FastAPI(title="Celerity Shipping Stub", version="0.2.0", lifespan=lifespan)
    if runtime is not None:
        app.add_middleware(ObservationMiddleware, runtime=runtime)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "celerity-shipping"}

    @app.get("/quote", response_model=QuoteOut)
    def quote(zone: Zone = "national") -> QuoteOut:
        time.sleep(settings.shipping_delay_ms / 1000)
        return QuoteOut(zone=zone, shipping_cents=PRICES[zone])

    return app
