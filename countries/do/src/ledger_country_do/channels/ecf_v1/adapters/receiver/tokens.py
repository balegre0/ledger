"""The receiver's credentials (spec §3.8, §6.3): its seeds exchanged for tokens, tokens checked, both purged.

A signed seed (``seeds``) is exchanged for a token in one unit: the seed taken and the token kept, or
neither. A token is 32 random bytes (URL-safe base64), kept by its SHA-256 for an hour
(``receiver_tokens``, outside row-level security), bound to the company, its mode and environment, and to
the sender (the RNCs and cédulas its certificate names, or a DGII stand-in). A request names it as
``Authorization: Bearer``; the receiver answers who sent it, for that company and environment only.

Times are the channel's clock (``ChannelContext.clock``), which the receiver's answers state too.
``purge`` deletes what expired, ``PURGE_BATCH`` rows at a time, each batch in a unit of its own
(``do_ecf.purge_receiver_credentials``, every hour).
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import delete, insert, select
lazy from sqlalchemy import Delete

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import ReceiverSeedRecord, ReceiverTokenRecord
from ledger_country_do.channels.ecf_v1.adapters.receiver import seeds
from ledger_country_do.channels.ecf_v1.application.receiving import Sender
lazy from ledger_country_do.channels.ecf_v1.adapters.receiver.seeds import SignedSeed
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.data import BoundDatabase

TOKEN_BYTES: Final = 32
TOKEN_LIFETIME: Final = timedelta(hours=1)
PURGE_BATCH: Final = 5_000


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Token:
    """A token issued: its value (answered once, never kept), when it was issued and until when it serves."""

    value: str
    issued_at: datetime
    expires_at: datetime


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@final
class ReceiverCredentials:
    """The receiver's seeds and tokens (see the module), in the channel's schema, outside any tenant."""

    __slots__ = ("_clock", "_database")

    def __init__(self, database: BoundDatabase, *, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def seed(self, company: UUID, environment: Environment) -> tuple[str, datetime]:
        """A new seed of ``company`` in ``environment``, and the moment it was made."""
        value, at = seeds.new_seed(), self._clock()
        async with self._database.unit(None) as unit:
            await seeds.keep(unit.connection, value, company=company, environment=environment, at=at)
        return value, at

    async def exchange(
        self, signed: SignedSeed, *, company: UUID, mode: Mode, environment: Environment
    ) -> Token | None:
        """A token for ``signed``'s sender, its seed taken in the same unit; ``None`` if it cannot be."""
        at = self._clock()
        token = Token(value=secrets.token_urlsafe(TOKEN_BYTES), issued_at=at, expires_at=at + TOKEN_LIFETIME)
        async with self._database.unit(None) as unit:
            taken = await seeds.take(
                unit.connection, signed.value, company=company, environment=environment, at=at
            )
            if not taken:
                return None
            await unit.connection.execute(
                insert(ReceiverTokenRecord).values(
                    token_hash=_hash(token.value),
                    company_id=company,
                    mode=mode.value,
                    environment=environment.value,
                    senders=sorted(signed.sender.identities),
                    stand_in=signed.sender.stand_in,
                    expires_at=token.expires_at,
                )
            )
        return token

    async def sender(self, token: str, *, company: UUID, environment: Environment) -> Sender | None:
        """Who ``token`` was issued to, if it is a live token of ``company`` in ``environment``."""
        async with self._database.unit(None) as unit:
            found = (
                await unit.connection.execute(
                    select(ReceiverTokenRecord.senders, ReceiverTokenRecord.stand_in).where(
                        ReceiverTokenRecord.token_hash == _hash(token),
                        ReceiverTokenRecord.company_id == company,
                        ReceiverTokenRecord.environment == environment.value,
                        ReceiverTokenRecord.expires_at > self._clock(),
                    )
                )
            ).one_or_none()
        if found is None:
            return None
        senders, stand_in = found
        return Sender(identities=frozenset(senders), stand_in=stand_in)

    async def purge(self, batch: int = PURGE_BATCH) -> None:
        """Delete every seed and token expired by now, ``batch`` rows a unit (see the module)."""
        now = self._clock()
        seeds_expired = (
            select(ReceiverSeedRecord.seed_hash)
            .where(ReceiverSeedRecord.expires_at <= now)
            .limit(batch)
            .scalar_subquery()
        )
        tokens_expired = (
            select(ReceiverTokenRecord.token_hash)
            .where(ReceiverTokenRecord.expires_at <= now)
            .limit(batch)
            .scalar_subquery()
        )
        for statement in (
            delete(ReceiverSeedRecord).where(ReceiverSeedRecord.seed_hash.in_(seeds_expired)),
            delete(ReceiverTokenRecord).where(ReceiverTokenRecord.token_hash.in_(tokens_expired)),
        ):
            while await self._deleted(statement) >= batch:
                pass

    async def _deleted(self, statement: Delete) -> int:
        """How many rows ``statement`` deleted, in a unit of its own."""
        async with self._database.unit(None) as unit:
            return (await unit.connection.execute(statement)).rowcount
