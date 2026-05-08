from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl

ListingStatus = Literal["ok", "out_of_stock", "unavailable", "error"]


class PerCurrency(BaseModel):
    per_gram_eur: float
    per_gram_dkk: float


class SpotPrice(BaseModel):
    gold: PerCurrency
    silver: PerCurrency


class Listing(BaseModel):
    dealer: str
    status: ListingStatus
    price_dkk: float | None = None
    premium_pct: float | None = None
    in_stock: bool | None = None
    brand: str | None = None
    url: HttpUrl | None = None
    error: str | None = None
    fetched_at: datetime


class PriceResponse(BaseModel):
    size_g: float
    fetched_at: datetime
    spot: SpotPrice | None
    fx_stale: bool
    listings: list[Listing]


class CoinListing(BaseModel):
    dealer: str
    status: ListingStatus
    coin_type: str | None = None
    size_label: str | None = None
    gross_weight_g: float | None = None
    purity: float | None = None
    fine_gold_g: float | None = None
    price_dkk: float | None = None
    premium_pct: float | None = None
    url: HttpUrl | None = None
    error: str | None = None
    fetched_at: datetime
