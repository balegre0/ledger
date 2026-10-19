from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import AsyncIterator

import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
lazy import httpx

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.answers import json_list, json_object, objects
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.emitter import MAX_UPLOAD
from ledger_country_do_testing.fake_dgii.state import (
    EveryCheck,
    Scripted,
    SignxmlCheck,
    Status,
    VerifierCheck,
    dgii_check,
)
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.samples import SIGNED_AT, SIGNER_VALID_FROM, consumer_invoice, credit_invoice
from ledger_country_do_testing.signatures import forge
from ledger_fiscal_kit.xml.hardened import MAX_DOCUMENT, texts
from ledger_testing.pki import Authority
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.pki import Issued

AUTH: Final = "/testecf/autenticacion/api/autenticacion"
RECEPTION: Final = "/testecf/recepcion/api/facturaselectronicas"
RESULT: Final = "/testecf/consultaresultado/api/consultas/estado"
TRACK_IDS: Final = "/testecf/consultatrackids/api/trackids/consulta"
RFCE: Final = "/testecf/recepcionfc/api/recepcion/ecf"
RFCE_QUERY: Final = "/testecf/consultarfce/api/Consultas/Consulta"
AUDIENCE: Final = "ecf.fake.test"


def xml(document: bytes, name: str = "101850043E310000000001.xml") -> dict[str, tuple[str, bytes, str]]:
    return {"xml": (name, document, "text/xml")}


async def token(http: httpx.AsyncClient, signer: KeyPairSigner, environment: str = "testecf") -> str:
    seed = (await http.get(f"/{environment}/autenticacion/api/autenticacion/semilla")).content
    answer = await http.post(
        f"/{environment}/autenticacion/api/autenticacion/validarsemilla",
        files=xml(await signer.sign_seed(seed, audience=AUDIENCE)),
    )
    assert answer.status_code == 200, answer.text
    return str(json_object(answer)["token"])


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def unsigned_credit_invoice(sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)


async def signed(signer: KeyPairSigner, document: bytes) -> bytes:
    return await signer.sign_document(document, signed_at=SIGNED_AT, reference="E310000000001")


async def signed_credit_invoice(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    return await signed(signer, unsigned_credit_invoice(sequence))


async def signed_summary(signer: KeyPairSigner, sequence: int = 1, *, large: bool = False) -> bytes:
    draft = consumer_invoice(sequence, large=large)
    return await signed(signer, rfce.render(draft, compute_totals(draft), security_code="dcp79q"))


def rsa(issued: Issued) -> RSAPrivateKey:
    assert isinstance(issued.key, RSAPrivateKey)
    return issued.key


def with_an_id(text: str) -> str:
    return text.replace("<Signature ", '<Signature Id="signature" ', 1)


async def test_a_signed_seed_buys_a_token_valid_an_hour(
    http: httpx.AsyncClient, signer: KeyPairSigner, clock: ManualClock
) -> None:
    now = clock()
    seed = await http.get(f"{AUTH}/semilla")
    assert seed.headers["content-type"].startswith("text/xml")
    assert violations(Format.SEED, seed.content, unsigned=True) == ()
    assert texts(seed.content, "fecha") == (now.astimezone(SANTO_DOMINGO).isoformat(),)
    answer = await http.post(
        f"{AUTH}/validarsemilla", files=xml(await signer.sign_seed(seed.content, audience=AUDIENCE))
    )
    body = json_object(answer)
    later = now + timedelta(hours=1)
    assert (body["expedido"], body["expira"]) == (f"{now:%Y-%m-%dT%H:%M:%SZ}", f"{later:%Y-%m-%dT%H:%M:%SZ}")


async def test_a_seed_serves_once_and_for_five_minutes(
    http: httpx.AsyncClient, signer: KeyPairSigner, clock: ManualClock
) -> None:
    signed_seed = await signer.sign_seed((await http.get(f"{AUTH}/semilla")).content, audience=AUDIENCE)
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(signed_seed))).status_code == 200
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(signed_seed))).status_code == 400
    late = await signer.sign_seed((await http.get(f"{AUTH}/semilla")).content, audience=AUDIENCE)
    clock.advance(timedelta(minutes=6))
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(late))).status_code == 400


async def test_a_seed_signed_by_a_stranger_or_not_xml_buys_nothing(http: httpx.AsyncClient) -> None:
    stranger = KeyPairSigner.from_issued(
        Authority.root(now=SIGNER_VALID_FROM).issue(person(), not_before=SIGNER_VALID_FROM)
    )
    signed_seed = await stranger.sign_seed((await http.get(f"{AUTH}/semilla")).content, audience=AUDIENCE)
    refused = await http.post(f"{AUTH}/validarsemilla", files=xml(signed_seed))
    assert (refused.status_code, json_object(refused)["mensaje"]) == (400, "La firma del XML no es válida")
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(b"not xml"))).status_code == 400
    assert (await http.post(f"{AUTH}/validarsemilla", data={"other": "x"})).status_code == 400
    assert (await http.post(f"{AUTH}/validarsemilla", data={"xml": "not a file"})).status_code == 400


async def test_the_fake_checks_signatures_with_our_verifier_strictly_and_with_signxml(
    issued: Issued, signer: KeyPairSigner
) -> None:
    document = await signed_credit_invoice(signer)
    trusted = [issued.certificate]
    stranger = Authority.root(now=SIGNER_VALID_FROM).issue(person(), not_before=SIGNER_VALID_FROM)
    assert dgii_check(trusted=trusted).verify(document)
    assert VerifierCheck(trusted=None).verify(document)
    assert not VerifierCheck(trusted=[stranger.certificate]).verify(document)
    assert not VerifierCheck(trusted=None).verify(b"not xml")
    assert not VerifierCheck(trusted=None).verify(unsigned_credit_invoice())
    tolerated = forge(unsigned_credit_invoice(), rsa(issued), issued.certificate, edit=with_an_id)
    assert VerifierCheck(trusted=trusted, profile=INBOUND).verify(tolerated)
    assert not VerifierCheck(trusted=trusted).verify(tolerated)
    assert not EveryCheck(
        SignxmlCheck(trusted=trusted), VerifierCheck(trusted=[stranger.certificate])
    ).verify(document)


async def test_services_need_a_live_token(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    document = await signed_credit_invoice(signer)
    assert (await http.post(RECEPTION, files=xml(document))).status_code == 401
    assert (
        await http.post(RECEPTION, files=xml(document), headers={"Authorization": "Basic x"})
    ).status_code == 401
    live = await token(http, signer)
    clock.advance(timedelta(hours=1))
    assert (await http.post(RECEPTION, files=xml(document), headers=bearer(live))).status_code == 401
    fresh = await token(http, signer)
    fake.state.dgii.revoke()
    assert (await http.post(RECEPTION, files=xml(document), headers=bearer(fresh))).status_code == 401


async def test_an_e_cf_is_received_with_a_track_id_and_accepted(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    live = await token(http, signer)
    received = await http.post(
        RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=bearer(live)
    )
    body = json_object(received)
    assert (body["error"], body["mensaje"]) == (None, None)
    track_id = str(body["trackId"])
    result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
    assert (result["codigo"], result["estado"], result["eNCF"], result["secuenciaUtilizada"]) == (
        1,
        "Aceptado",
        "E310000000001",
        True,
    )
    assert (result["rnc"], result["mensajes"]) == ("101850043", [])
    (record,) = fake.received
    assert (record.kind, record.file_name, record.rnc, record.track_id) == (
        "ecf",
        "101850043E310000000001.xml",
        "101850043",
        track_id,
    )


async def test_a_script_answers_en_proceso_then_aceptado_condicional(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.by_encf["E310000000002"] = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Monto fuera de tolerancia"),), processing=2
    )
    live = await token(http, signer)
    received = await http.post(
        RECEPTION, files=xml(await signed_credit_invoice(signer, 2)), headers=bearer(live)
    )
    track_id = str(json_object(received)["trackId"])
    codes = [
        json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
        for _ in range(3)
    ]
    assert [answer["codigo"] for answer in codes] == [3, 3, 4]
    assert [answer["secuenciaUtilizada"] for answer in codes] == [None, None, True]
    assert codes[0]["mensajes"] == []
    assert codes[2]["mensajes"] == [{"valor": "Monto fuera de tolerancia", "codigo": 613}]


async def test_what_breaks_the_schema_or_the_signature_is_rejected_and_the_number_released(
    http: httpx.AsyncClient, signer: KeyPairSigner, issued: Issued
) -> None:
    live = await token(http, signer)
    broken = (await signed_credit_invoice(signer)).replace(b"<TipoIngresos>01</TipoIngresos>", b"")
    unsigned = unsigned_credit_invoice(3)
    stranger = KeyPairSigner.from_issued(
        Authority.root(now=SIGNER_VALID_FROM).issue(person(), not_before=SIGNER_VALID_FROM)
    )
    by_a_stranger = await signed_credit_invoice(stranger, 4)
    not_strict = forge(unsigned_credit_invoice(5), rsa(issued), issued.certificate, edit=with_an_id)
    for document, code in ((broken, 1), (unsigned, 1), (by_a_stranger, 2), (not_strict, 2)):
        track_id = str(
            json_object(await http.post(RECEPTION, files=xml(document), headers=bearer(live)))["trackId"]
        )
        result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
        assert (result["codigo"], result["estado"], result["secuenciaUtilizada"]) == (2, "Rechazado", False)
        assert objects(result["mensajes"])[0]["codigo"] == code


async def test_a_scripted_rejection_says_whether_the_number_is_reusable(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    live = await token(http, signer)
    sent = await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=bearer(live))
    track_id = str(json_object(sent)["trackId"])
    result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
    assert (result["codigo"], result["secuenciaUtilizada"]) == (2, False)


async def test_an_unknown_track_id_is_no_encontrado(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    result = json_object(
        await http.get(RESULT, params={"trackid": "nope"}, headers=bearer(await token(http, signer)))
    )
    assert (result["codigo"], result["estado"], result["trackId"]) == (0, "No encontrado", "nope")


async def test_reception_refuses_what_is_not_an_e_cf_it_takes(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = bearer(await token(http, signer))
    assert (await http.post(RECEPTION, files=xml(b"<a>"), headers=live)).status_code == 400
    assert (await http.post(RECEPTION, data={"other": "x"}, headers=live)).status_code == 400
    other = (await signed_credit_invoice(signer)).replace(b"<TipoeCF>31</TipoeCF>", b"<TipoeCF>99</TipoeCF>")
    assert (await http.post(RECEPTION, files=xml(other), headers=live)).status_code == 400
    small_draft = consumer_invoice()
    small = await signed(signer, ecf.render(small_draft, compute_totals(small_draft), signed_at=SIGNED_AT))
    refused = await http.post(RECEPTION, files=xml(small), headers=live)
    assert (refused.status_code, json_object(refused)["trackId"]) == (400, None)


async def test_a_consumer_invoice_whose_total_is_not_a_number_is_received_and_rejected(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = bearer(await token(http, signer))
    draft = consumer_invoice(large=True)
    full = await signed(signer, ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))
    odd = full.replace(b"<MontoTotal>295000.00</MontoTotal>", b"<MontoTotal>many</MontoTotal>")
    assert odd != full
    track_id = str(json_object(await http.post(RECEPTION, files=xml(odd), headers=live))["trackId"])
    result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=live))
    assert (result["codigo"], objects(result["mensajes"])[0]["codigo"]) == (2, 1)


async def test_track_ids_of_an_e_ncf_except_in_certecf(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = bearer(await token(http, signer))
    await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=live)
    params = {"rncemisor": "101850043", "encf": "E310000000001"}
    found = json_list(await http.get(TRACK_IDS, params=params, headers=live))
    assert [entry["estado"] for entry in found] == ["Aceptado"]
    nothing = json_list(await http.get(TRACK_IDS, params={**params, "encf": "E310000000009"}, headers=live))
    assert nothing == []


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"encf": "E310000000001"}, "El campo RNC Emisor es requerido."),
        ({"rncemisor": "101850043"}, "El campo ENCF es requerido."),
        ({"rncemisor": "1018500", "encf": "E310000000001"}, "La longitud del RNC Emisor es inválida."),
        ({"rncemisor": "101850043", "encf": "E3100000001"}, "La longitud del ENCF es inválida."),
        ({"rncemisor": "101850043", "encf": "B310000000001"}, "El ENCF es inválido."),
    ],
)
async def test_track_ids_need_an_rnc_and_an_e_ncf_as_dgii_validates_them(
    http: httpx.AsyncClient, signer: KeyPairSigner, params: dict[str, str], message: str
) -> None:
    refused = await http.get(TRACK_IDS, params=params, headers=bearer(await token(http, signer)))
    assert (refused.status_code, json_object(refused)["mensaje"]) == (400, message)


async def test_certecf_has_no_track_id_query(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    live = bearer(await token(http, signer, "certecf"))
    missing = await http.get(
        "/certecf/consultatrackids/api/trackids/consulta", params={"encf": "x"}, headers=live
    )
    assert missing.status_code == 404


async def test_query_parameters_are_read_whatever_their_case(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = bearer(await token(http, signer))
    sent = await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=live)
    track_id = str(json_object(sent)["trackId"])
    assert json_object(await http.get(RESULT, params={"TrackId": track_id}, headers=live))["codigo"] == 1
    params = {"RncEmisor": "101850043", "ENCF": "e310000000001"}
    assert len(json_list(await http.get(TRACK_IDS, params=params, headers=live))) == 1


async def test_an_rfce_is_decided_at_once_on_the_fc_host(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    live = bearer(await token(http, signer))
    summary = await signed_summary(signer)
    answer = await http.post(
        f"https://fc.dgii.gov.do{RFCE}", files=xml(summary, "101850043E320000000001.xml"), headers=live
    )
    body = json_object(answer)
    assert (body["codigo"], body["estado"], body["encf"], body["secuenciaUtilizada"], body["mensajes"]) == (
        1,
        "Aceptado",
        "E320000000001",
        True,
        [],
    )
    assert "trackId" not in body
    assert (fake.received[-1].host, fake.received[-1].kind, fake.received[-1].track_id) == (
        "fc.dgii.gov.do",
        "rfce",
        None,
    )
    query = {"RNC_Emisor": "101850043", "ENCF": "E320000000001", "Cod_Seguridad_eCF": "dcp79q"}
    status = json_object(await http.get(RFCE_QUERY, params=query, headers=live))
    assert (
        status["codigo"],
        status["estado"],
        status["rnc"],
        status["encf"],
        status["secuenciaUtilizada"],
    ) == (
        "1",
        "Aceptado",
        "101850043",
        "E320000000001",
        True,
    )
    wrong = json_object(
        await http.get(RFCE_QUERY.lower(), params={**query, "Cod_Seguridad_eCF": "x"}, headers=live)
    )
    assert (wrong["codigo"], wrong["estado"], wrong["secuenciaUtilizada"], wrong["mensajes"]) == (
        "0",
        "No encontrado",
        None,
        [],
    )
    no_rfce = json_object(await http.get(RESULT, params={"trackid": ""}, headers=live))
    assert no_rfce["codigo"] == 0


async def test_an_rfce_accepted_conditionally_or_rejected_is_queried_as_such(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.by_encf["E320000000002"] = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Monto fuera de tolerancia"),), processing=3
    )
    live = bearer(await token(http, signer))
    body = json_object(await http.post(RFCE, files=xml(await signed_summary(signer, 2)), headers=live))
    assert (body["codigo"], body["estado"]) == (4, "Aceptado Condicional")
    assert body["mensajes"] == [{"codigo": "613", "valor": "Monto fuera de tolerancia"}]
    query = {"rnc_emisor": "101850043", "encf": "E320000000002", "cod_seguridad_ecf": "dcp79q"}
    status = json_object(await http.get(RFCE_QUERY, params=query, headers=live))
    assert (status["codigo"], status["estado"]) == ("4", "Aceptado Condicional")
    assert status["mensajes"] == [{"valor": "Monto fuera de tolerancia", "codigo": 613}]
    draft = consumer_invoice(3)
    unsigned = rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    rejected = json_object(await http.post(RFCE, files=xml(unsigned), headers=live))
    assert (rejected["codigo"], rejected["secuenciaUtilizada"]) == (2, False)
    status = json_object(await http.get(RFCE_QUERY, params={**query, "encf": "E320000000003"}, headers=live))
    assert (status["codigo"], status["estado"], status["secuenciaUtilizada"]) == ("2", "Rechazado", False)


async def test_an_rfce_of_the_threshold_or_more_is_rejected(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = bearer(await token(http, signer))
    body = json_object(
        await http.post(RFCE, files=xml(await signed_summary(signer, large=True)), headers=live)
    )
    assert (body["codigo"], body["secuenciaUtilizada"]) == (2, True)
    assert objects(body["mensajes"])[0]["codigo"] == "3"
    assert (await http.post(RFCE, files=xml(b"x"), headers=live)).status_code == 400


async def test_outages_unknown_paths_and_dgii_s_case_insensitivity(
    http: httpx.AsyncClient, fake: FakeDgii
) -> None:
    fake.script.outage = 2
    assert [(await http.get(f"{AUTH}/semilla")).status_code for _ in range(3)] == [503, 503, 200]
    assert (await http.get("/prod/autenticacion/api/autenticacion/semilla")).status_code == 404
    assert (await http.get("/testecf/nothing/here")).status_code == 404
    assert (await http.post(f"{AUTH}/semilla")).status_code == 404
    assert (await http.get("/TesteCF/Autenticacion/API/Autenticacion/Semilla")).status_code == 200
    fake.script.outage = -1
    assert (await http.get(f"{AUTH}/semilla")).status_code == 503
    fake.script.outage = 0
    live = fake.state.token_for_test()
    assert (await http.get(RESULT, params={"trackid": "x"}, headers=bearer(live))).status_code == 200


def test_without_a_clock_the_fake_keeps_utc_time(issued: Issued, receiver_identity: Issued) -> None:
    fake = FakeDgii(check=dgii_check(trusted=None), receiver=receiver_identity)
    before = datetime.now(UTC)
    assert before <= fake.state.clock() <= datetime.now(UTC)
    assert fake.state.public_url == DEFAULT_PUBLIC_URL
    assert issued.certificate != receiver_identity.certificate


def padded(size: int) -> bytes:
    """A well-formed document of exactly ``size`` bytes."""
    return b"<a>" + b" " * (size - len(b"<a></a>")) + b"</a>"


async def test_a_body_beyond_the_document_limit_is_refused_before_it_is_read(
    http: httpx.AsyncClient, fake: FakeDgii
) -> None:
    live = bearer(fake.state.token_for_test())
    assert MAX_DOCUMENT < MAX_UPLOAD
    declared_too_large = padded(MAX_UPLOAD)
    file_too_large = padded(MAX_DOCUMENT + 1)
    for document in (declared_too_large, file_too_large):
        assert (await http.post(RECEPTION, files=xml(document), headers=live)).status_code == 413
    assert (await http.post(RECEPTION, files=xml(padded(MAX_DOCUMENT)), headers=live)).status_code == 400

    chunk = 64 * 1024

    async def undeclared() -> AsyncIterator[bytes]:
        yield b'--b\r\nContent-Disposition: form-data; name="xml"; filename="a.xml"\r\n\r\n'
        for _ in range(MAX_UPLOAD // chunk + 1):
            yield b" " * chunk

    streamed = await http.post(
        RECEPTION, content=undeclared(), headers={**live, "Content-Type": "multipart/form-data; boundary=b"}
    )
    assert "content-length" not in streamed.request.headers
    assert streamed.status_code == 413
    seed_too_large = await http.post(f"{AUTH}/validarsemilla", files=xml(file_too_large))
    assert seed_too_large.status_code == 413
    assert fake.received == []
