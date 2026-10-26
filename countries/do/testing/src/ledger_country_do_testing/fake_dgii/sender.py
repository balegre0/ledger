"""An emitter that sends a receiver what other taxpayers send it (DT-EMI; DGII's Pasos 9 and 11).

``Sender`` drives any receiver of DGII's emisor-receptor contract, given its base (a directory entry's URL):
ours (``/do-ecf-v1/{environment}/{rnc}``) or the fake's simulator. It asks a seed, signs it with its own
certificate and exchanges it for a token (``token``), then posts an e-CF (``send_ecf``) or a commercial
approval (``send_acecf``), each signed in DGII's profile before it is sent, as multipart ``xml``. What the
receiver answers is returned as it came: a test reads it (a signed ARECF, a bare status).
"""

from http import HTTPStatus
from typing import ClassVar, Final, final

from pydantic import TypeAdapter
lazy import httpx

from ledger_country_do_testing.fake_dgii.emitter import fields
from ledger_kernel.errors import InvalidInputError
lazy from ledger_country_do_testing.local_signer import KeyPairSigner

SEED: Final = "fe/autenticacion/api/semilla"
VALIDATION: Final = "fe/autenticacion/api/validacioncertificado"
RECEPTION: Final = "fe/recepcion/api/ecf"
APPROVAL: Final = "fe/aprobacioncomercial/api/ecf"
ACECF_FILE: Final = "ACECF.xml"
"""The approval's file name, as our buyer client sends it (unconfirmed on TesteCF: Task 23)."""
SEED_STEP: Final = "seed request"
VALIDATION_STEP: Final = "signed seed"
_TOKEN: Final = TypeAdapter(dict[str, str | None])


class SenderTokenRefusedError(InvalidInputError):
    """The receiver gave the sender no seed, or no token for its signed seed."""

    code: ClassVar[str] = "do_fake_sender_token_refused"

    def __init__(self, step: str, status: int) -> None:
        super().__init__(f"the receiver refused the sender's {step}: {status}")


@final
class Sender:
    """An emitter of ``signer``'s certificate sending over ``http`` (see the module)."""

    __slots__ = ("_http", "_signer")

    def __init__(self, http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
        self._http = http
        self._signer = signer

    async def token(self, base: str) -> str:
        """A token of the receiver at ``base``, for a seed this sender signed."""
        seed = await self._http.get(f"{base}/{SEED}")
        if seed.status_code != HTTPStatus.OK:
            raise SenderTokenRefusedError(SEED_STEP, seed.status_code)
        signed = self._signer.sign(seed.content)
        validated = await self._http.post(
            f"{base}/{VALIDATION}", files={"xml": ("semilla.xml", signed, "text/xml")}
        )
        if validated.status_code != HTTPStatus.OK:
            raise SenderTokenRefusedError(VALIDATION_STEP, validated.status_code)
        return str(_TOKEN.validate_json(validated.content)["token"])

    async def send_ecf(self, base: str, document: bytes, *, token: str | None) -> httpx.Response:
        """``document``, an unsigned e-CF, signed and sent as ``{RNCEmisor}{eNCF}.xml``."""
        rnc, encf = fields(document, "Encabezado/Emisor/RNCEmisor", "Encabezado/IdDoc/eNCF")
        return await self._post(f"{base}/{RECEPTION}", f"{rnc}{encf}.xml", document, token)

    async def send_acecf(self, base: str, document: bytes, *, token: str | None) -> httpx.Response:
        """``document``, an unsigned ACECF, signed and sent."""
        return await self._post(f"{base}/{APPROVAL}", ACECF_FILE, document, token)

    async def _post(self, url: str, name: str, document: bytes, token: str | None) -> httpx.Response:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        files = {"xml": (name, self._signer.sign(document), "text/xml")}
        return await self._http.post(url, files=files, headers=headers)
