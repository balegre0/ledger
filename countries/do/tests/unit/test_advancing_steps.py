"""The pieces the ``do_ecf.advance`` job decides with: waits, the track-id window, where DGII lists track ids.

DGII out of reach (a 429, no connection) is tried again no sooner than ``UNAVAILABLE_WAIT``, later as the
document was asked about more often; an unknown outcome is asked about for ``TRACK_ID_WINDOW`` from the
moment it became unknown; CerteCF lists no track ids, as the DGII client's endpoints say.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import Service, offered
from ledger_country_do.channels.ecf_v1.application.documents import (
    DocumentIncoherentError,
    EcfDocument,
    Signature,
)
from ledger_country_do.channels.ecf_v1.application.ports import Archived
from ledger_country_do.channels.ecf_v1.application.steps import Again, Finished, ParkReason, Wait
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.mapping import issuer_rnc
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_country_do.channels.ecf_v1.domain.polling import (
    POLL_EVERY,
    TRACK_ID_WINDOW,
    UNAVAILABLE_WAIT,
    after_unavailable,
    next_poll,
    window_open,
)
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do_testing.fiscal import fiscal_draft

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_dgii_out_of_reach_is_tried_again_no_sooner_than_the_unavailable_wait() -> None:
    assert timedelta(seconds=30) == UNAVAILABLE_WAIT
    assert [after_unavailable(polls) for polls in range(8)] == [
        timedelta(seconds=30),
        timedelta(seconds=30),
        timedelta(seconds=30),
        timedelta(seconds=30),
        timedelta(seconds=30),
        timedelta(seconds=60),
        POLL_EVERY,
        POLL_EVERY,
    ]


@given(st.integers(min_value=0, max_value=10_000))
def test_the_wait_after_dgii_was_out_of_reach_is_never_shorter_than_a_poll_s(polls: int) -> None:
    assert after_unavailable(polls) == max(next_poll(polls), UNAVAILABLE_WAIT)


def test_an_unknown_outcome_is_asked_about_for_a_day_from_when_it_became_unknown() -> None:
    assert timedelta(hours=24) == TRACK_ID_WINDOW
    assert window_open(NOW, NOW)
    assert window_open(NOW, NOW + TRACK_ID_WINDOW - timedelta(microseconds=1))
    assert not window_open(NOW, NOW + TRACK_ID_WINDOW)
    assert not window_open(NOW, NOW + timedelta(days=3))


def test_the_window_compares_moments_with_their_offsets() -> None:
    with pytest.raises(NaiveMomentError):
        window_open(NOW.replace(tzinfo=None), NOW)
    with pytest.raises(NaiveMomentError):
        window_open(NOW, NOW.replace(tzinfo=None))


@pytest.mark.parametrize("environment", list(Environment))
def test_track_ids_are_listed_where_the_dgii_client_offers_them(environment: Environment) -> None:
    assert environment.lists_track_ids is offered(Service.TRACK_IDS, environment)
    assert environment.lists_track_ids is (environment is not Environment.CERTECF)


def test_dgii_files_a_document_under_its_issuer_s_rnc() -> None:
    draft = fiscal_draft()
    assert draft.issuer.tax_id is not None
    assert issuer_rnc(draft) == draft.issuer.tax_id.value == "101850043"


def test_a_run_answers_a_wait_with_its_reason_a_moment_or_nothing_left() -> None:
    assert Wait(ParkReason.OUTCOME_UNKNOWN).reason.value == "outcome_unknown"
    assert Again(NOW).at == NOW
    assert Finished() == Finished()
    assert {reason.value for reason in ParkReason} == {
        "certificate",
        "blocked",
        "outcome_unknown",
        "answer_mismatch",
        "range_missing",
    }


def a_document() -> EcfDocument:
    """A numbered 31 of the sample draft."""
    return EcfDocument(
        id=UUID(int=7),
        invoice=fiscal_draft().document,
        environment=Environment.TESTECF,
        encf=Encf(EcfType.CREDIT_INVOICE, 1),
        range_id=UUID(int=8),
        range_expires_on=None,
        state=EcfDocumentState.NUMBERED,
        route=Route.RECEPTION,
        delivery=DeliveryState.NOT_STARTED,
        issued_on=NOW.date(),
        signature=None,
        draft=fiscal_draft(),
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


SIGNATURE = Signature(
    signed_at=NOW,
    security_code="AbC123",
    qr_url="https://ecf.dgii.gov.do/testecf/consultatimbre?x",
    emission_mode=EmissionMode.NORMAL,
    ecf=Archived(key="do-ecf-v1/test/o/c/ecf/a.xml", sha256=bytes(32)),
    rfce=None,
)


def test_a_document_lacking_what_its_state_requires_is_a_defect_never_sent() -> None:
    """The table's checks forbid these rows; were one read, the job stops on it rather than guess."""
    numbered = a_document()
    for missing, read in (
        ("a signature", numbered.signed),
        ("a track id", numbered.tracked),
        ("the moment its outcome became unknown", numbered.unknown_since),
    ):
        with pytest.raises(DocumentIncoherentError, match=missing) as caught:
            read()
        assert (caught.value.code, caught.value.kind) == ("do_ecf_document_incoherent", "internal")
    with pytest.raises(DocumentIncoherentError, match="a summary"):
        SIGNATURE.sent(Route.RFCE)


def test_what_is_sent_is_the_summary_on_the_rfce_route_else_the_e_cf() -> None:
    summarized = replace(SIGNATURE, rfce=Archived(key="do-ecf-v1/test/o/c/rfce/b.xml", sha256=bytes(32)))
    assert SIGNATURE.sent(Route.RECEPTION) == SIGNATURE.ecf
    assert summarized.sent(Route.RFCE) == summarized.rfce
    signed = replace(a_document(), signature=SIGNATURE, track_id="t", track_id_since=NOW)
    assert (signed.signed(), signed.tracked(), signed.unknown_since()) == (SIGNATURE, "t", NOW)
