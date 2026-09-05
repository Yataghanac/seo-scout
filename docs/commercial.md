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

Nothing else is sent anywhere. There is no telemetry, no update check, and no account. All
results live in one SQLite file the customer owns.

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
