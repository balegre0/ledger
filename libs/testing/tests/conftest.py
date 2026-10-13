"""Fixtures of the toolkit's own tests: one real PostgreSQL for the session."""

lazy from collections.abc import Iterator

import pytest

from ledger_testing.containers import Postgres


@pytest.fixture(scope="session")
def server() -> Iterator[Postgres]:
    """A real PostgreSQL; each test makes the databases it needs in it."""
    with Postgres.started() as started:
        yield started
