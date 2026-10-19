"""``/v1/invoices`` through the ``api`` app: a company's documents, as a client creates, reads, issues and
voids them, with identity (the company, its roles, its establishments) answering through its own API."""

import re
from typing import Final
lazy from collections.abc import Mapping

import pytest
lazy import httpx

from ledger_invoicing.adapters.http.models import InvoiceView
from ledger_invoicing.domain.ids import INVOICE_PREFIX
from ledger_kernel.ids import format_id, new_id, parse_id
from ledger_platform.http.idempotency import MAX_STORED_BODY
from ledger_platform.http.limits import MAX_BODY_BYTES
from ledger_testing.api import TestPrincipal, created, document, page
lazy from ledger_testing.issuer import LocalIssuer

LINE: Final = {"description": "Widget", "quantity": "2", "unit_price": "50", "taxes": ["do.itbis.standard"]}
INVOICE: Final = {"type": "invoice", "lines": [LINE]}
DRAFT: Final = {**INVOICE, "draft": True}
OTHER_COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": "130000012"}],
    "legal_name": "Otra SRL",
}
UUID_TEXT: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
MARK: Final = "Zq7Marker"
"""Text a refused request carries, which no answer may repeat."""


def posting(
    principal: TestPrincipal, company: str, key: str, *, mode: str = "test", version: int | None = None
) -> dict[str, str]:
    """A POST's headers: the caller's token and key, the mode, the company and, if given, ``If-Match``."""
    headers = principal.posting(key, mode) | {"Ledger-Company": company}
    return headers if version is None else headers | {"If-Match": f'"{version}"'}


def reading(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    """A GET's headers: the caller's token, the mode and the company."""
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


async def made(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, body: Mapping[str, object], key: str
) -> dict[str, object]:
    """The document ``owner`` creates in ``company`` with ``body``, as answered."""
    response = await client.post("/v1/invoices", json=body, headers=posting(owner, company, key))
    assert response.status_code == 201, response.text
    return document(response)


def code_of(response: httpx.Response) -> tuple[int, object]:
    return response.status_code, document(response)["code"]


async def test_an_invoice_is_created_issued_and_read_back_with_its_version(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    response = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert response.status_code == 201
    made_ = document(response)
    parse_id(INVOICE_PREFIX, str(made_["id"]))
    assert response.headers["location"] == f"/v1/invoices/{made_['id']}"
    assert response.headers["etag"] == '"1"'
    assert {
        name: made_[name] for name in ("object", "livemode", "type", "direction", "status", "version")
    } == {
        "object": "invoice",
        "livemode": False,
        "type": "invoice",
        "direction": "sales",
        "status": "issued",
        "version": 1,
    }
    assert (made_["currency"], made_["regime"], made_["establishment"], made_["fiscal"]) == (
        "DOP",
        "do",
        "MAIN",
        [],
    )
    assert made_["seller"] == {"legal_name": "Acme SRL", "tax_id": {"scheme": "do.rnc", "value": "101850043"}}
    assert made_["totals"] == {
        "currency": "DOP",
        "net": "100.00",
        "tax": "18.00",
        "gross": "118.00",
        "taxes": [{"category": "do.itbis.standard", "percent": "18", "base": "100.00", "amount": "18.00"}],
    }
    assert made_["lines"] == [
        {
            "description": "Widget",
            "quantity": "2",
            "unit_price": "50",
            "discount": "0",
            "gross": "100",
            "base": "100.00",
            "taxes": [
                {
                    "category": "do.itbis.standard",
                    "kind": "standard",
                    "percent": "18",
                    "since": "2019-01-01",
                    "amount": "18.00",
                }
            ],
        }
    ]
    view = InvoiceView.model_validate_json(response.content)  # the answer is the model it declares
    assert (view.livemode, view.functional_totals) == (False, None)
    read = await client.get(response.headers["location"], headers=reading(owner, company))
    assert (read.status_code, document(read), read.headers["etag"]) == (200, made_, '"1"')


async def test_a_retry_replays_the_first_answer_and_another_body_under_its_key_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    first = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    again = await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-1"))
    assert (again.status_code, again.content, again.headers["idempotent-replayed"]) == (
        201,
        first.content,
        "true",
    )
    other = await client.post("/v1/invoices", json=DRAFT, headers=posting(owner, company, "i-1"))
    assert code_of(other) == (422, "idempotency_key_reused")
    listed = page(await client.get("/v1/invoices", headers=reading(owner, company)))
    assert len(listed["items"]) == 1


async def test_a_draft_is_issued_at_the_version_if_match_names(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    draft = await made(client, owner, company, DRAFT, "d-1")
    assert (draft["status"], draft["seller"], draft["version"]) == ("draft", None, 1)
    path = f"/v1/invoices/{draft['id']}/issue"
    unconditional = await client.post(path, headers=posting(owner, company, "issue-1"))
    assert unconditional.status_code == 428
    stale = await client.post(path, headers=posting(owner, company, "issue-1", version=7))
    assert stale.status_code == 412
    issued = await client.post(path, headers=posting(owner, company, "issue-1", version=1))
    assert (issued.status_code, issued.headers["etag"]) == (200, '"2"')
    assert (document(issued)["status"], document(issued)["version"]) == ("issued", 2)
    retried = await client.post(path, headers=posting(owner, company, "issue-1", version=1))
    assert (retried.content, retried.headers["idempotent-replayed"]) == (issued.content, "true")
    twice = await client.post(path, headers=posting(owner, company, "issue-2", version=2))
    assert code_of(twice) == (409, "invoice_not_issuable")


async def test_an_issued_document_is_voided_once_at_the_version_if_match_names(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    invoice = await made(client, owner, company, INVOICE, "i-1")
    path = f"/v1/invoices/{invoice['id']}/void"
    stale = await client.post(path, json={}, headers=posting(owner, company, "void-1", version=2))
    assert stale.status_code == 412
    voided = await client.post(
        path, json={"reason": "  Wrong   buyer "}, headers=posting(owner, company, "void-1", version=1)
    )
    assert voided.status_code == 200
    assert {name: document(voided)[name] for name in ("status", "version", "void_reason")} == {
        "status": "voided",
        "version": 2,
        "void_reason": "Wrong buyer",
    }
    retried = await client.post(
        path, json={"reason": "  Wrong   buyer "}, headers=posting(owner, company, "void-1", version=1)
    )
    assert (retried.content, retried.headers["idempotent-replayed"]) == (voided.content, "true")
    again = await client.post(path, headers=posting(owner, company, "void-2", version=2))
    assert code_of(again) == (409, "invoice_not_voidable")


async def test_a_note_corrects_an_issued_invoice_which_then_stands_for_it(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    invoice = await made(client, owner, company, INVOICE, "i-1")
    note = await made(
        client, owner, company, {**INVOICE, "type": "credit_note", "corrects": invoice["id"]}, "n-1"
    )
    assert (note["type"], note["corrects"]) == ("credit_note", invoice["id"])
    path = f"/v1/invoices/{invoice['id']}/void"
    refused = await client.post(path, headers=posting(owner, company, "void-1", version=1))
    assert code_of(refused) == (409, "invoice_not_voidable")
    unnamed = await client.post(
        "/v1/invoices", json={**INVOICE, "type": "debit_note"}, headers=posting(owner, company, "n-2")
    )
    assert code_of(unnamed) == (422, "correction_required")


async def test_a_business_key_is_held_once_and_its_conflict_names_the_holder(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    keyed = {**INVOICE, "external_reference": "ORD-1"}
    first = await made(client, owner, company, keyed, "i-1")
    taken = await client.post("/v1/invoices", json=keyed, headers=posting(owner, company, "i-2"))
    assert code_of(taken) == (409, "external_reference_taken")
    assert document(taken)["existing_id"] == first["id"]
    live = await client.post("/v1/invoices", json=keyed, headers=posting(owner, company, "i-3", mode="live"))
    assert live.status_code == 201  # the key is the mode's


async def test_documents_are_listed_by_their_filters_page_by_page_in_one_mode(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    first = await made(client, owner, company, {**INVOICE, "external_reference": "ORD-1"}, "i-1")
    second = await made(client, owner, company, INVOICE, "i-2")
    draft = await made(client, owner, company, DRAFT, "i-3")
    await client.post("/v1/invoices", json=INVOICE, headers=posting(owner, company, "i-4", mode="live"))

    async def ids(**query: str | int) -> list[object]:
        found = await client.get("/v1/invoices", params=query, headers=reading(owner, company))
        assert found.status_code == 200, found.text
        return [item["id"] for item in page(found)["items"]]

    assert await ids() == [first["id"], second["id"], draft["id"]]
    assert await ids(status="draft") == [draft["id"]]
    assert await ids(type="credit_note") == []
    assert await ids(external_reference="ORD-1", direction="sales") == [first["id"]]
    head = page(await client.get("/v1/invoices", params={"limit": 2}, headers=reading(owner, company)))
    assert head["has_more"]
    assert head["next_cursor"] is not None
    assert await ids(limit=2, starting_after=head["next_cursor"]) == [draft["id"]]
    bad = await client.get("/v1/invoices", params={"status": "lost"}, headers=reading(owner, company))
    assert code_of(bad) == (422, "invalid_request")


async def test_a_document_is_found_by_its_own_public_id_in_its_company_and_mode_only(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    invoice = await made(client, owner, company, INVOICE, "i-1")
    other = await created(client, "/v1/companies", OTHER_COMPANY, owner.posting("company-2"))
    missing = format_id(INVOICE_PREFIX, new_id())
    for path, headers in (
        (f"/v1/invoices/{missing}", reading(owner, company)),
        (f"/v1/invoices/{company}", reading(owner, company)),  # a company's id names no document
        ("/v1/invoices/nonsense", reading(owner, company)),
        (f"/v1/invoices/{invoice['id']}", reading(owner, other)),
        (f"/v1/invoices/{invoice['id']}", reading(owner, company, mode="live")),
    ):
        found = await client.get(path, headers=headers)
        assert code_of(found) == (404, "invoice_not_found"), path
        assert missing not in found.text


async def test_a_role_reads_or_changes_documents_as_its_permissions_say(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, local_issuer: LocalIssuer
) -> None:
    invoice = await made(client, owner, company, INVOICE, "i-1")
    viewer, issuer, stranger = (
        TestPrincipal(local_issuer, subject=subject) for subject in ("3001", "3002", "3003")
    )
    for principal, role in ((viewer, "viewer"), (issuer, "issuer")):
        granted = await client.put(
            f"/v1/companies/{company}/memberships/{principal.subject}",
            json={"role": role},
            headers=owner.bearer(),
        )
        assert granted.status_code in {200, 201}
    assert (await client.get("/v1/invoices", headers=reading(viewer, company))).status_code == 200
    created_ = await client.post("/v1/invoices", json=INVOICE, headers=posting(viewer, company, "v-1"))
    assert code_of(created_) == (403, "permission_denied")
    assert (
        await client.post("/v1/invoices", json=INVOICE, headers=posting(issuer, company, "s-1"))
    ).status_code == 201
    voided = await client.post(
        f"/v1/invoices/{invoice['id']}/void", headers=posting(issuer, company, "s-2", version=1)
    )
    assert code_of(voided) == (403, "permission_denied")
    unknown = await client.get("/v1/invoices", headers=reading(stranger, company))
    assert code_of(unknown) == (404, "company_not_found")


async def test_a_request_without_its_mode_or_its_company_is_refused(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    unmoded = await client.get("/v1/invoices", headers=owner.bearer() | {"Ledger-Company": company})
    assert code_of(unmoded) == (400, "mode_required")
    nameless = await client.get("/v1/invoices", headers=owner.bearer() | {"Ledger-Mode": "test"})
    assert code_of(nameless) == (422, "invalid_request")


async def test_a_document_in_another_currency_carries_its_functional_totals(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    dollars = await made(
        client, owner, company, {**INVOICE, "currency": "USD", "exchange_rate": "60.5"}, "i-1"
    )
    assert (dollars["currency"], dollars["exchange_rate"]) == ("USD", "60.5")
    functional = InvoiceView.model_validate(dollars).functional_totals
    assert functional is not None
    assert (functional.currency, functional.gross) == ("DOP", "7139.00")
    unrated = await client.post(
        "/v1/invoices", json={**INVOICE, "currency": "USD"}, headers=posting(owner, company, "i-2")
    )
    assert code_of(unrated) == (422, "exchange_rate_required")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"lines": [{**LINE, "description": f"{MARK}\u0000"}]}, "invalid_text"),
        ({"lines": [{**LINE, "taxes": [f"do.itbis.{MARK.lower()}"]}]}, "invalid_line"),
        ({"lines": [{**LINE, "quantity": f"1{MARK}"}]}, "invalid_request"),
        ({"buyer": {"legal_name": "Beta", "email": f"{MARK}@"}}, "invalid_email"),
        (
            {"buyer": {"legal_name": "Beta", "tax_id": {"scheme": "do.rnc", "value": f"{MARK}1"}}},
            "invalid_tax_id",
        ),
        ({"external_reference": f" {MARK}"}, "invalid_text"),
        ({"fiscal": {"do": {"ecf_type": MARK}}}, "field_unknown"),
        ({"establishment": "NOPE"}, "establishment_unknown"),
        ({"colour": MARK}, "invalid_request"),
    ],
    ids=[
        "description",
        "tax",
        "quantity",
        "email",
        "tax-id",
        "reference",
        "field",
        "establishment",
        "unknown",
    ],
)
async def test_a_refused_document_s_problem_never_repeats_what_was_sent(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, change: dict[str, object], code: str
) -> None:
    response = await client.post(
        "/v1/invoices", json={**INVOICE, **change}, headers=posting(owner, company, "x")
    )
    assert code_of(response) == (422, code)
    assert MARK not in response.text
    assert MARK.lower() not in response.text


async def test_the_largest_document_a_request_can_carry_is_kept_and_its_answer_replayed(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    """1000 lines of long descriptions: a request under 1 MiB whose priced answer is over it."""
    line = {**LINE, "description": "é" * 440}
    body = {**INVOICE, "lines": [line] * 1000}
    sent = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "big"))
    assert len(sent.request.content) < MAX_BODY_BYTES < len(sent.content) <= MAX_STORED_BODY
    assert sent.status_code == 201
    again = await client.post("/v1/invoices", json=body, headers=posting(owner, company, "big"))
    assert (again.content, again.headers["idempotent-replayed"]) == (sent.content, "true")
    too_many = await client.post(
        "/v1/invoices", json={**INVOICE, "lines": [LINE] * 1001}, headers=posting(owner, company, "huge")
    )
    assert code_of(too_many) == (422, "invalid_request")


async def test_every_id_the_api_shows_is_a_type_id_and_no_internal_id_is_shown(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    invoice = await made(client, owner, company, INVOICE, "i-1")
    note = await client.post(
        "/v1/invoices",
        json={**DRAFT, "type": "credit_note", "corrects": invoice["id"]},
        headers=posting(owner, company, "n-1"),
    )
    answers = [
        note,
        await client.get("/v1/invoices", headers=reading(owner, company)),
        await client.get(f"/v1/invoices/{invoice['id']}", headers=reading(owner, company)),
    ]
    for response in answers:
        assert response.status_code in {200, 201}
        assert UUID_TEXT.search(response.text) is None
        assert UUID_TEXT.search(str(response.headers)) is None


async def test_a_buyer_is_kept_as_sent_its_tax_id_read_by_its_scheme(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str
) -> None:
    buyer = {
        "legal_name": " Beta   SRL ",
        "tax_id": {"scheme": "do.rnc", "value": "130-00001-2"},
        "address": "Calle 1, Santo Domingo",
        "email": "compras@beta.example",
    }
    sold = await made(client, owner, company, {**INVOICE, "buyer": buyer}, "i-1")
    assert sold["buyer"] == {
        "legal_name": "Beta SRL",
        "tax_id": {"scheme": "do.rnc", "value": "130000012"},
        "address": "Calle 1, Santo Domingo",
        "email": "compras@beta.example",
    }
