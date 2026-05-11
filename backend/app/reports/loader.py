"""Load snapshot rows into typed dataclasses for analytics.

The DB returns asyncpg Records with Decimal numerics. Analytics code wants
plain floats, so we normalize at load time. Pure functions (no DB calls) so
they're trivially testable on in-memory dicts.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class BarPoint:
    fetched_at: datetime
    dealer: str
    size_g: float
    status: str
    price_dkk: float | None
    spot_dkk_per_g: float | None


@dataclass(frozen=True)
class CoinPoint:
    fetched_at: datetime
    dealer: str
    coin_type: str | None
    size_label: str | None
    gross_weight_g: float | None
    purity: float | None
    fine_gold_g: float | None
    status: str
    price_dkk: float | None
    spot_dkk_per_g: float | None


@dataclass(frozen=True)
class SpotPoint:
    fetched_at: datetime
    gold_dkk_per_g: float | None
    silver_dkk_per_g: float | None


def _float(v: object) -> float | None:
    if v is None:
        return None
    return float(v)  # type: ignore[arg-type]


def rows_to_bars(rows: Iterable[Mapping[str, object]]) -> list[BarPoint]:
    return [
        BarPoint(
            fetched_at=row["fetched_at"],  # type: ignore[arg-type]
            dealer=row["dealer"],          # type: ignore[arg-type]
            size_g=float(row["size_g"]),   # type: ignore[arg-type]
            status=row["status"],          # type: ignore[arg-type]
            price_dkk=_float(row["price_dkk"]),
            spot_dkk_per_g=_float(row["spot_gold_dkk_per_g"]),
        )
        for row in rows
    ]


def rows_to_coins(rows: Iterable[Mapping[str, object]]) -> list[CoinPoint]:
    return [
        CoinPoint(
            fetched_at=row["fetched_at"],  # type: ignore[arg-type]
            dealer=row["dealer"],          # type: ignore[arg-type]
            coin_type=row.get("coin_type"),  # type: ignore[arg-type]
            size_label=row.get("size_label"),  # type: ignore[arg-type]
            gross_weight_g=_float(row.get("gross_weight_g")),
            purity=_float(row.get("purity")),
            fine_gold_g=_float(row.get("fine_gold_g")),
            status=row["status"],          # type: ignore[arg-type]
            price_dkk=_float(row.get("price_dkk")),
            spot_dkk_per_g=_float(row.get("spot_gold_dkk_per_g")),
        )
        for row in rows
    ]


def rows_to_spot(rows: Iterable[Mapping[str, object]]) -> list[SpotPoint]:
    return [
        SpotPoint(
            fetched_at=row["fetched_at"],  # type: ignore[arg-type]
            gold_dkk_per_g=_float(row.get("gold_dkk_per_g")),
            silver_dkk_per_g=_float(row.get("silver_dkk_per_g")),
        )
        for row in rows
    ]


async def load_bars(
    conn: asyncpg.Connection, start_dt: datetime, end_dt: datetime,
) -> list[BarPoint]:
    rows = await conn.fetch(
        """
        SELECT fetched_at, dealer, size_g, status, price_dkk, spot_gold_dkk_per_g
        FROM bar_snapshots
        WHERE fetched_at >= $1 AND fetched_at < $2
        ORDER BY dealer, size_g, fetched_at ASC
        """,
        start_dt, end_dt,
    )
    return rows_to_bars(rows)


async def load_coins(
    conn: asyncpg.Connection, start_dt: datetime, end_dt: datetime,
) -> list[CoinPoint]:
    rows = await conn.fetch(
        """
        SELECT fetched_at, dealer, coin_type, size_label,
               gross_weight_g, purity, fine_gold_g,
               status, price_dkk, spot_gold_dkk_per_g
        FROM coin_snapshots
        WHERE fetched_at >= $1 AND fetched_at < $2
        ORDER BY dealer, coin_type, size_label, fetched_at ASC
        """,
        start_dt, end_dt,
    )
    return rows_to_coins(rows)


async def load_spot(
    conn: asyncpg.Connection, start_dt: datetime, end_dt: datetime,
) -> list[SpotPoint]:
    rows = await conn.fetch(
        """
        SELECT fetched_at, gold_dkk_per_g, silver_dkk_per_g
        FROM spot_snapshots
        WHERE fetched_at >= $1 AND fetched_at < $2
        ORDER BY fetched_at ASC
        """,
        start_dt, end_dt,
    )
    return rows_to_spot(rows)
