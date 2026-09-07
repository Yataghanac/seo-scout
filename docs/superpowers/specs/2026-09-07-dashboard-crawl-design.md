# Crawl from the dashboard — design (2026-09-07)

Goal: a client opens a hosted dashboard, pastes a URL, and gets an audit — without a terminal.
Today the API is read-only and the empty state tells the reader to go run a CLI command.

## Decisions taken with the owner

- **Deployment**: hosted, reachable by a client. Not localhost-only. This makes authentication
  and target safety mandatory rather than optional.
- **Target safety**: free-text URL entry with a private-range blocklist; when the operator
  configures an allowlist, only those registrable domains are accepted.
- **Authentication**: one shared token in `.env`, gating reads and writes. No user accounts —
  that stays on the README's "Explicitly not built" list. Unset token means open, so local use
  is unchanged.
- **Execution**: an `asyncio` task inside the uvicorn process, with the crawler *injected* into
  `create_app` rather than imported by `api`.
- Out of scope: cancelling a running crawl, a job queue, more than one concurrent crawl, TLS.

## 1. Architecture: the injected seam

`api` must keep depending on `store` only (CLAUDE.md). So the API never imports the crawler:

```python
create_app(db_path, *, crawl_runner=None, token=None, allowlist=())
```

`crawl_runner` is a callable `(url: str, max_pages: int | None) -> Awaitable[None]`.
`cli.serve` injects the real implementation, which builds a `Crawler` exactly as `cli._crawl`
does. Tests inject a fake. This mirrors the existing injection of the OpenAI `Completer` and
the crawler's `sleep`, so **no architecture rule is amended**.

When `crawl_runner` is `None` the POST endpoint returns `501`, so an embedder that only wants
the read-only API gets one.

## 2. `api/targets.py` — pure, table-testable

```python
def check_host(host: str, allowlist: Sequence[str]) -> str | None
def blocked_ip(ip: str) -> str | None
```

Both return a human-readable refusal reason, or `None` to allow.

`check_host` rejects: a host with no dot (bare intranet names), any `.local` suffix, and — when
`allowlist` is non-empty — any host whose registrable domain is not in it. It reuses
`urls.registrable_domain` so allowlisting `example.com` covers `blog.example.com`.

`blocked_ip` rejects loopback, private, link-local (including `169.254.169.254`), reserved and
unique-local addresses, via `ipaddress`. It is given a resolved address; resolution is I/O and
belongs to the caller.

The scheme check is not duplicated here — `urls.link_pair` already rejects non-http(s), and the
endpoint calls it first.

**The allowlist narrows; it never widens.** Both checks always apply on the API path: an
allowlisted domain that resolves into a private range is still refused. An allowlist answers
"which sites may this deployment audit", not "which sites may bypass the address rules".

**This gates the API only. The CLI is untouched.** `seo-scout crawl http://127.0.0.1:8099/`
keeps working, which is what `dev/fixture_site.py` depends on. Someone with shell access on
the box can already crawl anything; the check exists for the person typing into a browser.

**Resolution is cached per host** with an LRU, the way `urls.registrable_domain` caches the
public-suffix lookup. `allowed` is consulted for every link enqueued, not only for redirect
hops, so an uncached DNS lookup per link would dominate a crawl. A same-site crawl asks about
a handful of hosts.

## 3. Every redirect hop, not just the pasted URL

A permitted public URL can `302` into `169.254.169.254`. `Fetcher.fetch(url, allowed=...)`
already consults `allowed` before **every** hop, which is why the third review pass added it.
Target safety composes into that hook:

```python
allowed = lambda u: policy.allowed(u) and targets.ok(u)
```

The real crawl runner builds this composed callable. Nothing in the fetcher changes.

## 4. Endpoints

| method | path | behaviour |
|---|---|---|
| `POST` | `/api/login` | token in body; on match sets an `HttpOnly`, `SameSite=Strict` cookie. `401` otherwise. |
| `POST` | `/api/crawls` | `{url, max_pages?}`. `202` on accept. `400` with the refusal reason for a blocked or unparseable target. `409` if a crawl is already running. `501` if no runner was injected. |
| `GET` | `/api/runs` | unchanged shape; a row with status `running` now reports a live page count from `count_pages()` instead of the stored `0`. |

`POST /api/crawls` returns **no run id**. `create_run` happens inside `Crawler.run`, so
returning one would mean restructuring the crawler or awaiting a handshake. The dashboard
already polls `/api/runs`; it picks up the running row from there. No new read endpoint.

Auth is a router-level dependency covering every `/api/*` route. `GET /` (the HTML shell) stays
public: it contains no data, and serving it lets the JS render the token prompt.

## 5. Concurrency and process lifetime

One crawl at a time, enforced by an `asyncio.Lock` held by the runner; a second POST gets `409`.

A crawl dies with the server. On startup the app marks every run still `running` as `failed`
with `"interrupted"`. Without this, a killed process leaves a row that `previous_run` would
select as the baseline for the next diff — the failure the fourth review pass fixed for
in-process crashes. Reconciliation happens in a FastAPI lifespan handler, before serving.

## 6. Dashboard

- Header gains a URL input and a **Crawl** button.
- While any run is `running`: a progress line (`crawling https://… — 12 pages`), the button
  disabled, polling `/api/runs` every 2 s. Existing code already renders a notice for a
  non-`complete` run; this extends it rather than replacing it.
- On completion the finished run is selected and rendered by the existing path.
- A `401` from any call swaps the main panel for a token prompt that posts to `/api/login`.
- Refusal reasons from `400` are shown verbatim next to the input.

## 7. Configuration

Two new `Settings` fields, both optional, both added to `.env.example` **and**
`src/seo_scout/env.example` (a test enforces they stay identical):

- `SEO_SCOUT_TOKEN` — shared secret. Empty means no auth.
- `SEO_SCOUT_ALLOWED_DOMAINS` — comma-separated registrable domains. Empty means blocklist-only.

`serve` prints whether auth is on, the way the effective-config line already reports settings —
never the token itself.

## Testing

- `targets.py`: table test over hosts and IP literals, allowlist on and off. Pure, no network.
- Auth: `401` without a token, `200` with, and fully open when unset.
- `POST /api/crawls` against a **fake runner**: `202`, `409` when locked, `400` for a blocked
  target, `501` with no runner. No test crawls anything, so the suite stays offline and inside
  its time budget.
- The composed `allowed` callable: a hop into a private range is refused.
- Startup reconciliation: a seeded `running` row becomes `failed` after app start.
- `dev/fixture_site.py` exercises the real path by hand.

## Known limitations, to be stated in the README

- **DNS rebinding is not defended.** A host can pass the check and resolve elsewhere at fetch
  time. Pinning the resolved address through httpx is real work; the limitation is documented
  rather than implied away.
- **No cancel.** `asyncio.CancelledError` derives from `BaseException`, so `Crawler.run`'s
  guard would not catch it and the row would stay `running`. `--max-pages` and the 30-minute
  wall clock bound the exposure.
- **One shared token**, so no per-user audit trail.
- **One crawl at a time**, no queue.
