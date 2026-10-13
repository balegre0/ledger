from ledger_testing.containers import libpq_url


def test_psycopg_takes_the_same_url_without_the_driver() -> None:
    assert libpq_url("postgresql+psycopg://app:secret@host:5432/db") == "postgresql://app:secret@host:5432/db"
