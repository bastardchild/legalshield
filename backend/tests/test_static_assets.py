"""
Front-end asset and static-file guards (KNOWN_ISSUES #20, #27).

htmx and Alpine are functional dependencies: without them the results page never polls
and nothing renders. Loading them from a CDN meant a blocked or slow unpkg took the app
down, so both are vendored under `app/static/vendor/`.
"""
from pathlib import Path

import pytest

TEMPLATE_DIR = Path("app/templates")
STATIC_DIR = Path("app/static")
VENDOR_DIR = STATIC_DIR / "vendor"

VENDORED = ["htmx-1.9.12.min.js", "alpine-3.14.1.min.js"]


def _base_html() -> str:
    return (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")


class TestVendoredAssets:
    @pytest.mark.parametrize("name", VENDORED)
    def test_file_exists_and_is_not_empty(self, name):
        path = VENDOR_DIR / name
        assert path.exists(), f"{path} missing"
        assert path.stat().st_size > 10_000, f"{path} looks truncated"

    @pytest.mark.parametrize("name", VENDORED)
    def test_referenced_from_base_template(self, name):
        assert f"/static/vendor/{name}" in _base_html()

    def test_no_script_loaded_from_a_cdn(self):
        """A CDN outage must not stop the page from polling."""
        html = _base_html()
        for line in html.splitlines():
            if "<script" in line and "src=" in line:
                assert "http://" not in line and "https://" not in line, (
                    f"script still loaded remotely: {line.strip()}"
                )

    def test_alpine_is_deferred(self):
        """Alpine must not run before app.js has registered its components."""
        html = _base_html()
        alpine_tag = next(
            line for line in html.splitlines() if "alpine-3.14.1.min.js" in line
        )
        assert "defer" in alpine_tag

    def test_htmx_is_not_deferred(self):
        # htmx processes hx-* attributes during parse; deferring it breaks the first swap.
        html = _base_html()
        htmx_tag = next(line for line in html.splitlines() if "htmx-1.9.12.min.js" in line)
        assert "defer" not in htmx_tag


class TestFontFallbacks:
    """Google Fonts is still remote, so every stack needs a local fallback (#20)."""

    def _css(self) -> str:
        return (STATIC_DIR / "app.css").read_text(encoding="utf-8")

    def test_no_bare_inter_stack(self):
        assert "'Inter', sans-serif" not in self._css()

    def test_no_bare_mono_stack(self):
        assert "'JetBrains Mono', monospace" not in self._css()

    def test_inter_has_system_fallbacks(self):
        assert "'Inter', system-ui" in self._css()

    def test_mono_has_system_fallbacks(self):
        assert "'JetBrains Mono', ui-monospace" in self._css()


class TestInlineStyles:
    """Font stacks inline in templates bypass the CSS fallbacks entirely (#27)."""

    @pytest.mark.parametrize(
        "name", ["base.html", "upload.html", "result.html", "partials/status.html", "partials/result.html"]
    )
    def test_no_inline_font_family(self, name):
        html = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        assert "font-family:" not in html, f"{name} declares a font inline; use a CSS class"

    def test_mono_utility_class_exists(self):
        assert ".mono" in (STATIC_DIR / "app.css").read_text(encoding="utf-8")
