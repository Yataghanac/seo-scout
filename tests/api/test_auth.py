"""One shared token. No accounts, so nothing to enumerate and no password to hash."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seo_scout.api.app import create_app
from seo_scout.store import db


def _client(tmp_path: Path, token: str | None) -> TestClient:
    path = str(tmp_path / "t.db")
    db.connect(path).close()
    return TestClient(create_app(path, token=token))


def test_everything_is_open_when_no_token_is_configured(tmp_path: Path) -> None:
    assert _client(tmp_path, None).get("/api/runs").status_code == 200


def test_the_api_is_refused_without_the_token(tmp_path: Path) -> None:
    assert _client(tmp_path, "s3cret").get("/api/runs").status_code == 401


def test_logging_in_opens_the_api(tmp_path: Path) -> None:
    client = _client(tmp_path, "s3cret")
    assert client.post("/api/login", json={"token": "s3cret"}).status_code == 200
    assert client.get("/api/runs").status_code == 200  # the cookie is carried by the client


def test_the_wrong_token_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, "s3cret")
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401
    assert client.get("/api/runs").status_code == 401


def test_the_dashboard_shell_stays_public(tmp_path: Path) -> None:
    """It holds no data, and serving it is what lets the page render a token prompt."""
    assert _client(tmp_path, "s3cret").get("/").status_code == 200


def test_the_login_cookie_is_not_readable_by_javascript(tmp_path: Path) -> None:
    """A token in a script-readable cookie is a token one XSS away from being stolen."""
    client = _client(tmp_path, "s3cret")
    response = client.post("/api/login", json={"token": "s3cret"})
    assert "httponly" in response.headers["set-cookie"].lower()


def test_the_docs_page_is_gone_when_a_token_is_configured(tmp_path: Path) -> None:
    """FastAPI mounts /api/docs on the app itself, not through the guarded router, so it
    must be disabled outright rather than relying on the router's dependency to reach it.
    A 404 (route does not exist) is chosen over a 401 (route exists but refuses you) so
    that an unauthenticated caller cannot even confirm the route is there."""
    assert _client(tmp_path, "s3cret").get("/api/docs").status_code == 404


def test_the_openapi_schema_is_gone_when_a_token_is_configured(tmp_path: Path) -> None:
    """docs_url=None alone leaves /openapi.json serving the full schema — every path and
    request model, including the login route — to anyone unauthenticated. openapi_url
    must be disabled too."""
    assert _client(tmp_path, "s3cret").get("/openapi.json").status_code == 404


def test_the_docs_page_stays_available_with_no_token_configured(tmp_path: Path) -> None:
    """No token means the deployment is local and unauthenticated by design; docs remain
    a convenience for local use."""
    assert _client(tmp_path, None).get("/api/docs").status_code == 200


def test_the_openapi_schema_stays_available_with_no_token_configured(tmp_path: Path) -> None:
    assert _client(tmp_path, None).get("/openapi.json").status_code == 200
