from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DOCUMENTED,
    RFCE_DOCUMENTED,
    RFCE_QUERY_DOCUMENTED,
    RFCE_QUERY_MAP,
    RFCE_STATUS_MAP,
    STATUS_MAP,
    DgiiCode,
    DgiiMessage,
    DgiiService,
    EcfState,
    NumberFate,
    RfceQueryCode,
    UnknownDgiiCodeError,
    decide,
)
from ledger_fiscal_kit.status import (
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    AuthorityMessage,
    FiscalStatus,
    Severity,
    StatusMap,
    UnknownAuthorityCodeError,
    UnmappedAuthorityCodeError,
)
lazy from ledger_fiscal_kit.status import StatusEntry

NOTE: Final = (DgiiMessage("613", "La sumatoria de los montos ... excede la tolerancia"),)
KEPT: Final = (
    AuthorityMessage(code="613", message="La sumatoria de los montos ... excede la tolerancia", lang="es"),
)


def test_every_code_dgii_documents_is_mapped_and_nothing_else() -> None:
    assert set(DOCUMENTED) == set(DgiiCode)
    assert set(RFCE_DOCUMENTED) <= set(DOCUMENTED)
    assert DOCUMENTED[DgiiCode.ACCEPTED_CONDITIONALLY] == "Aceptado Condicional"
    assert {code: STATUS_MAP.entry(code).state for code in DgiiCode} == {
        DgiiCode.NOT_FOUND: EcfState.OUTCOME_UNKNOWN,
        DgiiCode.ACCEPTED: EcfState.ACCEPTED,
        DgiiCode.REJECTED: EcfState.REJECTED,
        DgiiCode.IN_PROCESS: EcfState.IN_PROCESS,
        DgiiCode.ACCEPTED_CONDITIONALLY: EcfState.ACCEPTED_CONDITIONALLY,
    }
    assert all(RFCE_STATUS_MAP.entry(code) == STATUS_MAP.entry(code) for code in RFCE_DOCUMENTED)
    with pytest.raises(UnknownAuthorityCodeError):
        RFCE_STATUS_MAP.entry(DgiiCode.IN_PROCESS)


def test_the_exhaustiveness_check_is_the_kit_s() -> None:
    without_in_process: dict[DgiiCode, StatusEntry[EcfState]] = {
        code: STATUS_MAP.entry(code) for code in DgiiCode if code is not DgiiCode.IN_PROCESS
    }
    with pytest.raises(UnmappedAuthorityCodeError, match="code 3 has no entry"):
        StatusMap[DgiiCode, EcfState](without_in_process, documented=DOCUMENTED)


def test_aceptado_condicional_is_valid_with_observations() -> None:
    outcome = decide(4, sequence_used=True, messages=NOTE, service=DgiiService.RESULT)
    assert (outcome.state, outcome.status, outcome.severity) == (
        EcfState.ACCEPTED_CONDITIONALLY,
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        Severity.WARNING,
    )
    assert outcome.final
    assert outcome.number is NumberFate.USED
    assert outcome.messages == KEPT


def test_aceptado_uses_the_number() -> None:
    outcome = decide(1, sequence_used=None, messages=(), service=DgiiService.RESULT)
    assert (outcome.status, outcome.severity, outcome.final, outcome.number) == (
        FiscalStatus.ACCEPTED,
        Severity.INFO,
        True,
        NumberFate.USED,
    )


@pytest.mark.parametrize(
    ("sequence_used", "fate"),
    [(False, NumberFate.RELEASED), (True, NumberFate.BURNED), (None, NumberFate.BURNED)],
)
def test_a_rejection_burns_the_number_unless_dgii_releases_it(
    *, sequence_used: bool | None, fate: NumberFate
) -> None:
    outcome = decide(2, sequence_used=sequence_used, messages=(), service=DgiiService.RESULT)
    assert (outcome.status, outcome.severity, outcome.final, outcome.number) == (
        FiscalStatus.REJECTED,
        Severity.ERROR,
        True,
        fate,
    )


@pytest.mark.parametrize(("code", "state"), [(3, EcfState.IN_PROCESS), (0, EcfState.OUTCOME_UNKNOWN)])
def test_en_proceso_and_no_encontrado_are_not_final(code: int, state: EcfState) -> None:
    outcome = decide(code, sequence_used=None, messages=(), service=DgiiService.RESULT)
    assert (outcome.state, outcome.status, outcome.final, outcome.number) == (
        state,
        FiscalStatus.PENDING,
        False,
        None,
    )


@pytest.mark.parametrize(
    ("code", "service"),
    [
        (5, DgiiService.RESULT),
        (-1, DgiiService.RESULT),
        (3, DgiiService.RFCE),
        (0, DgiiService.RFCE),
        (3, DgiiService.RFCE_QUERY),
        (5, DgiiService.RFCE_QUERY),
    ],
)
def test_a_code_dgii_never_documented_for_the_service_is_an_error_not_a_guess(
    code: int, service: DgiiService
) -> None:
    with pytest.raises(UnknownDgiiCodeError) as caught:
        decide(code, sequence_used=None, messages=(), service=service)
    assert (caught.value.kind, caught.value.code) == ("unavailable", "do_ecf_dgii_code_unknown")
    assert isinstance(caught.value.__cause__, UnknownAuthorityCodeError)


def test_dgii_s_messages_are_kept_verbatim_within_the_kit_s_bounds() -> None:
    many = tuple(DgiiMessage(str(number), "x" * 900) for number in range(30))
    outcome = decide(4, sequence_used=True, messages=many, service=DgiiService.RESULT)
    assert [message.code for message in outcome.messages] == [str(number) for number in range(MAX_MESSAGES)]
    assert all(len(message.message) == MAX_MESSAGE_TEXT for message in outcome.messages)
    assert all(message.message.endswith("...") for message in outcome.messages)


def test_a_dgii_message_is_an_authority_message_in_spanish() -> None:
    assert NOTE[0].authority() == KEPT[0]


def test_consultarfce_s_codes_are_mapped_and_nothing_else() -> None:
    assert RFCE_QUERY_DOCUMENTED == {
        RfceQueryCode.NOT_FOUND: "No encontrado",
        RfceQueryCode.ACCEPTED: "Aceptado",
        RfceQueryCode.REJECTED: "Rechazado",
        RfceQueryCode.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
    }
    assert set(RfceQueryCode) == {0, 1, 2, 4}
    not_found = RFCE_QUERY_MAP.entry(RfceQueryCode.NOT_FOUND)
    assert (not_found.state, not_found.status, not_found.severity, not_found.final) == (
        EcfState.OUTCOME_UNKNOWN,
        FiscalStatus.PENDING,
        Severity.INFO,
        False,
    )
    for code in (RfceQueryCode.ACCEPTED, RfceQueryCode.REJECTED, RfceQueryCode.ACCEPTED_CONDITIONALLY):
        assert RFCE_QUERY_MAP.entry(code) == STATUS_MAP.entry(code)


def test_consultarfce_not_found_leaves_the_outcome_unknown() -> None:
    outcome = decide(0, sequence_used=None, messages=(), service=DgiiService.RFCE_QUERY)
    assert outcome.code is RfceQueryCode.NOT_FOUND
    assert (outcome.state, outcome.status, outcome.final, outcome.number, outcome.words) == (
        EcfState.OUTCOME_UNKNOWN,
        FiscalStatus.PENDING,
        False,
        None,
        "No encontrado",
    )


@pytest.mark.parametrize("code", [1, 2, 4])
def test_consultarfce_s_answers_decide_like_the_reception_s(code: int) -> None:
    query = decide(code, sequence_used=False, messages=NOTE, service=DgiiService.RFCE_QUERY)
    reception = decide(code, sequence_used=False, messages=NOTE, service=DgiiService.RFCE)
    assert query.code is RfceQueryCode(code)
    assert reception.code is DgiiCode(code)
    assert (query.entry, query.messages, query.number, query.words) == (
        reception.entry,
        reception.messages,
        reception.number,
        reception.words,
    )


def test_an_outcome_names_its_code_as_dgii_documents_it() -> None:
    assert decide(3, sequence_used=None, messages=(), service=DgiiService.RESULT).words == "En Proceso"
    assert decide(2, sequence_used=False, messages=(), service=DgiiService.RFCE_QUERY).number is (
        NumberFate.RELEASED
    )
