from typing import ClassVar, Final, final
lazy from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from granian.constants import Interfaces

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.answers import json_object
from ledger_country_do_testing.fake_dgii import serve
from ledger_country_do_testing.fake_dgii.app import FakeRootUnreadableError, create_app
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.samples import SIGNED_AT, credit_invoice
from ledger_country_do_testing.signatures import forge
from ledger_testing.pki import Authority, Issued, ec_key

AUTH = "/testecf/autenticacion/api/autenticacion"
SIMULATOR: Final = "/testecf/emisorreceptor/fe"


@final
class Recorder:
    """Stands in for granian's server: records how it was built and that it was served."""

    built: ClassVar[list[dict[str, object]]] = []
    served: ClassVar[int] = 0

    def __init__(self, **options: object) -> None:
        Recorder.built.append(options)

    def serve(self) -> None:
        Recorder.served += 1


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[Recorder]:
    Recorder.built.clear()
    Recorder.served = 0
    monkeypatch.setattr(serve, "Granian", Recorder)
    return Recorder


def test_the_fake_serves_on_loopback_8471_by_default(recorder: type[Recorder]) -> None:
    serve.main([])
    serve.main(["--port", "8472"])
    assert [
        (built["address"], built["port"], built["factory"], built["interface"]) for built in recorder.built
    ] == [
        ("127.0.0.1", 8471, True, Interfaces.ASGI),
        ("127.0.0.1", 8472, True, Interfaces.ASGI),
    ]
    assert recorder.served == 2


@pytest.mark.parametrize(
    "argv", [["--host", "192.168.1.10"], ["--port", "8000"], ["--port", "0"], ["--port", "65536"]]
)
def test_only_loopback_is_bound_and_port_8000_is_never_taken(
    recorder: type[Recorder], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        serve.main(argv)
    assert caught.value.code == 2
    assert recorder.built == []


async def test_the_factory_builds_a_fake_that_trusts_any_signer_and_knows_its_public_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_FAKE_DGII_PUBLIC_URL", "http://127.0.0.1:9999")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1:9999"
    ) as http:
        seed = await http.get(f"{AUTH}/semilla")
        directory = await http.get(
            "/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "131098193"}
        )
    assert seed.status_code == 200
    assert b"http://127.0.0.1:9999/testecf/emisorreceptor" in directory.content


async def test_the_factory_s_fake_takes_any_signer_but_only_dgii_s_signature_profile() -> None:
    stranger = Authority.root("ANY CA").issue(person())
    assert isinstance(stranger.key, RSAPrivateKey)
    signer = KeyPairSigner.from_issued(stranger)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1:8471"
    ) as http:
        seeds = [(await http.get(f"{AUTH}/semilla")).content for _ in range(2)]
        signed = signer.sign(seeds[0])
        with_an_id = forge(
            seeds[1],
            stranger.key,
            stranger.certificate,
            edit=lambda text: text.replace("<Signature ", '<Signature Id="signature" ', 1),
        )
        answers = [
            await http.post(f"{AUTH}/validarsemilla", files={"xml": ("semilla.xml", sent, "text/xml")})
            for sent in (signed, with_an_id)
        ]
    assert [answer.status_code for answer in answers] == [200, 400]


def test_in_its_container_the_fake_listens_on_the_address_it_is_given(
    recorder: type[Recorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose publishes it on the host's loopback alone; the stack's apps reach it by its service name."""
    serve.main(["--host", "::1"])
    monkeypatch.setenv("LEDGER_FAKE_DGII_CONTAINER", "1")
    serve.main(["--host", "172.18.0.5"])
    with pytest.raises(SystemExit):
        serve.main(["--host", "fake-dgii"])
    assert [(built["address"], built["port"]) for built in recorder.built] == [
        ("::1", 8471),
        ("172.18.0.5", 8471),
    ]


async def test_the_factory_s_simulator_signs_under_the_root_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The local stack's roots file holds the simulator's root (deploy/local/do/roots.sh writes both)."""
    root = Authority.root("LEDGER LOCAL FAKE DGII ROOT")
    given = tmp_path / "fake-dgii-root.pem"
    given.write_bytes(root.issued.pem() + root.issued.key_pem())
    monkeypatch.setenv("LEDGER_FAKE_DGII_ROOT", str(given))
    stranger = Authority.root("ANY CA").issue(person())
    signer = KeyPairSigner.from_issued(stranger)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1:8471"
    ) as http:
        seed = (await http.get(f"{SIMULATOR}/autenticacion/api/semilla")).content
        validated = await http.post(
            f"{SIMULATOR}/autenticacion/api/validacioncertificado",
            files={"xml": ("semilla.xml", signer.sign(seed), "text/xml")},
        )
        token = {"Authorization": f"Bearer {json_object(validated)['token']}"}
        draft = credit_invoice()
        document = signer.sign(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))
        answer = await http.post(
            f"{SIMULATOR}/recepcion/api/ecf",
            files={"xml": ("101850043E310000000001.xml", document, "text/xml")},
            headers=token,
        )
    verified = verify_enveloped(answer.content)
    assert verified.certificate.issuer == root.certificate.subject


def unusable_roots() -> dict[str, bytes]:
    root = Authority.root("LEDGER LOCAL FAKE DGII ROOT")
    return {
        "empty": b"",
        "garbage": b"not a pem",
        "no key": root.issued.pem(),
        "no certificate": root.issued.key_pem(),
        "not rsa": root.issued.pem() + Issued(root.certificate, ec_key()).key_pem(),
    }


@pytest.mark.parametrize("kind", ["empty", "garbage", "no key", "no certificate", "not rsa"])
def test_a_root_without_its_rsa_key_and_certificate_is_refused_without_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    given = tmp_path / "secret-name.pem"
    given.write_bytes(unusable_roots()[kind])
    monkeypatch.setenv("LEDGER_FAKE_DGII_ROOT", str(given))
    with pytest.raises(FakeRootUnreadableError) as caught:
        create_app()
    assert "secret-name" not in str(caught.value)


def test_a_root_file_that_cannot_be_read_is_refused_without_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LEDGER_FAKE_DGII_ROOT", str(tmp_path / "secret-name.pem"))
    with pytest.raises(FakeRootUnreadableError) as caught:
        create_app()
    assert "secret-name" not in str(caught.value)
