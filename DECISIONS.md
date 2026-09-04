# Design decisions

One entry per phase. Written so a non-author can defend the design on camera.

## Phase 0 — Scaffold

**What it does.** Sets up the package layout, config, JSON logging, shared models, the
quality gate (ruff, mypy --strict, pytest) and CI, before any feature code exists.

**Non-obvious decision.** Configuration is a single pydantic-settings object with a printed,
redacted "effective config" line. Every knob has a validated default and a ceiling, so a typo
in `.env` fails at startup rather than mid-crawl, and the crawl caps can be lowered by flags
but never raised past the hard limits.

**What I chose not to do.** No structlog. Stdlib `logging` plus a 30-line JSON formatter and a
`contextvars` run_id gives the same result with one fewer dependency to explain.

**Failure mode prevented.** The 400-lines-per-module rule is enforced by a test, and the
50-lines-per-function rule by ruff's statement count, so the architecture constraints from the
brief cannot quietly erode as phases are added.
