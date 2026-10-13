from datetime import UTC, datetime

import pytest

from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_country_do_testing.pki import Authority, pem
from ledger_country_do_testing.xmlsec import CONTEXT, XMLSEC_IMAGE, Xmlsec


def test_xmlsec1_refuses_a_document_without_a_signature_and_a_naive_moment() -> None:
    root = Authority.root()
    with Xmlsec.started() as xmlsec:
        verdict = xmlsec.verify(b"<a/>", trusted=pem(root.certificate), untrusted=b"", at=datetime.now(UTC))
        with pytest.raises(NaiveMomentError):
            xmlsec.verify(b"<a/>", trusted=b"", untrusted=b"", at=datetime.now(UTC).replace(tzinfo=None))
    assert not verdict.verified
    assert "ignature" in verdict.output


def test_the_image_is_ours_and_pinned() -> None:
    dockerfile = CONTEXT.joinpath("Dockerfile").read_text(encoding="utf-8")
    assert XMLSEC_IMAGE.startswith("ledger-do-xmlsec1:")
    assert "@sha256:" in dockerfile
    assert "xmlsec1=1.2.41-1+b1" in dockerfile
