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
  none of the others. `cli` wires everything.
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

## Pending (open review findings, 2026-09-06)

A second code review of commit d3797d8 (identity-vs-request URL fix) found these. None are
fixed yet. Fix in this order, tests first; 1-4 are confirmed regressions.

1. `audit/page.py:69` broken_links regression: `parsed.links` are now request spellings but
   `status_by_url` is keyed by normalized page ids, so `<a href="/dead/">` to a 404 is never
   reported. Root fix: parser returns (key, request) pairs; audit and crawler consume keys.
2. `crawler/crawler.py:233` robots checked on the normalized key, fetched on the request
   spelling; `Disallow: /private/` allows `/private` but not `/private/`. Also `plan()` checks
   the verbatim seed so dry-run and crawl disagree. Check `policy.allowed(request)`.
3. `urls.py:36` `resolve()` keeps `user:pw@` (uses `parts.netloc`); httpx sends Basic auth
   and the credential URL is stored. Rebuild netloc from hostname+port like `normalize()`.
4. `ai/pipeline.py:192` `prior = violations` runs on both iterations, so a rejected reason
   repeats the repair violations and loses the first attempt's. Snapshot after initial only;
   test with two different violation codes.
5. `parse.py:64` key computed, discarded, recomputed: 3-4 URL parses per anchor (~6.5 s on
   150k links vs ~1.8 s). Same structural fix as 1.
6. `crawler/fetch.py:106` loop check is exact string equality; case/port-only Location and
   A->B->A cycles still burn 10 hops. Need a "wire form" (casefold host, drop default port
   and fragment, keep path/query) compared against current plus every hop in the chain.
7. `crawler/sitemap.py:66` prefix guess tries only the full start path: `/uv/guides/` never
   finds `/uv/sitemap.xml`; `/docs/index.html` guesses under the file. Walk parents
   deepest-first (bounded), skip a trailing segment with an extension, stop on first hit.
8. `audit/service.py:50` `worst_pages` still includes clean pages for export/report/Slack;
   dashboard filters client-side and its `byUrl.get(...) || {issues: []}` fallback renders
   "No issues" for a page that has them. Exclude no-issue pages in `summarize_run`.
9. `cli.py:190` next-step hint prints an unquoted `--db` path (breaks on spaces); also
   `_browser_url` double-brackets `[::1]`.
10. `crawler/frontier.py:14` `request` default re-enables the normalized fetch for any caller
    that omits it. Make it required.

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
