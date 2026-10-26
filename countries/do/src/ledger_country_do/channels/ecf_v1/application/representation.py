"""A signed e-CF's printed representation (spec §8.6.7, §9.8; Decisions 12, 13, 26; IT §18.1).

It is rendered (``Printing``) from the signed e-CF the archive keeps, read back against its SHA-256 (Decision
12), never from the invoice: what it prints is what DGII received, with the security code, QR URL and
emission mode the document recorded when it was signed. Every field it prints that the invoice does not carry
is also in the document's status (``details["do"]``, §9.8), so a client may print its own.

The first render archives the PDF (``representation``, kept as long as the e-CF) and records its key and
SHA-256 on the document; every later one answers the archived bytes, rendering nothing. Rendering is
deterministic (Decision 26), so two first renders at once archive one object and record it once; should two
differ (two versions of the PDF library side by side during a rollout), the one recorded first is the one
both answer.

A document not signed yet has none (``NotSignedError``, a conflict: it is signed within seconds, or waits
for a certificate); an invoice the channel never issued has none (``DocumentNotIssuedError``).
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, final

from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError
from ledger_kernel.errors import NotFoundError
lazy from ledger_country_do.channels.ecf_v1.application.documents import Signature
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archive
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_fiscal_kit.drafts import DocumentRef
lazy from ledger_kernel.tenancy import Tenant


class NotSignedError(EcfConflictError):
    """The document is not signed yet: it has no printed representation until it is."""

    code: ClassVar[str] = "do_ecf_not_signed"

    def __init__(self) -> None:
        super().__init__("the e-CF is not signed yet: its printed representation follows its signature")


class DocumentNotIssuedError(EcfError, NotFoundError):
    """The channel never issued the invoice an e-CF, in the call's company and mode."""

    code: ClassVar[str] = "do_ecf_document_not_issued"

    def __init__(self) -> None:
        super().__init__("no e-CF was issued for the invoice in the company and mode")


class Printing(Protocol, metaclass=ABCMeta):
    """Renders a signed e-CF's printed representation, as a PDF."""

    @abstractmethod
    async def pdf(self, signed_ecf: bytes, signature: Signature) -> bytes:
        """``signed_ecf`` as printed, with what ``signature`` recorded of it: the same input, the same PDF."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Represented:
    """A document's printed representation: its e-NCF (which names the file) and the PDF."""

    encf: Encf
    pdf: bytes = field(repr=False)


@final
class Representing:
    """The printed representations of the channel's documents (see the module)."""

    __slots__ = ("_archive", "_printing", "_units")

    def __init__(self, units: NumberingUnits, archive: Archive, printing: Printing) -> None:
        self._units = units
        self._archive = archive
        self._printing = printing

    async def representation(self, tenant: Tenant, invoice: DocumentRef) -> Represented:
        """``invoice``'s e-CF as printed: archived on its first render, answered from the archive after."""
        async with self._units.unit(tenant) as unit:
            document = await unit.documents.of_invoice(invoice)
            if document is None:
                raise DocumentNotIssuedError
            kept = await unit.documents.representation(document.id)
        signature = document.signature
        if signature is None:
            raise NotSignedError
        if kept is not None:
            return Represented(encf=document.encf, pdf=await self._archive.read(kept))
        pdf = await self._printing.pdf(await self._archive.read(signature.ecf), signature)
        archived = await self._archive.representation(tenant, pdf)
        async with self._units.unit(tenant) as unit:
            recorded = await unit.documents.represented(document.id, archived)
        if recorded != archived:  # another render, of other bytes, recorded first
            pdf = await self._archive.read(recorded)
        return Represented(encf=document.encf, pdf=pdf)
