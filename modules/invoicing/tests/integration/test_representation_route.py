"""``GET /v1/invoices/{id}/representation?channel=`` (§8.6.7, Decision 22): a document's printed form, through
the ``Renderer`` chain.

invoicing owns the path and never imports a channel: the first renderer (fiscal, for the channel the query
names) that answers renders it; none that does is 404 ``representation_unavailable``. A reader of the
company's documents may fetch it, as it may read the document.
"""

from typing import ClassVar, Final, final, override
lazy from collections.abc import Sequence

import pytest
lazy import httpx

from ledger_invoicing.api import RenderedDocument, Renderer
from ledger_invoicing.domain.ids import INVOICE_PREFIX
from ledger_kernel.errors import UnavailableError
from ledger_kernel.ids import format_id, new_id, parse_id
from ledger_platform.runtime import facade
from ledger_testing.api import TestPrincipal, document
lazy from ledger_invoicing.api import InvoiceInfo, IssuePolicy
lazy from ledger_platform.runtime import App, Call
lazy from ledger_testing.issuer import LocalIssuer

INVOICE: Final = {
    "type": "invoice",
    "lines": [{"description": "Widget", "quantity": "2", "unit_price": "50", "taxes": ["do.itbis.standard"]}],
}
PDF: Final = RenderedDocument(
    media_type="application/pdf", content=b"%PDF-1.7 ...", filename="E310000000001.pdf"
)
PATH: Final = "/v1/invoices/{}/representation"
NO_QUERY: Final[dict[str, str]] = {}


@final
class Rendering(Renderer):
    """A renderer a test scripts: what it answers (``None``: it renders nothing), and what it was asked."""

    def __init__(self) -> None:
        self.answer: RenderedDocument | None = None
        self.unavailable = False
        self.asked: list[tuple[InvoiceInfo, str]] = []

    @override
    @facade
    async def render(self, call: Call, document: InvoiceInfo, channel: str) -> RenderedDocument | None:
        self.asked.append((document, channel))
        if self.unavailable:
            raise ChannelUnavailableError
        return self.answer


class ChannelUnavailableError(UnavailableError):
    """What a channel answers while it cannot render (fiscal's ``ChannelNotReadyError``, say)."""

    code: ClassVar[str] = "probe_channel_unavailable"

    def __init__(self) -> None:
        super().__init__("the channel cannot render now")


@pytest.fixture
def first() -> Rendering:
    return Rendering()


@pytest.fixture
def second() -> Rendering:
    return Rendering()


@pytest.fixture
def implementations(
    first: Rendering, second: Rendering
) -> Sequence[tuple[str, int, IssuePolicy | None, Renderer | None]]:
    return (("probe_second", 20, None, second), ("probe_first", 10, None, first))


@pytest.fixture
def api(extended_api: App) -> App:
    """The ``api`` app with the test's two renderer modules installed."""
    return extended_api


def reading(principal: TestPrincipal, company: str, mode: str = "test") -> dict[str, str]:
    return principal.bearer() | {"Ledger-Company": company, "Ledger-Mode": mode}


async def issued(client: httpx.AsyncClient, owner: TestPrincipal, company: str) -> str:
    headers = owner.posting("i-1", "test") | {"Ledger-Company": company}
    response = await client.post("/v1/invoices", json=INVOICE, headers=headers)
    assert response.status_code == 201, response.text
    return str(document(response)["id"])


async def test_the_first_renderer_that_answers_renders_the_document(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, first: Rendering, second: Rendering
) -> None:
    invoice = await issued(client, owner, company)
    second.answer = PDF
    response = await client.get(
        PATH.format(invoice), params={"channel": "do-ecf-v1"}, headers=reading(owner, company)
    )
    assert response.status_code == 200, response.text
    assert (response.content, response.headers["content-type"]) == (PDF.content, "application/pdf")
    assert response.headers["content-disposition"] == 'inline; filename="E310000000001.pdf"'
    assert (response.headers["cache-control"], response.headers["x-content-type-options"]) == (
        "private, no-store",
        "nosniff",
    )
    [(asked, channel)] = first.asked
    assert (asked.id, channel) == (parse_id(INVOICE_PREFIX, invoice), "do-ecf-v1")
    assert second.asked == first.asked
    first.answer = RenderedDocument(media_type="application/pdf", content=b"first", filename="a.pdf")
    again = await client.get(
        PATH.format(invoice), params={"channel": "do-ecf-v1"}, headers=reading(owner, company)
    )
    assert again.content == b"first"
    assert len(second.asked) == 1  # the first answered: the second is not asked


async def test_no_renderer_that_answers_is_no_representation(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, first: Rendering, second: Rendering
) -> None:
    invoice = await issued(client, owner, company)
    response = await client.get(
        PATH.format(invoice), params={"channel": "do-ecf-v1"}, headers=reading(owner, company)
    )
    assert (response.status_code, document(response)["code"]) == (404, "representation_unavailable")
    assert (len(first.asked), len(second.asked)) == (1, 1)


async def test_a_representation_is_of_the_caller_s_own_documents_and_names_a_channel(
    client: httpx.AsyncClient,
    owner: TestPrincipal,
    company: str,
    local_issuer: LocalIssuer,
    first: Rendering,
) -> None:
    invoice = await issued(client, owner, company)
    first.answer = PDF
    missing = format_id(INVOICE_PREFIX, new_id())
    cases = (
        (PATH.format(missing), {"channel": "do-ecf-v1"}, reading(owner, company), 404, "invoice_not_found"),
        (
            PATH.format(invoice),
            {"channel": "do-ecf-v1"},
            reading(owner, company, "live"),
            404,
            "invoice_not_found",
        ),
        (PATH.format(invoice), NO_QUERY, reading(owner, company), 422, "invalid_request"),
        (PATH.format(invoice), {"channel": "DO ECF"}, reading(owner, company), 422, "invalid_request"),
        (
            PATH.format(invoice),
            {"channel": "do-ecf-v1"},
            TestPrincipal(local_issuer, subject="9999").bearer()
            | {"Ledger-Company": company, "Ledger-Mode": "test"},
            404,
            "company_not_found",
        ),
    )
    for path, params, headers, status, code in cases:
        response = await client.get(path, params=params, headers=headers)
        assert (response.status_code, document(response)["code"]) == (status, code), path
    assert first.asked == []  # nobody was asked to render what the caller may not read


async def test_a_renderer_whose_channel_is_unavailable_answers_503(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, first: Rendering
) -> None:
    invoice = await issued(client, owner, company)
    first.unavailable = True
    response = await client.get(
        PATH.format(invoice), params={"channel": "do-ecf-v1"}, headers=reading(owner, company)
    )
    assert (response.status_code, document(response)["code"]) == (503, "probe_channel_unavailable")


async def test_a_draft_has_no_representation_and_no_renderer_is_asked(
    client: httpx.AsyncClient, owner: TestPrincipal, company: str, first: Rendering
) -> None:
    headers = owner.posting("d-1", "test") | {"Ledger-Company": company}
    response = await client.post("/v1/invoices", json={**INVOICE, "draft": True}, headers=headers)
    first.answer = PDF
    printed = await client.get(
        PATH.format(document(response)["id"]),
        params={"channel": "do-ecf-v1"},
        headers=reading(owner, company),
    )
    assert (printed.status_code, document(printed)["code"]) == (404, "representation_unavailable")
    assert first.asked == []
