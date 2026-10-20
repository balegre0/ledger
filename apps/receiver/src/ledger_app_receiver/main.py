"""``ledger-receiver``: the platform serves the ``receiver`` app (``ledger_platform.runtime.serving``)."""

from ledger_platform.runtime import serve


def main() -> int:
    """Serve the ``receiver`` app until the process is stopped; the exit status."""
    return serve("receiver")
