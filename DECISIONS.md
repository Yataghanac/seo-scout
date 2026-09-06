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
sitemaps still take priority and skip both guesses. *(Superseded twice since; the fourth
review pass below has the current order: the start path's guesses first, each source with
its own file budget, the host root only as a last resort.)*

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

## Post-launch — Review pass: identity versus request URL, and nine smaller fixes

**Trigger.** A code review of the post-launch commits found that the redirect fix had been
applied one layer too shallow. `fetch()` requested redirect targets verbatim, but the crawler
still requested the *start URL and every link* in `normalize()`'s slash-stripped form. On any
site that canonicalises with a trailing slash that meant two requests per page and a
crawler-manufactured redirect hop on every one: run 4 of `peps.python.org` showed 17 of 20
pages with a 301 the server never asked for, and a site-wide http->https redirect would have
tripped the `redirect_chain` rule on every page.

**Decision.** Two URL forms, two jobs, kept apart everywhere: `urls.normalize()` is the
page's identity (frontier dedupe, `pages.url`, `links`, `sitemap_urls`, audit keys);
`urls.resolve()` is what gets fetched (resolved against the base, fragment dropped, spelling
kept). The parser returns links as written, the frontier carries `(key, depth, request)`,
sitemap seeds keep the sitemap's spelling with normalized keys alongside, and the start URL is
fetched as the user typed it. Re-crawling the PEP site and the uv docs after the change: zero
redirect hops, identical page sets and sitemap counts.

**Also fixed from the same review.** A fragment-only `Location` (`#top`) looped ten
rate-limited requests; it now stops after one. The path-prefix sitemap guess collapsed to the
host root when the start URL carried a query string, skipped versioned prefixes like `/3.12/`,
and was never tried when robots.txt named a sitemap; it is now built from URL parts and
always tried alongside. `serve --open` opened a browser tab even when the port was taken, and
built the URL from the bind address (`0.0.0.0`, `::`); the timer is now cancelled when uvicorn
exits and the URL is a connectable loopback form. The next-step hint dropped `--db` and landed
in cron mail from `report`; it now carries the database path and `report` prints it only on a
terminal. *Start here* listed clean pages and ranked ties differently from the export's
`worst_pages`; it now renders from the server's ranking and only pages with issues, and its
narrow-screen rule was moved after the base rule it overrides. `init` had a hand-copied
fallback template that had already drifted from `.env.example`; the template is now shipped
inside the package and a test asserts the two files are identical. Repaired suggestions store
the bare violation list rather than a prose sentence, and rejected ones keep both attempts.

## Post-launch — Second review pass: keys and spellings all the way down

**Trigger.** A second review of the identity-versus-request commit found that the split had
been carried through the fetcher and the frontier but not through the parser's consumers.
`ParsedPage.links` held request spellings, so the `broken_links` rule looked up
`https://site/dead/` in a status table keyed by `https://site/dead` and reported nothing;
the crawler checked robots.txt against the key, so `Disallow: /private/` (which permits
`/private`) let `/private/` through; and every anchor was parsed three or four times because
the key was computed, discarded and recomputed (about 6.5 s per 150k links).

**Decision.** The parser is the one place a link's two forms are derived, and it returns
both: `ParsedPage.links` is a list of `Link(key, url)`. `urls.link_pair()` produces the pair
from a single split; `normalize()` and `resolve()` are now its two halves, so they cannot
drift. Consumers pick the half that matches what they compare against: the audit's
`internal_links` and the crawler's frontier use `key`, the fetcher and the robots check use
`url`. `Frontier.add()` requires the request spelling rather than defaulting to the key, so a
future caller cannot quietly reintroduce the normalised request.

**Netloc is rebuilt, never copied.** `resolve()` used to keep `parts.netloc` verbatim, so a
link written as `https://user:pw@host/` would have been requested with Basic auth and stored
with the credential. Both forms now rebuild the netloc from hostname and port (bracketing
IPv6, which `normalize()` had also been mangling), so credentials never reach the wire or the
database.

**Redirect loops compare wire forms.** The loop check was string equality against the
current URL, so `Location: HTTPS://HOST/a`, `:443`, or an A->B->A cycle each burned ten
rate-limited requests. `fetch()` now keeps the wire form (`resolve()`) of every URL requested
in the chain and stops at the first repeat. `/a` and `/a/` remain distinct, as they must.

**Also from the same review.** Sitemap discovery walks the start path's directories
deepest-first (three at most, a trailing file segment skipped) and stops at the first that
answers, so `/uv/guides/` finds `/uv/sitemap.xml` and `/docs/index.html` does not look under
the file. `summarize_run` excludes issue-free pages from `worst_pages`, so the export, the
report and Slack agree with the dashboard that it is a to-do list; the dashboard's fallback
for a page outside its loaded list now shows the issue count instead of "No issues". A
rejected suggestion's reason kept the repair violations twice; the first attempt's are now
snapshotted once. The next-step hint quotes a database path containing whitespace, and
`_browser_url` no longer double-brackets an already-bracketed IPv6 host.

## Post-launch — Third review pass: nothing a page serves may abort a crawl

**Trigger.** A third review of the previous fix found two ways one hostile page could end a
whole run: an anchor like `<a href="https://[::1/x">` raised `ValueError` out of the parser
(the `urljoin` call sat outside the `try` that guarded `urlsplit`), and a `Location:
javascript:void(0)` raised `httpx.InvalidURL`, which is not a `TransportError`, so the retry
loop let it escape. Either left the run marked `running` forever and every later audit and AI
pass of that run failed with it. The same review found robots.txt enforced only at enqueue,
so `/go` -> 301 `/private/` fetched and stored a disallowed page, and four regressions in the
sitemap prefix walk that the previous pass had introduced.

**Decision.** The parser and the fetcher classify; they never raise for something a site
sent. `link_pair()` parses inside one `try` and returns None for anything the standard
library rejects, so a bad anchor is one dropped link. `_request()` catches `InvalidURL` and
reports `bad_redirect`; httpx has already discarded the response by then, so the row keeps
status 0 (the same value a failed request gets) with the reason alongside *(the fourth pass
recovers the real status through a response hook)*. The `skipped`
vocabulary now distinguishes `redirect_loop` (a hop already in the chain), `too_many_redirects`
(ten distinct hops), `bad_redirect` (a Location no crawler can follow, `ftp://` included) and
`off_site_redirect`, so the dashboard's error column says which one happened.

**One gate for every request.** `Fetcher.fetch()` takes an `allowed` callable and asks it
before each redirect hop; the crawler passes `policy.allowed`, the same function that gates
`plan()` and the frontier. A redirect into a disallowed path stops with
`disallowed_redirect`, the disallowed URL recorded as `final_url` and never requested. The
frontier no longer repeats the same-site check `_process` already made, and seeds arrive as
`Link(key, url)` pairs so `run()` stops re-normalizing them.

**Sitemap walk, corrected.** The prefix guesses run before the robots.txt sitemaps again:
a large index could exhaust the 50-file cap before the start path's own sitemap was ever
requested, and a guess robots.txt already named is now simply skipped as visited rather than
letting the walk continue up to a sibling site. The file-versus-directory heuristic is gone:
`/3.12` and `/index.html` look alike, so `/docs/index.html/sitemap.xml` costs one 404 and the
bound pays for it. A 200 counts as a hit only when the body parsed as a `urlset` or
`sitemapindex`; a soft-404 HTML shell no longer stops the walk empty-handed.

**One shape for a link.** `urls.Link(key, url)` is a NamedTuple returned by `link_pair()`
and carried unchanged by the parser, the sitemap seeds and the crawler, replacing a pydantic
model, a bare tuple and two ad-hoc pairs. `registrable_domain()` caches the public-suffix
lookup per host (`same_site` runs once per link; a crawl asks about a handful of hosts).

**Also from the same review.** Failed fetches store the requested spelling as `final_url`
like every other row. The next-step hint quotes any database path a shell would split on
(`&`, `(`, whitespace), not only whitespace; `$` and `"` are left alone because bash and
PowerShell escape them differently. The dashboard's *Start here* row for a page outside the
loaded list shows the issue count and is not clickable, since there is no detail to open.

## Post-launch — Fourth review pass: a run row never stays `running`

**Trigger.** A review of the third pass found that its invariant ("nothing a page serves may
abort a crawl") held for anchors and for two of the four ways a Location header can be
unfollowable, and nowhere else. httpx wraps a malformed http(s) Location (`https://[::1/x`,
`https://e.com:abc/`) into `RemoteProtocolError`, a transport error, so it was retried three
times with backoff and counted towards the three-strikes abort. A sitemap-index child or a
robots.txt `Sitemap:` line with the same shape raised straight out of discovery, because index
children bypassed `link_pair` and `httpx.InvalidURL` is not an `HTTPError`. And discovery ran
after `create_run` but outside any handler, so every one of these left the run row `running`
forever, where `previous_run` would pick it up as the baseline for the next diff.

**Decision: the invariant lives in one place.** `Crawler.run()` wraps everything after
`create_run` in a handler that marks the run `failed` with the exception's name and message,
then re-raises. Bugs still surface; they no longer poison the database. The specific holes
are closed too, but the guard is what makes the promise true for the next one.

**Decision: the fetcher never guesses.** httpx builds the next request from a Location header
even with `follow_redirects=False`, and by the time it raises the response is closed. A
response event hook, registered once per client, runs before that build and keeps the
response in a task-local `ContextVar`; `_request()` hands it back as an ordinary 3xx attempt
and `fetch()` classifies the Location with the same code as any other hop. The real status is
stored, nothing is retried, and there is no exception-message sniffing. Building the request
(`client.build_request`) happens outside that try, so a URL httpx refuses outright (a bad IPv4
literal, an over-long URL, a malformed punycode label, which idna reports as a
`UnicodeError`) is its own label, `bad_url`, with status 0 and no request made. The same
`UNFETCHABLE` tuple guards the one-shot fetches of robots.txt and sitemaps, and
`registrable_domain()` returns empty for anything the parser rejects, so `same_site()` is
total: a hostile canonical href could have crashed the audit the same way.

**Sitemap discovery, third and last ordering.** The start path's guesses come first because
the user named that site; robots.txt sitemaps follow; the host root is tried only when robots
names nothing and no guess answered, since after a hit it can only seed sibling sites. Each
`drain` call has its own file budget (50) over a shared `visited` set, so a large index on
either side cannot starve the other, which was the real defect behind both orderings. Every
URL a sitemap names now goes through `link_pair` before anything touches it.

**One shape, one order.** `Frontier` carries `(Link, depth)`, so `_process`, `_enqueue` and
`_record_failure` take a `Link` instead of a key and a spelling as separate positionals; the
docstring on `Link` is now true. `_enqueue` checks the frontier's `seen` set before asking
protego: a link met again on later pages (most of them) paid ~13 us for a robots match that
could not change anything, about two seconds per 5,000-page crawl. The API's score sort
breaks ties like `summarize_run` (issue count, then URL), so the dashboard's loaded list
always contains the pages its *Start here* panel names; the count-only fallback row stays as
a degradation path, not a feature. The next-step hint always double-quotes the database path:
bash strips an unquoted backslash (`C:\sites\t.db` arrives as `C:sitest.db`, and `connect()`
would create that file), and double quotes are harmless in PowerShell and cmd. The
`registrable_domain` cache test asserts `cache_info()` after `cache_clear()` instead of
monkeypatching the extractor, so it cannot pass vacuously on a warm cache.
