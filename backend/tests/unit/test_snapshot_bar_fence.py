"""POST /snapshot must fence bar premiums against the canonical spot.

The four size runs each fetch spot independently. orchestrator.run() computes
premium — and therefore runs flag_bar_premium_outliers — only when its OWN
spot lookup succeeded, but /snapshot persisted every run's listings either
way, filling spot_gold_dkk_per_g from whichever run did get a spot. A row
from a spot-less run therefore landed as status='ok' with a price that had
never been checked against any spot, paired with a spot that made it look
checked. Every downstream reader (/history, buy_context, alerts, reports)
recomputes premium from exactly those two columns.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Listing, PerCurrency, PriceResponse, SpotPrice

SPOT_DKK_PER_G = 695.0
FETCHED_AT = "2026-09-22T12:00:00+00:00"


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test")


def _spot(gold_dkk_per_g: float) -> SpotPrice:
    return SpotPrice(
        gold=PerCurrency(per_gram_eur=gold_dkk_per_g / 7.45,
                         per_gram_dkk=gold_dkk_per_g),
        silver=PerCurrency(per_gram_eur=1.1, per_gram_dkk=8.2),
    )


def _listing(dealer: str, price_dkk: float | None, premium_pct: float | None) -> Listing:
    return Listing(
        dealer=dealer, status="ok", price_dkk=price_dkk,
        premium_pct=premium_pct, fetched_at=FETCHED_AT,
    )


def _capture_bar_rows():
    """A fake pool that records the executemany payload for bar_snapshots."""
    captured: dict = {}
    fake_conn = AsyncMock()

    async def _executemany(sql, rows):
        if "bar_snapshots" in sql:
            captured["bars"] = list(rows)

    fake_conn.executemany = _executemany

    class _Tx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    fake_conn.transaction = lambda: _Tx()
    fake_conn.fetchrow = AsyncMock(return_value=None)   # no outlier baseline

    class _Acquired:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, *a):
            return False

    fake_pool = AsyncMock()
    fake_pool.acquire = lambda: _Acquired()
    return fake_pool, captured


def _run_snapshot(results):
    fake_pool, captured = _capture_bar_rows()

    async def _fake_run(size_g):
        return results[size_g]

    with patch("app.main.get_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("app.main.run", new=_fake_run), \
         patch("app.main.evaluate_alerts", new=AsyncMock()), \
         patch("app.main.prune_snapshots", new=AsyncMock(return_value={})), \
         patch("app.main._safe_fetch_coins", new=AsyncMock(return_value=[])):
        client = TestClient(app)
        r = client.post("/snapshot", headers={"X-API-Key": "test"})
    assert r.status_code == 200, r.text
    return captured.get("bars", [])


def _ok_response(size_g: float, spot, listings) -> PriceResponse:
    return PriceResponse(
        size_g=size_g, fetched_at=FETCHED_AT,
        spot=spot, listings=listings, fx_stale=False,
    )


def test_listings_from_a_spotless_run_are_still_fenced():
    """The 5g run's spot lookup timed out, so orchestrator.run() left its
    premiums uncomputed and never fenced them. A wrong-field grab (the
    per-gram price, 715 kr, instead of the 5g bar price) must not survive
    into bar_snapshots as status='ok'.
    """
    good = _ok_response(2.5, _spot(SPOT_DKK_PER_G), [_listing("Tavex", 1800.0, 3.5)])
    # spot=None → premium_pct=None → orchestrator's fence skipped it entirely.
    spotless = _ok_response(5.0, None, [_listing("Nordisk Guld", 715.0, None)])
    results = {2.5: good, 5.0: spotless, 10.0: _ok_response(10.0, _spot(SPOT_DKK_PER_G), []),
               20.0: _ok_response(20.0, _spot(SPOT_DKK_PER_G), [])}

    rows = _run_snapshot(results)

    nordisk = [r for r in rows if r[1] == "Nordisk Guld"]
    assert len(nordisk) == 1
    status, price = nordisk[0][3], nordisk[0][4]
    assert status == "error", (
        "a 715 kr '5 g bar' is -79% premium — far outside BAR_PREMIUM_BOUNDS_PCT — "
        "and must be flipped to error before it reaches bar_snapshots"
    )
    assert price is None


def test_every_row_is_written_with_the_one_validated_spot():
    """Only the first run's spot passes the /snapshot outlier guard, so that
    is the only value allowed onto disk. Writing each run's own spot let three
    unvalidated values through per tick."""
    canonical = _spot(SPOT_DKK_PER_G)
    drifted = _spot(500.0)   # 28% low — never seen by the outlier guard
    results = {
        2.5: _ok_response(2.5, canonical, [_listing("Tavex", 1800.0, 3.5)]),
        5.0: _ok_response(5.0, drifted, [_listing("Vitus Guld", 3600.0, 3.5)]),
        10.0: _ok_response(10.0, None, [_listing("Plaza", 7150.0, None)]),
        20.0: _ok_response(20.0, canonical, []),
    }

    rows = _run_snapshot(results)

    assert rows, "expected bar rows to be persisted"
    spots = {r[7] for r in rows}
    assert spots == {SPOT_DKK_PER_G}, (
        f"all rows must carry the validated spot, got {spots}"
    )


def test_good_listings_survive_the_fence():
    """Guard against over-fencing: a normal in-bounds bar stays ok."""
    results = {
        2.5: _ok_response(2.5, _spot(SPOT_DKK_PER_G), []),
        # 5 g at 695/g = 3475 ref; 3600 paid = +3.6% premium, well in bounds.
        5.0: _ok_response(5.0, None, [_listing("Vitus Guld", 3600.0, None)]),
        10.0: _ok_response(10.0, _spot(SPOT_DKK_PER_G), []),
        20.0: _ok_response(20.0, _spot(SPOT_DKK_PER_G), []),
    }

    rows = _run_snapshot(results)

    vitus = [r for r in rows if r[1] == "Vitus Guld"]
    assert len(vitus) == 1
    assert vitus[0][3] == "ok"
    assert vitus[0][4] == 3600.0
