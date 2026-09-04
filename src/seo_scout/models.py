"""Shared pydantic models. Pure data; no I/O."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    critical = "critical"
    warning = "warning"
    notice = "notice"

    @property
    def weight(self) -> int:
        """Points subtracted from a page's 100-point score per issue."""
        return {"critical": 15, "warning": 5, "notice": 2}[self.value]


class Issue(BaseModel):
    """One finding from one rule on one page."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    message: str
