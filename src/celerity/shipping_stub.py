"""Deterministic local downstream service; no payment provider or internet API."""

import time

from fastapi import FastAPI

from celerity.config import Settings
from celerity.schemas import QuoteOut, Zone

app = FastAPI(title="Celerity Shipping Stub", version="0.1.0")
settings = Settings()
PRICES = {"local": 250, "national": 500, "international": 1500}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "celerity-shipping"}


@app.get("/quote", response_model=QuoteOut)
def quote(zone: Zone = "national") -> QuoteOut:
    # Only the downstream test service introduces controlled latency.
    time.sleep(settings.shipping_delay_ms / 1000)
    return QuoteOut(zone=zone, shipping_cents=PRICES[zone])
