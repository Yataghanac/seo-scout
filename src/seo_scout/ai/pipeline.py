"""The AI stage: pick weak pages, ask the model, validate, repair once, persist atomically."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel

from seo_scout.ai.cache import CachedSuggestion, cache_key, get_cached, put_cached
from seo_scout.ai.client import AIUnavailable, Completer, Completion
from seo_scout.ai.cost import Budget, TokenCounter, estimate_call_usd, price_usd, tiktoken_counter
from seo_scout.ai.prompt import PROMPT_VERSION, build_messages
from seo_scout.ai.schema import Suggestion, response_format
from seo_scout.ai.validate import PageFacts, Violation, summarize_violations, validate
from seo_scout.config import Settings
from seo_scout.models import SuggestionRow
from seo_scout.parse import parse_html
from seo_scout.store import repo_ai, repo_issues, repo_pages

log = logging.getLogger("seo_scout.ai")

MAX_COMPLETION_TOKENS = 300
_WEAK_RULE_PREFIXES = ("title_", "meta_description_")


class AIRunReport(BaseModel):
    considered: int = 0
    calls: int = 0
    cached: int = 0
    ok: int = 0
    repaired: int = 0
    rejected: int = 0
    skipped: int = 0
    unavailable: bool = False
    budget_exhausted: bool = False
    cost_usd: float = 0.0
    warning: str | None = None


@dataclass
class _Call:
    kind: str
    prompt_tokens: int
    completion_tokens: int
    usd: float


@dataclass
class _Outcome:
    status: str
    suggestion: Suggestion | None = None
    reason: str | None = None
    calls: list[_Call] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return sum(c.usd for c in self.calls)


@dataclass
class _Ctx:
    conn: sqlite3.Connection
    run_id: int
    settings: Settings
    completer: Completer
    counter: TokenCounter
    budget: Budget
    semaphore: asyncio.Semaphore
    report: AIRunReport
    unavailable_reason: str | None = None


async def enrich_run(
    conn: sqlite3.Connection,
    run_id: int,
    settings: Settings,
    completer: Completer | None,
    *,
    counter: TokenCounter | None = None,
) -> AIRunReport:
    """Run the AI stage over one audited run. Never raises for API problems."""
    candidates = _candidates(conn, run_id)
    report = AIRunReport(considered=len(candidates))
    if completer is None:
        for facts in candidates:
            _persist(conn, run_id, facts, _Outcome("skipped", reason="OPENAI_API_KEY unset"))
        report.skipped = len(candidates)
        report.warning = "OPENAI_API_KEY unset: deterministic results only"
        return report
    if not candidates:
        return report
    ctx = _Ctx(
        conn=conn,
        run_id=run_id,
        settings=settings,
        completer=completer,
        counter=counter or tiktoken_counter(settings.openai_model),
        budget=Budget(settings.max_cost_usd),
        semaphore=asyncio.Semaphore(settings.ai_concurrency),
        report=report,
    )
    await asyncio.gather(*(_process(ctx, facts) for facts in candidates))
    report.cost_usd = ctx.budget.spent
    report.warning = _warning(ctx)
    log.info("ai stage finished", extra=report.model_dump())
    return report


def _candidates(conn: sqlite3.Connection, run_id: int) -> list[PageFacts]:
    weak = {
        i.url
        for i in repo_issues.list_issues(conn, run_id)
        if i.rule_id.startswith(_WEAK_RULE_PREFIXES)
    }
    facts = []
    for page in repo_pages.list_pages(conn, run_id):
        if page.url in weak and page.html is not None:
            parsed = parse_html(page.html, page.final_url)
            facts.append(
                PageFacts(
                    url=page.url,
                    title=parsed.title,
                    meta_description=parsed.meta_description,
                    body_text=parsed.text,
                )
            )
    return facts


async def _process(ctx: _Ctx, facts: PageFacts) -> None:
    async with ctx.semaphore:
        key = cache_key(facts, ctx.settings.openai_model, PROMPT_VERSION)
        hit = get_cached(ctx.conn, key)
        if hit is not None:
            outcome = _Outcome(hit.status, hit.suggestion, hit.reason)
            _persist(ctx.conn, ctx.run_id, facts, outcome, cached=True)
            ctx.report.cached += 1
            _count(ctx.report, hit.status)
            return
        outcome = await _attempt(ctx, facts)
        _persist(ctx.conn, ctx.run_id, facts, outcome)
        if outcome.status in {"ok", "repaired", "rejected"}:
            entry = CachedSuggestion(
                status=outcome.status, suggestion=outcome.suggestion, reason=outcome.reason
            )
            with ctx.conn:
                put_cached(ctx.conn, key, ctx.settings.openai_model, PROMPT_VERSION, entry)
        _count(ctx.report, outcome.status)


async def _attempt(ctx: _Ctx, facts: PageFacts) -> _Outcome:
    """Initial call, validate, at most one repair call, then accept or reject."""
    messages = build_messages(facts)
    calls: list[_Call] = []
    violations: list[Violation] = []
    prior: list[Violation] = []  # what the first attempt got wrong, kept for the record
    for kind in ("initial", "repair"):
        if ctx.unavailable_reason:
            return _Outcome("unavailable", reason=ctx.unavailable_reason, calls=calls)
        estimate = estimate_call_usd(
            ctx.settings.openai_model,
            ctx.counter,
            messages,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )
        if not ctx.budget.can_afford(estimate):
            ctx.report.budget_exhausted = True
            reason = f"budget: ${estimate:.4f} needed, ${ctx.budget.remaining:.4f} left"
            return _Outcome("skipped", reason=reason, calls=calls)
        try:
            completion = await ctx.completer.complete(
                messages, response_format=response_format(), max_tokens=MAX_COMPLETION_TOKENS
            )
        except AIUnavailable as exc:
            ctx.unavailable_reason = str(exc)
            return _Outcome("unavailable", reason=str(exc), calls=calls)
        usd = price_usd(
            ctx.settings.openai_model, completion.prompt_tokens, completion.completion_tokens
        )
        ctx.budget.record(usd)
        ctx.report.calls += 1
        calls.append(_Call(kind, completion.prompt_tokens, completion.completion_tokens, usd))
        suggestion, violations = _parse_and_validate(completion, facts)
        if not violations and suggestion is not None:
            why = summarize_violations(prior) if prior else None
            return _Outcome("repaired" if prior else "ok", suggestion, reason=why, calls=calls)
        previous: Suggestion | str = suggestion or completion.content or completion.refusal or ""
        messages = build_messages(facts, previous=previous, violations=violations)
        if kind == "initial":
            prior = violations  # the repair's own violations must not overwrite these
    reason = (
        f"repair attempt:\n{summarize_violations(violations)}\n"
        f"first attempt:\n{summarize_violations(prior)}"
    )
    return _Outcome("rejected", reason=reason, calls=calls)


def _parse_and_validate(
    completion: Completion, facts: PageFacts
) -> tuple[Suggestion | None, list[Violation]]:
    if completion.content is None:
        detail = completion.refusal or "empty response"
        return None, [Violation(field="title", code="refusal", detail=detail[:200])]
    try:
        suggestion = Suggestion.model_validate_json(completion.content)
    except ValueError as exc:
        return None, [Violation(field="title", code="invalid_json", detail=str(exc)[:200])]
    return suggestion, validate(suggestion, facts)


def _persist(
    conn: sqlite3.Connection,
    run_id: int,
    facts: PageFacts,
    outcome: _Outcome,
    *,
    cached: bool = False,
) -> None:
    """One transaction per page: calls and the suggestion row land together or not at all."""
    s = outcome.suggestion
    row = SuggestionRow(
        url=facts.url,
        original_title=facts.title,
        original_meta=facts.meta_description,
        diagnosis=s.diagnosis if s else None,
        title=s.title if s else None,
        meta_description=s.meta_description if s else None,
        status=outcome.status,
        reason=outcome.reason,
        cached=cached,
        cost_usd=outcome.cost,
    )
    with conn:
        for call in outcome.calls:
            record = repo_ai.CallRecord(
                facts.url, call.kind, call.prompt_tokens, call.completion_tokens, call.usd
            )
            repo_ai.record_call(conn, run_id, record)
        repo_ai.upsert_suggestion(conn, run_id, row)


def _count(report: AIRunReport, status: str) -> None:
    if status in {"ok", "repaired", "rejected", "skipped"}:
        setattr(report, status, getattr(report, status) + 1)
    elif status == "unavailable":
        report.unavailable = True


def _warning(ctx: _Ctx) -> str | None:
    if ctx.unavailable_reason:
        return f"AI unavailable, deterministic results only: {ctx.unavailable_reason}"
    if ctx.report.budget_exhausted:
        # Four decimals like every other cost in the codebase: `--max-cost 0.001` is a real
        # cap, and rounding it to "$0.00" reads as no budget rather than a tenth of a cent.
        noun = "page" if ctx.report.skipped == 1 else "pages"
        return (
            f"AI budget of ${ctx.budget.max_usd:.4f} exhausted after ${ctx.budget.spent:.4f}; "
            f"{ctx.report.skipped} {noun} skipped"
        )
    return None
