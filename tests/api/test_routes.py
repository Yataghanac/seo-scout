"""Dashboard API over a seeded on-disk database (each request opens its own connection)."""

import csv
import io
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from seo_scout.api.app import create_app
from seo_scout.audit.service import audit_run
from seo_scout.models import FetchedPage, SuggestionRow
from seo_scout.store import db, repo_ai, repo_pages, repo_runs

DB = "dash.db"
WORDS = " ".join(f"w{i}" for i in range(320))


def page(url: str, html: str | None, status: int = 200) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        status=status,
        depth=0,
        content_type="text/html" if html is not None else None,
        bytes=len(html or ""),
        elapsed_ms=10,
        fetched_at=datetime(2026, 9, 4, tzinfo=UTC),
        html=html,
    )


def good(url: str) -> str:
    return (
        '<html lang="en"><head><title>Fresh Roasted Coffee Beans Delivered Weekly</title>'
        '<meta name="description" content="Order freshly roasted single-origin coffee beans, '
        'delivered to your door every week with free tasting notes and brewing guides.">'
        f'<link rel="canonical" href="{url}"><meta property="og:title" content="t">'
        '<meta property="og:description" content="d"></head><body><h1>H</h1>'
        f'<p>{WORDS}</p><a href="/dead">x</a></body></html>'
    )


def seed(conn: sqlite3.Connection) -> int:
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 50})
    repo_pages.insert_page(conn, run_id, page("https://e.com/", good("https://e.com/")))
    repo_pages.insert_page(conn, run_id, page("https://e.com/weak", "<html><body>x</body></html>"))
    repo_pages.insert_page(conn, run_id, page("https://e.com/dead", None, status=404))
    repo_pages.insert_links(conn, run_id, "https://e.com/", ["https://e.com/dead"])
    repo_runs.finish_run(conn, run_id, "complete", pages=3)
    audit_run(conn, run_id)
    with conn:
        repo_ai.record_call(
            conn, run_id, repo_ai.CallRecord("https://e.com/weak", "initial", 1000, 100, 0.0035)
        )
        repo_ai.upsert_suggestion(
            conn,
            run_id,
            SuggestionRow(
                url="https://e.com/weak",
                original_title=None,
                original_meta=None,
                diagnosis=None,
                title=None,
                meta_description=None,
                status="rejected",
                reason="- title: title_too_short (11 characters (minimum 30))",
                cached=False,
                cost_usd=0.0035,
            ),
        )
    return run_id


@pytest.fixture
def client() -> TestClient:
    with db.connect(DB) as conn:
        seed(conn)
        second = repo_runs.create_run(conn, "https://other.test/", {})
        repo_runs.finish_run(conn, second, "partial", pages=0, error="interrupted")
    conn.close()
    return TestClient(create_app(DB))


def test_index_serves_the_dashboard(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SEO Scout" in r.text
    assert "chart.js" in r.text.lower()


@pytest.mark.parametrize("field", ["#crawl-url", "#signin-token"])
def test_enter_is_an_explicit_listener_not_implicit_submission(
    client: TestClient, field: str
) -> None:
    """Both Enter keys are wired by hand rather than left to the browser.

    A bare `<form>` submits on Enter through implicit submission, which browsers decide on
    the `keypress` event -- and automation dispatches `keydown`/`keyup` without it, so that
    path can never be confirmed outside a human's hands. An explicit `keydown` listener can.
    """
    body = client.get("/").text
    listener = f'$("{field}").addEventListener("keydown"'
    line = next((ln for ln in body.splitlines() if listener in ln), None)
    assert line is not None, f"{field} has no keydown listener"
    assert 'e.key === "Enter"' in line


def test_runs_are_listed_newest_first(client: TestClient) -> None:
    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()
    assert [run["id"] for run in runs] == [2, 1]
    assert runs[1]["start_url"] == "https://e.com/"
    assert runs[1]["status"] == "complete"
    assert runs[0]["error"] == "interrupted"


def test_summary_has_scores_issues_ai_and_cost(client: TestClient) -> None:
    r = client.get("/api/runs/1/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["id"] == 1
    assert body["summary"]["pages_audited"] == 2
    assert body["summary"]["issues_by_rule"]["broken_links"] == 1
    assert body["summary"]["issues_by_severity"]["critical"] >= 1
    assert body["ai"]["rejected"] == 1
    assert body["ai"]["calls"] == 1
    assert body["ai"]["usd"] == pytest.approx(0.0035)
    assert body["rules"]["broken_links"]["severity"] == "critical"
    assert body["rules"]["broken_links"]["explanation"]


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/99/summary").status_code == 404
    assert client.get("/api/runs/99/pages").status_code == 404
    assert client.get("/api/runs/99/export.csv").status_code == 404


def test_pages_are_paginated_and_carry_issues_and_ai(client: TestClient) -> None:
    r = client.get("/api/runs/1/pages")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["size"] == 50
    by_url = {p["url"]: p for p in body["items"]}
    home = by_url["https://e.com/"]
    assert home["score"] == 85
    assert [i["rule_id"] for i in home["issues"]] == ["broken_links"]
    assert home["ai"] is None
    weak = by_url["https://e.com/weak"]
    assert weak["ai"]["status"] == "rejected"
    assert "title_too_short" in weak["ai"]["reason"]


def test_pages_filter_by_severity_and_rule(client: TestClient) -> None:
    critical = client.get("/api/runs/1/pages", params={"severity": "critical"}).json()
    assert {p["url"] for p in critical["items"]} == {"https://e.com/", "https://e.com/weak"}
    notice_only = client.get("/api/runs/1/pages", params={"severity": "notice"}).json()
    assert {p["url"] for p in notice_only["items"]} == {"https://e.com/weak"}
    by_rule = client.get("/api/runs/1/pages", params={"rule": "broken_links"}).json()
    assert [p["url"] for p in by_rule["items"]] == ["https://e.com/"]
    assert by_rule["total"] == 1


def test_pages_sort_and_page_bounds(client: TestClient) -> None:
    asc = client.get("/api/runs/1/pages", params={"sort": "score", "order": "asc"}).json()
    assert [p["score"] for p in asc["items"]] == sorted(p["score"] for p in asc["items"])
    desc = client.get("/api/runs/1/pages", params={"sort": "score", "order": "desc"}).json()
    assert desc["items"][0]["score"] >= desc["items"][-1]["score"]
    small = client.get("/api/runs/1/pages", params={"size": 1, "page": 2}).json()
    assert len(small["items"]) == 1 and small["total"] == 2
    beyond = client.get("/api/runs/1/pages", params={"size": 1, "page": 9}).json()
    assert beyond["items"] == []


def test_invalid_filters_are_422(client: TestClient) -> None:
    assert client.get("/api/runs/1/pages", params={"severity": "loud"}).status_code == 422
    assert client.get("/api/runs/1/pages", params={"size": 0}).status_code == 422
    assert client.get("/api/runs/1/pages", params={"sort": "colour"}).status_code == 422


def test_csv_export(client: TestClient) -> None:
    r = client.get("/api/runs/1/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "run-1" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2
    weak = next(row for row in rows if row["url"] == "https://e.com/weak")
    assert weak["ai_status"] == "rejected"
    assert "title_missing" in weak["issues"]
    assert weak["score"] == "64"


def test_json_export(client: TestClient) -> None:
    r = client.get("/api/runs/1/export.json")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"run", "summary", "ai", "pages"}
    assert len(body["pages"]) == 2
    assert body["pages"][0]["issues"]
