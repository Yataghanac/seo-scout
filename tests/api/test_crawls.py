"""Starting a crawl over HTTP. No test crawls anything: the runner is a fake."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seo_scout.api import targets
from seo_scout.api.app import create_app
from seo_scout.models import FetchedPage
from seo_scout.store import db, repo_pages, repo_runs


class FakeRunner:
    """Stands in for the real crawler. Records what it was asked to do."""

    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.started: list[tuple[str, int | None]] = []

    def start(self, url: str, max_pages: int | None) -> bool:
        if self.busy:
            return False
        self.started.append((url, max_pages))
        return True


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every public host in these tests resolves, publicly. Nothing touches the network."""
    targets.resolve_reason.cache_clear()
    monkeypatch.setattr(targets, "resolve_reason", lambda host: None)


def _client(tmp_path: Path, runner: object = None, allowlist: tuple[str, ...] = ()) -> TestClient:
    path = str(tmp_path / "t.db")
    db.connect(path).close()
    return TestClient(create_app(path, crawl_runner=runner, allowlist=allowlist))


def test_a_public_url_starts_a_crawl(tmp_path: Path) -> None:
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "https://example.com"})
    assert response.status_code == 202
    # link_pair()'s request spelling always carries a path; a bare host gets "/".
    assert runner.started == [("https://example.com/", None)]


def test_max_pages_is_passed_through(tmp_path: Path) -> None:
    runner = FakeRunner()
    _client(tmp_path, runner).post(
        "/api/crawls", json={"url": "https://example.com", "max_pages": 10}
    )
    assert runner.started == [("https://example.com/", 10)]


def test_an_internal_target_is_refused_with_a_readable_reason(tmp_path: Path) -> None:
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 400
    assert "loopback" in response.json()["detail"]
    assert runner.started == []


def test_a_non_http_url_is_refused(tmp_path: Path) -> None:
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert runner.started == []


def test_a_domain_outside_the_allowlist_is_refused(tmp_path: Path) -> None:
    runner = FakeRunner()
    client = _client(tmp_path, runner, allowlist=("client.com",))
    assert client.post("/api/crawls", json={"url": "https://example.com"}).status_code == 400
    assert runner.started == []


def test_a_domain_inside_the_allowlist_is_accepted(tmp_path: Path) -> None:
    runner = FakeRunner()
    client = _client(tmp_path, runner, allowlist=("client.com",))
    assert client.post("/api/crawls", json={"url": "https://client.com"}).status_code == 202


def test_a_second_crawl_is_refused_while_one_runs(tmp_path: Path) -> None:
    response = _client(tmp_path, FakeRunner(busy=True)).post(
        "/api/crawls", json={"url": "https://example.com"}
    )
    assert response.status_code == 409


def test_a_read_only_deployment_says_so(tmp_path: Path) -> None:
    response = _client(tmp_path, None).post("/api/crawls", json={"url": "https://example.com"})
    assert response.status_code == 501


def test_a_host_that_does_not_resolve_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DNS gate is consulted, not just the pure check."""
    monkeypatch.setattr(targets, "resolve_reason", lambda host: f"{host} does not resolve")
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "https://nope.example"})
    assert response.status_code == 400
    assert "does not resolve" in response.json()["detail"]
    assert runner.started == []


def test_a_running_run_reports_pages_fetched_so_far(tmp_path: Path) -> None:
    """`runs.pages` is only written at the end; mid-crawl the dashboard needs the live count."""
    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    for n in range(3):
        repo_pages.insert_page(
            conn,
            run_id,
            FetchedPage(
                url=f"https://e.com/{n}",
                final_url=f"https://e.com/{n}",
                status=200,
                depth=0,
                content_type="text/html",
                bytes=10,
                elapsed_ms=1,
                fetched_at=datetime.now(UTC),
                html="<p>x</p>",
            ),
        )
    conn.close()
    runs = TestClient(create_app(path)).get("/api/runs").json()
    assert runs[0]["status"] == "running"
    assert runs[0]["pages"] == 3


def test_a_finished_run_reports_its_stored_count(tmp_path: Path) -> None:
    """Only running rows are overlaid; a finished run's stored total must be left alone."""
    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    repo_runs.finish_run(conn, run_id, "complete", pages=7)
    conn.close()
    runs = TestClient(create_app(path)).get("/api/runs").json()
    assert runs[0]["pages"] == 7
