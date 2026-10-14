"""URLs of the test PostgreSQL server in the forms its clients take.

The server itself is ``ledger_testing.postgres``'s: one per run, shared by every xdist worker.
"""

lazy from ledger_platform.config.settings import DATABASE_URL_SCHEME


def libpq_url(url: str) -> str:
    """The URL psycopg itself takes for ``url``, an SQLAlchemy ``postgresql+psycopg://`` URL."""
    return url.replace(DATABASE_URL_SCHEME, "postgresql://", 1)
