import logging

import pytest
import structlog

from ledger_testing.logs import restored_logging


def test_the_block_starts_from_the_baseline_and_leaves_logging_as_it_found_it() -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    with restored_logging():
        root.handlers[:] = [logging.NullHandler()]
        root.setLevel(logging.CRITICAL)
        structlog.contextvars.bind_contextvars(company_id="c-1")
    assert (root.handlers, root.level) == (handlers, level)
    assert structlog.contextvars.get_contextvars() == {}


def test_logging_is_restored_even_when_the_block_fails() -> None:
    root = logging.getLogger()
    level = root.level

    def fail() -> None:
        with restored_logging():
            root.setLevel(logging.CRITICAL)
            _ = 1 / 0

    with pytest.raises(ZeroDivisionError):
        fail()
    assert root.level == level


def test_the_baseline_writes_plain_text_without_colors(capsys: pytest.CaptureFixture[str]) -> None:
    with restored_logging():
        structlog.stdlib.get_logger().info("baseline.works", answer=42)
    written = capsys.readouterr().out
    assert "baseline.works" in written
    assert "answer=42" in written
    assert "\x1b[" not in written
