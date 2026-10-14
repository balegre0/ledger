"""``ledger-api``: the platform serves the ``api`` app (``ledger_platform.runtime.serving``, §3.3)."""

from ledger_platform.runtime import serve


def main() -> int:
    """Serve the ``api`` app until the process is stopped; the exit status."""
    return serve("api")
