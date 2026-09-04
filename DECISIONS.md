# Design decisions

One entry per phase. Written so a non-author can defend the design on camera.

## Phase 0 — Scaffold

**What it does.** Sets up the package layout, config, JSON logging, shared models, the
quality gate (ruff, mypy --strict, pytest) and CI, before any feature code exists.

**Non-obvious decision.** Configuration is a single pydantic-settings object with a printed,
redacted "effective config" line. Every knob has a validated default and a ceiling, so a typo
in `.env` fails at startup rather than mid-crawl, and the crawl caps can be lowered by flags
but never raised past the hard limits.

**What I chose not to do.** No structlog. Stdlib `logging` plus a 30-line JSON formatter and a
`contextvars` run_id gives the same result with one fewer dependency to explain.

**Failure mode prevented.** The 400-lines-per-module rule is enforced by a test, and the
50-lines-per-function rule by ruff's statement count, so the architecture constraints from the
brief cannot quietly erode as phases are added.

## Phase 1 — Crawler

**What it does.** Async breadth-first crawl of one site: robots.txt and sitemaps first, then
pages, with per-host rate limiting, hard caps, manual redirect following, and every page
written to SQLite the moment it is fetched.

**Non-obvious decision.** Redirects are followed by hand (`follow_redirects=False`) so the
full chain is recorded, and off-site redirects stop the chain instead of following it. Just as
deliberately, an unreadable robots.txt (5xx or network failure) disallows everything and the
run is marked `failed`, while a missing one (404) allows everything. That is the conservative
reading Google documents, and it means a TLS or proxy problem can never turn into an
accidental unpoliced crawl.

**What I chose not to do.** No worker pool with queues and locks. A single loop keeps at most
`max_concurrency` tasks in flight with `asyncio.wait`, so the concurrency cap is structural
and there is nothing to get out of sync. No async SQLite driver either: writes are
sub-millisecond and one connection on the event loop is simpler to reason about than a thread
pool.

**Failure mode prevented.** The network dying mid-crawl. A single failed URL is recorded as a
status-0 page and the crawl continues; three consecutive transport failures abort the run as
`partial` with the pages so far intact, and Ctrl+C does the same. There is no state that
exists only in memory.

## Phase 2 — Deterministic audit engine

**What it does.** Runs 24 pure rules over every HTML page of a run, scores each page out of
100, and rolls the results up into a site summary. Works with no OpenAI key at all; this is
the layer that always produces value.

**Non-obvious decision.** A rule is a plain function `(page, context) -> list[str]` registered
with a decorator that carries its id, severity and a one-paragraph explanation. Rules never
see the database or the network: cross-page facts (duplicate titles, inbound links, sitemap
membership, link-target status codes) are computed once into a `CrawlContext` and handed in.
That is what makes the table-driven fixture tests possible: one HTML file, one exact set of
expected rule ids, no mocking.

**What I chose not to do.** No rule engine, plugin loader or YAML configuration. Adding a rule
is adding a function; the registry rejects duplicate ids and a test asserts every registered
rule is covered by a fixture or a dedicated test. Scores are a flat weighted sum
(critical 15, warning 5, notice 2, floored at 0) rather than a tuned model, because a score you
can recompute in your head is one you can defend.

**Failure mode prevented.** Re-running the audit is idempotent: issues and scores for a run
are replaced in one transaction, so a crash mid-audit can never leave half of the old results
mixed with half of the new ones. Pages without an HTML body (404s, binaries, network
failures) are excluded from scoring instead of being punished for content they never had.
