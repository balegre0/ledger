from typing import ClassVar, final

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from granian.constants import Interfaces

from ledger_country_do_testing.fake_dgii import serve
from ledger_country_do_testing.fake_dgii.app import create_app
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import person
from ledger_country_do_testing.signatures import forge
from ledger_testing.pki import Authority

AUTH = "/testecf/autenticacion/api/autenticacion"


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
