from datetime import UTC, datetime
lazy from collections.abc import Iterator

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do_testing.pki import Authority, pem
from ledger_country_do_testing.signatures import SHAPES, sign_with_signxml
from ledger_country_do_testing.xmlsec import Xmlsec
lazy from ledger_country_do_testing.pki import Issued


@pytest.fixture(scope="module")
def xmlsec() -> Iterator[Xmlsec]:
    with Xmlsec.started() as started:
        yield started


@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_shape_we_sign_verifies_in_xmlsec1_with_its_chain(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued, shape: str
) -> None:
    signed = sign_enveloped(SHAPES[shape], issued.key, issued.certificate, seed=shape == "seed")
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    assert "SignedInfo References (ok/all): 1/1" in verdict.output


def test_xmlsec1_verifies_what_signxml_signs(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_with_signxml(SHAPES["ecf"], issued)
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert verdict.verified, verdict.output
    assert "SignedInfo References (ok/all): 1/1" in verdict.output


def test_xmlsec1_refuses_a_tampered_one(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate).replace(b"101850043", b"101850044")
    verdict = xmlsec.verify(
        signed,
        trusted=pem(authority.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert not verdict.verified
    assert "data and digest do not match" in verdict.output


def test_xmlsec1_refuses_a_signer_outside_the_chain(
    xmlsec: Xmlsec, intermediate: Authority, issued: Issued
) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate)
    stranger = Authority.root()
    verdict = xmlsec.verify(
        signed,
        trusted=pem(stranger.certificate),
        untrusted=pem(intermediate.certificate),
        at=datetime.now(UTC),
    )
    assert not verdict.verified
    assert "SignedInfo References (ok/all): 0/0" in verdict.output
