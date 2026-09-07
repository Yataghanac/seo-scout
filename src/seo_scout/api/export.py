"""CSV and JSON exports of one run."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

from seo_scout.api.schemas import PageView

CSV_COLUMNS = [
    "url",
    "final_url",
    "status",
    "depth",
    "score",
    "critical",
    "warning",
    "notice",
    "issues",
    "ai_status",
    "original_title",
    "ai_title",
    "original_meta",
    "ai_meta",
    "ai_reason",
]

# Excel, LibreOffice and Sheets evaluate a cell that opens with one of these. Half the columns
# here are text the audited site chose to serve (its <title>, its description) or text a model
# wrote from that page, and the file exists to be opened in a spreadsheet.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def as_text(value: str) -> str:
    """Mark a value as text for a spreadsheet, leaving anything harmless untouched.

    A leading apostrophe is the spreadsheet's own "treat this as text" marker: it is stripped
    on display, survives a round trip, and still reads as the original string in an editor.
    """
    return "'" + value if value.startswith(_FORMULA_LEAD) else value


def csv_rows(pages: list[PageView]) -> Iterator[str]:
    """Stream one CSV line at a time so large runs never sit in memory twice."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    yield buffer.getvalue()
    for page in pages:
        buffer.seek(0)
        buffer.truncate()
        ai = page.ai
        row: dict[str, object] = {
            "url": page.url,
            "final_url": page.final_url,
            "status": page.status,
            "depth": page.depth,
            "score": page.score,
            "critical": page.counts["critical"],
            "warning": page.counts["warning"],
            "notice": page.counts["notice"],
            "issues": ";".join(i.rule_id for i in page.issues),
            "ai_status": ai.status if ai else "",
            "original_title": ai.original_title if ai else "",
            "ai_title": ai.title if ai else "",
            "original_meta": ai.original_meta if ai else "",
            "ai_meta": ai.meta_description if ai else "",
            "ai_reason": ai.reason if ai else "",
        }
        writer.writerow({k: as_text(v) if isinstance(v, str) else v for k, v in row.items()})
        yield buffer.getvalue()
