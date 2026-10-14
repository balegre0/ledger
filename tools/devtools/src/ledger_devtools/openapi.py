"""Each module's OpenAPI snapshot (§10): its paths of the ``api`` app's document, and what they reference.

The document is made from the code: the ``api`` app is built on placeholder settings (nothing connects, nobody
is trusted) with every installed module, and asked for its schema.
"""

import functools
import json
from importlib import metadata
from typing import ClassVar, Final, TypeIs, override
lazy from collections.abc import Iterable, Iterator, Mapping

from pydantic import SecretStr, TypeAdapter
lazy from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from ledger_kernel.errors import LedgerError
from ledger_platform.runtime import App, Settings
lazy from ledger_devtools.workspace import Package

_SCHEMA_REF: Final = "#/components/schemas/"
_DOCUMENT: Final = TypeAdapter(dict[str, object])
_ITEMS: Final = TypeAdapter(list[object])
_ISSUER: Final = "https://issuer.invalid"
_AUDIENCE: Final = "ledger"


class _Placeholders(Settings):
    """``Settings`` from what it is given alone: no ``LEDGER_*`` variable of the shell reaches it."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """The values given, and nothing else."""
        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)


class NotInstalledError(LedgerError):
    """A module the ``api`` app mounts is not installed, so the document would silently lack its paths."""

    code: ClassVar[str] = "devtools_module_not_installed"

    def __init__(self, manifest: str, distribution: str) -> None:
        cannot = "is not installed, so its OpenAPI document cannot be made: run `uv sync --all-packages`"
        super().__init__(f"{manifest}: {distribution} {cannot}")


def require_installed(modules: Iterable[Package]) -> None:
    """Refuse to make the document while one of ``modules`` is not installed: the app finds installed ones."""
    for module in modules:
        try:
            metadata.distribution(module.distribution)
        except metadata.PackageNotFoundError as error:
            raise NotInstalledError(module.manifest_path, module.distribution) from error


@functools.cache
def api_document() -> dict[str, object]:
    """The ``api`` app's whole document, built once per process.

    The document depends neither on whom the app trusts nor on where its database is: placeholders.
    """
    settings = _Placeholders(
        environment="test",
        database_url=SecretStr("postgresql+psycopg://openapi@localhost/openapi"),
        token_issuer=_ISSUER,
        token_audience=_AUDIENCE,
        token_jwks_url=f"{_ISSUER}/oauth/v2/keys",
    )
    return _DOCUMENT.validate_python(App("api", settings=settings).asgi().openapi())


def _is_table(value: object) -> TypeIs[Mapping[str, object]]:
    return isinstance(value, dict)


def _references(value: object) -> Iterator[str]:
    """The component schemas ``value`` names, directly."""
    if _is_table(value):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith(_SCHEMA_REF):
                yield item.removeprefix(_SCHEMA_REF)
            else:
                yield from _references(item)
    elif isinstance(value, list):
        for item in _ITEMS.validate_python(value):
            yield from _references(item)


def _reached(paths: Mapping[str, object], schemas: Mapping[str, object]) -> dict[str, object]:
    """The schemas ``paths`` reference, and those they reference in turn."""
    reached: dict[str, object] = {}
    pending = list(_references(paths))
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached[name] = schemas[name]
        pending.extend(_references(schemas[name]))
    return reached


def _owns(prefixes: tuple[str, ...], path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def openapi_text(package: Package, document: Mapping[str, object]) -> str:
    """``package``'s paths of ``document`` (by its manifest's prefixes), with the schemas they reach: JSON."""
    prefixes = () if package.manifest.http is None else package.manifest.http.prefixes
    paths = _DOCUMENT.validate_python(document.get("paths", {}))
    owned = {path: item for path, item in paths.items() if _owns(prefixes, path)}
    components = _DOCUMENT.validate_python(document.get("components", {}))
    schemas = _DOCUMENT.validate_python(components.get("schemas", {}))
    restricted = {
        **document,
        "paths": owned,
        "components": {**components, "schemas": _reached(owned, schemas)},
    }
    return json.dumps(restricted, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
