"""
The DOCS_ENABLED switch (config setting -> FastAPI constructor kwargs).

When disabled, FastAPI simply does not register `/docs`, `/redoc` or `/openapi.json`, so
each answers the same 404 as any unknown path: no schema, no interactive console, no
information leak. The tests build a throwaway app from `docs_urls()` rather than importing
`app.main:app`, mirroring the middleware tests in `test_security.py`.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_settings():
    """get_settings is lru_cached, so each test must start from a clean configuration."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app() -> FastAPI:
    docs_url, redoc_url, openapi_url = get_settings().docs_urls()
    app = FastAPI(docs_url=docs_url, redoc_url=redoc_url, openapi_url=openapi_url)

    @app.get("/hello")
    async def hello():
        return {"ok": True}

    return app


class TestDocsUrls:
    def test_enabled_returns_all_three_paths(self):
        docs_url, redoc_url, openapi_url = get_settings().docs_urls()
        assert docs_url == "/docs"
        assert redoc_url == "/redoc"
        assert openapi_url == "/openapi.json"

    def test_disabled_returns_none_for_all_three(self, monkeypatch):
        monkeypatch.setenv("DOCS_ENABLED", "false")
        assert get_settings().docs_urls() == (None, None, None)


class TestDocsRoutes:
    def test_docs_reachable_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DOCS_ENABLED", "true")
        with TestClient(_app()) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/redoc").status_code == 200
            assert client.get("/openapi.json").status_code == 200

    def test_docs_are_404_when_disabled(self, monkeypatch):
        monkeypatch.setenv("DOCS_ENABLED", "false")
        with TestClient(_app()) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            # The app itself keeps working; only the docs surface is gone.
            assert client.get("/hello").status_code == 200
