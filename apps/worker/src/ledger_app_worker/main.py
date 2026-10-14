"""``ledger-worker``: the platform serves the ``worker`` app (``ledger_platform.runtime.serving``, §3.3)."""

from ledger_platform.runtime import serve


def main() -> int:
    """Serve the ``worker`` app until the process is stopped; the exit status."""
    return serve("worker")
