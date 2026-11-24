from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200, examples=["Espresso beans 1kg"])
    price_cents: int = Field(ge=0, le=100_000_000, examples=[2490])
    stock: int = Field(ge=0, le=1_000_000, examples=[25])


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_cents: int
    stock: int


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0, examples=[1])
    quantity: int = Field(ge=1, le=100, examples=[2])


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    unit_price_cents: int
    total_cents: int
    status: Literal["reserved", "cancelled"]
    created_at: datetime
    cancelled_at: datetime | None

    @field_validator("created_at", "cancelled_at")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite returns naive datetimes; Postgres returns aware ones. Always emit UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
