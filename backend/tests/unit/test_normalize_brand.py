"""Tests for normalize_brand — the canonical mapper from raw dealer brand
strings to either a plain brand label or the canonical 'Mixed' sentinel."""
import pytest

from app.scrapers.base import normalize_brand


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_returns_none_for_empty(raw: str | None) -> None:
    assert normalize_brand(raw) is None


@pytest.mark.parametrize("brand", ["PAMP", "Argor", "Heimerle Meule", "Valcambi"])
def test_returns_brand_unchanged_for_known_brands(brand: str) -> None:
    assert normalize_brand(brand) == brand


def test_trims_whitespace_on_passthrough() -> None:
    assert normalize_brand("  PAMP  ") == "PAMP"


@pytest.mark.parametrize(
    "raw",
    [
        "Blandede Mærker",
        "blandede mærker",
        "BLANDEDE MÆRKER",
        "Blandede Merker",          # without æ
        "Guldbarre Blandede Mærker",  # surrounding context
        "Forskellige Mærker",
        "forskellige merker",
        "Diverse Mærker",
        "div. mærker",
        "Vilkårlige LBMA producenter",
        "vilkårlige LBMA-producenter",
        "Various Brands",
        "mixed brands",
    ],
)
def test_collapses_mixed_brand_variants_to_mixed(raw: str) -> None:
    assert normalize_brand(raw) == "Mixed"
