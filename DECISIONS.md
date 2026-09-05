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

## Phase 5 — Diffing and automation

**What it does.** `seo-scout diff a b` compares two runs of the same site: pages added and
removed, per-page score changes, issues fixed and issues introduced, as a terminal table or
JSON. `seo-scout report <url>` is the scheduler entry point: crawl, find the previous run of
that site, diff, write `reports/run-N.json` and `.md`, and post a one-line summary to Slack if
`SLACK_WEBHOOK_URL` is set.

**Non-obvious decision.** A diff compares rule ids per page, not issue messages. Messages
contain counts and quoted text ("Title is used on 3 pages") that change for reasons unrelated
to the page itself, so comparing them would report churn instead of fixes. Rule ids are stable
identities: a rule that fired last week and not this week is a fix, full stop. Pages that
appear or disappear are reported separately and never counted as fixed or introduced issues.

**What I chose not to do.** No `--schedule` daemon. A process that sleeps for a week is a
process that dies for a week; cron and Windows Task Scheduler already do this job well, so
the README gives one line for each. No Slack SDK either: an incoming webhook is one HTTP POST,
and a failed post is logged and reported, never allowed to fail the report.

**Failure mode prevented.** Automation that silently drifts. The report command is
idempotent per run, exits non-zero only when the crawl itself failed, and the very first run
of a site writes a baseline report rather than erroring on "nothing to compare", so a scheduler
can be pointed at a new site with no manual bootstrapping.

## Phase 6 — Docs and verification

**What it does.** README with a four-command quickstart, a mermaid architecture diagram, the
crawling policy, the AI validation design and its rationale, cost characteristics, cron and
Task Scheduler one-liners, and an honest limitations list. The definition-of-done crawl
(`crawl https://books.toscrape.com --max-pages 50`) ran clean: 50 pages in 26 s, 187 issues,
AI stage skipped with one warning because no key was configured.

**Non-obvious decision.** The README leads with what the validator rejects and why, not with
features. For a portfolio piece the reviewer's first question is "how do you know the model's
output is right?", and the answer has to be visible without opening the code. The limitations
section is written to be quoted back at me: no JS rendering, English-calibrated heuristics,
single host, a heuristic score, a truncation check that can false-positive. Naming them is
cheaper than being asked about them.

**What I chose not to do.** No `--schedule` flag, no Docker, no hosted demo. Two scheduler
one-liners cover the automation story on every OS the tool runs on, and a screenshot of a real
crawl covers the demo. The dashboard grew two query-string hooks (`?theme=`, `?page=`) purely
so the screenshot is reproducible from one headless-browser command that lives in CLAUDE.md.

**Failure mode prevented.** Documentation drifting from behaviour. The quickstart commands,
the sample output block and the cost table are the literal output and arithmetic of the code as
committed; the CLAUDE.md "pending" list records the two things that need a human (a GitHub
token scope and an API key) so the next session does not rediscover them.

## Post-launch — Redirect targets are requested verbatim

**What happened.** The first crawl of `peps.python.org` recorded 17 of 20 pages as
`too_many_redirects` after a single 301. The server redirects `/pep-0008` to `/pep-0008/`;
the fetcher ran the `Location` header through `normalize()`, which collapses trailing slashes
for frontier dedupe, and so requested `/pep-0008` again, ten times.

**Root cause, not symptom.** `normalize()` answers "is this the same page?"; it was being asked
"what should I request next?". Those are different questions. The fix asks the right one:
`fetch()` now resolves `Location` with plain `urljoin()` and requests exactly what the server
named. `final_url` therefore keeps the server's spelling, which is also the correct base for
resolving the page's relative links. The one downstream consumer that needs identity, the
frontier's `mark_seen`, normalizes on its side.

**Failure mode prevented.** Any site that canonicalises with a trailing slash (GitHub Pages,
most static hosts, Django's `APPEND_SLASH`) was invisible to the audit beyond its home page.
The regression tests pin both halves: the fetcher follows `/a -> /a/`, and the crawler treats
a later link to `/a` as the page it already has.

## Post-launch — Repaired suggestions keep the first attempt's violations

**What happened.** The first text-heavy live run (`peps.python.org`) triggered five repairs.
The dashboard could say *that* a repair happened but not *why*: `reason` was only written on
rejection, so the evidence of what the model got wrong the first time was thrown away the
moment the second answer passed.

**Decision.** A `repaired` outcome now carries `"first attempt failed validation:"` plus the
same violation summary the model was shown. It rides the existing `reason` column, the cache
row and the CSV export unchanged; the dashboard renders it in the warning colour instead of
the rejection red so a repaired page does not read as a failure.

**Why it matters for the design.** The validator is the argument for the whole AI layer. A
reviewer should be able to open any repaired page and see the exact check that caught the
first answer, which is the difference between "we validate" and "here is the validation
working". It also gives a cheap signal for prompt tuning: if the same code dominates the
repair reasons, the prompt, not the validator, is what to fix.

## Post-launch — Sitemap discovery also looks under the start path

**What happened.** The first crawl of `docs.astral.sh/uv/` found "1 seed" from a sitemap
that lists 84 pages. That host's robots.txt is an HTML page (no `Sitemap:` line), so discovery
fell back to `/sitemap.xml` at the host root, which is a 404. The real file is
`/uv/sitemap.xml`: MkDocs, Docusaurus and most docs generators write the sitemap at the site's
base path, and a docs host often serves several such sites under one domain.

**Decision.** With no robots hint, try the host root first and then `<start path>/sitemap.xml`
when the start URL has a directory-like path (no dot in its last segment). One extra request at
most, and it is only made when the start URL says the site is not at the root. Robots-declared
sitemaps still take priority and skip both guesses.

**Consequence worth knowing.** Seeds change crawl order. On a capped run the pages you get
are the first N seeds, so a site that gains a sitemap between two runs will show a large
added/removed diff that is churn, not change. The diff report is honest about it either way;
this note exists so nobody reads that churn as a regression.

## Post-launch — Ease-of-use pass and commercial footing

**What changed.** `seo-scout init` writes `.env` from the template (never overwrites);
`serve --open` launches the browser; `crawl` and `report` end with the next command to run.
The dashboard gained a *Start here* list (five weakest pages, each with its headline issue),
a legend for the severity pips, a dismissable one-line "how to read this" strip, and copy
buttons on every validated title and meta. README leads with three commands and what the
reader will see; the rationale moved below. A `LICENSE` (MIT) and `docs/commercial.md` were
added. Design note: `docs/superpowers/specs/2026-09-05-ease-of-use-design.md`.

**Why these and not more.** Each one removes a specific question a first-time user asks:
"where do I start", "what do the coloured dots mean", "how do I get this text into the
CMS", "what do I run next". None adds a dependency, an API call, or a build step; the
dashboard is still one static file and the CLI is still one module under 400 lines.

**Commercial decision.** The code stays public under MIT. The saleable things are setup,
hosting, adaptation and support, and `docs/commercial.md` exists so a buyer's first three
questions (what leaves my machine, what does it cost, what will it not do) are answered in
writing before the demo. Selling source licences for code already public under MIT would
not survive a customer's lawyer, so that path was closed deliberately.

## Post-launch — Install path verified from the wheel and from GitHub

**What was checked.** `uv build` produces a wheel that contains the dashboard HTML, the
SQL schema and the LICENSE. `uv tool install` from that wheel, and directly from
`git+https://github.com/Yataghanac/seo-scout`, gives a `seo-scout` command that runs `init`,
`crawl` and `serve` end to end with no source checkout. `init` writes a minimal `.env` when
no template is present, which is the tool-install case.

**What it changed.** The README quickstart now leads with the tool install, since that is
what a customer types; the clone-and-`uv run` path moved under Development. The install line
pins `--python 3.12` because on a machine whose default Python is older uv otherwise refuses
with a resolution error, which is the first thing a non-developer would hit. `[project.urls]`
was added so the package page, when it is published, links back to the repo and to this file.
No PyPI publish yet: a GitHub install needs no account and no release process.
