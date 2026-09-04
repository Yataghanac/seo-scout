"""Rule registry. A rule is a pure function (page, context) -> list of messages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.models import Severity

RuleFn = Callable[[AuditPage, CrawlContext], list[str]]


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    explanation: str
    check: RuleFn


RULES: list[Rule] = []


def rule(rule_id: str, severity: Severity, explanation: str) -> Callable[[RuleFn], RuleFn]:
    """Register `fn` under `rule_id`. Each message it returns becomes one Issue."""

    def register(fn: RuleFn) -> RuleFn:
        if any(r.id == rule_id for r in RULES):
            raise ValueError(f"duplicate rule id: {rule_id}")
        RULES.append(Rule(rule_id, severity, explanation.strip(), fn))
        return fn

    return register


def explanations() -> dict[str, str]:
    return {r.id: r.explanation for r in RULES}
