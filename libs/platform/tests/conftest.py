"""Fixtures of the platform's tests. Later tasks add real PostgreSQL and NATS here."""

import logging
lazy from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """``configure_logging`` changes process-wide state: put it back after every test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
