from typing import Final

import pytest
lazy import httpx

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import (
    SignatureInvalidError,
    verify_enveloped,
)
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.answers import json_list, json_object
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL
from ledger_country_do_testing.fake_dgii.state import DirectoryEntry
from ledger_country_do_testing.samples import SIGNED_AT, credit_invoice
lazy from ledger_country_do_testing.fake_dgii.app import FakeDgii
lazy from ledger_country_do_testing.local_signer import KeyPairSigner
lazy from ledger_country_do_testing.pki import Issued

SIMULATOR: Final = "/testecf/emisorreceptor/fe"
ACECF: Final = (
    b'<?xml version="1.0" encoding="utf-8"?><ACECF><DetalleAprobacionComercial><Version>1.0</Version>'
    b"<RNCEmisor>101850043</RNCEmisor><eNCF>E310000000001</eNCF><FechaEmision>13-10-2026</FechaEmision>"
    b"<MontoTotal>4887.58</MontoTotal><RNCComprador>131098193</RNCComprador><Estado>1</Estado>"
    b"<FechaHoraAprobacionComercial>13-10-2026 10:00:00</FechaHoraAprobacionComercial>"
    b"</DetalleAprobacionComercial></ACECF>"
)


def xml(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("101850043E310000000001.xml", document, "text/xml")}


async def receiver_token(http: httpx.AsyncClient, signer: KeyPairSigner) -> dict[str, str]:
    seed = (await http.get(f"{SIMULATOR}/autenticacion/api/semilla")).content
    answer = await http.post(
        f"{SIMULATOR}/autenticacion/api/validacioncertificado", files=xml(await signer.sign_seed(seed))
    )
    return {"Authorization": f"Bearer {json_object(answer)['token']}"}


async def signed_credit_invoice(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


async def test_testecf_s_directory_answers_dgii_s_simulator_for_any_rnc(
    http: httpx.AsyncClient, fake: FakeDgii
) -> None:
    (entry,) = json_list(
        await http.get(
            "/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "131098193"}
        )
    )
    base = f"{DEFAULT_PUBLIC_URL}/testecf/emisorreceptor"
    assert entry == {
        "nombre": "EMISOR RECEPTOR DGII",
        "rnc": "131098193",
        "urlRecepcion": base,
        "urlAceptacion": base,
        "urlOpcional": base,
    }
    fake.state.receiver_requires_auth = False
    (open_entry,) = json_list(
        await http.get(
            "/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "1"}
        )
    )
    assert open_entry["urlOpcional"] is None


async def test_in_production_only_registered_receivers_are_listed_and_certecf_has_no_directory(
    http: httpx.AsyncClient, fake: FakeDgii
) -> None:
    fake.state.directory["131098193"] = DirectoryEntry(
        name="CONSTRUCTORA DEL CARIBE SRL",
        rnc="131098193",
        reception="https://r.example/a",
        approval="https://r.example/a",
        authentication=None,
    )
    by_rnc = "/ecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc"
    assert json_list(await http.get(by_rnc, params={"RNC": "101850043"})) == []
    assert [entry["rnc"] for entry in json_list(await http.get(by_rnc, params={"RNC": "131098193"}))] == [
        "131098193"
    ]
    assert len(json_list(await http.get("/ecf/consultadirectorio/api/consultas/listado"))) == 1
    assert (await http.get("/certecf/consultadirectorio/api/consultas/listado")).status_code == 404
    assert (await http.get(by_rnc.replace("/ecf/", "/certecf/"), params={"RNC": "1"})).status_code == 404


async def test_the_simulator_receives_an_e_cf_and_answers_a_signed_arecf(
    http: httpx.AsyncClient, signer: KeyPairSigner, receiver_identity: Issued, fake: FakeDgii
) -> None:
    answer = await http.post(
        f"{SIMULATOR}/recepcion/api/ecf",
        files=xml(await signed_credit_invoice(signer)),
        headers=await receiver_token(http, signer),
    )
    assert violations(Format.ARECF, answer.content) == ()
    assert verify_enveloped(answer.content).certificate == receiver_identity.certificate
    detail = "DetalleAcusedeRecibo/"
    found = texts(
        answer.content, f"{detail}RNCEmisor", f"{detail}RNCComprador", f"{detail}eNCF", f"{detail}Estado"
    )
    assert found == ("101850043", "131098193", "E310000000001", "0")
    assert fake.received[-1].kind == "buyer"


async def test_the_simulator_says_why_it_did_not_receive(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    headers = await receiver_token(http, signer)
    document = await signed_credit_invoice(signer)
    reasons: list[tuple[str | None, ...]] = []
    fake.state.receiver_rncs = frozenset({"131098193"})
    for sent in (
        document.replace(b"<TipoIngresos>01</TipoIngresos>", b""),
        document.replace(b"101850043", b"101850044"),
        document,
        document,
    ):
        answer = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(sent), headers=headers)
        reasons.append(
            texts(
                answer.content, "DetalleAcusedeRecibo/Estado", "DetalleAcusedeRecibo/CodigoMotivoNoRecibido"
            )
        )
    assert reasons == [("1", "1"), ("1", "2"), ("0", None), ("1", "3")]
    fake.state.receiver_rncs = frozenset({"999999999"})
    other = await http.post(
        f"{SIMULATOR}/recepcion/api/ecf", files=xml(await signed_credit_invoice(signer, 9)), headers=headers
    )
    assert texts(other.content, "DetalleAcusedeRecibo/CodigoMotivoNoRecibido") == ("4",)


async def test_the_simulator_needs_its_own_token_when_it_publishes_authentication(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    document = await signed_credit_invoice(signer)
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document))).status_code == 401
    dgii_token = {"Authorization": f"Bearer {fake.state.token_for_test()}"}
    assert (
        await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document), headers=dgii_token)
    ).status_code == 401
    fake.state.receiver_requires_auth = False
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document))).status_code == 200
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(b"<a>"))).status_code == 400


async def test_a_tampered_arecf_does_not_verify(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.state.receiver_requires_auth = False
    fake.state.tamper_arecf = True
    answer = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(await signed_credit_invoice(signer)))
    assert b"<Estado>1</Estado>" in answer.content
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(answer.content)


async def test_the_simulator_takes_a_signed_commercial_approval(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    headers = await receiver_token(http, signer)
    approval = await signer.sign_document(ACECF)
    assert (
        await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(approval), headers=headers)
    ).status_code == 200
    assert fake.state.approvals == [approval]
    assert (
        await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(ACECF), headers=headers)
    ).status_code == 400
    not_an_approval = (b"<a>", b"<ACECF />", await signer.sign_document(b"<ACECF><Otro>1</Otro></ACECF>"))
    for sent in not_an_approval:
        answer = await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(sent), headers=headers)
        assert answer.status_code == 400
    changed = approval.replace(b"<MontoTotal>4887.58</MontoTotal>", b"<MontoTotal>4887.59</MontoTotal>")
    assert (
        await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(changed), headers=headers)
    ).status_code == 400
    assert fake.state.approvals == [approval]
    assert (
        await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(approval))
    ).status_code == 401
