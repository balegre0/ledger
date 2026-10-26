"""The receiver's seeds (spec §3.8, §6.3): 32 random bytes, kept by their SHA-256 five minutes, taken once.

A seed is handed out for a company in an environment, base64 in DGII's ``SemillaModel`` (``valor``, then the
moment it was made, ``fecha``). Whoever wants a token signs it and sends it back: the receiver reads it
(``read_inbound_seed``), verifies the signature and trusts its certificate (``inbound.Inbound.seed``), then
takes the seed in the unit that issues the token (``take``): it must be one handed out for that company and
environment, unexpired and never taken. Only its SHA-256 is kept (``receiver_seeds``, outside row-level
security). A seed that cannot be read is ``SeedUnreadableError`` (400); one whose signature does not hold,
whose certificate is not trusted or names nobody, is ``SeedUntrustedError`` (401).
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import insert, update
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import ReceiverSeedRecord
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
from ledger_kernel.errors import UnauthenticatedError
lazy from ledger_country_do.channels.ecf_v1.application.receiving import Sender
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

SEED_BYTES: Final = 32
SEED_LIFETIME: Final = timedelta(minutes=5)


class SeedUnreadableError(EcfInvalidError):
    """What was sent as a signed seed is no ``SemillaModel`` read hardened, valid for its schema."""

    code: ClassVar[str] = "do_ecf_receiver_seed_unreadable"

    def __init__(self) -> None:
        super().__init__("the document is not a signed seed")


class SeedUntrustedError(EcfError, UnauthenticatedError):
    """A signed seed whose signature does not hold, under a certificate not trusted, or naming nobody."""

    code: ClassVar[str] = "do_ecf_receiver_seed_untrusted"

    def __init__(self) -> None:
        super().__init__("the seed's signature is not trusted")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignedSeed:
    """A seed sent back signed: its value, and who signed it."""

    value: str
    sender: Sender


def new_seed() -> str:
    """A fresh seed's value: ``SEED_BYTES`` random bytes, in base64."""
    return base64.b64encode(secrets.token_bytes(SEED_BYTES)).decode()


def seed_hash(value: str) -> bytes:
    """What the receiver keeps of a seed: the SHA-256 of its value as written."""
    return hashlib.sha256(value.encode()).digest()


async def keep(
    connection: AsyncConnection, value: str, *, company: UUID, environment: Environment, at: datetime
) -> None:
    """Keep ``value``, a seed handed out ``at`` for ``company`` in ``environment``."""
    await connection.execute(
        insert(ReceiverSeedRecord).values(
            seed_hash=seed_hash(value),
            company_id=company,
            environment=environment.value,
            expires_at=at + SEED_LIFETIME,
            used_at=None,
        )
    )


async def take(
    connection: AsyncConnection, value: str, *, company: UUID, environment: Environment, at: datetime
) -> bool:
    """Whether ``value`` is a seed of ``company`` in ``environment``, live ``at`` and unused: then used."""
    taken = await connection.execute(
        update(ReceiverSeedRecord)
        .where(
            ReceiverSeedRecord.seed_hash == seed_hash(value),
            ReceiverSeedRecord.company_id == company,
            ReceiverSeedRecord.environment == environment.value,
            ReceiverSeedRecord.expires_at > at,
            ReceiverSeedRecord.used_at.is_(None),
        )
        .values(used_at=at)
        .returning(ReceiverSeedRecord.seed_hash)
    )
    return taken.one_or_none() is not None
