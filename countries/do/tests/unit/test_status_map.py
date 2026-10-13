import pytest

from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DOCUMENTED,
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    RFCE_DOCUMENTED,
    STATUS_MAP,
    DgiiCode,
    DgiiMessage,
    EcfState,
    FiscalStatus,
    NumberFate,
    Severity,
    UnknownDgiiCodeError,
    decide,
)

NOTE = (DgiiMessage("613", "La sumatoria de los montos ... excede la tolerancia"),)


def test_every_code_dgii_documents_is_mapped_and_nothing_else() -> None:
    assert set(STATUS_MAP) == set(DgiiCode)
    assert set(DOCUMENTED) == {code.value for code in DgiiCode}
    assert set(RFCE_DOCUMENTED) <= set(DOCUMENTED)
    assert DOCUMENTED[4] == "Aceptado Condicional"


def test_aceptado_condicional_is_valid_with_observations() -> None:
    outcome = decide(4, sequence_used=True, messages=NOTE)
    assert (outcome.state, outcome.status, outcome.severity) == (
        EcfState.ACCEPTED_CONDITIONALLY,
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        Severity.WARNING,
    )
    assert outcome.final
    assert outcome.number is NumberFate.USED
    assert outcome.messages == NOTE


def test_aceptado_uses_the_number() -> None:
    outcome = decide(1, sequence_used=None, messages=())
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
    outcome = decide(2, sequence_used=sequence_used, messages=())
    assert (outcome.status, outcome.severity, outcome.final, outcome.number) == (
        FiscalStatus.REJECTED,
        Severity.ERROR,
        True,
        fate,
    )


@pytest.mark.parametrize(("code", "state"), [(3, EcfState.IN_PROCESS), (0, EcfState.OUTCOME_UNKNOWN)])
def test_en_proceso_and_no_encontrado_are_not_final(code: int, state: EcfState) -> None:
    outcome = decide(code, sequence_used=None, messages=())
    assert (outcome.state, outcome.status, outcome.final, outcome.number) == (
        state,
        FiscalStatus.PENDING,
        False,
        None,
    )


@pytest.mark.parametrize(("code", "rfce"), [(5, False), (-1, False), (3, True), (0, True)])
def test_a_code_dgii_never_documented_for_the_service_is_an_error_not_a_guess(
    *, code: int, rfce: bool
) -> None:
    with pytest.raises(UnknownDgiiCodeError) as caught:
        decide(code, sequence_used=None, messages=(), rfce=rfce)
    assert caught.value.kind == "unavailable"


def test_dgii_s_messages_are_kept_verbatim_within_bounds() -> None:
    many = tuple(DgiiMessage(str(number), "x" * 900) for number in range(30))
    outcome = decide(4, sequence_used=True, messages=many)
    assert len(outcome.messages) == MAX_MESSAGES
    assert all(len(message.text) == MAX_MESSAGE_TEXT for message in outcome.messages)
