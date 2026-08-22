"""
Template regression tests.

`test_all_templates_compile` is the guard for the P0 defect where
`partials/result.html` contained an orphaned `{% elif %}` block and raised
TemplateSyntaxError on every poll, so the results page never rendered.
"""
import re

import pytest
from jinja2 import TemplateSyntaxError

from tests.conftest import ALL_TEMPLATES

STATUSES = ["uploaded", "processing", "done", "failed"]


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_all_templates_compile(jinja_env, name):
    try:
        jinja_env.get_template(name)
    except TemplateSyntaxError as exc:
        pytest.fail(f"{name} failed to compile at line {exc.lineno}: {exc.message}")


@pytest.mark.parametrize("status", STATUSES)
def test_status_partial_exposes_marker(jinja_env, contract_factory, status):
    html = jinja_env.get_template("partials/status.html").render(
        contract=contract_factory(status), contract_id="abc-123"
    )
    assert f'data-status-marker="{status}"' in html


@pytest.mark.parametrize("status", STATUSES)
def test_status_partial_does_not_self_poll(jinja_env, contract_factory, status):
    """Polling belongs to the #status-area container only (KNOWN_ISSUES #15)."""
    html = jinja_env.get_template("partials/status.html").render(
        contract=contract_factory(status), contract_id="abc-123"
    )
    assert "hx-trigger" not in html
    assert "hx-get" not in html


@pytest.mark.parametrize("status", STATUSES)
def test_result_partial_exposes_marker(jinja_env, contract_factory, status):
    html = jinja_env.get_template("partials/result.html").render(
        contract=contract_factory(status), contract_id="abc-123", analysis={}
    )
    assert f'data-result-status="{status}"' in html


def test_result_partial_renders_findings(jinja_env, contract_factory, row_factory):
    analysis = {
        "risk_clause": row_factory({
            "findings": [{
                "clause_type": "non_compete",
                "severity": "critical",
                "original_text": "Dilarang bekerja 5 tahun di seluruh Indonesia.",
                "explanation": "Sangat tidak proporsional.",
                "recommendation": "Batasi 12 bulan.",
                "confidence": 0.97,
            }]
        }),
        "tax_compliance": row_factory({
            "findings": [{
                "issue_type": "pph23",
                "severity": "high",
                "original_text": "Pembayaran tanpa pemotongan pajak.",
                "explanation": "PPh 23 tidak dipotong.",
                "recommendation": "Potong 2% dan terbitkan bukti potong.",
                "applicable_regulation": "UU PPh Pasal 23",
            }]
        }),
        "counter_draft": row_factory({
            "counter_draft": "PASAL 1 - LINGKUP PEKERJAAN\nRevisi.",
            "summary_of_changes": ["Non-compete dipangkas ke 12 bulan."],
            "negotiation_notes": "Sampaikan sebagai penyelarasan risiko.",
        }),
    }
    html = jinja_env.get_template("partials/result.html").render(
        contract=contract_factory("done"), contract_id="abc-123", analysis=analysis
    )

    assert "Non Compete" in html
    assert "badge-critical" in html
    assert "97%" in html
    assert "UU PPh Pasal 23" in html
    assert "PASAL 1" in html
    assert "Non-compete dipangkas ke 12 bulan." in html
    assert 'data-result-status="done"' in html


def test_result_partial_renders_agent_errors(jinja_env, contract_factory, row_factory):
    """A failed agent shows its error instead of breaking the page."""
    analysis = {
        "risk_clause": row_factory(None, "LLM returned invalid JSON"),
        "tax_compliance": row_factory(None, "provider timeout"),
    }
    html = jinja_env.get_template("partials/result.html").render(
        contract=contract_factory("done"), contract_id="abc-123", analysis=analysis
    )
    assert "LLM returned invalid JSON" in html
    assert "provider timeout" in html
    assert "Draft belum tersedia." in html


def test_result_partial_survives_malformed_findings(strict_jinja_env, contract_factory, row_factory):
    """
    Missing keys and a string confidence must not raise (KNOWN_ISSUES #14).
    Rendered under StrictUndefined so any unguarded access fails loudly.
    """
    analysis = {
        "risk_clause": row_factory({"findings": [
            {},                                              # everything missing
            {"severity": "sedang", "confidence": "high"},     # unmapped severity, non-numeric confidence
        ]}),
        "tax_compliance": row_factory({"findings": [{"severity": None}]}),
    }
    html = strict_jinja_env.get_template("partials/result.html").render(
        contract=contract_factory("done"), contract_id="abc-123", analysis=analysis
    )
    assert 'data-result-status="done"' in html


def test_severity_stripe_classes_are_known(jinja_env, contract_factory, row_factory):
    """
    The stripe class is derived from severity's first letter; only c/h/m/l are styled
    in app.css. Any other value must fall back rather than emit a dead class.
    """
    findings = [{"severity": s, "clause_type": "other", "original_text": "x",
                 "explanation": "y", "recommendation": "z", "confidence": 0.9}
                for s in ("critical", "high", "medium", "low")]
    html = jinja_env.get_template("partials/result.html").render(
        contract=contract_factory("done"), contract_id="abc-123",
        analysis={"risk_clause": row_factory({"findings": findings})},
    )
    emitted = set(re.findall(r"fc fc-s(\w)", html))
    assert emitted <= {"c", "h", "m", "l"}
    assert emitted == {"c", "h", "m", "l"}
