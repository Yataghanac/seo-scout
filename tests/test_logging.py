import json
import logging

import pytest

from seo_scout.logging import bind_run_id, configure_logging


def test_json_lines_carry_run_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(verbose=False)
    bind_run_id(42)
    logging.getLogger("seo_scout.test").info("hello", extra={"url": "https://a.test/"})
    out = capsys.readouterr().err.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["run_id"] == 42
    assert record["msg"] == "hello"
    assert record["level"] == "INFO"
    assert record["url"] == "https://a.test/"


def test_verbose_enables_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(verbose=True)
    logging.getLogger("seo_scout.test").debug("dbg")
    assert '"msg": "dbg"' in capsys.readouterr().err
