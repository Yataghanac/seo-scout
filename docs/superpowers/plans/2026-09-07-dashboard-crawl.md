# Crawl from the dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A client opens the hosted dashboard, pastes a URL, presses Crawl, and watches the audit appear — with authentication and target safety, and without a terminal.

**Architecture:** `api` never imports `crawler`. `create_app` accepts an injected crawl runner, a shared token and an allowlist; `cli.serve` wires the real runner. Target safety is a pure module composed into the fetcher's existing per-hop `allowed` hook. The crawl runs as an `asyncio` task in the uvicorn process, one at a time.

**Tech Stack:** FastAPI, pydantic v2 / pydantic-settings, sqlite3, httpx, vanilla JS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-07-dashboard-crawl-design.md`

**Two refinements made while writing this plan against the real code:**
1. `resolve_reason()` (the DNS lookup) lives in `api/targets.py` beside the pure functions rather than in a caller. It is the only I/O in the module and is clearly marked; splitting it across modules bought nothing.
2. Startup reconciliation only touches runs **older than `wall_clock_seconds`**. Reconciling every `running` row would kill a CLI crawl that is legitimately in progress against the same database when someone starts `serve`. A run older than the hard wall-clock cap cannot still be running legitimately.

---

### Task 1: Target safety — the pure decisions

**Files:**
- Create: `src/seo_scout/api/targets.py`
- Test: `tests/api/test_targets.py`

- [ ] **Step 1: Write the failing test**

```python
"""Which URLs a browser client may ask this server to fetch."""

from __future__ import annotations

import pytest

from seo_scout.api.targets import blocked_ip, check_url


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # the cloud metadata endpoint
        "0.0.0.0",
        "::1",
        "fd00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_internal_addresses_are_refused(ip: str) -> None:
    assert blocked_ip(ip) is not None


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert blocked_ip(ip) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://intranet/",
        "http://box.local/",
        "http://127.0.0.1:8099/",
        "http://[::1]/",
    ],
)
def test_internal_urls_are_refused(url: str) -> None:
    assert check_url(url) is not None


def test_an_ordinary_public_url_is_allowed() -> None:
    assert check_url("https://example.com/a/b") is None


def test_the_reason_names_the_host() -> None:
    reason = check_url("http://box.local/")
    assert reason is not None and "box.local" in reason


def test_an_allowlist_refuses_everything_else() -> None:
    assert check_url("https://example.com/", ["client.com"]) is not None
    assert check_url("https://client.com/", ["client.com"]) is None


def test_an_allowlist_covers_subdomains_of_the_listed_domain() -> None:
    assert check_url("https://blog.client.com/", ["client.com"]) is None


def test_an_allowlist_does_not_widen_the_address_rules() -> None:
    """The allowlist says which sites, never which addresses may be reached."""
    assert check_url("http://127.0.0.1/", ["127.0.0.1"]) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_targets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'seo_scout.api.targets'`

- [ ] **Step 3: Write the implementation**

```python
"""Which URLs a browser client may ask this server to fetch.

The dashboard lets a stranger type a URL that this process then requests, which is a
server-side request forgery hole unless something says no. `check_url` and `blocked_ip` are
pure; `resolve_reason` is the one function here that does I/O, and it is cached per host
because `allowed` is consulted for every link a crawl enqueues, not only for redirect hops.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from functools import lru_cache
from urllib.parse import urlsplit

from seo_scout.urls import registrable_domain

_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")


def blocked_ip(value: str) -> str | None:
    """A refusal reason for an address a public crawl has no business reaching, else None."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return f"{value} is not an IP address"
    if getattr(ip, "ipv4_mapped", None) is not None:
        return blocked_ip(str(ip.ipv4_mapped))  # ::ffff:127.0.0.1 is loopback
    if ip.is_loopback:
        return f"{value} is a loopback address"
    if ip.is_link_local:
        return f"{value} is a link-local address"
    if ip.is_private:
        return f"{value} is a private address"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return f"{value} is a reserved address"
    return None


def check_url(url: str, allowlist: Sequence[str] = ()) -> str | None:
    """A refusal reason for a URL a client may not ask us to crawl, else None.

    No DNS here: this is the cheap, pure gate. `resolve_reason` does the lookup.
    """
    host = (urlsplit(url).hostname or "").strip().lower()
    if not host:
        return "that URL has no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if allowlist:
            return f"{host} is an address, not one of this deployment's allowed sites"
        return blocked_ip(host)
    if host.endswith(_LOCAL_SUFFIXES):
        return f"{host} is a local network name"
    if "." not in host:
        return f"{host} is a bare hostname, not a public domain"
    if allowlist:
        domain = registrable_domain(url)
        if domain.lower() not in {d.strip().lower() for d in allowlist}:
            return f"{domain or host} is not one of this deployment's allowed sites"
    return None


@lru_cache(maxsize=512)
def resolve_reason(host: str) -> str | None:
    """A refusal reason if any address `host` resolves to is internal, else None.

    Every address is checked, not just the first: a host can publish one public and one
    private record. A host that does not resolve is refused rather than left to the crawler.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return f"{host} does not resolve"
    for info in infos:
        if reason := blocked_ip(str(info[4][0])):
            return reason
    return None


def ok(url: str, allowlist: Sequence[str] = ()) -> bool:
    """The whole gate as one predicate, for composing into the crawler's `allowed` hook."""
    if check_url(url, allowlist) is not None:
        return False
    host = (urlsplit(url).hostname or "").strip().lower()
    return resolve_reason(host) is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_targets.py -q`
Expected: PASS, all cases.

- [ ] **Step 5: Check the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/api/targets.py tests/api/test_targets.py
git commit -m "feat(api): refuse crawl targets that resolve inside the network"
```

---

### Task 2: Configuration — token and allowlist

**Files:**
- Modify: `src/seo_scout/config.py:38-39` (after the Automation block)
- Modify: `.env.example`, `src/seo_scout/env.example` (must stay identical — a test enforces it)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_dashboard_auth_is_off_by_default() -> None:
    settings = Settings()
    assert settings.dashboard_token is None
    assert settings.allowed_domains == []


def test_allowed_domains_parses_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEO_SCOUT_ALLOWED_DOMAINS", "client.com, other.co.uk ")
    assert Settings().allowed_domains == ["client.com", "other.co.uk"]


def test_the_token_is_redacted_in_the_effective_config_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEO_SCOUT_DASHBOARD_TOKEN", "hunter2")
    line = Settings().effective_config_line()
    assert "hunter2" not in line
    assert "dashboard_token=set" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -q -k "dashboard or allowed_domains"`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'dashboard_token'`

- [ ] **Step 3: Write the implementation**

In `src/seo_scout/config.py`, add these imports at the top:

```python
from pydantic import Field, field_validator
```

Add after the `slack_webhook_url` line:

```python
    # Hosted dashboard. Both empty means: no auth, and any public site may be crawled.
    dashboard_token: str | None = None
    allowed_domains: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _split_domains(cls, value: object) -> object:
        """Accept `a.com, b.com` from the environment; pydantic would want JSON."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value
```

Change the `secrets` set in `effective_config_line` to include the token:

```python
        secrets = {"openai_api_key", "slack_webhook_url", "dashboard_token"}
```

- [ ] **Step 4: Add the same two keys to both env templates**

Append to `.env.example` **and** `src/seo_scout/env.example`, identically:

```
# Hosted dashboard only. Empty = no login and no domain restriction.
SEO_SCOUT_DASHBOARD_TOKEN=
SEO_SCOUT_ALLOWED_DOMAINS=
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -q`
Expected: PASS — including `test_packaged_env_template_matches_the_repo_example`.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/config.py .env.example src/seo_scout/env.example tests/test_config.py
git commit -m "feat(config): dashboard token and allowed-domains settings"
```

---

### Task 3: Authentication

**Files:**
- Create: `src/seo_scout/api/auth.py`
- Test: `tests/api/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
"""One shared token. No accounts, so nothing to enumerate and no password to hash."""

from __future__ import annotations

from fastapi.testclient import TestClient

from seo_scout.api.app import create_app
from seo_scout.store import db


def _client(tmp_path: object, token: str | None) -> TestClient:
    path = f"{tmp_path}/t.db"
    db.connect(path).close()
    return TestClient(create_app(path, token=token))


def test_everything_is_open_when_no_token_is_configured(tmp_path: object) -> None:
    assert _client(tmp_path, None).get("/api/runs").status_code == 200


def test_the_api_is_refused_without_the_token(tmp_path: object) -> None:
    assert _client(tmp_path, "s3cret").get("/api/runs").status_code == 401


def test_logging_in_opens_the_api(tmp_path: object) -> None:
    client = _client(tmp_path, "s3cret")
    assert client.post("/api/login", json={"token": "s3cret"}).status_code == 200
    assert client.get("/api/runs").status_code == 200  # cookie carried by the test client


def test_the_wrong_token_is_refused(tmp_path: object) -> None:
    client = _client(tmp_path, "s3cret")
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401


def test_the_dashboard_shell_stays_public(tmp_path: object) -> None:
    """It holds no data, and serving it is what lets the page render a token prompt."""
    assert _client(tmp_path, "s3cret").get("/").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_auth.py -q`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'token'`

- [ ] **Step 3: Write the implementation**

Create `src/seo_scout/api/auth.py`:

```python
"""One shared token, compared in constant time.

Deliberately not user accounts: the README lists those under "Explicitly not built", and a
single secret the operator rotates by editing `.env` is the honest minimum for a dashboard
one client opens. An unset token means the deployment is open, so local use is unchanged.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

COOKIE = "seo_scout_token"
login_router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    token: str


def token_guard(request: Request) -> None:
    """Refuse the request unless it carries the configured token. No token, no guard."""
    expected = request.app.state.token
    if not expected:
        return
    supplied = request.cookies.get(COOKIE) or ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="authentication required")


@login_router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    expected = request.app.state.token
    if not expected or not secrets.compare_digest(body.token, expected):
        raise HTTPException(status_code=401, detail="that token is not right")
    response.set_cookie(COOKIE, expected, httponly=True, samesite="strict")
    return {"status": "ok"}
```

- [ ] **Step 4: Wire it into the app factory**

Replace `src/seo_scout/api/app.py` in full:

```python
"""FastAPI application factory: the JSON API plus the single-file dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from seo_scout import __version__
from seo_scout.api.auth import login_router, token_guard
from seo_scout.api.routes import CrawlRunner, router


def create_app(
    db_path: str,
    *,
    crawl_runner: CrawlRunner | None = None,
    token: str | None = None,
    allowlist: Sequence[str] = (),
) -> FastAPI:
    app = FastAPI(title="SEO Scout", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.db_path = db_path
    app.state.crawl_runner = crawl_runner
    app.state.token = token
    app.state.allowlist = list(allowlist)
    app.include_router(login_router)
    app.include_router(router, dependencies=[Depends(token_guard)])
    index = resources.files("seo_scout.api").joinpath("static/index.html")

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app
```

Note: `CrawlRunner` is defined in Task 4. Do Task 4's Step 3 before running these tests, or
import it temporarily as `object`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS. Existing API tests still pass because every new parameter defaults to off.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/api/auth.py src/seo_scout/api/app.py tests/api/test_auth.py
git commit -m "feat(api): optional shared-token auth for a hosted dashboard"
```

---

### Task 4: The crawl endpoint, against a fake runner

**Files:**
- Modify: `src/seo_scout/api/routes.py`
- Test: `tests/api/test_crawls.py`

- [ ] **Step 1: Write the failing test**

```python
"""Starting a crawl over HTTP. No test crawls anything: the runner is a fake."""

from __future__ import annotations

from fastapi.testclient import TestClient

from seo_scout.api.app import create_app
from seo_scout.store import db


class FakeRunner:
    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.started: list[tuple[str, int | None]] = []

    def start(self, url: str, max_pages: int | None) -> bool:
        if self.busy:
            return False
        self.started.append((url, max_pages))
        return True


def _client(tmp_path: object, runner: object = None, allowlist: tuple[str, ...] = ()) -> TestClient:
    path = f"{tmp_path}/t.db"
    db.connect(path).close()
    return TestClient(create_app(path, crawl_runner=runner, allowlist=allowlist))


def test_a_public_url_starts_a_crawl(tmp_path: object) -> None:
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "https://example.com"})
    assert response.status_code == 202
    assert runner.started == [("https://example.com", None)]


def test_max_pages_is_passed_through(tmp_path: object) -> None:
    runner = FakeRunner()
    _client(tmp_path, runner).post(
        "/api/crawls", json={"url": "https://example.com", "max_pages": 10}
    )
    assert runner.started == [("https://example.com", 10)]


def test_an_internal_target_is_refused_with_a_readable_reason(tmp_path: object) -> None:
    runner = FakeRunner()
    response = _client(tmp_path, runner).post("/api/crawls", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 400
    assert "loopback" in response.json()["detail"]
    assert runner.started == []


def test_a_non_http_url_is_refused(tmp_path: object) -> None:
    response = _client(tmp_path, FakeRunner()).post("/api/crawls", json={"url": "file:///etc"})
    assert response.status_code == 400


def test_a_domain_outside_the_allowlist_is_refused(tmp_path: object) -> None:
    client = _client(tmp_path, FakeRunner(), allowlist=("client.com",))
    assert client.post("/api/crawls", json={"url": "https://example.com"}).status_code == 400


def test_a_second_crawl_is_refused_while_one_runs(tmp_path: object) -> None:
    response = _client(tmp_path, FakeRunner(busy=True)).post(
        "/api/crawls", json={"url": "https://example.com"}
    )
    assert response.status_code == 409


def test_a_read_only_deployment_says_so(tmp_path: object) -> None:
    response = _client(tmp_path, None).post("/api/crawls", json={"url": "https://example.com"})
    assert response.status_code == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_crawls.py -q`
Expected: FAIL — 404 on `/api/crawls`, the route does not exist.

- [ ] **Step 3: Write the implementation**

In `src/seo_scout/api/routes.py`, add to the imports:

```python
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from seo_scout.api import targets
from seo_scout.store import repo_pages
from seo_scout.urls import link_pair
```

Add after the `Conn` definition:

```python
class CrawlRunner(Protocol):
    """What the API needs from whoever can crawl. `api` never imports `crawler`."""

    def start(self, url: str, max_pages: int | None) -> bool:
        """Begin a crawl in the background. False when one is already running."""


class CrawlRequest(BaseModel):
    url: str
    max_pages: int | None = None
```

Add the endpoint at the end of the file:

```python
@router.post("/crawls", status_code=202)
def start_crawl(body: CrawlRequest, request: Request) -> dict[str, str]:
    """Accept a crawl. The run id is not returned: the dashboard polls /api/runs for it."""
    runner: CrawlRunner | None = request.app.state.crawl_runner
    if runner is None:
        raise HTTPException(status_code=501, detail="this deployment cannot start crawls")
    link = link_pair(body.url)
    if link is None:
        raise HTTPException(status_code=400, detail="that is not an http(s) URL")
    allowlist = request.app.state.allowlist
    if reason := targets.check_url(link.url, allowlist):
        raise HTTPException(status_code=400, detail=reason)
    host = urlsplit(link.url).hostname or ""
    if reason := targets.resolve_reason(host):
        raise HTTPException(status_code=400, detail=reason)
    if not runner.start(link.url, body.max_pages):
        raise HTTPException(status_code=409, detail="a crawl is already running")
    return {"status": "started"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS. `test_an_internal_target_is_refused_with_a_readable_reason` proves the gate
runs before the runner is touched.

- [ ] **Step 5: Check the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/api/routes.py tests/api/test_crawls.py
git commit -m "feat(api): POST /api/crawls, gated by target safety and a single-crawl lock"
```

---

### Task 5: Live page count for a running run

**Files:**
- Modify: `src/seo_scout/api/routes.py` (the `list_runs` endpoint)
- Test: `tests/api/test_crawls.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_crawls.py`:

```python
from seo_scout.store import repo_pages, repo_runs


def test_a_running_run_reports_pages_fetched_so_far(tmp_path: object) -> None:
    """`runs.pages` is only written at the end; mid-crawl the dashboard needs the live count."""
    path = f"{tmp_path}/t.db"
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
```

Add to that test file's imports:

```python
from datetime import UTC, datetime

from seo_scout.models import FetchedPage
```

`insert_page(conn, run_id, page: FetchedPage)` takes the model, not keywords —
`tests/api/test_routes.py:21` builds one the same way.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_crawls.py -q -k running_run`
Expected: FAIL — `assert 0 == 3`, because `runs.pages` is still the stored default.

- [ ] **Step 3: Write the implementation**

Replace the `list_runs` endpoint in `src/seo_scout/api/routes.py`:

```python
@router.get("/runs")
def list_runs(conn: Conn) -> list[Run]:
    """A finished run reports its stored page count; a running one reports what it has so far."""
    return [
        run.model_copy(update={"pages": repo_pages.count_pages(conn, run.id)})
        if run.status == "running"
        else run
        for run in repo_runs.list_runs(conn)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seo_scout/api/routes.py tests/api/test_crawls.py
git commit -m "feat(api): report live page count while a run is still going"
```

---

### Task 6: Reconcile runs interrupted by a killed process

**Files:**
- Modify: `src/seo_scout/store/repo_runs.py`
- Modify: `src/seo_scout/api/app.py`
- Test: `tests/store/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_store.py`:

```python
def test_a_long_dead_running_row_is_reconciled(conn: sqlite3.Connection) -> None:
    """A killed process leaves `running` forever, and `previous_run` would diff against it."""
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", run_id))
    conn.commit()
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 1
    run = repo_runs.get_run(conn, run_id)
    assert run is not None and run.status == "failed"
    assert run.error is not None and "interrupted" in run.error


def test_a_crawl_still_inside_the_wall_clock_is_left_alone(conn: sqlite3.Connection) -> None:
    """A CLI crawl may be running against this database right now; do not kill its row."""
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 0
    run = repo_runs.get_run(conn, run_id)
    assert run is not None and run.status == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_store.py -q -k reconcile`
Expected: FAIL — `AttributeError: module 'seo_scout.store.repo_runs' has no attribute 'reconcile_interrupted'`

- [ ] **Step 3: Write the implementation**

Add to `src/seo_scout/store/repo_runs.py`:

```python
def reconcile_interrupted(conn: sqlite3.Connection, *, older_than_seconds: int) -> int:
    """Mark long-dead `running` rows as failed. Returns how many were changed.

    A process killed mid-crawl cannot run its own error handler, so the row would stay
    `running` and `previous_run` would pick it as the baseline for the next diff. Only rows
    older than the wall-clock ceiling are touched: a crawl younger than that may be a CLI run
    in progress against this same database.
    """
    cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE runs SET status = 'failed', finished_at = ?, error = ? "
        "WHERE status = 'running' AND started_at < ?",
        (_now(), "interrupted: the process died before the crawl finished", cutoff),
    )
    conn.commit()
    return int(cur.rowcount)
```

Add to that file's imports whatever is missing from:

```python
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 4: Call it at app startup**

In `src/seo_scout/api/app.py`, add these imports:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from seo_scout.store import db, repo_runs
```

Add before `create_app`, and pass `lifespan=lifespan` to the `FastAPI(...)` call:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conn = db.connect(app.state.db_path)
    try:
        repo_runs.reconcile_interrupted(conn, older_than_seconds=app.state.wall_clock_seconds)
    finally:
        conn.close()
    yield
```

`create_app` gains a parameter with the same default as `Settings`:

```python
    wall_clock_seconds: int = 1800,
```

set as `app.state.wall_clock_seconds = wall_clock_seconds` alongside the other state.

Note: `app.state.db_path` must be assigned before the lifespan runs. FastAPI runs `lifespan`
on startup, after `create_app` returns, so this ordering is already correct.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/store/repo_runs.py src/seo_scout/api/app.py tests/store/test_store.py
git commit -m "feat(store): reconcile runs left running by a killed process"
```

---

### Task 7: The real crawl runner

**Files:**
- Modify: `src/seo_scout/cli.py` (the `serve` command, plus a new runner class)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
async def test_the_runner_refuses_a_second_crawl_while_one_is_running(tmp_path: Path) -> None:
    """One crawl at a time: the lock is what makes the endpoint's 409 true."""
    settings = cli.Settings(db=str(tmp_path / "t.db"))
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_crawl(url: str, max_pages: int | None) -> None:
        started.set()
        await release.wait()

    runner = cli.BackgroundCrawler(settings, _crawl=slow_crawl)
    assert runner.start("https://e.com/", None) is True
    await started.wait()
    assert runner.start("https://e.com/", None) is False
    release.set()
    await runner.wait()
    assert runner.start("https://e.com/", None) is True  # free again
    release.set()
    await runner.wait()
```

Add `import asyncio` to the test file's imports if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k runner_refuses`
Expected: FAIL — `AttributeError: module 'seo_scout.cli' has no attribute 'BackgroundCrawler'`

- [ ] **Step 3: Write the implementation**

Add to `src/seo_scout/cli.py`:

```python
class BackgroundCrawler:
    """Runs one crawl at a time as an asyncio task, so the API can stay read-only in shape.

    The API holds this behind its `CrawlRunner` protocol and never imports the crawler, which
    is what keeps `api` depending on `store` alone.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        allowlist: Sequence[str] = (),
        _crawl: Callable[[str, int | None], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._allowlist = list(allowlist)
        self._crawl = _crawl or self._real_crawl
        self._task: asyncio.Task[None] | None = None

    def start(self, url: str, max_pages: int | None) -> bool:
        if self._task is not None and not self._task.done():
            return False
        self._task = asyncio.create_task(self._crawl(url, max_pages))
        return True

    async def wait(self) -> None:
        """For tests: await the running crawl, swallowing its errors as the task does."""
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _real_crawl(self, url: str, max_pages: int | None) -> None:
        settings, allowlist = self._settings, self._allowlist
        with closing(db.connect(settings.db)) as conn:
            async with _client(settings) as client:
                crawler = Crawler(settings=settings, conn=conn, client=client)
                crawler.target_ok = lambda u: targets.ok(u, allowlist)  # every hop, every link
                result = await crawler.run(url, max_pages=max_pages)
            if result.status != "failed":
                audit_run(conn, result.run_id)
                if settings.openai_api_key:
                    await _enrich(conn, settings, result.run_id)
```

Add to `cli.py` imports:

```python
from collections.abc import Awaitable, Callable, Sequence

from seo_scout.api import targets
```

- [ ] **Step 4: Give the crawler the extra gate**

In `src/seo_scout/crawler/crawler.py`, add to `Crawler.__init__` (after `self._fetcher = ...`):

```python
        # A second gate beside robots.txt, set by whoever built this crawler. The API sets it
        # so a client-supplied URL cannot redirect into the network; the CLI leaves it open.
        self.target_ok: Callable[[str], bool] = lambda _url: True
```

Find where `policy.allowed` is passed to the fetcher and to `_enqueue`, and compose:

```python
    def _allowed(self, policy: RobotsPolicy) -> Allowed:
        return lambda url: policy.allowed(url) and self.target_ok(url)
```

Use `self._allowed(state.policy)` everywhere `state.policy.allowed` was passed. Add
`Callable` to the module's `collections.abc` import.

- [ ] **Step 5: Write the failing test for the composed gate**

Append to `tests/crawler/test_crawler.py`:

```python
@respx.mock
async def test_a_redirect_into_a_blocked_target_is_not_followed(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    """A public page may 302 into the network; the extra gate refuses the hop."""
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(302, headers={"location": "https://e.com/private"})
    )
    settings = Settings(min_delay=0.0, db=":memory:")
    crawler = Crawler(settings=settings, conn=conn, client=client)
    crawler.target_ok = lambda url: "private" not in url
    result = await crawler.run("https://e.com/")
    (page,) = repo_pages.list_pages(conn, result.run_id)
    assert page.error == "disallowed_redirect"
```

Match the existing test file's fixtures and imports. `disallowed_redirect` is confirmed
correct: `fetch.py:166-167` sets exactly that when the `allowed` gate refuses a hop, and it
is the value stored in the page row's `error` column.

- [ ] **Step 6: Wire the runner into `serve`**

In `cli.py`, replace the body of `serve` up to `uvicorn.run` with:

```python
    settings = _settings(None, db_path, None, None)
    _require_db(settings)
    url = _browser_url(host, port)
    typer.echo(f"dashboard: {url}  (db: {settings.db})")
    typer.echo(f"auth: {'on' if settings.dashboard_token else 'off (anyone who can reach this port)'}")
    truststore.inject_into_ssl()
    app = create_app(
        settings.db,
        crawl_runner=BackgroundCrawler(settings, allowlist=settings.allowed_domains),
        token=settings.dashboard_token,
        allowlist=settings.allowed_domains,
        wall_clock_seconds=settings.wall_clock_seconds,
    )
    timer = _open_later(url) if open_browser else None
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
```

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`
Expected: all pass. If `cli.py` exceeds 400 lines, move `BackgroundCrawler` into a new
`src/seo_scout/crawl_runner.py` — `tests/test_module_size.py` enforces the ceiling.

- [ ] **Step 8: Commit**

```bash
git add src/seo_scout/cli.py src/seo_scout/crawler/crawler.py tests/
git commit -m "feat(cli): serve injects a background crawler gated by target safety"
```

---

### Task 8: The dashboard controls

**Files:**
- Modify: `src/seo_scout/api/static/index.html`

- [ ] **Step 1: Add the form to the header**

Immediately after the `<h1>` line, add:

```html
  <form id="crawlbar" autocomplete="off">
    <input id="crawl-url" type="url" placeholder="https://example.com" required>
    <button id="crawl-go" type="submit">Crawl</button>
    <span id="crawl-msg"></span>
  </form>
```

- [ ] **Step 2: Add the behaviour**

Add before the final `init()` call:

```javascript
async function postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  return r.json();
}

$("#crawlbar").onsubmit = async (e) => {
  e.preventDefault();
  const msg = $("#crawl-msg");
  msg.textContent = "";
  try {
    await postJSON("/api/crawls", {url: $("#crawl-url").value.trim()});
    msg.textContent = "started";
    pollWhileRunning();
  } catch (err) {
    msg.textContent = err.message;   // the server's refusal reason, verbatim
  }
};

async function pollWhileRunning() {
  const go = $("#crawl-go");
  for (;;) {
    const runs = await (await fetch("/api/runs")).json();
    const live = runs.find((r) => r.status === "running");
    go.disabled = Boolean(live);
    if (!live) { $("#crawl-msg").textContent = ""; await init(); return; }
    $("#crawl-msg").textContent = `crawling ${live.start_url} — ${live.pages} pages`;
    await new Promise((r) => setTimeout(r, 2000));
  }
}
```

- [ ] **Step 3: Handle a 401 by asking for the token**

Wrap the existing `init()` error handler:

```javascript
init().catch(async (e) => {
  if (!String(e.message).includes("401")) {
    $("#notice").hidden = false;
    $("#notice").textContent = `Could not load data: ${e.message}`;
    return;
  }
  const token = window.prompt("This dashboard is protected. Enter the access token:");
  if (!token) return;
  try { await postJSON("/api/login", {token}); location.reload(); }
  catch { $("#notice").hidden = false; $("#notice").textContent = "That token was not accepted."; }
});
```

For this to fire, the fetch helper `init()` uses must throw on a non-OK response including the
status — check the existing helper and add `if (!r.ok) throw new Error("HTTP " + r.status)` if
it does not already.

- [ ] **Step 4: Style the bar**

Add to the `<style>` block, matching the existing token names:

```css
#crawlbar { display: flex; gap: .5rem; align-items: center; margin-left: auto; }
#crawl-url { min-width: 18rem; }
#crawl-msg { font-size: .85em; opacity: .8; }
```

- [ ] **Step 5: Verify by hand against the fixture site**

```bash
uv run python dev/fixture_site.py
```

In another shell: `uv run seo-scout serve --db fix.db` (create `fix.db` with one CLI crawl
first, since `serve` refuses a database that does not exist). In the browser, paste
`http://127.0.0.1:8099/` and confirm it is **refused** with a loopback message — the CLI can
crawl it, the browser cannot. Then confirm a real public site starts, shows a rising page
count, and selects itself when done.

- [ ] **Step 6: Commit**

```bash
git add src/seo_scout/api/static/index.html
git commit -m "feat(dashboard): paste a URL and crawl, with progress and a token prompt"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`, `DECISIONS.md`, `docs/commercial.md`, `CLAUDE.md`

- [ ] **Step 1: README — a hosted section**

Under Quickstart, add a short "Hosting it for someone else" section covering
`SEO_SCOUT_DASHBOARD_TOKEN`, `SEO_SCOUT_ALLOWED_DOMAINS`, that the crawl box refuses internal
addresses, and the limitations: DNS rebinding undefended, no cancel, one crawl at a time.
Update the sentence that calls the API read-only.

- [ ] **Step 2: Check the README invariant tests still hold**

Run: `uv run pytest tests/test_readme.py -q`
Expected: PASS. If the rule count or caps wording moved, fix the README, not the test.

- [ ] **Step 3: DECISIONS.md — one entry**

Add "Starting a crawl from the dashboard", covering: why the runner is injected rather than
imported (the layering rule holds), why target safety composes into the existing `allowed`
hook, why the allowlist narrows and never widens, why the gate is on the API path only,
why reconciliation uses the wall-clock cutoff, and why cancel is absent.

- [ ] **Step 4: commercial.md — the data table gains a row**

The "What leaves the customer's machine" table needs the hosted case: who can reach the
dashboard, and that the token is the only credential.

- [ ] **Step 5: CLAUDE.md — record the new shape**

Update the architecture line to note that `api` takes an injected crawl runner and never
imports `crawler`, and clear the Pending section.

- [ ] **Step 6: Full gate and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/
git add -A
git commit -m "docs: hosting the dashboard, and the decisions behind the crawl button"
git push
```

---

## Self-review

**Spec coverage.** Injected seam → Task 3/4/7. `targets.py` → Task 1. Redirect-hop composition
→ Task 7 steps 4–5. Endpoints → Tasks 3, 4, 5. Concurrency lock → Tasks 4, 7. Startup
reconciliation → Task 6. Dashboard → Task 8. Configuration → Task 2. Testing → in every task.
Known limitations → Task 9. No section is unimplemented.

**Type consistency.** `CrawlRunner.start(url, max_pages) -> bool` is defined in Task 4 and
implemented identically by `FakeRunner` (Task 4) and `BackgroundCrawler` (Task 7).
`targets.check_url`, `targets.blocked_ip`, `targets.resolve_reason` and `targets.ok` are
defined in Task 1 and used with those names in Tasks 4 and 7. `create_app`'s keyword arguments
(`crawl_runner`, `token`, `allowlist`, `wall_clock_seconds`) match across Tasks 3, 6 and 7.

**Two places the implementer must check rather than trust:** the real signature of
`repo_pages.insert_page` (Task 5 Step 1) and the exact `skipped` value the fetcher records for
a refused redirect hop (Task 7 Step 5). Both are called out in the steps.
