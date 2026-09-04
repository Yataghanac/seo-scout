# SEO Scout — notes for future sessions

Self-hosted crawler + SEO audit + validated GPT-4o title/meta rewrites + dashboard.
Portfolio project; every design decision must be explainable (see DECISIONS.md).

## Commands

```bash
uv sync                                  # install (set UV_SYSTEM_CERTS=1 on this machine)
uv run seo-scout crawl https://site --max-pages 50
uv run seo-scout serve                   # dashboard on :8000
uv run pytest                            # offline, < 15 s
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/
```

## Stack (fixed, do not substitute)

Python 3.12+, httpx, selectolax, protego, sqlite3 (no ORM), openai (Structured Outputs),
tiktoken, pydantic v2 + pydantic-settings, typer, FastAPI + uvicorn, one static HTML dashboard
with Chart.js from CDN. Tests: pytest + pytest-asyncio + respx; a fake OpenAI client.
tldextract is the one addition (registrable-domain check). No LangChain/Scrapy/Celery/Docker.

## Architecture rules

- `src/seo_scout/{crawler,audit,ai,store,api,diff}` + `cli.py`, `config.py`, `logging.py`, `models.py`
- Layers point inward: `crawler`→`store`; `audit`→`models` only; `ai`→`models`,`store`;
  `api`/`diff`→`store`; `store` imports none of the others. `cli` wires everything.
- I/O at the edges. Rules and validators are pure functions.
- Every function < 50 lines (ruff `max-statements=40`), every module < 400 lines (tested).
- Config: one `Settings` object in `config.py`; secrets only via env / `.env`.
- Logging: JSON lines to stderr with `run_id`; `--verbose` for per-URL debug.

## Workflow

Each phase: tests first → green → ruff/mypy clean → DECISIONS.md entry → one conventional
commit → push → CI green. Zero network calls in tests.

## Phase status

- [x] 0 scaffold
- [ ] 1 crawler
- [ ] 2 audit engine
- [ ] 3 AI layer
- [ ] 4 dashboard + export
- [ ] 5 diff + automation
- [ ] 6 docs
