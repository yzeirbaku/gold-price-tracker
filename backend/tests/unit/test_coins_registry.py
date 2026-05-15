from decimal import Decimal

import pytest

from app.coins import COINS, FINE_GOLD_PRECISION, fine_gold_g, resolve


def test_registry_has_expected_types() -> None:
    assert "Krugerrand" in COINS
    assert "Maple Leaf" in COINS
    assert "Sovereign" in COINS
    assert "Ducat" in COINS
    assert "Danish 20 kr" in COINS
    assert "Danish 10 kr" in COINS


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Title, (coin_type, size_label, gross_g, purity)
        ("Krugerrand 1/4 oz 2023", ("Krugerrand", "1/4 oz", 8.48, 0.9167)),
        ("Krugerrand 1/4oz", ("Krugerrand", "1/4 oz", 8.48, 0.9167)),
        ("Krugerrand quarter ounce", ("Krugerrand", "1/4 oz", 8.48, 0.9167)),
        ("1/10 oz Krugerrand random year", ("Krugerrand", "1/10 oz", 3.39, 0.9167)),
        ("Maple Leaf 1/4 oz", ("Maple Leaf", "1/4 oz", 7.78, 0.9999)),
        ("Vienna Philharmonic 1/2 oz 2024", ("Vienna Philharmonic", "1/2 oz", 15.55, 0.9999)),
        ("Britannia 1/10 oz", ("Britannia", "1/10 oz", 3.11, 0.9999)),
        ("Full Sovereign 2023", ("Sovereign", "Full", 7.99, 0.9167)),
        ("Sovereign", ("Sovereign", "Full", 7.99, 0.9167)),
        ("Half Sovereign", ("Sovereign", "Half", 3.99, 0.9167)),
        ("1 Ducat", ("Ducat", "1 ducat", 3.49, 0.9860)),
        ("4 Ducat 1915", ("Ducat", "4 ducat", 13.96, 0.9860)),
        ("Panda 8 g 2024", ("Panda", "8 g", 8.00, 0.9999)),
        ("Panda 1 g", ("Panda", "1 g", 1.00, 0.9999)),
        # Danish Scandinavian-Monetary-Union kroner
        ("Dansk 20 kroner Christian X", ("Danish 20 kr", "Christian X", 8.9606, 0.900)),
        ("Dansk 20 kroner Christian IX", ("Danish 20 kr", "Christian IX", 8.9606, 0.900)),
        ("Dansk 20 kroner Frederik VIII", ("Danish 20 kr", "Frederik VIII", 8.9606, 0.900)),
        ("Dansk 10 kroner Christian IX", ("Danish 10 kr", "Christian IX", 4.4803, 0.900)),
        ("Dansk 10 kroner Christian X", ("Danish 10 kr", "Christian X", 4.4803, 0.900)),
        ("Dansk 10 kroner Frederik VIII", ("Danish 10 kr", "Frederik VIII", 4.4803, 0.900)),
    ],
)
def test_resolve_known_titles(title: str, expected: tuple) -> None:
    out = resolve(title)
    assert out is not None
    coin_type, size_label, gross_g, purity, fine_g = out
    assert (coin_type, size_label, gross_g, purity) == expected
    assert abs(fine_g - gross_g * purity) < 0.01


@pytest.mark.parametrize(
    "title",
    [
        "Random gold coin",
        "Investment gold bar 5g",
        "",
        "Mexican Peso 50",
    ],
)
def test_resolve_unknown_returns_none(title: str) -> None:
    assert resolve(title) is None


def test_one_oz_resolves_but_caller_filters() -> None:
    # Registry includes 1 oz entries; the >20g cap is enforced by callers.
    out = resolve("Krugerrand 1 oz")
    assert out is not None
    assert out[1] == "1 oz"
    assert out[4] > 20  # fine gold > 20g — caller will skip


# ── precision invariant ─────────────────────────────────────────────────────
# alerts._index_coin_mins quantizes scraper output via Decimal("0.0001"); the
# user-facing alerts.fine_gold_g is sourced from /alerts/options which is also
# computed via fine_gold_g(). These tests lock down that contract so a future
# refactor can't silently bucket "same coin" under two different keys.


def test_fine_gold_g_matches_decimal_quantize_invariant() -> None:
    """Every (gross, purity) in the registry must round-trip through both
    fine_gold_g() and Decimal.quantize at FINE_GOLD_PRECISION identically.
    If this breaks, alerts and /history/coin can drift apart silently."""
    for sizes in COINS.values():
        for gross, purity in sizes.values():
            via_helper = fine_gold_g(gross, purity)
            via_decimal = (
                Decimal(str(gross)) * Decimal(str(purity))
            ).quantize(FINE_GOLD_PRECISION)
            assert Decimal(str(via_helper)) == via_decimal


def test_resolve_returns_helper_quantized_fine_gold_g() -> None:
    """resolve() must return fine_gold_g via the canonical helper — not its
    own ad-hoc round() — otherwise the invariant the helper guarantees is
    bypassed for the scraper path."""
    # Pick a non-trivial coin: Krugerrand 1/2 oz, 16.96 × 0.9167 should land
    # somewhere with non-zero 4th-decimal digit.
    out = resolve("Krugerrand 1/2 oz")
    assert out is not None
    _, _, gross_g, purity, fine_g = out
    assert fine_g == fine_gold_g(gross_g, purity)
