"""Seed local Postgres with 30 days of fake snapshots.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.seed

Refuses to run against any host that isn't localhost — guard against
accidentally truncating Neon production data.

The data shape mirrors what the real cron writes: one spot row + 28
dealer rows (7 dealers × 4 sizes) per tick, every 30 minutes for 30
days. Spot follows a bounded random walk; each dealer/size has a fixed
typical premium with small noise on top, plus a small chance of an
'error' status row to exercise that UI path.
"""
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import asyncpg

from app.db import SCHEMA_SQL

# Typical premiums per dealer × size. Reverse-engineered from a few live
# /prices snapshots — small bars carry higher per-gram premium than large
# bars. Brand is held stable per (dealer, size); real brand-drift only
# matters once real data lands.
DEALER_PROFILES: dict[str, dict[float, tuple[str | None, float]]] = {
    "Tavex": {
        2.5: ("PAMP", 0.105), 5.0: ("PAMP", 0.090),
        10.0: ("Argor", 0.075), 20.0: ("Argor", 0.060),
    },
    "Vitus Guld": {
        2.5: ("Valcambi", 0.100), 5.0: ("Valcambi", 0.088),
        10.0: ("PAMP", 0.083), 20.0: ("PAMP", 0.060),
    },
    "Plaza": {
        2.5: ("Mixed", 0.115), 5.0: ("Mixed", 0.095),
        10.0: ("Mixed", 0.080), 20.0: ("Mixed", 0.065),
    },
    "Nordisk Guld": {
        2.5: ("Heimerle", 0.110), 5.0: ("Heimerle", 0.092),
        10.0: ("Heimerle", 0.078), 20.0: ("Heimerle", 0.065),
    },
    "Sero Guld": {
        2.5: ("Argor", 0.108), 5.0: ("Argor", 0.091),
        10.0: ("Argor", 0.077), 20.0: ("Argor", 0.062),
    },
    "Nyfortuna": {
        2.5: ("PAMP", 0.112), 5.0: ("PAMP", 0.094),
        10.0: ("PAMP", 0.080), 20.0: ("PAMP", 0.066),
    },
    "Jan Jørgensen Smykker": {
        2.5: ("Mixed", 0.130), 5.0: ("Mixed", 0.110),
        10.0: ("Mixed", 0.095), 20.0: ("Mixed", 0.080),
    },
}

# Coin offerings per dealer. Each entry: (coin_type, size_label, gross_g,
# purity, fine_g, base_premium). Plaza and Jan Jørgensen are absent — they
# don't carry bullion coins in real life. Premiums are slightly higher than
# bars (typical real-world coin markup) and small fractional sizes carry a
# higher per-gram premium than half-oz.
COIN_PROFILES: dict[str, list[tuple[str, str, float, float, float, float]]] = {
    "Tavex": [
        ("Krugerrand", "1/2 oz", 16.96, 0.9167, 15.5472, 0.110),
        ("Krugerrand", "1/4 oz", 8.48, 0.9167, 7.7736, 0.135),
        ("Krugerrand", "1/10 oz", 3.39, 0.9167, 3.1076, 0.180),
        ("Maple Leaf", "1/2 oz", 15.55, 0.9999, 15.5484, 0.115),
        ("Maple Leaf", "1/4 oz", 7.78, 0.9999, 7.7792, 0.140),
        ("Britannia", "1/2 oz", 15.55, 0.9999, 15.5484, 0.118),
        ("Britannia", "1/4 oz", 7.78, 0.9999, 7.7792, 0.142),
        ("Vienna Philharmonic", "1/2 oz", 15.55, 0.9999, 15.5484, 0.117),
        ("Ducat", "1 ducat", 3.49, 0.9860, 3.4411, 0.165),
    ],
    "Vitus Guld": [
        ("Krugerrand", "1/2 oz", 16.96, 0.9167, 15.5472, 0.108),
        ("Krugerrand", "1/4 oz", 8.48, 0.9167, 7.7736, 0.132),
        ("Krugerrand", "1/10 oz", 3.39, 0.9167, 3.1076, 0.175),
        ("Maple Leaf", "1/2 oz", 15.55, 0.9999, 15.5484, 0.112),
        ("Maple Leaf", "1/4 oz", 7.78, 0.9999, 7.7792, 0.138),
        ("Maple Leaf", "1/10 oz", 3.11, 0.9999, 3.1097, 0.185),
        ("Vienna Philharmonic", "1/2 oz", 15.55, 0.9999, 15.5484, 0.114),
        ("Britannia", "1/4 oz", 7.78, 0.9999, 7.7792, 0.140),
        ("Ducat", "1 ducat", 3.49, 0.9860, 3.4411, 0.160),
        ("American Eagle", "1/4 oz", 8.48, 0.9167, 7.7736, 0.145),
    ],
    "Nordisk Guld": [
        ("Britannia", "1/2 oz", 15.55, 0.9999, 15.5484, 0.122),
        ("Britannia", "1/4 oz", 7.78, 0.9999, 7.7792, 0.148),
        ("Maple Leaf", "1/2 oz", 15.55, 0.9999, 15.5484, 0.120),
        ("Maple Leaf", "1/4 oz", 7.78, 0.9999, 7.7792, 0.145),
        ("Maple Leaf", "1/10 oz", 3.11, 0.9999, 3.1097, 0.190),
        ("Ducat", "1 ducat", 3.49, 0.9860, 3.4411, 0.170),
    ],
    "Sero Guld": [
        ("Krugerrand", "1/2 oz", 16.96, 0.9167, 15.5472, 0.112),
        ("Krugerrand", "1/4 oz", 8.48, 0.9167, 7.7736, 0.138),
        ("Krugerrand", "1/10 oz", 3.39, 0.9167, 3.1076, 0.182),
        ("Vienna Philharmonic", "1/2 oz", 15.55, 0.9999, 15.5484, 0.116),
        ("Vienna Philharmonic", "1/4 oz", 7.78, 0.9999, 7.7792, 0.142),
        ("Britannia", "1/2 oz", 15.55, 0.9999, 15.5484, 0.119),
    ],
    "Nyfortuna": [
        ("Krugerrand", "1/2 oz", 16.96, 0.9167, 15.5472, 0.113),
        ("Krugerrand", "1/4 oz", 8.48, 0.9167, 7.7736, 0.139),
        ("Maple Leaf", "1/2 oz", 15.55, 0.9999, 15.5484, 0.116),
        ("American Eagle", "1/2 oz", 16.97, 0.9167, 15.5564, 0.120),
        ("Panda", "1/2 oz", 15.55, 0.9999, 15.5484, 0.130),
    ],
}

DAYS_BACK = 30
TICK_MINUTES = 30
# ~current real-world DKK-per-gram gold (live row in PWA shows 10,448 DKK at
# +8.3% premium for 10g → ~964 DKK/g spot). Seed mirrors that so historical
# chart values look continuous with what the live /prices row displays.
START_SPOT_DKK_PER_G = 950.0
SPOT_DRIFT_STDDEV = 0.6        # DKK per tick — gives realistic ±2-3% over 30d
SPOT_MIN, SPOT_MAX = 880.0, 1020.0
PREMIUM_NOISE_STDDEV = 0.004   # ±0.4 percentage points
ERROR_PROBABILITY = 0.015      # ~1.5% of dealer rows are errors

# Spot ratios — gold-EUR-per-g and silver-DKK/EUR-per-g are derived as fixed
# multiples of gold-DKK so they move coherently. Approximate to current FX
# (~0.134 EUR/DKK) and silver/gold ratio (~0.0133).
EUR_PER_DKK = 0.134
SILVER_DKK_RATIO = 0.0133


def _ensure_local(dsn: str) -> None:
    host = urlparse(dsn).hostname
    if host not in {"localhost", "127.0.0.1", "db"}:  # 'db' is the docker-compose service name
        print(f"refusing to seed non-local DB host: {host!r}", file=sys.stderr)
        sys.exit(1)


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set — point it at the local Postgres first.", file=sys.stderr)
        sys.exit(1)
    _ensure_local(dsn)

    random.seed(42)  # reproducible seed runs

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        # Bootstrap schema (idempotent) and wipe any previous seed.
        await conn.execute(SCHEMA_SQL)
        await conn.execute(
            "TRUNCATE bar_snapshots, coin_snapshots, spot_snapshots RESTART IDENTITY"
        )

        # Snap "now" down to the most recent 30-min boundary so the chart
        # x-axis ends on a clean tick.
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // TICK_MINUTES) * TICK_MINUTES)
        start = now - timedelta(days=DAYS_BACK)
        ticks = int((now - start).total_seconds() // (TICK_MINUTES * 60))

        spot_dkk = START_SPOT_DKK_PER_G
        spot_rows: list[tuple] = []
        dealer_rows: list[tuple] = []
        coin_rows: list[tuple] = []

        for i in range(ticks):
            ts = start + timedelta(minutes=TICK_MINUTES * i)
            spot_dkk += random.gauss(0, SPOT_DRIFT_STDDEV)
            spot_dkk = max(SPOT_MIN, min(SPOT_MAX, spot_dkk))
            spot_rounded = round(spot_dkk, 4)

            spot_rows.append((
                ts,
                spot_rounded,
                round(spot_dkk * EUR_PER_DKK, 4),
                round(spot_dkk * SILVER_DKK_RATIO, 4),
                round(spot_dkk * SILVER_DKK_RATIO * EUR_PER_DKK, 4),
                False,
            ))

            for dealer, sizes in DEALER_PROFILES.items():
                for size_g, (brand, base_prem) in sizes.items():
                    if random.random() < ERROR_PROBABILITY:
                        dealer_rows.append((
                            ts, dealer, size_g, "error",
                            None, brand, "fake_seed_error", spot_rounded,
                        ))
                        continue
                    prem = base_prem + random.gauss(0, PREMIUM_NOISE_STDDEV)
                    price = spot_dkk * size_g * (1 + prem)
                    dealer_rows.append((
                        ts, dealer, size_g, "ok",
                        round(price, 2), brand, None, spot_rounded,
                    ))

            for dealer, coins in COIN_PROFILES.items():
                for coin_type, size_label, gross_g, purity, fine_g, base_prem in coins:
                    if random.random() < ERROR_PROBABILITY:
                        coin_rows.append((
                            ts, dealer, coin_type, size_label,
                            gross_g, purity, fine_g, "error",
                            None, "fake_seed_error", spot_rounded, None,
                        ))
                        continue
                    prem = base_prem + random.gauss(0, PREMIUM_NOISE_STDDEV)
                    price = spot_dkk * fine_g * (1 + prem)
                    slug_dealer = dealer.lower().replace(" ", "-")
                    slug_type = coin_type.lower().replace(" ", "-")
                    slug_size = size_label.replace(" ", "").replace("/", "-")
                    coin_rows.append((
                        ts, dealer, coin_type, size_label,
                        gross_g, purity, fine_g, "ok",
                        round(price, 2), None, spot_rounded,
                        f"https://example.invalid/{slug_dealer}/{slug_type}-{slug_size}",
                    ))

        await conn.executemany(
            """
            INSERT INTO spot_snapshots (
                fetched_at, gold_dkk_per_g, gold_eur_per_g,
                silver_dkk_per_g, silver_eur_per_g, fx_stale
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            spot_rows,
        )
        await conn.executemany(
            """
            INSERT INTO bar_snapshots (
                fetched_at, dealer, size_g, status, price_dkk,
                brand, error, spot_gold_dkk_per_g
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            dealer_rows,
        )
        await conn.executemany(
            """
            INSERT INTO coin_snapshots (
                fetched_at, dealer, coin_type, size_label,
                gross_weight_g, purity, fine_gold_g, status,
                price_dkk, error, spot_gold_dkk_per_g, listing_url
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            coin_rows,
        )

        print(
            f"seeded {len(spot_rows)} spot rows + {len(dealer_rows)} bar rows "
            f"+ {len(coin_rows)} coin rows ({DAYS_BACK} days × {ticks} ticks)"
        )

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
