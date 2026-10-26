"""What the channel archives (§6.8; Decisions 11-13), and how: ``ArchiveKind`` and ``PortArchive``.

A key is fiscal-kit's ``archive_key(...)`` with ``kind`` an ``ArchiveKind``:
``do-ecf-v1/<mode>/<organization>/<company>/<kind>/<sha256 hex>.<extension>``. The kit checks only a kind's
shape; which kinds exist is the channel's.

``PortArchive`` is the use cases' ``Archive`` over fiscal-kit's ``ArchivePort`` (the app's archive): each
document kept once under its key, until ten years from the day kept (Código Tributario art. 50 h; the app's
archive keeps none less than its own retention), and read back checked against its SHA-256 (a mismatch or a
missing object passes as the archive's defect); its unavailability the channel's ``ArchiveUnavailableError``.
``ledger-migrate`` archives nothing and binds no archive: its channel keeps nothing (``UnboundArchive``).
"""

from enum import StrEnum
from typing import ClassVar, Final, final, override

from ledger_country_do.channels.ecf_v1.application.ports import Archive, Archived, ArchiveUnavailableError
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_fiscal_kit.ports import ArchiveUnavailableError as PortUnavailableError
from ledger_fiscal_kit.ports import archive_key
from ledger_platform.runtime.archiving import years_after
lazy from ledger_fiscal_kit.ports import ArchivePort
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Tenant

RETENTION_YEARS: Final = 10
"""How long a signed document is kept from the day it is (Código Tributario art. 50 h; DGII's IT §9.1)."""
XML: Final = "application/xml"


class ArchiveKind(StrEnum):
    """What an archived object is."""

    ECF = "ecf"
    """A signed e-CF, as submitted."""
    RFCE = "rfce"
    """A signed consumer-invoice summary (RFCE), as submitted."""
    ANECF = "anecf"
    """A signed range void (ANECF)."""
    ACECF = "acecf"
    """A signed commercial approval or rejection (ACECF), sent or received."""
    ARECF = "arecf"
    """A signed receipt (ARECF), sent or received."""
    ARECF_REFUSED = "arecf-refused"
    """A buyer's receipt that failed verification: kept as evidence, never trusted."""
    RECEIVED = "received"
    """An e-CF a supplier sent us, raw, as it arrived."""
    REPRESENTATION = "representation"
    """A printed representation (PDF), as first rendered."""


@final
class PortArchive(Archive):
    """``Archive`` over the app's ``ArchivePort`` (see the module)."""

    __slots__ = ("_clock", "_port")

    def __init__(self, port: ArchivePort, *, clock: Clock) -> None:
        self._port = port
        self._clock = clock

    @override
    async def ecf(self, tenant: Tenant, signed: bytes) -> Archived:
        return await self._kept(tenant, ArchiveKind.ECF, signed)

    @override
    async def rfce(self, tenant: Tenant, signed: bytes) -> Archived:
        return await self._kept(tenant, ArchiveKind.RFCE, signed)

    @override
    async def receipt(self, tenant: Tenant, arecf: bytes, *, refused: bool) -> Archived:
        return await self._kept(tenant, ArchiveKind.ARECF_REFUSED if refused else ArchiveKind.ARECF, arecf)

    @override
    async def read(self, archived: Archived) -> bytes:
        try:
            return await self._port.get(archived.key, sha256=archived.sha256)
        except PortUnavailableError as error:
            raise ArchiveUnavailableError from error

    async def _kept(self, tenant: Tenant, kind: ArchiveKind, content: bytes) -> Archived:
        key = archive_key(channel=KEY, tenant=tenant, kind=kind.value, content=content, extension="xml")
        retain_until = years_after(self._clock().date(), RETENTION_YEARS)
        try:
            stored = await self._port.put_once(key, content, retain_until=retain_until, media_type=XML)
        except PortUnavailableError as error:
            raise ArchiveUnavailableError from error
        return Archived(key=stored.key, sha256=stored.sha256)


class ArchiveNotBoundError(EcfError):
    """An app that binds no archive was asked to keep a document: a defect (``ledger-migrate`` binds none)."""

    code: ClassVar[str] = "do_ecf_archive_not_bound"

    def __init__(self) -> None:
        super().__init__("this app keeps no document: it binds no archive")


@final
class UnboundArchive(Archive):
    """The archive of an app that keeps nothing (``ledger-migrate``): every request is a defect."""

    __slots__ = ()

    @override
    async def ecf(self, tenant: Tenant, signed: bytes) -> Archived:
        raise ArchiveNotBoundError

    @override
    async def rfce(self, tenant: Tenant, signed: bytes) -> Archived:
        raise ArchiveNotBoundError

    @override
    async def receipt(self, tenant: Tenant, arecf: bytes, *, refused: bool) -> Archived:
        raise ArchiveNotBoundError

    @override
    async def read(self, archived: Archived) -> bytes:
        raise ArchiveNotBoundError
