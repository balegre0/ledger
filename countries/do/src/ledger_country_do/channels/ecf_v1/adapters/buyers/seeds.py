"""A counterparty's seed the issuer signed: what the caller audits (Decision 20), never a secret."""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SeedSigned:
    """A seed signed as the issuing company for a buyer's (or an emitter's) receiver.

    ``buyer``: the counterparty's RNC or cédula, whole (the caller masks it, ``ledger_kernel.tax_ids.masked``,
    before it reaches an audit row's detail); ``endpoint_host``: the host the seed came from; ``at``: when it
    was signed. Never the seed, its signature or a token.
    """

    buyer: str
    endpoint_host: str
    at: datetime
