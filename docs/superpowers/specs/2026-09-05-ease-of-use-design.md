# Ease-of-use pass — design (2026-09-05)

Goal: a non-developer can get from "I have a URL" to "I know what to fix" without reading
the architecture, and a content editor can act on a proposal in one click.

## Decisions taken with the owner

- Repo stays public under MIT (LICENSE added). Commercial offer is setup, hosting,
  adaptation and support; `docs/commercial.md` states data handling, support and liability.
- All four UX items below are in scope. Nothing else.

## 1. Dashboard: Start here, legend, help strip

- `#start` panel above the charts: the five lowest-scoring pages, each with its score and
  the first warning-or-worse issue message. Click selects the page (same as the table).
- Legend under the table toolbar: three pips labelled critical / warning / notice.
- One-line help strip under the tiles: "Score is a triage order, not a grade. Fix the pages at
  the top of *Start here* first." Dismiss persists in `localStorage`.
- No new API calls: everything derives from `state.pages` already loaded.

## 2. Dashboard: copy buttons

- A `copy` button in the header of each *proposed* box. Uses `navigator.clipboard.writeText`;
  the label flips to "copied" for 1.5 s. Hidden when there is no validated proposal.

## 3. CLI: guided next steps

- `seo-scout init`: copy `.env.example` to `.env` if absent, print what to edit. Never
  overwrites; exit 0 either way.
- `seo-scout serve --open`: open the dashboard URL in the default browser after binding.
- `crawl` and `report` end with `next: seo-scout serve --open` (to stderr, so JSON stdout of
  `report --json`-style consumers is untouched).

## 4. README: shorter quickstart

- Above the fold: one sentence, the screenshot, three commands (`init`, `crawl`, `serve
  --open`), "what you will see". Everything about design rationale moves below a
  "How it works" heading. No content is deleted, only reordered.

## Testing

- `init`: creates the file, refuses to overwrite, works when the template is missing.
- `serve --open`: the browser opener is injected and asserted, uvicorn is stubbed.
- Dashboard: verified in the browser against the project DB (start panel content, legend,
  copy button behaviour via a clipboard stub).
