"""``ledger-signer``: the platform serves the ``signer`` app (``ledger_platform.runtime.serving``, §3.3)."""

from ledger_platform.runtime import serve


def main() -> int:
    """Serve the ``signer`` app (probes over HTTP, its service over gRPC) until stopped; the exit status."""
    return serve("signer")
