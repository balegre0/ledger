"""DGII's JSON answers, read strictly enough to trust and loosely enough to survive DGII's additions.

Each model reads the fields the client uses by DGII's names and ignores the rest (DGII adds fields without
notice: ``secuenciaUtilizada`` arrived in 2022). DGII spells keys inconsistently (``eNCF``/``encf``) and types
a message's ``codigo`` as a number in one service and a string in another (the RFCE's): message codes are read
as either and kept as text; a message's text is DGII's ``valor``. An answer that does not fit is unreadable,
never guessed. Validation errors never repeat their input (``hide_input_in_errors``): a token answer that does
not fit must not put the token in an error.
"""

from typing import ClassVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

BEARER_ALPHABET = r"^[A-Za-z0-9._~+/=-]+$"
"""What a bearer token is made of (RFC 6750's ``b64token``): nothing that could break out of its header."""


class _Answer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", frozen=True, populate_by_name=True, hide_input_in_errors=True
    )


class TokenAnswer(_Answer):
    """``validarsemilla``/``validacioncertificado``: ``{token, expira, expedido}`` (UTC)."""

    token: str = Field(min_length=1, max_length=4096, pattern=BEARER_ALPHABET, repr=False)
    expires: str = Field(alias="expira", max_length=64)


class MessageAnswer(_Answer):
    """One of DGII's messages."""

    code: int | str = Field(default="", alias="codigo")
    text: str = Field(default="", alias="valor", max_length=2000)


class ReceptionAnswer(_Answer):
    """``facturaselectronicas``: a track id, or an error and its message."""

    track_id: str | None = Field(default=None, alias="trackId", max_length=128)
    error: str | None = Field(default=None, max_length=256)
    message: str | None = Field(default=None, alias="mensaje", max_length=2000)


class ResultAnswer(_Answer):
    """``consultaresultado`` and the RFCE's reception: code, messages, ``secuenciaUtilizada``, RNC, e-NCF."""

    code: int = Field(alias="codigo")
    messages: list[MessageAnswer] = Field(
        default_factory=list[MessageAnswer], alias="mensajes", max_length=200
    )
    sequence_used: bool | None = Field(default=None, alias="secuenciaUtilizada")
    rnc: str | None = Field(default=None, max_length=11)
    encf: str | None = Field(default=None, validation_alias=AliasChoices("eNCF", "encf"), max_length=13)


class TrackIdAnswer(_Answer):
    """One entry of ``consultatrackids``."""

    track_id: str = Field(alias="trackId", min_length=1, max_length=128)
    received_at: str = Field(alias="fechaRecepcion", min_length=1, max_length=64)
    """When DGII received it, as DGII writes it: the order of the track ids (``client``)."""


class DirectoryAnswer(_Answer):
    """One entry of ``consultadirectorio``: a receiver's service bases."""

    name: str = Field(default="", alias="nombre", max_length=300)
    rnc: str = Field(max_length=11)
    reception: str = Field(alias="urlRecepcion", min_length=1, max_length=2000)
    approval: str = Field(default="", alias="urlAceptacion", max_length=2000)
    authentication: str | None = Field(default=None, alias="urlOpcional", max_length=2000)
