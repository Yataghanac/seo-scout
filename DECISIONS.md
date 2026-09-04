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

## Phase 3 — The AI layer

**What it does.** For pages whose title or meta description failed a deterministic rule, asks
GPT-4o for a rewritten title, meta description and a one-line diagnosis, using Structured
Outputs with a strict JSON schema. Every answer is checked by `ai/validate.py` before it is
stored; the model gets exactly one repair attempt with the specific violations listed, and a
second failure is recorded as `rejected` with the reason. Unchanged pages are served from a
content-hash cache at zero cost, and a pre-flight token estimate stops the run before it can
exceed `--max-cost`.

**Non-obvious decision.** The validator is deliberately dumb. Lengths are counted, "unchanged"
is byte equality, "invented facts" means a number, price, year, percentage or claim phrase in
the proposal that does not appear anywhere in the page text, and "truncated" is a punctuation
and last-word check. No second model call judges the first. That makes every rejection
explainable in one sentence and reproducible in a unit test, which is worth more here than
catching subtler hallucinations. The last-word heuristic (a final word that is not on the page
but is a prefix of a word that is) will occasionally flag a legitimate title; the cost of that
is one repair call, whereas the cost of the opposite mistake is showing a cut-off title as a
recommendation.

**What I chose not to do.** No LangChain, no agent loop, no retries beyond one repair, no
embeddings. Prompt-injection defense is a regex strip plus explicit delimiters plus a system
prompt that names page text as data; it is not a classifier, because the validator is the real
backstop: an injected instruction can at worst produce an off-task suggestion, and an off-task
suggestion is rejected on the same grounds as any other.

**Failure mode prevented.** Partial or unvalidated writes. Each page's API calls and its
suggestion row are committed in one transaction, and the suggestion columns are only ever
populated from a validated object. Missing key, bad key, rate limit, timeout and outage all
map to one `AIUnavailable` path that marks remaining pages `unavailable`, prints one warning,
and leaves the deterministic audit untouched. Cost is never a surprise: the budget gate runs
before each call, the run total is printed, and re-running on an unchanged site costs $0.

## Phase 4 — Dashboard and export

**What it does.** `seo-scout serve` runs FastAPI with four JSON endpoints (runs, summary,
paginated/filterable pages, CSV and JSON export) and one static HTML page that renders a score
histogram, an issues-by-rule chart, a sortable page table, and a before/after panel showing
the original and proposed title and meta with the validator's verdict and reason.

**Non-obvious decision.** Filtering, sorting and pagination happen in Python over one query's
rows, not in SQL. A run is hard-capped at 500 pages, so the whole result set is a few hundred
small objects; pushing `WHERE severity = ?` into the repository layer would have leaked API
concerns into `store` for no measurable gain. The dashboard fetches all pages once and does
its own filtering client-side for the same reason, so every filter change is instant.

**What I chose not to do.** No frontend framework, no build step, no bundler: one HTML file,
vanilla JS, Chart.js from a pinned CDN URL with a Subresource Integrity hash. The recording
needs the data to be legible, not the framework to be impressive. Each request opens and
closes its own SQLite connection instead of sharing a pool; at this scale the connection
cost is microseconds and the isolation removes a whole class of threading bugs.

**Failure mode prevented.** Showing an unvalidated suggestion. The API serves suggestion
columns straight from the store, and those columns are only ever written from a validated
object (Phase 3), so the dashboard can render "proposed title" without re-checking anything.
Rejected pages show the exact violations instead of a blank, which is what makes the AI layer
auditable on camera.
