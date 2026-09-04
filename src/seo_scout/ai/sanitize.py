"""Page text is untrusted input. Strip instruction-like sequences and fence the rest."""

from __future__ import annotations

import re

# Order matters: whole-line role prefixes and chat markup go first so the phrase patterns
# below never double-count text already removed.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[ \t]*(?:system|assistant|user|human|ai)\s*:[^\n]*\n?", re.I | re.M),
    re.compile(r"<\|[^|>]*\|>[^\n]*\n?", re.I),
    re.compile(r"\[/?INST\][^\n]*", re.I),
    re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+|of\s+)?(?:previous|prior|above|earlier|preceding)"
        r"\s+(?:instructions?|prompts?|directions?|rules?|messages?)[^.\n]*[.\n]?",
        re.I,
    ),
    re.compile(
        r"disregard\s+(?:all\s+|any\s+)?(?:the\s+|of\s+)?(?:previous|prior|above|earlier)"
        r"[^.\n]*[.\n]?",
        re.I,
    ),
    re.compile(r"new\s+instructions?\s*:[^.\n]*[.\n]?", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\s+[^.\n]*[.\n]?", re.I),
)
_SPACES = re.compile(r"[ \t]{2,}")


def strip_instructions(text: str) -> tuple[str, int]:
    """Remove sequences that read as instructions to a model. Returns (cleaned, removed)."""
    removed = 0
    cleaned = text
    for pattern in _PATTERNS:
        cleaned, n = pattern.subn(" ", cleaned)
        removed += n
    if removed == 0:
        return text, 0
    return _SPACES.sub(" ", cleaned).strip(), removed


def wrap_untrusted(text: str, label: str) -> str:
    """Fence text in delimiters and neutralise any forged closing delimiter inside it."""
    closing = f"</{label}>"
    escaped = text.replace(closing, f"&lt;/{label}&gt;")
    return f"<{label}>\n{escaped}\n{closing}"


def clean_for_prompt(text: str, label: str, *, max_chars: int) -> str:
    cleaned, _ = strip_instructions(text)
    return wrap_untrusted(cleaned[:max_chars], label)
