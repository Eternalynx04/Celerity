"""A real HTTP boundary, so redundant work is observable without fake API sleeps."""

import httpx
from pydantic import ValidationError

from celerity.schemas import QuoteOut, Zone
from celerity.telemetry import shipping_request


class ShippingUnavailable(Exception):
    pass


class ShippingGateway:
    def __init__(self, client: httpx.Client):
        self.client = client

    def quote(self, zone: Zone, *, calls: int = 1) -> QuoteOut:
        try:
            result: QuoteOut | None = None
            for _ in range(calls):
                with shipping_request(zone) as headers:
                    response = self.client.get("/quote", params={"zone": zone}, headers=headers)
                    response.raise_for_status()
                    result = QuoteOut.model_validate(response.json())
                    if result.zone != zone:
                        raise ShippingUnavailable("Shipping service returned a mismatched zone.")
            if result is None:
                raise ShippingUnavailable("No shipping quote was requested.")
            return result
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise ShippingUnavailable("Shipping quote is temporarily unavailable.") from exc
