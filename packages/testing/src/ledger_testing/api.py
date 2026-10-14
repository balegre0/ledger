"""Calling a module's public API in a test: a principal's headers, its answers as JSON, what a POST made.

Every module's API takes a bearer token, an ``Idempotency-Key`` and a ``Ledger-Mode`` on each ``POST``, and
answers pages as ``{"items": [...], "has_more": ..., "next_cursor": ..., "previous_cursor": ...}`` (§9.7):
these helpers speak that, with tokens of ``LocalIssuer``.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, TypedDict, final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter
lazy import httpx

from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT
lazy from ledger_testing.issuer import LocalIssuer

_CREATED: Final = 201
_SHOWN: Final = 500
"""How much of an unexpected answer's body a failure shows."""


class Page(TypedDict):
    """A page of a list: its items, whether more follow, and the cursors of the pages on either side."""

    items: list[dict[str, object]]
    has_more: bool
    next_cursor: str | None
    previous_cursor: str | None


_DOCUMENT: Final = TypeAdapter(dict[str, object])
_PAGE: Final = TypeAdapter(Page)


class NotCreatedError(AssertionError):
    """A request that should have created something did not: what came back, for the test's report."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"expected 201 Created, got {response.status_code}: {response.text[:_SHOWN]}")


def document(response: httpx.Response) -> dict[str, object]:
    """The JSON object a response holds: a resource, or a problem."""
    return _DOCUMENT.validate_json(response.content)


def page(response: httpx.Response) -> Page:
    """The page a list answered."""
    return _PAGE.validate_json(response.content)


@final
@dataclass(frozen=True, slots=True)
class TestPrincipal:
    """A principal of ``issuer``, ``subject`` of ``organization`` holding ``roles``: its requests' headers."""

    __test__: ClassVar[bool] = False
    """Not a test class, though its name starts like one: pytest leaves it uncollected."""

    issuer: LocalIssuer
    subject: str = SUBJECT
    organization: str = PROVIDER_ORGANIZATION
    roles: tuple[str, ...] = ()
    """Project roles the provider granted the caller in its organization (none by default)."""

    def bearer(self) -> dict[str, str]:
        """``Authorization: Bearer`` with a fresh token of the caller."""
        token = self.issuer.token(subject=self.subject, organization=self.organization, roles=self.roles)
        return {"Authorization": f"Bearer {token}"}

    def posting(self, key: str, mode: str = "test") -> dict[str, str]:
        """The headers of a ``POST``: the caller's token, ``key`` as its ``Idempotency-Key`` and its mode."""
        return self.bearer() | {"Idempotency-Key": key, "Ledger-Mode": mode}


async def created(
    client: httpx.AsyncClient, path: str, payload: Mapping[str, object], headers: Mapping[str, str]
) -> str:
    """``POST`` ``payload`` to ``path``: the ``id`` of what it made, else ``NotCreatedError``."""
    response = await client.post(path, json=payload, headers=headers)
    if response.status_code != _CREATED:
        raise NotCreatedError(response)
    return str(document(response)["id"])
