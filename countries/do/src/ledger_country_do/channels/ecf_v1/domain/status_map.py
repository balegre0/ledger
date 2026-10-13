"""DGII's status codes, each mapped to a channel state, a fiscal status and a severity (spec §8.6.4).

DT-DGII, "Consulta de resultado e-CF": 0 No encontrado, 1 Aceptado, 2 Rechazado, 3 En Proceso, 4 Aceptado
Condicional. Aceptado Condicional "implica la validez del e-CF": it is accepted, delivered like an accepted
one, its messages kept as warnings to fix in future documents, nothing re-issued ([06] §1). A rejection burns
the e-NCF unless DGII says ``secuenciaUtilizada = false`` (invalid certificate or signature, invalid XML,
signer not delegated, e-NCF not authorized or expired, RNC not an electronic issuer, unknown or inactive),
which releases it for reuse; an absent flag burns it (never reuse a number DGII may have recorded). The RFCE
answers at once with 1, 2 or 4 (F2). A code DGII never documented for the service is an error, never a guess.
Messages are kept verbatim, at most 20 of 500 characters (spec §8.4, kit events).

``FiscalStatus`` and ``Severity`` are provisional copies of fiscal-kit's (spec §8.4), swapped at integration.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping, Sequence

from ledger_country_do.channels.ecf_v1.domain.errors import EcfUnavailableError, truncate

MAX_MESSAGES: Final = 20
MAX_MESSAGE_TEXT: Final = 500
_MAX_MESSAGE_CODE: Final = 64


class DgiiCode(IntEnum):
    """DGII's ``codigo`` of a validation result."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


DOCUMENTED: Final[Mapping[int, str]] = MappingProxyType(
    {0: "No encontrado", 1: "Aceptado", 2: "Rechazado", 3: "En Proceso", 4: "Aceptado Condicional"}
)
"""DT-DGII's codes and words for ``consultaresultado``, as DGII writes them."""
RFCE_DOCUMENTED: Final[Mapping[int, str]] = MappingProxyType(
    {1: "Aceptado", 2: "Rechazado", 4: "Aceptado Condicional"}
)
"""What the RFCE reception answers (DT-DGII lists the words; the codes are assumed equal: F2)."""


class EcfState(StrEnum):
    """The channel states DGII's answers lead to (a subset of the e-CF state machine, spec §8.6.4)."""

    IN_PROCESS = "in_process"
    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FiscalStatus(StrEnum):
    """The country-neutral status of a fiscal document (fiscal-kit's, spec §8.4)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_OBSERVATIONS = "accepted_with_observations"
    REJECTED = "rejected"
    VOIDED = "voided"


class Severity(StrEnum):
    """What an outcome asks of the platform: nothing, attention, or a person (spec §8.3)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NumberFate(StrEnum):
    """What became of the e-NCF (spec §8.6.3, number accounting)."""

    USED = "used"
    BURNED = "burned"
    RELEASED = "released"


@final
@dataclass(frozen=True, slots=True)
class StatusEntry:
    """Where a DGII code leads."""

    state: EcfState
    status: FiscalStatus
    severity: Severity
    final: bool


STATUS_MAP: Final[Mapping[DgiiCode, StatusEntry]] = MappingProxyType(
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


@final
@dataclass(frozen=True, slots=True)
class DgiiMessage:
    """One of DGII's messages: its code (a number or a word, as text) and its text, verbatim."""

    code: str
    text: str


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """DGII's answer about one document, mapped."""

    code: DgiiCode
    entry: StatusEntry
    messages: tuple[DgiiMessage, ...]
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


def _fate(code: DgiiCode, *, sequence_used: bool | None) -> NumberFate | None:
    """Rejected: burned unless DGII released it; any other final code used it; not final: undecided."""
    if code is DgiiCode.REJECTED:
        return NumberFate.RELEASED if sequence_used is False else NumberFate.BURNED
    return NumberFate.USED if STATUS_MAP[code].final else None


def decide(
    code: int, *, sequence_used: bool | None, messages: Sequence[DgiiMessage], rfce: bool = False
) -> Outcome:
    """DGII's ``code`` (of the RFCE service when ``rfce``) mapped (see the module)."""
    if code not in (RFCE_DOCUMENTED if rfce else DOCUMENTED):
        raise UnknownDgiiCodeError(code)
    dgii = DgiiCode(code)
    kept = tuple(
        DgiiMessage(truncate(message.code, _MAX_MESSAGE_CODE), truncate(message.text, MAX_MESSAGE_TEXT))
        for message in messages[:MAX_MESSAGES]
    )
    return Outcome(dgii, STATUS_MAP[dgii], kept, _fate(dgii, sequence_used=sequence_used))
