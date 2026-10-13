"""Calling a service's public API in a test: a caller's headers, its answers as JSON, what a POST made.

Every service's API takes a bearer token, an ``Idempotency-Key`` on each ``POST``, and answers pages as
``{"items": [...], "next_cursor": ...}``: these helpers speak that, with tokens of ``LocalIssuer``.
"""

from dataclasses import dataclass
from typing import Final, TypedDict, final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter
lazy import httpx

from ledger_testing.issuer import PROVIDER_ORGANIZATION, SUBJECT
lazy from ledger_testing.issuer import LocalIssuer

_CREATED: Final = 201
_SHOWN: Final = 500
"""How much of an unexpected answer's body a failure shows."""


class Page(TypedDict):
    """A page of a list: its items and the cursor of the next page (``None`` on the last)."""

    items: list[dict[str, object]]
    next_cursor: str | None


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
class Caller:
    """A principal of ``issuer`` (``subject`` of ``organization``), as the headers of its requests."""

    issuer: LocalIssuer
    subject: str = SUBJECT
    organization: str = PROVIDER_ORGANIZATION

    def bearer(self) -> dict[str, str]:
        """``Authorization: Bearer`` with a fresh token of the caller."""
        token = self.issuer.token(subject=self.subject, organization=self.organization)
        return {"Authorization": f"Bearer {token}"}

    def posting(self, key: str) -> dict[str, str]:
        """The headers of a ``POST``: the caller's token, and ``key`` as its ``Idempotency-Key``."""
        return self.bearer() | {"Idempotency-Key": key}


async def created(
    client: httpx.AsyncClient, path: str, payload: Mapping[str, object], headers: Mapping[str, str]
) -> str:
    """``POST`` ``payload`` to ``path``: the ``id`` of what it made, else ``NotCreatedError``."""
    response = await client.post(path, json=payload, headers=headers)
    if response.status_code != _CREATED:
        raise NotCreatedError(response)
    return str(document(response)["id"])
