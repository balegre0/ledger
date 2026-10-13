"""identity's OpenAPI document (3.1), as published in ``modules/identity/openapi.json`` (§10).

The document is made from the code: the service is built with placeholder settings (nothing connects) and its
app asked for its schema. A test keeps the published file equal to it.
"""

import json
import sys
from pathlib import Path
from typing import Final

from pydantic import SecretStr
lazy from fastapi import FastAPI

from ledger_identity.main import build
from ledger_identity.settings import IdentitySettings

_USAGE: Final = "usage: ledger-identity-openapi PATH\n"
_WRONG_USAGE: Final = 2
_DATABASE: Final = "postgresql+psycopg://openapi@localhost/openapi"
_ISSUER: Final = "https://issuer.invalid"
"""Placeholders: the document does not depend on whom the service trusts, and nothing is fetched."""
_AUDIENCE: Final = "ledger"
_KEYS: Final = "https://issuer.invalid/oauth/v2/keys"


def app() -> FastAPI:
    """Identity's app on placeholder settings: what the document is made from (nothing connects)."""
    settings = IdentitySettings(
        database_url=SecretStr(_DATABASE),
        token_issuer=_ISSUER,
        token_audience=_AUDIENCE,
        token_jwks_url=_KEYS,
    )
    return build(settings).app


def document() -> str:
    """The OpenAPI document of identity's public API, as JSON with sorted keys."""
    return json.dumps(app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    """``ledger-identity-openapi PATH``: write the document to ``PATH``."""
    match sys.argv[1:]:
        case [path]:
            Path(path).write_text(document(), encoding="utf-8", newline="\n")
            return 0
        case _:
            sys.stderr.write(_USAGE)
            return _WRONG_USAGE
