"""``POST /v1/invoices`` through fiscal to do-ecf-v1 (§8.5 step 2): what the channel's ``check`` refuses.

The ``api`` app serves identity, invoicing, fiscal (whose ``IssuePolicy`` asks the channel) and the signer
beside the channel. A pending enrollment refuses the invoice (fiscal's ``fiscal_enrollment_pending``); an
active one has the channel check the draft: a type without a range to number it is
``do_ecf_range_missing``, nothing stored.

**Known gap (Task 20's as-built note, an owner item):** DGII's e-CF requires the issuer's address
(``DireccionEmisor``, ``seller.address``), and no module keeps one: identity's companies and establishments
hold no address (spec §16.4's cut list: "establishments beyond minimal CRUD (addresses ...)", until "a
country needing them") and invoicing's ``SellerInfo`` snapshots a legal name and a tax id only. So every
invoice routed to do-ecf-v1 is refused today; the test that pins it is replaced by the synchronous path's
(e-NCF, security code, QR within the budget; the recovery path; the dead advance job) once the address
exists.
"""

from typing import Final

from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg

from ledger_testing.api import document
lazy from ledger_testing.api import TestPrincipal

ENROLLMENTS: Final = "/v1/fiscal/enrollments"
RANGES: Final = "/v1/do/numbering_ranges"
A31: Final = {"environment": "testecf", "ecf_type": "31", "first": 1, "last": 100, "expires_on": "2028-12-31"}
LINE: Final = {
    "description": "CEMENTO GRIS 42.5 KG",
    "quantity": "2",
    "unit_price": "500.00",
    "taxes": ["do.itbis.standard"],
}
BUYER: Final = {
    "legal_name": "CONSTRUCTORA DEL CARIBE SRL",
    "tax_id": {"scheme": "do.rnc", "value": "131098193"},
}
INVOICE: Final = {"type": "invoice", "buyer": BUYER, "lines": [LINE]}
ERRORS: Final = TypeAdapter(list[dict[str, str | None]])


def posting(principal: TestPrincipal, company: str, key: str) -> dict[str, str]:
    return principal.posting(key) | {"Ledger-Company": company}


def reading(principal: TestPrincipal, company: str) -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": "test"}


async def enrolled(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, *, ranged: bool = True
) -> None:
    """``company`` enrolled on do-ecf-v1 in ``testecf``: active once a range of 31 is registered."""
    body = {"channel": "do-ecf-v1", "environment": "testecf"}
    response = await client.post(ENROLLMENTS, json=body, headers=posting(owner, company, "enroll"))
    assert response.status_code == 201, response.text
    if not ranged:
        return
    registered = await client.post(RANGES, json=A31, headers=posting(owner, company, "range"))
    assert registered.status_code == 201, registered.text
    reviewed = await client.get(f"{ENROLLMENTS}/{document(response)['id']}", headers=reading(owner, company))
    assert document(reviewed)["status"] == "active"


async def refused(client: httpx.AsyncClient, owner: TestPrincipal, company: str, body: object) -> list[str]:
    """The codes of the objections ``body`` is refused with (422 ``invalid_document``)."""
    response = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "invoice"))
    assert (response.status_code, document(response)["code"]) == (422, "invalid_document"), response.text
    return [str(error["code"]) for error in ERRORS.validate_python(document(response)["errors"])]


async def test_a_pending_enrollment_refuses_the_invoice(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    await enrolled(client, owner, company, ranged=False)
    assert await refused(client, owner, company, INVOICE) == ["fiscal_enrollment_pending"]


async def test_a_type_without_a_range_to_number_it_is_objected_and_nothing_is_stored(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, admin: psycopg.AsyncConnection
) -> None:
    await enrolled(client, owner, company)
    consumer = {"type": "invoice", "lines": [LINE]}  # no buyer's RNC: a 32, whose range is not registered
    assert "do_ecf_range_missing" in await refused(client, owner, company, consumer)
    stored = await admin.execute("SELECT count(*) FROM invoicing.invoices")
    assert await stored.fetchall() == [(0,)]


async def test_no_module_keeps_the_issuer_s_address_dgii_requires_so_the_invoice_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """The known gap (see the module): replaced by the synchronous path's tests once the address exists."""
    await enrolled(client, owner, company)
    assert await refused(client, owner, company, INVOICE) == ["do_ecf_value_required"]
