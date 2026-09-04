"""Render a RunDiff as a terminal table, Markdown, or JSON."""

from __future__ import annotations

from seo_scout.diff.differ import RunDiff


def _headline(d: RunDiff) -> str:
    delta = d.average_after - d.average_before
    return (
        f"average score {d.average_before:g} -> {d.average_after:g} ({delta:+g})   "
        f"pages {d.pages_before} -> {d.pages_after}"
    )


def to_table(d: RunDiff) -> str:
    lines = [f"run {d.run_a} -> run {d.run_b}", _headline(d)]
    if d.is_empty:
        lines.append("no changes")
        return "\n".join(lines)
    if d.pages_added:
        lines.append(f"pages added ({len(d.pages_added)}):")
        lines += [f"  + {url}" for url in d.pages_added]
    if d.pages_removed:
        lines.append(f"pages removed ({len(d.pages_removed)}):")
        lines += [f"  - {url}" for url in d.pages_removed]
    if d.score_changes:
        lines.append(f"score changes ({len(d.score_changes)}):")
        width = max(len(c.url) for c in d.score_changes)
        lines += [
            f"  {c.url.ljust(width)}  {c.before:>3} -> {c.after:>3}  ({c.delta:+d})"
            for c in d.score_changes
        ]
    if d.issues_fixed:
        lines.append(f"issues fixed ({len(d.issues_fixed)}):")
        lines += [f"  {i.url}  {i.rule_id}" for i in d.issues_fixed]
    if d.issues_introduced:
        lines.append(f"issues introduced ({len(d.issues_introduced)}):")
        lines += [f"  {i.url}  {i.rule_id}" for i in d.issues_introduced]
    return "\n".join(lines)


def to_markdown(d: RunDiff) -> str:
    parts = [f"# Run {d.run_a} -> run {d.run_b}", "", f"**{_headline(d)}**", ""]
    if d.is_empty:
        parts.append("No changes.")
        return "\n".join(parts) + "\n"
    parts += ["## Pages added", *(f"- {u}" for u in d.pages_added or ["_none_"]), ""]
    parts += ["## Pages removed", *(f"- {u}" for u in d.pages_removed or ["_none_"]), ""]
    parts += ["## Score changes", "| URL | before | after | delta |", "|---|---|---|---|"]
    parts += [f"| {c.url} | {c.before} | {c.after} | {c.delta:+d} |" for c in d.score_changes]
    parts += ["", "## Issues fixed", *(f"- {i.url}: {i.rule_id}" for i in d.issues_fixed or [])]
    if not d.issues_fixed:
        parts.append("_none_")
    parts += ["", "## Issues introduced"]
    parts += [f"- {i.url}: {i.rule_id}" for i in d.issues_introduced] or ["_none_"]
    return "\n".join(parts) + "\n"


def to_json(d: RunDiff) -> str:
    return d.model_dump_json(indent=2)
