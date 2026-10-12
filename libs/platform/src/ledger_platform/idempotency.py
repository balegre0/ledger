"""Idempotency keys: a POST sent again with the same key changes state once and answers the same both times.

A route that changes state takes an ``Idempotency-Key`` (§10) and runs its work through ``Idempotency.once``
inside the transaction that does the work: the key is claimed first, the work runs, and its response is stored
in the claim's row, so the claim, the state change, its events and the response commit together or not at all.

* The same key again with the same request (method, path and body) answers the stored response without running
  the work: the outcome is ``replayed``.
* The same key with another request is an ``IdempotencyKeyReusedError`` (422).
* Two requests with one key at once: the second's claim waits for the first's transaction to end, then replays
  its response; if the first rolled back, the second does the work. A wait longer than the database's
  ``lock_timeout`` is an ``IdempotencyKeyInUseError`` (409); retried later, the request replays.
* A request that fails stores nothing (its transaction rolls back), so its key is free for a retry.
* A key is kept for ``retention`` (24 hours) from its first use; after that the same key runs the work again.

A key belongs to its caller: a row is keyed by the organization, the subject and the key. The table is under
``organization_isolation``, so a transaction sees its organization's keys only, and expired rows are deleted
by the organization's own requests: each claim deletes up to ``purge_batch`` of them, oldest first.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Mapping

import psycopg
from sqlalchemy import DateTime, Index, LargeBinary, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Mapped, mapped_column
lazy from sqlalchemy import ColumnElement

from ledger_kernel.errors import ConflictError, InvalidInputError, LedgerError, excerpt
from ledger_platform.database import PlatformTable
from ledger_platform.options import InvalidOptionError, require_at_least
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_platform.database import UnitOfWork

DEFAULT_RETENTION: Final = timedelta(hours=24)
MAX_KEY_LENGTH: Final = 255
PURGE_BATCH: Final = 100
_KEY: Final = re.compile(rf"[!-~]{{1,{MAX_KEY_LENGTH}}}", re.ASCII)
"""Visible ASCII without spaces: what a client can put in a header and see back unchanged."""
_LENGTH_BYTES: Final = 8


class IdempotencyRecord(PlatformTable):
    """A key a caller used, the request it came with (as a fingerprint) and the response it got."""

    __tablename__: str = "idempotency_keys"

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    """SHA-256 of the method, the path and the canonical body of the request that first used the key."""
    status: Mapped[int | None] = mapped_column()
    headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_idempotency_keys_expiry", IdempotencyRecord.organization_id, IdempotencyRecord.expires_at)
"""The purge reads an organization's expired keys without visiting the others."""


class InvalidIdempotencyKeyError(InvalidInputError):
    """A key that is not 1 to ``MAX_KEY_LENGTH`` visible ASCII characters."""

    code: ClassVar[str] = "invalid_idempotency_key"

    def __init__(self, key: str) -> None:
        super().__init__(
            f"an Idempotency-Key is 1 to {MAX_KEY_LENGTH} visible ASCII characters, not {excerpt(key)}"
        )


class IdempotencyKeyReusedError(InvalidInputError):
    """The key was used for another request: a client bug, never to be retried as it is."""

    code: ClassVar[str] = "idempotency_key_reused"

    def __init__(self) -> None:
        super().__init__("this Idempotency-Key was used for a different request")


class IdempotencyKeyInUseError(ConflictError):
    """A request with the same key is still being handled; retrying later replays its response."""

    code: ClassVar[str] = "idempotency_key_in_use"

    def __init__(self) -> None:
        super().__init__("a request with this Idempotency-Key is still being handled; retry later")


class UnansweredIdempotencyKeyError(LedgerError):
    """A committed key without a stored response: the claim and the response always commit together."""

    code: ClassVar[str] = "unanswered_idempotency_key"

    def __init__(self) -> None:
        super().__init__("a committed idempotency key has no stored response")


class InvalidIdempotencyOptionError(InvalidOptionError):
    """An idempotency option that cannot work."""

    code: ClassVar[str] = "invalid_idempotency_option"
    label: ClassVar[str] = "idempotency option"


@final
@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A response as it is kept and replayed: the status, the body's bytes and the headers that go with it."""

    status: int
    body: bytes
    headers: Mapping[str, str]


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """The response of a request, and whether it was replayed from an earlier one with the same key."""

    response: StoredResponse
    replayed: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Attempt:
    """One use of a key: whose, which key, and the fingerprint of the request it came with."""

    organization: OrganizationId
    subject: SubjectId
    key: str
    fingerprint: bytes

    @classmethod
    def of(cls, principal: Principal, key: str, *, method: str, path: str, body: bytes) -> Attempt:
        """``principal``'s use of ``key`` for the request ``method path`` with ``body`` (canonical bytes)."""
        if _KEY.fullmatch(key) is None:
            raise InvalidIdempotencyKeyError(key)
        digest = hashlib.sha256()
        for part in (method.encode(), path.encode(), body):
            digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))  # no part can run into the next
            digest.update(part)
        return cls(
            organization=principal.organization,
            subject=principal.subject,
            key=key,
            fingerprint=digest.digest(),
        )


_KEY_COLUMNS: Final = (IdempotencyRecord.organization_id, IdempotencyRecord.subject, IdempotencyRecord.key)
"""What identifies a key: its caller (organization and subject) and the key itself."""


def _row_of(attempt: Attempt) -> tuple[ColumnElement[bool], ...]:
    """The row of ``attempt``'s key."""
    return (
        IdempotencyRecord.organization_id == attempt.organization,
        IdempotencyRecord.subject == attempt.subject,
        IdempotencyRecord.key == attempt.key,
    )


@final
class Idempotency:
    """Runs the work of a request once per key (see the module)."""

    __slots__ = ("_purge_batch", "_retention")

    def __init__(self, *, retention: timedelta = DEFAULT_RETENTION, purge_batch: int = PURGE_BATCH) -> None:
        if retention <= timedelta(0):
            raise InvalidIdempotencyOptionError(
                option="retention", requirement="longer than zero", value=retention
            )
        require_at_least(InvalidIdempotencyOptionError, "purge_batch", purge_batch)
        self._retention = retention
        self._purge_batch = purge_batch

    async def once(
        self, unit: UnitOfWork, attempt: Attempt, work: Callable[[], Awaitable[StoredResponse]]
    ) -> Outcome:
        """Claim ``attempt``'s key in ``unit`` and run ``work``, or replay the response stored for it.

        ``unit`` is the transaction the work runs in, scoped to ``attempt``'s organization (or one of its
        companies); it raises ``IdempotencyKeyReusedError`` and ``IdempotencyKeyInUseError``.
        """
        await self._purge(unit, attempt.organization)
        if await self._claim(unit, attempt):
            response = await work()
            await unit.connection.execute(
                update(IdempotencyRecord)
                .where(*_row_of(attempt))
                .values(status=response.status, headers=dict(response.headers), body=response.body)
            )
            return Outcome(response, replayed=False)
        return Outcome(await self._stored(unit, attempt), replayed=True)

    async def _claim(self, unit: UnitOfWork, attempt: Attempt) -> bool:
        """Insert the key, or take over an expired one; false when the key is still in force."""
        fresh = insert(IdempotencyRecord).values(
            organization_id=attempt.organization,
            subject=attempt.subject,
            key=attempt.key,
            fingerprint=attempt.fingerprint,
            expires_at=func.now() + self._retention,
        )
        claim = fresh.on_conflict_do_update(
            index_elements=_KEY_COLUMNS,
            set_={
                "fingerprint": fresh.excluded["fingerprint"],
                "status": None,
                "headers": None,
                "body": None,
                "expires_at": fresh.excluded["expires_at"],
            },
            where=IdempotencyRecord.expires_at <= func.now(),
        ).returning(IdempotencyRecord.key)
        try:
            claimed = (await unit.connection.execute(claim)).first()
        except OperationalError as error:
            if isinstance(error.orig, psycopg.errors.LockNotAvailable):
                raise IdempotencyKeyInUseError from error
            raise
        return claimed is not None

    async def _stored(self, unit: UnitOfWork, attempt: Attempt) -> StoredResponse:
        """The response stored for the key, if the request is the one that first used it."""
        fingerprint, status, headers, body = (
            await unit.connection.execute(
                select(
                    IdempotencyRecord.fingerprint,
                    IdempotencyRecord.status,
                    IdempotencyRecord.headers,
                    IdempotencyRecord.body,
                ).where(*_row_of(attempt))
            )
        ).one()
        if fingerprint != attempt.fingerprint:
            raise IdempotencyKeyReusedError
        if status is None or headers is None or body is None:
            raise UnansweredIdempotencyKeyError
        return StoredResponse(status, body, headers)

    async def _purge(self, unit: UnitOfWork, organization: OrganizationId) -> None:
        """Delete up to ``purge_batch`` expired keys of the organization, oldest first, skipping held ones.

        The expiry index serves the read in its order, so a purge visits the keys it deletes and no others.
        """
        expired = (
            select(*_KEY_COLUMNS)
            .where(
                IdempotencyRecord.organization_id == organization, IdempotencyRecord.expires_at <= func.now()
            )
            .order_by(IdempotencyRecord.expires_at)
            .limit(self._purge_batch)
            .with_for_update(skip_locked=True)
        )
        await unit.connection.execute(delete(IdempotencyRecord).where(tuple_(*_KEY_COLUMNS).in_(expired)))
