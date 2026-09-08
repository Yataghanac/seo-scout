# SEO Scout — notes for future sessions

Self-hosted crawler + SEO audit + validated GPT-4o title/meta rewrites + dashboard.
Portfolio project; every design decision must be explainable (see DECISIONS.md).

## Commands

```bash
uv sync                                  # install (set UV_SYSTEM_CERTS=1 on this machine)
uv run seo-scout crawl https://site --max-pages 50
uv run seo-scout init                    # .env from template (never overwrites)
uv run seo-scout serve --open            # dashboard on :8000, opens the browser
uv run seo-scout diff 1 2 [--json]       # compare two runs
uv run seo-scout report https://site     # crawl + diff vs last run + reports/ + Slack
uv run pytest                            # offline, < 15 s
uv run pytest --cov=seo_scout.audit --cov=seo_scout.ai --cov-report=term-missing
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/
```

## Stack (fixed, do not substitute)

Python 3.12+, httpx, selectolax, protego, sqlite3 (no ORM), openai (Structured Outputs),
tiktoken, pydantic v2 + pydantic-settings, typer, FastAPI + uvicorn, one static HTML dashboard
with Chart.js from CDN. Tests: pytest + pytest-asyncio + respx; a fake OpenAI client.
Additions: tldextract (registrable-domain check), defusedxml (untrusted sitemap XML),
truststore (OS certificate store for corporate TLS). No LangChain/Scrapy/Celery/Docker.

## Architecture rules

- `src/seo_scout/{crawler,audit,ai,store,api,diff}` + `cli.py`, `config.py`, `logging.py`, `models.py`
- Layers point inward: `crawler`→`store`; `audit`→`models` only; `ai`→`models`,`store`;
  `api`→`store` (+ rule metadata from `audit.registry`); `diff`→`store`; `store` imports
  none of the others. `api` never imports `crawler`, even to let the dashboard start a crawl:
  `create_app` takes an optional `crawl_runner` (a `Protocol`, `api/routes.py`) and the real
  implementation is injected from outside, the same seam already used for the OpenAI
  `Completer` and the crawler's `sleep`. `cli.py` and `crawl_runner.py` are the two wiring
  modules — `cli` wires the whole app together; `crawl_runner.BackgroundCrawler` is the one
  piece of `crawler`-importing code the API is ever handed, so `cli.serve` builds it and
  passes it in rather than `api` reaching for it.
- I/O at the edges. Rules and validators are pure functions; `audit/service.py` is the
  only audit module that touches the database.
- Every function < 50 lines (ruff `max-statements=40`), every module < 400 lines (tested).
- Config: one `Settings` object in `config.py`; secrets only via env / `.env`.
  `.env.example` and `src/seo_scout/env.example` must stay identical (a test enforces it);
  `seo-scout init` writes the packaged copy.
- URLs: `urls.normalize()` is a page's identity (dedupe, storage, audit keys);
  `urls.resolve()` is what gets fetched. Never request the normalized form: sites that
  canonicalise with a trailing slash would answer with a redirect the crawler caused.
- Logging: JSON lines to stderr with `run_id`; `--verbose` for per-URL debug.

## AI layer invariants

- `ai/validate.py` is the gate: nothing the model returns is stored as a suggestion unless
  `validate()` returns no violations. One repair attempt, then `rejected` with the reason.
- Page text is untrusted: `ai/sanitize.py` strips instruction-like text and fences the rest.
- Cache key = sha256(url | title | meta | body[:2000] | model | PROMPT_VERSION). Bump
  `PROMPT_VERSION` in `ai/prompt.py` to invalidate deliberately.
- Budget is checked *before* every call with a tiktoken estimate; actual usage is recorded.
- Tests never touch the network: `tests/ai/fakes.py` scripts the model; respx mocks the SDK.

## Regenerating the README screenshot

Serve the project DB, then capture with headless Chrome/Edge (dark theme, first page open):

```bash
uv run seo-scout serve &
chrome --headless --hide-scrollbars --window-size=1440,1750 --virtual-time-budget=8000 \
  --screenshot=docs/screenshot.png "http://127.0.0.1:8000/?run=1&theme=dark&still=1&size=6&page=first"
```

Query hooks: `?theme=dark|light`, `?still=1` (no animation), `?size=N`, `?run=<id>`,
`?page=first|<url>`.

## Pending

The Enter-key question is as settled as this machine can settle it (2026-09-08). The **crawl
bar is confirmed**: typing a URL and pressing Enter fires the `keydown` listener, posts
`/api/crawls` and shows the gate's answer — `127.0.0.1 is a loopback address` — so one
keystroke exercised both the listener and the SSRF gate. The **sign-in field is confirmed up
to the browser's own boundary**: its `submit` handler runs (wrong token → "That token was not
accepted."; right token → the dashboard loads). What no automation here could do is press
Enter for real. Browser automation dispatches `keydown` and `keyup` only, with `code=""` and
`which=0`, and never the `keypress` Chrome uses to decide implicit form submission — measured
on the page, not assumed; a real Chrome could not reach the server to try. What remains is a
standard `<form>` with a submit button and a handler proven to work, which is precisely what
implicit submission triggers. Thirty seconds at a real keyboard would close the last inch.

Nothing else is open. The four review passes of 2026-09-06, the six live-run findings of 2026-09-07,
and the dashboard-crawl feature (`POST /api/crawls`, the SSRF gate in `api/targets.py`,
shared-token auth, startup reconciliation) are fixed and recorded in DECISIONS.md.

The pipeline has been exercised end to end against live sites on the current code: crawl,
audit, gpt-4o rewrites, a second run served entirely from the cache, diff, report files and
the dashboard, the JSON API and both exports. Reading commands (`serve`, `audit`, `ai`,
`diff`) refuse a `--db` that does not exist; only `crawl` and `report` create one. Untrusted
page text now has three guards, one per sink: `ai/sanitize.py` for the model, `esc()` in the
dashboard for HTML, `api/export.as_text()` for spreadsheet formulas.

All 25 audit rules have fired on a live crawl. `dev/fixture_site.py` serves one broken
page per rule over real HTTP (plus a two-hop redirect, a 404 target, a sitemap orphan and two
oversize responses) and fires 22 of them in one crawl; the suite mocks transport with respx,
and that fixture exercises what respx replaces. Run by hand, never from pytest — tests stay
offline. See DECISIONS.md, "Every rule, against a real socket".

The three guards have been exercised for real: `--max-cost` refuses the first call and spends
$0.0000 when the cap is below the estimate, `report` posts to Slack once (never on a baseline
run) and exits 0 even when the webhook is unreachable, and `init` refuses to overwrite an
existing `.env`.

## Workflow

Each phase: tests first → green → ruff/mypy clean → DECISIONS.md entry → one conventional
commit → push → CI green. Zero network calls in tests.

## Phase status

- [x] 0 scaffold
- [x] 1 crawler
- [x] 2 audit engine
- [x] 3 AI layer
- [x] 4 dashboard + export
- [x] 5 diff + automation
- [x] 6 docs
