# Using SEO Scout commercially

SEO Scout is MIT-licensed: you may use, modify, host and sell services around it without
asking. This page is for the case where you deploy it for a client, or a client asks what it
does with their data. Adjust the *Support* and *Liability* sections to your own offer.

## What leaves the customer's machine

| data | where it goes | when |
|---|---|---|
| HTTP requests for the customer's pages | the customer's own web server | every crawl |
| Up to 4,000 characters of visible page text, plus the current title and meta | OpenAI (`api.openai.com`), under the API key in `.env` | only for pages that failed a title or meta rule, and only when a key is configured |
| A one-line run summary (site, score change, counts) | Slack, if `SLACK_WEBHOOK_URL` is set | after each `report` run |
| The audit data itself (page scores, titles, metas, AI suggestions) and the ability to start a crawl | whoever can reach the dashboard's port | continuously, if `serve` is exposed beyond localhost |

Nothing else is sent anywhere. There is no telemetry, no update check, and no per-user account.
All results live in one SQLite file the customer owns.

**Hosting the dashboard for a client.** `serve` can run on a box the client reaches over the
network instead of the operator's own laptop. That turns "who can see the audit and start a
crawl" from "whoever has a terminal on this machine" into "whoever can reach this port", so two
things follow: set `SEO_SCOUT_DASHBOARD_TOKEN` before exposing it, since that shared token is the
*only* credential — there are no per-user accounts to create or revoke, and unset means the
dashboard is completely open to anyone who can reach it — and put the app behind a reverse proxy
for TLS, since it does not terminate HTTPS itself. `SEO_SCOUT_ALLOWED_DOMAINS` limits which sites
a client can point the crawl button at, on top of the built-in refusal of loopback, private and
link-local addresses. None of this replaces normal network hygiene: a token in an unencrypted
cookie on an unencrypted connection is a token a network path can read.

**Whose OpenAI key.** Use the customer's key when their page content is sensitive or when
they want the data relationship with OpenAI to be theirs. OpenAI's API terms at the time of
writing do not train on API inputs; confirm the current terms with them, not with us.

**Turning the AI off.** Leave `OPENAI_API_KEY` empty, or pass `--no-ai`. The deterministic
audit, dashboard, diffs and reports all work without it.

## Cost the customer will see

Only OpenAI usage. Measured: about $0.002 to $0.004 per page that needs a rewrite on the
first run, and $0 for unchanged pages afterwards because of the content cache. A hard cap
(`--max-cost`, default $1.00) stops the AI stage before it can exceed the budget. Compute for
crawling is negligible on any laptop.

## What it will not do

- Render JavaScript. Single-page apps audit as empty shells.
- Measure rankings, traffic or backlinks. It audits on-page hygiene only.
- Publish anything. Proposed titles and metas are suggestions; a person pastes them.
- Crawl faster than the site allows. robots.txt, crawl-delay and the built-in rate limit
  cannot be switched off.

## Support (template, edit to your offer)

- **Setup**: install on one machine, configure `.env`, one scheduled run, one hour of
  training. Fixed price.
- **Support window**: business days, response within one business day, for N months.
- **Covered**: install problems, crawl failures on the agreed sites, dashboard bugs.
- **Not covered**: changes to the rule set or prompts (quoted separately), OpenAI outages,
  changes the customer makes to the code.

## Liability (template)

The software audits pages and proposes text. The customer decides what to publish. Neither
the author nor the integrator is responsible for ranking changes, for content the customer
publishes, or for charges incurred under the customer's own OpenAI account. The MIT license
applies to the code itself; see `LICENSE`.
