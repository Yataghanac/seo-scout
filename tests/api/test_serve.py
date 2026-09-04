from typing import Any

import pytest
from typer.testing import CliRunner

from seo_scout import cli

runner = CliRunner()


def test_serve_starts_uvicorn_on_the_requested_port(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    result = runner.invoke(cli.app, ["serve", "--port", "8123", "--db", "x.db"])
    assert result.exit_code == 0, result.output
    assert captured["port"] == 8123
    assert captured["host"] == "127.0.0.1"
    assert captured["app"].title == "SEO Scout"
    assert "http://127.0.0.1:8123" in result.output
