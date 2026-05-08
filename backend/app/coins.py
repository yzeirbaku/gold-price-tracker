"""Static registry of recognized bullion gold coins.

The resolver does case-insensitive substring matching on the listing title
to identify (coin_type, size_label) and returns the standard (gross_weight,
purity, fine_gold) for that variant. Titles that don't match a registered
type are returned as None — the caller is expected to skip them.

Sources for weights/purity:
- Krugerrand, American Eagle: 22-carat (.9167) bullion; gross weight reflects
  the alloy. 1 oz Krugerrand = 33.93 g gross, 31.10 g fine gold.
- Maple Leaf, Vienna Philharmonic, Britannia (post-2013), Panda: .9999
  fineness; gross ≈ fine.
- Sovereign: 22-carat, 7.988 g gross full / 3.994 g half / 1.997 g quarter.
- Ducat (Austrian): .9860 fineness.
"""

# Each entry: coin_type → { size_label: (gross_weight_g, purity) }
COINS: dict[str, dict[str, tuple[float, float]]] = {
    "Krugerrand": {
        "1 oz": (33.93, 0.9167), "1/2 oz": (16.96, 0.9167),
        "1/4 oz": (8.48, 0.9167), "1/10 oz": (3.39, 0.9167),
    },
    "Maple Leaf": {
        "1 oz": (31.10, 0.9999), "1/2 oz": (15.55, 0.9999),
        "1/4 oz": (7.78, 0.9999), "1/10 oz": (3.11, 0.9999),
        "1/20 oz": (1.555, 0.9999),
    },
    "Vienna Philharmonic": {
        "1 oz": (31.10, 0.9999), "1/2 oz": (15.55, 0.9999),
        "1/4 oz": (7.78, 0.9999), "1/10 oz": (3.11, 0.9999),
        # The Phil also strikes a 1/25 oz €4 face-value coin.
        "1/25 oz": (1.244, 0.9999),
    },
    "American Eagle": {
        "1 oz": (33.93, 0.9167), "1/2 oz": (16.97, 0.9167),
        "1/4 oz": (8.48, 0.9167), "1/10 oz": (3.39, 0.9167),
    },
    "Britannia": {
        "1 oz": (31.10, 0.9999), "1/2 oz": (15.55, 0.9999),
        "1/4 oz": (7.78, 0.9999), "1/10 oz": (3.11, 0.9999),
    },
    "Sovereign": {
        "Full": (7.99, 0.9167), "Half": (3.99, 0.9167), "Quarter": (1.99, 0.9167),
    },
    "Ducat": {
        "1 ducat": (3.49, 0.9860), "4 ducat": (13.96, 0.9860),
    },
    "Panda": {
        # Modern (post-2016) gram-denominated
        "30 g": (30.00, 0.9999), "15 g": (15.00, 0.9999),
        "8 g": (8.00, 0.9999), "3 g": (3.00, 0.9999), "1 g": (1.00, 0.9999),
        # Pre-2016 fractional troy ounce
        "1 oz": (31.10, 0.9999), "1/2 oz": (15.55, 0.9999),
        "1/4 oz": (7.78, 0.9999), "1/10 oz": (3.11, 0.9999),
        "1/20 oz": (1.555, 0.9999),
    },
}

# Title-fragment synonyms per coin_type. Lowercase; first hit wins.
_TYPE_ALIASES: dict[str, list[str]] = {
    "Krugerrand": ["krugerrand", "kruger rand"],
    "Maple Leaf": ["maple leaf", "maple"],
    "Vienna Philharmonic": [
        "vienna philharmonic", "wiener philharmoniker",
        # Danish: "Østrigsk Philharmoniker". Standalone "philharmoniker" alone
        # is enough; "philharmonic" is left in for English-language listings
        # where the full word ends in -ic.
        "philharmoniker", "philharmonic", "filharmoniker",
    ],
    "American Eagle": [
        "american eagle", "us eagle",
        # Danish dealers usually keep "Eagle" English; "Amerikansk Eagle".
        "amerikansk eagle",
        # Some dealers spell out "American Gold Eagle" — the "gold" word
        # in the middle breaks substring matching against "american eagle".
        "american gold eagle",
    ],
    "Britannia": ["britannia"],
    "Sovereign": ["sovereign"],
    "Ducat": ["ducat", "dukat"],
    "Panda": ["panda"],
}

# Size-label synonyms per (coin_type, size_label). Lowercase. We sort the
# matches by alias-length descending at lookup so "1/10 oz" beats "1 oz" on
# strings that contain both substrings.
_FRACTIONAL_TYPES = (
    "Krugerrand", "Maple Leaf", "Vienna Philharmonic",
    "American Eagle", "Britannia", "Panda",
)

_SIZE_ALIASES: dict[tuple[str, str], list[str]] = {
    # Fractional ounce sizes — apply to all coin types that use them.
    # 1/20 oz only exists for Maple Leaf and Panda.
    **{
        (t, "1/20 oz"): ["1/20 oz", "1/20oz", "1/20 unze", "1/20 ounce"]
        for t in ("Maple Leaf", "Panda")
    },
    # 1/25 oz only exists for Vienna Philharmonic.
    ("Vienna Philharmonic", "1/25 oz"): [
        "1/25 oz", "1/25oz", "1/25 unze", "1/25 ounce",
    ],
    **{
        (t, "1/10 oz"): [
            "1/10 oz", "1/10oz", "0.1 oz", "1/10 unze", "1/10 ounce",
            "tenth oz", "tenth ounce",
        ]
        for t in _FRACTIONAL_TYPES
    },
    **{
        (t, "1/4 oz"): [
            "1/4 oz", "1/4oz", "0.25 oz", "1/4 unze", "1/4 ounce",
            "quarter oz", "quarter ounce",
        ]
        for t in _FRACTIONAL_TYPES
    },
    **{
        (t, "1/2 oz"): [
            "1/2 oz", "1/2oz", "0.5 oz", "1/2 unze", "1/2 ounce",
            "half oz", "half ounce",
        ]
        for t in _FRACTIONAL_TYPES
    },
    **{
        (t, "1 oz"): ["1 oz", "1oz", "1 ounce", "1 unze", "one oz", "one ounce"]
        for t in _FRACTIONAL_TYPES
    },
    # Sovereigns
    ("Sovereign", "Full"): ["full sovereign", "1 sovereign"],
    ("Sovereign", "Half"): ["half sovereign", "1/2 sovereign"],
    ("Sovereign", "Quarter"): ["quarter sovereign", "1/4 sovereign"],
    # Ducat
    ("Ducat", "1 ducat"): ["1 ducat", "1 dukat", "single ducat", "single dukat"],
    ("Ducat", "4 ducat"): ["4 ducat", "4 dukat", "fire ducat", "fire dukat"],
    # Panda — gram-denominated
    ("Panda", "30 g"): ["30 g", "30g", "30 gram"],
    ("Panda", "15 g"): ["15 g", "15g", "15 gram"],
    ("Panda", "8 g"): ["8 g", "8g", "8 gram"],
    ("Panda", "3 g"): ["3 g", "3g", "3 gram"],
    ("Panda", "1 g"): ["1 g", "1g", "1 gram"],
}


def resolve(title: str) -> tuple[str, str, float, float, float] | None:
    """Identify (coin_type, size_label, gross_g, purity, fine_g) from a title.

    Returns None if no recognized coin type appears or no recognized size
    matches for that type. Caller decides whether to enforce the >20g cap.
    """
    if not title:
        return None
    tl = title.lower()
    for coin_type, aliases in _TYPE_ALIASES.items():
        if not any(a in tl for a in aliases):
            continue
        # Sovereign default — bare "sovereign" with no qualifier means Full.
        if coin_type == "Sovereign" and "half" not in tl and "quarter" not in tl:
            gross_g, purity = COINS["Sovereign"]["Full"]
            return ("Sovereign", "Full", gross_g, purity, round(gross_g * purity, 4))
        # Try size labels longest-first so "1/10 oz" doesn't get caught by "1 oz".
        candidates = [
            (size_label, sorted(syns, key=len, reverse=True))
            for (ct, size_label), syns in _SIZE_ALIASES.items()
            if ct == coin_type
        ]
        candidates.sort(key=lambda c: max((len(s) for s in c[1]), default=0), reverse=True)
        for size_label, syns in candidates:
            if any(s in tl for s in syns):
                gross_g, purity = COINS[coin_type][size_label]
                return (coin_type, size_label, gross_g, purity, round(gross_g * purity, 4))
        return None
    return None
