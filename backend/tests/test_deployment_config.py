"""
Deployment-configuration guards (KNOWN_ISSUES #26).

Static checks on the compose files. They exist because each of these mistakes is invisible
until it is in production:

* a `./backend:/app` bind mount means the running code is whatever is on the host disk, not
  the image that was built and tested;
* `--reload` with nothing to watch adds a supervisor process that obscures crash exit codes;
* publishing 5432/6379 on a server exposes the database with the credentials
  `legalshield:legalshield` and an unauthenticated Redis.

Parsing YAML by hand rather than with a library, so the test needs no extra dependency: the
structure being checked is shallow and the assertions are about substrings within a known
service block.

The compose files live above the Docker build context, so the `test` service mounts them at
`/deploy`. `../` is tried as well, for the case where pytest runs from a checkout rather than
the container.
"""
from pathlib import Path

import pytest


def _locate(name: str) -> Path | None:
    for candidate in (Path("/deploy") / name, Path("..") / name):
        if candidate.exists():
            return candidate
    return None


BASE_NAME = "docker-compose.yml"
PROD_NAME = "docker-compose.prod.yml"

SOURCE_MOUNT = "./backend:/app"
APP_SERVICES = ("api", "worker", "migrate")


def _text(name: str) -> str:
    path = _locate(name)
    if path is None:
        pytest.skip(f"{name} is not reachable from the working directory")
    return path.read_text(encoding="utf-8")


def base() -> str:
    return _text(BASE_NAME)


def prod() -> str:
    return _text(PROD_NAME)


def _service_block(text: str, name: str) -> str:
    """The lines of one top-level service, up to the next service at the same indent."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.rstrip() == f"  {name}:"), None
    )
    assert start is not None, f"service {name!r} not found"
    block = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith("    ") and not line.startswith("#"):
            break
        block.append(line)
    return "\n".join(block)


def _directives(text: str, name: str) -> str:
    """
    A service block with `#` comment lines removed.

    Needed for "this flag must not appear" checks: the comments in the prod overlay explain
    the flags they forbid, so a naive substring search matches its own explanation.
    """
    return "\n".join(
        line
        for line in _service_block(text, name).splitlines()
        if not line.lstrip().startswith("#")
    )


class TestProdOverlayExists:
    def test_file_is_present(self):
        assert prod().strip(), "docker-compose.prod.yml is empty"

    def test_documents_how_to_use_it(self):
        assert "-f docker-compose.prod.yml" in prod()


class TestProdDropsTheSourceMount:
    """The whole point of the overlay: run the image, not the host's working tree."""

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_no_source_bind_mount(self, service):
        assert SOURCE_MOUNT not in _directives(prod(), service), (
            f"{service} still mounts the source tree over the image in production"
        )

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_volumes_are_overridden_not_merged(self, service):
        """Compose merges volume lists by default, so the inherited mount would survive."""
        block = _service_block(prod(), service)
        assert "volumes: !override" in block

    @pytest.mark.parametrize("service", APP_SERVICES)
    def test_seed_data_is_still_available_read_only(self, service):
        block = _service_block(prod(), service)
        assert "./seed:/app/seed:ro" in block


class TestProdRuntimeFlags:
    def test_reload_is_off(self):
        assert "--reload" not in _directives(prod(), "api")

    def test_proxy_headers_are_not_wildcarded(self):
        """
        --forwarded-allow-ips=* makes uvicorn trust X-Forwarded-For from anyone, which is the
        value the rate limiter keys on: a caller could mint a fresh identity per request.
        """
        assert "--forwarded-allow-ips=*" not in _directives(prod(), "api")

    @pytest.mark.parametrize("service", ("postgres", "redis"))
    def test_database_ports_are_not_published(self, service):
        block = _service_block(prod(), service)
        assert "ports: !override []" in block, (
            f"{service} would still publish its port to the host"
        )

    @pytest.mark.parametrize("service", ("api", "worker", "postgres", "redis"))
    def test_long_running_services_restart(self, service):
        assert "restart: unless-stopped" in _service_block(prod(), service)

    def test_migrate_does_not_restart(self):
        """It is one-shot; `unless-stopped` would re-run it forever."""
        assert "restart: unless-stopped" not in _directives(prod(), "migrate")


class TestBaseFileStillSupportsDevelopment:
    def test_reload_is_on_in_the_base_file(self):
        assert "--reload" in _directives(base(), "api")

    def test_base_file_keeps_the_bind_mount(self):
        """--reload depends on it; the overlay is what removes it."""
        assert SOURCE_MOUNT in _directives(base(), "api")

    def test_test_service_is_behind_a_profile(self):
        """Otherwise `docker compose up` would start the test runner."""
        assert "profiles:" in _service_block(base(), "test")

    def test_test_service_cannot_reach_a_real_provider(self):
        assert "HERMES_BASE_URL: http://llm.invalid" in _service_block(base(), "test")

    def test_migrate_runs_before_the_app_services(self):
        for service in ("api", "worker"):
            assert "service_completed_successfully" in _service_block(base(), service), (
                f"{service} could start against an unmigrated database"
            )
