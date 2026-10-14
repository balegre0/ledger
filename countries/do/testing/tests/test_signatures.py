lazy from collections.abc import Callable

import pytest

from ledger_country_do_testing.pki import Authority, organization
from ledger_country_do_testing.signatures import (
    SHAPES,
    SignxmlShapeError,
    as_signer,
    as_verifier,
    sign_with_signxml,
    verify_with_signxml,
)


def test_signxml_signs_and_verifies_and_trusts_only_whom_it_is_told() -> None:
    issued = Authority.root().issue(organization())
    stranger = Authority.root().issue(organization())
    signed = sign_with_signxml(SHAPES["ecf"], issued)
    assert verify_with_signxml(signed)
    assert verify_with_signxml(signed, trusted=[issued.certificate])
    assert not verify_with_signxml(signed, trusted=[stranger.certificate])
    assert not verify_with_signxml(signed.replace(b"101850043", b"101850044"))
    assert not verify_with_signxml(SHAPES["ecf"])


def test_signxml_signs_in_the_ds_prefix_too() -> None:
    issued = Authority.root().issue(organization())
    signed = sign_with_signxml(SHAPES["ecf"], issued, prefixed=True)
    assert b"<ds:Signature" in signed
    assert verify_with_signxml(signed)


@pytest.mark.parametrize("described", [as_verifier, as_signer])
def test_an_object_signxml_did_not_make_is_refused_by_name(described: Callable[[object], object]) -> None:
    with pytest.raises(SignxmlShapeError, match="no longer what _"):
        described(object())
