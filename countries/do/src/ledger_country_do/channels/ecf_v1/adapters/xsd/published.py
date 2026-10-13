"""What DGII publishes for e-CF v1.0, as it published it: the XSDs, where each came from, and their SHA-256.

The files beside this module are DGII's own, byte for byte (BOMs and CRLF line ends included:
``.gitattributes`` keeps git from touching them), renamed only so that no path holds a space. Each is pinned
to the SHA-256 it had when fetched: a file that changed fails ``test_xsd_pins``, and replacing one is a
deliberate change of this module (a new hash, a new date), never a silent download.

Three of the fifteen do not compile as DGII publishes them; ``CORRECTIONS`` holds the least edit that makes
each compile, applied when the schema is loaded and never to the file.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Mapping

SOURCE: Final = (
    "https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/"
    "Documentacin%20sobre%20eCF/Documentaci%C3%B3n%20T%C3%A9cnica%20(XSD)/"
)
"""Where DGII publishes its XSDs (its page "Documentación sobre e-CF" links each file under it); fetched
on 2026-10-12."""


class Format(StrEnum):
    """Each XML format DGII defines, by the file of its schema."""

    ECF_31 = "ecf-31.xsd"
    ECF_32 = "ecf-32.xsd"
    ECF_33 = "ecf-33.xsd"
    ECF_34 = "ecf-34.xsd"
    ECF_41 = "ecf-41.xsd"
    ECF_43 = "ecf-43.xsd"
    ECF_44 = "ecf-44.xsd"
    ECF_45 = "ecf-45.xsd"
    ECF_46 = "ecf-46.xsd"
    ECF_47 = "ecf-47.xsd"
    RFCE = "rfce-32.xsd"
    """The summary of a consumer invoice (type 32) under DOP 250,000."""
    ANECF = "anecf.xsd"
    """The annulment of unused e-NCF ranges."""
    ACECF = "acecf.xsd"
    """A buyer's commercial approval or rejection."""
    ARECF = "arecf.xsd"
    """A buyer's acknowledgement of receipt."""
    SEED = "semilla.xsd"
    """The authentication seed (``SemillaModel``), signed to obtain a token."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Published:
    """One schema as DGII published it."""

    format: Format
    name: str
    """The file's name on DGII's site."""
    sha256: str
    """Of the file as fetched, lowercase hexadecimal."""
    root: str
    """The root element of the documents it describes."""

    @property
    def url(self) -> str:
        """Where it was fetched from."""
        return SOURCE + self.name.replace(" ", "%20")


def _published(format_: Format, name: str, sha256: str, root: str) -> Published:
    return Published(format=format_, name=name, sha256=sha256, root=root)


PUBLISHED: Final[Mapping[Format, Published]] = MappingProxyType(
    {
        entry.format: entry
        for entry in (
            _published(
                Format.ECF_31,
                "e-CF 31 v.1.0.xsd",
                "6f2909a93d84919518d2ae3c77fead4b35c3e8c95996b8af67b0040c2e2be298",
                "ECF",
            ),
            _published(
                Format.ECF_32,
                "e-CF 32 v.1.0.xsd",
                "151676df03c61335b4ac1eca97f99395488d57ea92793b49353602871e651f3c",
                "ECF",
            ),
            _published(
                Format.ECF_33,
                "e-CF 33 v.1.0.xsd",
                "087cd71beba11c19cf548e22b96363cb4aa210dc6f5c3c8a62122a42c88d2866",
                "ECF",
            ),
            _published(
                Format.ECF_34,
                "e-CF 34 v.1.0.xsd",
                "6c62ebabdea9a6f6d62ae85d9e1c2eb788f44f3f0b61945fe116700c4eaac2da",
                "ECF",
            ),
            _published(
                Format.ECF_41,
                "e-CF 41 v.1.0.xsd",
                "eae1993c637375bc1cbe80932411d87f5680cd54e77e4d1b2752d72a6c8b2ab3",
                "ECF",
            ),
            _published(
                Format.ECF_43,
                "e-CF 43 v.1.0.xsd",
                "776f030980c2c50cf0221e9263c55f367c8631727adff00ab45e2c7c1abafc52",
                "ECF",
            ),
            _published(
                Format.ECF_44,
                "e-CF 44 v.1.0.xsd",
                "19834f37a9f0e2db40f00c80d07c2bf92f9019f23474480f32cef1ba06af4e67",
                "ECF",
            ),
            _published(
                Format.ECF_45,
                "e-CF 45 v.1.0.xsd",
                "030492cc8ef7d1a09a89b16b241f8e5c4920dfd09c0b629b1db8e68406ecd6ca",
                "ECF",
            ),
            _published(
                Format.ECF_46,
                "e-CF 46 v.1.0.xsd",
                "e7f8613ade25c7efb88e84d34d7c0ad330d1b22238ad9ef90c80aed01911d67b",
                "ECF",
            ),
            _published(
                Format.ECF_47,
                "e-CF 47 v.1.0.xsd",
                "14daf18f52f63dd80e439a18fb60a102d74e71243ee90ee253367f61ce8e1994",
                "ECF",
            ),
            _published(
                Format.RFCE,
                "RFCE 32 v.1.0.xsd",
                "6aad535875661b05eef072963295202fc94b3b320b257791feb8d3c39a5f8ee6",
                "RFCE",
            ),
            _published(
                Format.ANECF,
                "ANECF v.1.0.xsd",
                "af2e6a16c2900dfa55264d6ebec58ccbbbb25c1eb9821ee18ccc44fc142d0e78",
                "ANECF",
            ),
            _published(
                Format.ACECF,
                "ACECF v.1.0.xsd",
                "072f65de202df8ec136a8d4493e0592172690a4e181047245401cc2e7b23c095",
                "ACECF",
            ),
            _published(
                Format.ARECF,
                "ARECF v1.0.xsd",
                "c6d186167159110959eb3f54706ecc7462ad9128fc2c3e03440c8352b1c66f10",
                "ARECF",
            ),
            _published(
                Format.SEED,
                "Semilla v.1.0.xsd",
                "bebef2033c930a30c8cef45b513db00403423125a7f3f48e100a73e3e355f67e",
                "SemillaModel",
            ),
        )
    }
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Correction:
    """The least edit that makes a published schema compile: ``wrong``, ``count`` times, becomes ``right``."""

    format: Format
    wrong: str
    right: str
    count: int
    reason: str


CORRECTIONS: Final[tuple[Correction, ...]] = (
    Correction(
        format=Format.ECF_31,
        wrong='name=" IndicadorServicioTodoIncluidoType"',
        right='name="IndicadorServicioTodoIncluidoType"',
        count=1,
        reason="a space before the type's name leaves the element that uses it without a type",
    ),
    Correction(
        format=Format.ACECF,
        wrong="(?:",
        right="(",
        count=1,
        reason="a non-capturing group is Perl's, not XML Schema's: the date pattern does not compile",
    ),
    Correction(
        format=Format.RFCE,
        wrong="(?:",
        right="(",
        count=4,
        reason="a non-capturing group is Perl's, not XML Schema's: date and amount patterns do not compile",
    ),
)
"""Every correction, each checked to apply exactly ``count`` times: when DGII fixes a file its hash changes,
and ``test_xsd_pins`` says so before a correction could silently stop applying."""
