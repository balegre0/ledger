"""DGII's status codes, each mapped to a channel state, a fiscal status and a severity (spec §8.6.4).

DT-DGII, "Consulta de resultado e-CF": 0 No encontrado, 1 Aceptado, 2 Rechazado, 3 En Proceso, 4 Aceptado
Condicional. Aceptado Condicional "implica la validez del e-CF": it is accepted, delivered like an accepted
one, its messages kept as warnings to fix in future documents, nothing re-issued ([06] §1). A rejection burns
the e-NCF unless DGII says ``secuenciaUtilizada = false`` (invalid certificate or signature, invalid XML,
signer not delegated, e-NCF not authorized or expired, RNC not an electronic issuer, unknown or inactive),
which releases it for reuse; an absent flag burns it (never reuse a number DGII may have recorded). The RFCE
answers at once with 1, 2 or 4 (F2). ``consultarfce``, which settles an RFCE whose answer was lost, answers
0 (pending: DGII holds the summary and has not decided), 1, 2 or 4 ([06] §2; Decision 37 of the v2-M3b plan).
A code DGII never documented for the service is an error, never a guess.
Messages are kept verbatim, as fiscal-kit's ``AuthorityMessage`` in Spanish, within the kit's bounds
(``kept``).

``FiscalStatus``, ``Severity``, ``StatusEntry`` and the exhaustiveness check (``StatusMap``) are fiscal-kit's
(spec §8.4); the states, the number's fate and what a code decides are the channel's.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping, Sequence

from ledger_country_do.channels.ecf_v1.domain.errors import EcfUnavailableError
from ledger_fiscal_kit.status import (
    AuthorityMessage,
    FiscalStatus,
    Severity,
    StatusEntry,
    StatusMap,
    UnknownAuthorityCodeError,
    kept,
)

_LANG: Final = "es"
"""The language DGII writes its messages in."""


class DgiiCode(IntEnum):
    """DGII's ``codigo`` of a validation result."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


DOCUMENTED: Final[Mapping[DgiiCode, str]] = MappingProxyType(
    {
        DgiiCode.NOT_FOUND: "No encontrado",
        DgiiCode.ACCEPTED: "Aceptado",
        DgiiCode.REJECTED: "Rechazado",
        DgiiCode.IN_PROCESS: "En Proceso",
        DgiiCode.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
    }
)
"""DT-DGII's codes and words for ``consultaresultado``, as DGII writes them."""
RFCE_DOCUMENTED: Final[Mapping[DgiiCode, str]] = MappingProxyType(
    {
        DgiiCode.ACCEPTED: "Aceptado",
        DgiiCode.REJECTED: "Rechazado",
        DgiiCode.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
    }
)
"""What the RFCE reception answers (DT-DGII lists the words; the codes are assumed equal: F2)."""


class RfceQueryCode(IntEnum):
    """``consultarfce``'s ``codigo``: 0 is pending there, not "not found" as for ``consultaresultado``."""

    PENDING = 0
    ACCEPTED = 1
    REJECTED = 2
    ACCEPTED_CONDITIONALLY = 4


RFCE_QUERY_DOCUMENTED: Final = frozenset(RfceQueryCode)
"""What ``consultarfce`` answers ([06] §2: states 0, 1 and 2, and Aceptado Condicional)."""


class DgiiService(StrEnum):
    """The services whose answers carry a ``codigo``, each with its own map."""

    RESULT = "consultaresultado"
    RFCE = "recepcionfc"
    RFCE_QUERY = "consultarfce"


class EcfState(StrEnum):
    """The channel states DGII's answers lead to (a subset of the e-CF state machine, spec §8.6.4)."""

    IN_PROCESS = "in_process"
    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"


class NumberFate(StrEnum):
    """What became of the e-NCF (spec §8.6.3, number accounting)."""

    USED = "used"
    BURNED = "burned"
    RELEASED = "released"


_ENTRIES: Final[Mapping[DgiiCode, StatusEntry[EcfState]]] = MappingProxyType(
    {
        DgiiCode.NOT_FOUND: StatusEntry(
            EcfState.OUTCOME_UNKNOWN, FiscalStatus.PENDING, Severity.INFO, final=False
        ),
        DgiiCode.ACCEPTED: StatusEntry(EcfState.ACCEPTED, FiscalStatus.ACCEPTED, Severity.INFO, final=True),
        DgiiCode.REJECTED: StatusEntry(EcfState.REJECTED, FiscalStatus.REJECTED, Severity.ERROR, final=True),
        DgiiCode.IN_PROCESS: StatusEntry(
            EcfState.IN_PROCESS, FiscalStatus.PENDING, Severity.INFO, final=False
        ),
        DgiiCode.ACCEPTED_CONDITIONALLY: StatusEntry(
            EcfState.ACCEPTED_CONDITIONALLY,
            FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
            Severity.WARNING,
            final=True,
        ),
    }
)


STATUS_MAP: Final = StatusMap[DgiiCode, EcfState](_ENTRIES, documented=DOCUMENTED)
"""Where each of ``consultaresultado``'s codes leads."""
RFCE_STATUS_MAP: Final = StatusMap[DgiiCode, EcfState](
    MappingProxyType({code: _ENTRIES[code] for code in RFCE_DOCUMENTED}), documented=RFCE_DOCUMENTED
)
"""Where each of the RFCE reception's codes leads: as ``consultaresultado``'s same code."""
RFCE_QUERY_MAP: Final = StatusMap[RfceQueryCode, EcfState](
    MappingProxyType(
        {
            RfceQueryCode.PENDING: StatusEntry(
                EcfState.IN_PROCESS, FiscalStatus.PENDING, Severity.INFO, final=False
            ),
            **{code: _ENTRIES[DgiiCode(code)] for code in RfceQueryCode if code is not RfceQueryCode.PENDING},
        }
    ),
    documented=RFCE_QUERY_DOCUMENTED,
)
"""Where each of ``consultarfce``'s codes leads: pending is in process; the others as the reception's."""
_MAPS: Final[Mapping[DgiiService, StatusMap[DgiiCode, EcfState] | StatusMap[RfceQueryCode, EcfState]]] = (
    MappingProxyType(
        {
            DgiiService.RESULT: STATUS_MAP,
            DgiiService.RFCE: RFCE_STATUS_MAP,
            DgiiService.RFCE_QUERY: RFCE_QUERY_MAP,
        }
    )
)


@final
@dataclass(frozen=True, slots=True)
class DgiiMessage:
    """One of DGII's messages: its code (a number or a word, as text) and its text, verbatim."""

    code: str
    text: str

    def authority(self) -> AuthorityMessage:
        """The message as fiscal-kit's, in DGII's language."""
        return AuthorityMessage(code=self.code, message=self.text, lang=_LANG)


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """DGII's answer about one document, mapped."""

    code: int
    """The ``codigo`` as DGII answered it; what it means is ``entry``'s (it differs between services)."""
    entry: StatusEntry[EcfState]
    messages: tuple[AuthorityMessage, ...]
    number: NumberFate | None
    """``None`` while DGII has not decided."""

    @property
    def state(self) -> EcfState:
        """The channel state."""
        return self.entry.state

    @property
    def status(self) -> FiscalStatus:
        """The fiscal status."""
        return self.entry.status

    @property
    def severity(self) -> Severity:
        """The severity."""
        return self.entry.severity

    @property
    def final(self) -> bool:
        """Whether DGII has decided."""
        return self.entry.final


class UnknownDgiiCodeError(EcfUnavailableError):
    """DGII answered a code it never documented for the service: read as an outage, retried later."""

    code: ClassVar[str] = "do_ecf_dgii_code_unknown"

    def __init__(self, found: int) -> None:
        super().__init__(
            f"DGII answered code {found}, which its documentation does not define for this service"
        )


def _fate(entry: StatusEntry[EcfState], *, sequence_used: bool | None) -> NumberFate | None:
    """Rejected: burned unless DGII released it; any other final code used it; not final: undecided."""
    if entry.state is EcfState.REJECTED:
        return NumberFate.RELEASED if sequence_used is False else NumberFate.BURNED
    return NumberFate.USED if entry.final else None


def decide(
    code: int,
    *,
    sequence_used: bool | None,
    messages: Sequence[DgiiMessage],
    service: DgiiService = DgiiService.RESULT,
) -> Outcome:
    """DGII's ``code``, as ``service`` answered it, mapped (see the module).

    ``UnknownDgiiCodeError`` (the kit's ``UnknownAuthorityCodeError`` as the channel's) for a code the
    service never documented.
    """
    try:
        entry = _MAPS[service].entry(code)
    except UnknownAuthorityCodeError as error:
        raise UnknownDgiiCodeError(code) from error
    found = kept([message.authority() for message in messages])
    return Outcome(code, entry, found, _fate(entry, sequence_used=sequence_used))
