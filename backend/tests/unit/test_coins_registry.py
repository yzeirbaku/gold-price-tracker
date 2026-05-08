import pytest

from app.coins import COINS, resolve


def test_registry_has_expected_types() -> None:
    assert "Krugerrand" in COINS
    assert "Maple Leaf" in COINS
    assert "Sovereign" in COINS
    assert "Ducat" in COINS


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
