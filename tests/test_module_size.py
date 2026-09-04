"""Architecture rule from the brief: every module under 400 lines."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "seo_scout"
MAX_LINES = 400


def test_every_module_is_under_400_lines() -> None:
    offenders = {
        p.relative_to(SRC).as_posix(): n
        for p in SRC.rglob("*.py")
        if (n := len(p.read_text(encoding="utf-8").splitlines())) >= MAX_LINES
    }
    assert offenders == {}, f"modules over {MAX_LINES} lines: {offenders}"
