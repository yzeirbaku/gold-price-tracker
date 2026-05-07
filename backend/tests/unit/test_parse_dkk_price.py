import pytest

from app.scrapers.base import parse_dkk_price


@pytest.mark.parametrize(
    "text, expected",
    [
        # Danish format: '.' as thousands, ',' as decimal
        ("5.366,32 kr", 5366.32),
        ("kr. 12.345,67", 12345.67),
        ("1.234.567,89", 1234567.89),
        # Danish whole-number prices (no decimal): the dot is thousands separator,
        # not a US-style decimal — this was the underpricing bug we caught.
        ("1.825 kr.", 1825.0),
        ("6.252 kr.", 6252.0),
        ("108.582 kr.", 108582.0),
        # US-style decimals (used by Vitus's OpenGraph meta) keep parsing as-is.
        ("5345.47", 5345.47),
        ("0.5", 0.5),
        # Comma decimals without thousands
        ("2,5", 2.5),
        ("5366,32", 5366.32),
        # No separators
        ("5366", 5366.0),
        # Unparseable
        ("", None),
        ("abc", None),
        ("kr.", None),
    ],
)
def test_parse_dkk_price(text: str, expected: float | None) -> None:
    assert parse_dkk_price(text) == expected
