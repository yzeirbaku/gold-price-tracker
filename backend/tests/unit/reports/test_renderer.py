import json
import re

from app.reports.renderer import render_report

SAMPLE_CONTEXT = {
    "kind": "weekly",
    "label": "Weekly Report (04-05-2026 00:00 \u2192 11-05-2026 00:00)",
    "kind_label": "Weekly Report",
    "period_text": "04-05-2026 00:00 \u2192 11-05-2026 00:00",
    "period_start": "2026-05-04",
    "period_end": "2026-05-10",
    "generated_at": "2026-05-11T00:30:14.221Z",
    "spot": {
        "gold": {
            "open": 952.0, "close": 964.0, "high": 968.0, "low": 951.0,
            "delta_dkk_per_g": 12.0, "delta_pct": 1.26,
        },
        "silver": {
            "open": 12.6, "close": 12.83, "high": 12.9, "low": 12.5,
            "delta_dkk_per_g": 0.23, "delta_pct": 1.83,
        },
        "weekend_flat": True,
        "fx_note": "USD/DKK +0.3% over period",
    },
    "fingerprints": [
        {
            "dealer": "Tavex",
            "cadence": {
                "total_changes": 32, "changes_per_week": 8.0,
                "median_interval_hours": 18.7,
                "latest_change": "2026-05-07T14:23:00+02:00",
            },
            "time_of_day": {"morning": 4, "afternoon": 12, "evening": 1, "night": 0},
            "day_of_week": [3, 5, 8, 6, 3, 0, 7],
            "weekend": {"change_count": 1, "summary": "1 change Sun May 10"},
            "spot_tracking": {
                "correlation": 0.91, "lag_hours": 0.33, "sensitivity": 0.87,
            },
            "premium_band": {"p25": 5.2, "p75": 6.1},
            "fingerprint_tag": "high-cadence \u00b7 tight-tracking \u00b7 weekend-active",
        },
    ],
    "bars": [
        {
            "size_g": 5.0,
            "rows": [
                {"dealer": "Nordisk Guld", "median_price_dkk": 4585.0,
                 "median_premium_pct": 4.6, "spread_pp": 0.4,
                 "pct_time_cheapest": 42.0, "sparkline": "\u2581\u2582\u2583\u2584"},
                {"dealer": "Market", "median_price_dkk": 4602.0,
                 "median_premium_pct": 6.2, "spread_pp": 3.1,
                 "pct_time_cheapest": None, "sparkline": ""},
            ],
        },
    ],
    "coins": [],
    "notable": [
        {"text": "Tavex 10g premium \u21931.8pp on Wed May 6 (7.2% \u2192 5.4%)",
         "magnitude": 1.8},
    ],
    "time_of_month": None,
}


def test_render_produces_self_contained_html() -> None:
    html = render_report(SAMPLE_CONTEXT)
    assert html.startswith("<!doctype html>") or html.lstrip().startswith("<!DOCTYPE")
    assert "<link" not in html.lower() or 'rel="stylesheet"' not in html.lower()
    assert "<script src" not in html  # no external scripts


def test_render_contains_expected_section_ids() -> None:
    html = render_report(SAMPLE_CONTEXT)
    for section_id in (
        "section-header", "section-spot", "section-fingerprints",
        "section-bars", "section-coins", "section-notable",
    ):
        assert f'id="{section_id}"' in html, f"missing {section_id}"


def test_json_sidecar_parses_back_to_context() -> None:
    html = render_report(SAMPLE_CONTEXT)
    m = re.search(
        r'<script[^>]*id="report-data"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    assert m is not None, "JSON sidecar block missing"
    data = json.loads(m.group(1))
    assert data["period"]["type"] == "weekly"
    assert data["fingerprints"][0]["dealer"] == "Tavex"
    assert data["bars"][0]["size_g"] == 5.0


def test_render_marks_monthly_time_of_month_when_present() -> None:
    ctx = {**SAMPLE_CONTEXT, "kind": "monthly", "time_of_month": [
        {"dealer": "Tavex", "weekly_avg_premium_pct": [5.0, 5.4, 5.6, 6.1],
         "delta_pp": 1.1},
    ]}
    html = render_report(ctx)
    assert 'id="section-time-of-month"' in html
    assert "Tavex" in html


def test_render_omits_time_of_month_for_weekly_reports() -> None:
    html = render_report(SAMPLE_CONTEXT)
    assert 'id="section-time-of-month"' not in html
