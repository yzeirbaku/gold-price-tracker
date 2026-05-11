"""Jinja2 environment + render entrypoint for reports.

The template lives next to this module under templates/. The renderer
expects a context dict already shaped for the template \u2014 see test fixtures
for the schema.

A JSON sidecar copy of the context is embedded inside a <script> block
inside every rendered report; the template uses `json_sidecar` as the
verbatim string (the builder is responsible for producing it).
"""
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_report(context: dict[str, Any]) -> str:
    """Render report.html with the given context.

    If the caller didn't pre-compute `json_sidecar`, we do it here so tests
    don't have to. The sidecar excludes itself (no recursion).
    """
    sidecar_payload = _build_sidecar_payload(context)
    full_context = {
        **context,
        "json_sidecar": json.dumps(sidecar_payload, separators=(",", ":")),
    }
    template = _env.get_template("report.html")
    return template.render(**full_context)


def _build_sidecar_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Strip render-only fields and produce the canonical machine-readable form."""
    return {
        "version": 1,
        "period": {
            "type": context.get("kind"),
            "start": context.get("period_start"),
            "end": context.get("period_end"),
            "label": context.get("label"),
        },
        "generated_at": context.get("generated_at"),
        "spot": context.get("spot"),
        "fingerprints": context.get("fingerprints", []),
        "bars": context.get("bars", []),
        "coins": context.get("coins", []),
        "notable": context.get("notable", []),
        "time_of_month": context.get("time_of_month"),
    }
