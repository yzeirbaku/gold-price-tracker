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
        await conn.execute("TRUNCATE dealer_snapshots, spot_snapshots RESTART IDENTITY")

        # Snap "now" down to the most recent 30-min boundary so the chart
        # x-axis ends on a clean tick.
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // TICK_MINUTES) * TICK_MINUTES)
        start = now - timedelta(days=DAYS_BACK)
        ticks = int((now - start).total_seconds() // (TICK_MINUTES * 60))

        spot_dkk = START_SPOT_DKK_PER_G
        spot_rows: list[tuple] = []
        dealer_rows: list[tuple] = []

        for i in range(ticks):
            ts = start + timedelta(minutes=TICK_MINUTES * i)
            spot_dkk += random.gauss(0, SPOT_DRIFT_STDDEV)
            spot_dkk = max(SPOT_MIN, min(SPOT_MAX, spot_dkk))

            spot_rows.append((
                ts,
                round(spot_dkk, 4),
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
                            None, brand, "fake_seed_error", round(spot_dkk, 4),
                        ))
                        continue
                    prem = base_prem + random.gauss(0, PREMIUM_NOISE_STDDEV)
                    price = spot_dkk * size_g * (1 + prem)
                    dealer_rows.append((
                        ts, dealer, size_g, "ok",
                        round(price, 2), brand, None, round(spot_dkk, 4),
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
            INSERT INTO dealer_snapshots (
                fetched_at, dealer, size_g, status, price_dkk,
                brand, error, spot_gold_dkk_per_g
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            dealer_rows,
        )

        print(
            f"seeded {len(spot_rows)} spot rows + {len(dealer_rows)} dealer rows "
            f"({DAYS_BACK} days × {ticks} ticks)"
        )

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
