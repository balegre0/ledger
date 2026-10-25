"""The receiver's trust in what emitters send it: the trusted roots, read when the app starts.

The documents other emitters send the receiver are signed under certificates that chain to the configured
roots (``CounterpartyTrust``). The receiver reads them at start as the worker does (``outbound.read_roots``,
in a thread): a receiver whose roots cannot be read, or hold no certificate, does not start
(``TrustedRootsUnreadableError``, which never names the file), rather than refusing every document later.
"""

import asyncio
from contextlib import asynccontextmanager
lazy from collections.abc import AsyncGenerator
lazy from pathlib import Path

from ledger_country_do.channels.ecf_v1.adapters.outbound import read_roots
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust


@asynccontextmanager
async def inbound_trust(roots: Path) -> AsyncGenerator[CounterpartyTrust]:
    """The policy over ``roots``, read when the block opens (see the module)."""
    yield CounterpartyTrust(roots=await asyncio.to_thread(read_roots, roots))
