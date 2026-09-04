# SEO Scout — notes for future sessions

Self-hosted crawler + SEO audit + validated GPT-4o title/meta rewrites + dashboard.
Portfolio project; every design decision must be explainable (see DECISIONS.md).

## Commands

```bash
uv sync                                  # install (set UV_SYSTEM_CERTS=1 on this machine)
uv run seo-scout crawl https://site --max-pages 50
uv run seo-scout serve                   # dashboard on :8000
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
- Logging: JSON lines to stderr with `run_id`; `--verbose` for per-URL debug.

## AI layer invariants

- `ai/validate.py` is the gate: nothing the model returns is stored as a suggestion unless
  `validate()` returns no violations. One repair attempt, then `rejected` with the reason.
- Page text is untrusted: `ai/sanitize.py` strips instruction-like text and fences the rest.
- Cache key = sha256(url | title | meta | body[:2000] | model | PROMPT_VERSION). Bump
  `PROMPT_VERSION` in `ai/prompt.py` to invalidate deliberately.
- Budget is checked *before* every call with a tiktoken estimate; actual usage is recorded.
- Tests never touch the network: `tests/ai/fakes.py` scripts the model; respx mocks the SDK.

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
- [ ] 6 docs
