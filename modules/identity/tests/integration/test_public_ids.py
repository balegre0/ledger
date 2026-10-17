"""Public ids are TypeIDs (§9.4): what the API shows, what its paths take, and what it never reveals."""

import re
from typing import Final
lazy from uuid import UUID

lazy import httpx

from ledger_identity.domain.ids import ESTABLISHMENT_PREFIX, MEMBERSHIP_PREFIX
from ledger_kernel.ids import COMPANY_PREFIX, ORGANIZATION_PREFIX, format_id, parse_id
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.api import created, document, page
lazy from ledger_testing.api import TestPrincipal

UUID_TEXT: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
"""An internal id in its canonical form: nothing the API sends or answers may contain one."""


def holds(public: object, prefix: str) -> UUID:
    """The id inside ``public``, which must be a TypeID of ``prefix``."""
    assert isinstance(public, str)
    return parse_id(prefix, public)


async def test_every_id_the_api_shows_is_a_type_id_and_no_internal_id_is_shown(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    holds(company, COMPANY_PREFIX)
    base = f"/v1/companies/{company}"
    opened = await client.post(
        f"{base}/establishments", json={"code": "SUC1", "name": "S"}, headers=owner.posting("e-1")
    )
    granted = await client.put(f"{base}/memberships/3001", json={"role": "viewer"}, headers=owner.bearer())
    answers = [
        opened,
        granted,
        await client.get(base, headers=owner.bearer()),
        await client.get("/v1/companies", headers=owner.bearer()),
        await client.get("/v1/organization", headers=owner.bearer()),
        await client.get(f"{base}/establishments", headers=owner.bearer()),
        await client.get(f"{base}/memberships", headers=owner.bearer()),
        await client.get(f"{base}/establishments/{document(opened)['id']}", headers=owner.bearer()),
    ]
    for answer in answers:
        assert answer.status_code in {200, 201}
        assert UUID_TEXT.search(answer.text) is None
        assert UUID_TEXT.search(str(answer.headers)) is None
    holds(document(opened)["id"], ESTABLISHMENT_PREFIX)
    assert document(opened)["company_id"] == company
    assert document(granted)["company_id"] == company
    holds(document(granted)["id"], MEMBERSHIP_PREFIX)
    assert document(answers[2])["organization_id"] == format_id(ORGANIZATION_PREFIX, organization_of("1001"))
    assert document(answers[4])["id"] == format_id(ORGANIZATION_PREFIX, organization_of("1001"))
    assert opened.headers["location"] == f"{base}/establishments/{document(opened)['id']}"


async def test_a_page_is_cut_by_opaque_cursors_that_show_no_internal_id(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    for code in ("SUC1", "SUC2"):
        await created(
            client,
            f"/v1/companies/{company}/establishments",
            {"code": code, "name": "S"},
            owner.posting(code),
        )
    first = page(
        await client.get(
            f"/v1/companies/{company}/establishments", params={"limit": 2}, headers=owner.bearer()
        )
    )
    assert isinstance(first["next_cursor"], str)
    assert UUID_TEXT.search(first["next_cursor"]) is None


def not_the_public_id_of(public: str, prefix: str, other: str) -> list[str]:
    """What is not ``public`` (a TypeID of ``prefix``) though it may be taken for it: the internal id, another
    type's id (``other``), another spelling, a cut or lengthened one, an empty one and garbage."""
    inside = parse_id(prefix, public)
    return [
        str(inside),
        format_id(other, inside),
        public.upper(),
        public + "0",
        public[:-1],
        f"{prefix}_",
        "x" * 300,
    ]


async def test_a_company_that_is_not_named_by_its_public_id_is_not_found_whatever_is_asked(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    for named in not_the_public_id_of(company, COMPANY_PREFIX, ESTABLISHMENT_PREFIX):
        base = f"/v1/companies/{named}"
        for method, suffix, extra in (
            ("GET", "", {}),
            ("PATCH", "", {"If-Match": '"1"'}),
            ("POST", "/deactivate", {"If-Match": '"1"'}),
            ("GET", "/establishments", {}),
            ("POST", "/establishments", {}),
            ("GET", "/memberships", {}),
            ("PUT", "/memberships/3001", {}),
        ):
            response = await client.request(
                method,
                base + suffix,
                json=None if method == "GET" else {"legal_name": "X"},
                headers=owner.posting("w-1") | extra,
            )
            assert (response.status_code, document(response)["code"]) == (404, "company_not_found")
            assert len(named) < 6 or named not in response.text  # "est_" is in "request_id"


async def test_an_establishment_that_is_not_named_by_its_public_id_is_not_found(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    path = f"/v1/companies/{company}/establishments"
    establishment = await created(client, path, {"code": "SUC1", "name": "S"}, owner.posting("e-1"))
    for named in not_the_public_id_of(establishment, ESTABLISHMENT_PREFIX, COMPANY_PREFIX):
        response = await client.get(f"{path}/{named}", headers=owner.bearer())
        assert (response.status_code, document(response)["code"]) == (404, "establishment_not_found")
        assert len(named) < 6 or named not in response.text  # "est_" is in "request_id"
