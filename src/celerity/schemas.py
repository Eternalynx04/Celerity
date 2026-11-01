"""Request/response contracts; prices are integer cents, in USD for this demo."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PositiveId = Annotated[int, Field(gt=0)]
Zone = Literal["local", "national", "international"]


class ProductOut(BaseModel):
    id: int
    name: str
    price_cents: int
    stock: int
    currency: Literal["USD"] = "USD"
    images: list[str]


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @field_validator("name", "email", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


class ItemIn(BaseModel):
    product_id: PositiveId
    quantity: int = Field(gt=0, le=100)


class OrderIn(BaseModel):
    customer_id: PositiveId
    items: list[ItemIn] = Field(min_length=1, max_length=20)
    shipping_zone: Zone = "national"

    @field_validator("items")
    @classmethod
    def unique_products(cls, items: list[ItemIn]) -> list[ItemIn]:
        ids = [item.product_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product must appear once; combine its quantities.")
        return items


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: int
    quantity: int
    unit_price_cents: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    status: str
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    currency: Literal["USD"] = "USD"
    created_at: datetime
    items: list[ItemOut]


class QuoteOut(BaseModel):
    zone: Zone
    shipping_cents: int = Field(ge=0)
    currency: Literal["USD"] = "USD"
