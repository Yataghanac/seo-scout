"""Structured JSON logging with a run_id on every line."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_run_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("run_id", default=None)

_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


def bind_run_id(run_id: int | None) -> None:
    _run_id.set(run_id)


def current_run_id() -> int | None:
    return _run_id.get()


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record, including any `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": _run_id.get(),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, verbose: bool) -> None:
    """Install the JSON handler on the seo_scout logger (idempotent)."""
    root = logging.getLogger("seo_scout")
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False
