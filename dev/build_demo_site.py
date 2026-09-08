"""Build `demo-site/`: the real dashboard, served against a frozen snapshot of real runs.

The hosted demo exists because the product cannot be hosted on a serverless platform — a crawl
runs for minutes and writes to SQLite, and both of those need a machine that stays up. So the
dashboard is deployed exactly as shipped, with three surgical changes: it reads static JSON
instead of the API, its export links point at static files, and the Crawl button explains why
it is disabled rather than failing. Everything else — filters, sorting, charts, the detail
panel, the AI before/after — is the shipped code running on genuine output.

Run it against a live `seo-scout serve` (the source of the snapshot):

    uv run python dev/build_demo_site.py --api http://127.0.0.1:8000 --runs 1 4
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "seo_scout" / "api" / "static" / "index.html"
OUT = ROOT / "demo-site"
REPO = "https://github.com/Yataghanac/seo-scout"

# One mapper inside api(), rather than edits at every call site: the demo stays a thin skin
# over the shipped file, and a later change to a fetch path here fails loudly instead of
# silently drifting.
PATH_MAPPER = """async function api(path) {
  const r = await fetch(path);"""

MAPPER_FN = """// Hosted demo: the API is a folder of files written by dev/build_demo_site.py.
function demoPath(path) {
  if (path === "/api/runs") return "data/runs.json";
  let m = path.match(/^\\/api\\/runs\\/(\\d+)\\/summary$/);
  if (m) return `data/summary-${m[1]}.json`;
  m = path.match(/^\\/api\\/runs\\/(\\d+)\\/pages/);
  if (m) return `data/pages-${m[1]}.json`;
  return path;
}

async function api(path) {
  const r = await fetch(demoPath(path));"""

DISABLED = (
    "Crawling is disabled in this hosted demo: a crawl runs for minutes and writes to SQLite, "
    "which a serverless host cannot do. Everything below is real output from a real crawl."
)

CRAWL_STUB = """async function startCrawl() {
  setCrawlMsg(DISABLED_MESSAGE, false);
  return;
  // eslint-disable-next-line no-unreachable
  const btn = $("#crawl-go"), input = $("#crawl-url");"""

BANNER = """  <div class="help" id="demo-note" style="border-left-color: var(--accent)">
    <span><b>Hosted demo.</b> A real 50-page crawl of books.toscrape.com, audited and rewritten
    by GPT-4o, frozen as static data. Every filter, chart, score and rewrite below is genuine
    output — only starting a new crawl is disabled here.</span>
    <a href="REPO_URL" target="_blank" rel="noopener">source &amp; install</a>
  </div>
  <div id="notice" class="warn" hidden></div>"""


def fetch(api: str, path: str) -> bytes:
    with urllib.request.urlopen(f"{api}{path}") as response:
        return bytes(response.read())


def snapshot(api: str, runs: list[int], data: Path) -> None:
    """Write the exact payloads the dashboard asks for, under the names demoPath() expects."""
    listed = {run["id"]: run for run in json.loads(fetch(api, "/api/runs"))}
    # The dashboard opens whatever is first, so the order asked for on the command line is the
    # order kept — the strongest run leads, rather than merely the newest.
    kept = [listed[run_id] for run_id in runs if run_id in listed]
    if len(kept) != len(runs):
        missing = sorted(set(runs) - {run["id"] for run in kept})
        raise SystemExit(f"runs not found on {api}: {missing}")
    (data / "runs.json").write_bytes(json.dumps(kept, indent=1).encode())
    for run_id in runs:
        (data / f"summary-{run_id}.json").write_bytes(fetch(api, f"/api/runs/{run_id}/summary"))
        (data / f"pages-{run_id}.json").write_bytes(
            fetch(api, f"/api/runs/{run_id}/pages?size=500&sort=score&order=asc")
        )
        (data / f"export-{run_id}.csv").write_bytes(fetch(api, f"/api/runs/{run_id}/export.csv"))
        (data / f"export-{run_id}.json").write_bytes(fetch(api, f"/api/runs/{run_id}/export.json"))


def rewrite(html: str) -> str:
    """Three changes, each asserted: a missing anchor means the dashboard moved under us."""
    stub = CRAWL_STUB.replace("DISABLED_MESSAGE", json.dumps(DISABLED))
    for old, new in (
        (PATH_MAPPER, MAPPER_FN),
        (
            """async function startCrawl() {
  const btn = $("#crawl-go"), input = $("#crawl-url");""",
            stub,
        ),
        (
            '$("#csv").href = `/api/runs/${id}/export.csv`; '
            '$("#json").href = `/api/runs/${id}/export.json`;',
            '$("#csv").href = `data/export-${id}.csv`; $("#json").href = `data/export-${id}.json`;',
        ),
        (
            '<a href="/api/docs">API</a>',
            f'<a href="{REPO}" target="_blank" rel="noopener">CODE</a>',
        ),
        ('  <div id="notice" class="warn" hidden></div>', BANNER.replace("REPO_URL", REPO)),
        (
            'placeholder="https://example.com — paste a site to crawl"',
            'placeholder="crawling is disabled in this demo" readonly',
        ),
    ):
        if old not in html:
            raise SystemExit(f"anchor not found in index.html, demo build is stale:\n{old[:90]}")
        html = html.replace(old, new, 1)
    return html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, nargs="+", default=[1, 4])
    args = parser.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    data = OUT / "data"
    data.mkdir(parents=True)
    snapshot(args.api, args.runs, data)
    (OUT / "index.html").write_text(rewrite(SOURCE.read_text(encoding="utf-8")), encoding="utf-8")
    # GitHub Pages runs Jekyll unless told not to, and Jekyll would drop the data directory.
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    written = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"demo-site/: {len(written)} files, {size // 1024} KB")
    for name in written:
        print(" ", name)


if __name__ == "__main__":
    main()
