import io
import logging
from datetime import timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Final, final, override
lazy from collections.abc import Callable, Sequence
lazy from ipaddress import IPv6Address

import httpx
import pytest
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

from ledger_country_do_testing import spike
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.state import Scripted, Status, dgii_check
from ledger_country_do_testing.pki import bundle, pem
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending
lazy from ledger_country_do_testing.clock import ManualClock
lazy from ledger_country_do_testing.pki import Authority, Issued

PASSWORD: Final = "a password nobody prints"
ISSUER_RNC: Final = "101850043"
PUBLIC: Final = IPv4Address("93.184.215.14")
"""What the buyer's host name resolves to in these tests (nothing connects to it: in-process transport)."""


async def public_dns(host: str, port: int) -> Sequence[IPv4Address | IPv6Address]:
    del host, port
    return (PUBLIC,)


@final
class Watching(httpx.AsyncBaseTransport):
    """Passes every request on; keeps each bearer token sent and calls ``before`` with each request."""

    __slots__ = ("_before", "_inner", "tokens")

    def __init__(
        self, inner: httpx.AsyncBaseTransport, before: Callable[[httpx.Request], None] | None = None
    ) -> None:
        self._inner = inner
        self._before = before
        self.tokens: list[str] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            scheme, _, token = request.headers["authorization"].partition(" ")
            if scheme == "Bearer":
                self.tokens.append(token)
        if self._before is not None:
            self._before(request)
        return await self._inner.handle_async_request(request)


@pytest.fixture
def fake(issued: Issued, receiver_identity: Issued, clock: ManualClock) -> FakeDgii:
    """The fake, its directory naming its simulator under the tests' public name."""
    return FakeDgii(
        check=dgii_check(trusted=[issued.certificate]),
        receiver=receiver_identity,
        clock=clock,
        public_url=FAKE_HOSTS.ecf,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """The git-ignored ``.dgii`` directory the secrets live in."""
    found = tmp_path / ".dgii"
    found.mkdir()
    return found


@pytest.fixture
def environ(home: Path, issued: Issued, authority: Authority) -> dict[str, str]:
    (home / "certificate.p12").write_bytes(bundle(issued, password=PASSWORD))
    (home / "certificate.password").write_text(PASSWORD + "\n", encoding="utf-8")
    (home / "roots.pem").write_bytes(pem(authority.certificate))
    return {
        "LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "testecf",
        "LEDGER_DO_ECF_SPIKE_P12": str(home / "certificate.p12"),
        "LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": str(home / "certificate.password"),
        "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS": str(home / "roots.pem"),
        "LEDGER_DO_ECF_SPIKE_RNC": ISSUER_RNC,
        "LEDGER_DO_ECF_SPIKE_LEGAL_NAME": "FERRETERIA DUARTE SRL",
        "LEDGER_DO_ECF_SPIKE_ADDRESS": "AV. DUARTE 100, SANTO DOMINGO",
        "LEDGER_DO_ECF_SPIKE_BUYER_RNC": "131098193",
        "LEDGER_DO_ECF_SPIKE_BUYER_NAME": "CONSTRUCTORA DEL CARIBE SRL",
        "LEDGER_DO_ECF_SPIKE_ECF_HOST": FAKE_HOSTS.ecf,
        "LEDGER_DO_ECF_SPIKE_FC_HOST": FAKE_HOSTS.fc,
        "LEDGER_DO_ECF_SPIKE_OUT": str(home / "out"),
    }


def out_of(environ: dict[str, str]) -> Path:
    return Path(environ["LEDGER_DO_ECF_SPIKE_OUT"])


async def run(
    fake: FakeDgii,
    environ: dict[str, str],
    *argv: str,
    clock: ManualClock,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, str]:
    async def sleep(seconds: float) -> None:
        del seconds

    out = io.StringIO()
    code = await spike.run(
        list(argv),
        environ,
        out=out,
        wiring=spike.Wiring(
            transport=transport or httpx.ASGITransport(app=fake.app),
            resolve=public_dns,
            sleep=sleep,
            clock=clock,
        ),
    )
    return code, out.getvalue()


def assert_no_secret(output: str, *tokens: str) -> None:
    assert PASSWORD not in output
    assert "Bearer" not in output
    assert "BEGIN" not in output
    assert "MII" not in output
    for token in tokens:
        assert token not in output


async def test_auth_obtains_a_token_and_shows_none_of_it(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    watching = Watching(httpx.ASGITransport(app=fake.app))
    code, output = await run(fake, environ, "auth", clock=clock, transport=watching)
    assert code == 0
    assert "environment: testecf" in output
    assert "authenticated with testecf: a token was issued (not shown)" in output
    assert "codigo 0" in output
    assert watching.tokens
    assert_no_secret(output, *watching.tokens)
    assert_no_secret(caplog.text, *watching.tokens)


async def test_the_fake_on_loopback_is_reached_over_plain_http(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    loopback = {
        **environ,
        "LEDGER_DO_ECF_SPIKE_ECF_HOST": DEFAULT_PUBLIC_URL,
        "LEDGER_DO_ECF_SPIKE_FC_HOST": DEFAULT_PUBLIC_URL,
    }
    code, output = await run(fake, loopback, "emit", "--type", "31", "--sequence", "9000001", clock=clock)
    assert (code, "codigo 1 Aceptado" in output) == (0, True)
    assert fake.received[-1].host.startswith("127.0.0.1")


async def test_a_credit_invoice_is_signed_written_before_it_is_sent_and_recorded(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    out = out_of(environ)
    on_disk: list[bool] = []

    def before(request: httpx.Request) -> None:
        if request.url.path.endswith("facturaselectronicas"):
            on_disk.append((out / f"{ISSUER_RNC}E310000000001.xml").exists())
            on_disk.append("E310000000001" in (out / spike.USED_FILE).read_text(encoding="utf-8"))

    watching = Watching(httpx.ASGITransport(app=fake.app), before)
    code, output = await run(
        fake, environ, "emit", "--type", "31", "--sequence", "1", clock=clock, transport=watching
    )
    assert code == 0
    assert on_disk == [True, True]
    assert "e-NCF: E310000000001  route: reception  track id: " in output
    assert "DGII: codigo 1 Aceptado -> accepted (number used)" in output
    assert f"consultatimbre?RncEmisor={ISSUER_RNC}&RncComprador=131098193" in output
    assert (out / f"{ISSUER_RNC}E310000000001.xml").read_bytes() == fake.received[-1].document
    assert (out / spike.USED_FILE).read_text(encoding="utf-8").split() == ["E310000000001"]
    assert '"codigo": 1' in (out / f"{ISSUER_RNC}E310000000001.json").read_text(encoding="utf-8")
    assert_no_secret(output, *watching.tokens)
    assert_no_secret(caplog.text, *watching.tokens)
    again, refusal = await run(fake, environ, "emit", "--type", "31", "--sequence", "1", clock=clock)
    assert (again, "E310000000001 was already used" in refusal) == (2, True)
    assert len(fake.received) == 1


async def test_a_small_consumer_invoice_goes_as_an_rfce(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    code, output = await run(fake, environ, "emit", "--type", "32", "--sequence", "1", clock=clock)
    assert code == 0
    assert "route: rfce  track id: none" in output
    assert "consultatimbrefc?" in output
    out = out_of(environ)
    assert (out / f"{ISSUER_RNC}E320000000001.xml").exists()
    assert (out / f"{ISSUER_RNC}E320000000001.rfce.xml").read_bytes() == fake.received[-1].document


async def test_a_large_or_named_consumer_invoice_names_its_buyer(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    large, output = await run(
        fake, environ, "emit", "--type", "32", "--sequence", "2", "--large", clock=clock
    )
    assert (large, "route: reception" in output) == (0, True)
    named, named_output = await run(
        fake, environ, "emit", "--type", "32", "--sequence", "3", "--named", clock=clock
    )
    assert (named, "route: rfce" in named_output) == (0, True)
    assert b"131098193" in fake.received[-1].document


async def test_an_accepted_credit_invoice_is_delivered_to_its_buyer(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    code, output = await run(
        fake, environ, "emit", "--type", "31", "--sequence", "4", "--deliver", clock=clock
    )
    assert code == 0
    assert "buyer: received; receipt certificate identity: organizationIdentifier VATDO-" in output
    assert "131098193" not in output.partition("buyer:")[2]
    assert (out_of(environ) / f"{ISSUER_RNC}E310000000004.arecf.xml").exists()
    anonymous, summary = await run(
        fake, environ, "emit", "--type", "32", "--sequence", "4", "--deliver", clock=clock
    )
    assert (anonymous, "buyer: not applicable: the buyer has no RNC or cédula" in summary) == (0, True)


async def test_a_buyer_that_refuses_the_document_is_reported(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    fake.state.receiver_rncs = frozenset({"999999999"})
    code, output = await run(
        fake, environ, "emit", "--type", "31", "--sequence", "5", "--deliver", clock=clock
    )
    assert (code, "buyer: not received (reason" in output) == (1, True)


async def test_a_receipt_that_fails_is_reported_and_not_kept(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    fake.state.tamper_arecf = True
    code, output = await run(
        fake, environ, "emit", "--type", "31", "--sequence", "6", "--deliver", clock=clock
    )
    assert code == 1
    assert "DGII: codigo 1 Aceptado" in output
    assert "buyer: do_ecf_signature_invalid" in output
    assert not (out_of(environ) / f"{ISSUER_RNC}E310000000006.arecf.xml").exists()


async def test_delivery_needs_the_trusted_roots_before_anything_is_signed(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, home: Path
) -> None:
    missing = {**environ, "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS": ""}
    code, output = await run(
        fake, missing, "emit", "--type", "31", "--sequence", "7", "--deliver", clock=clock
    )
    assert (code, "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS is required to deliver" in output) == (2, True)
    (home / "empty.pem").write_bytes(b"no certificate here")
    empty = {**environ, "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS": str(home / "empty.pem")}
    code, output = await run(fake, empty, "emit", "--type", "31", "--sequence", "7", "--deliver", clock=clock)
    assert (code, "holds no PEM certificate" in output) == (2, True)
    unreadable = {**environ, "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS": str(home / "absent.pem")}
    code, output = await run(
        fake, unreadable, "emit", "--type", "31", "--sequence", "7", "--deliver", clock=clock
    )
    assert (code, "LEDGER_DO_ECF_SPIKE_TRUSTED_ROOTS names no readable file" in output) == (2, True)
    assert fake.received == []
    assert not (out_of(environ) / spike.USED_FILE).exists()


async def test_a_rejection_and_a_pending_document_exit_one(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    fake.script.default = Scripted(
        status=Status.REJECTED, messages=(("145", "El RNC no es emisor"),), sequence_used=False
    )
    rejected, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "8", clock=clock)
    assert (rejected, "-> rejected (number released)" in output, "145: El RNC no es emisor" in output) == (
        1,
        True,
        True,
    )
    fake.script.default = Scripted(processing=99)
    pending, waiting = await run(
        fake, environ, "emit", "--type", "31", "--sequence", "9", "--deliver", clock=clock
    )
    assert (pending, "still pending" in waiting, "buyer:" in waiting) == (1, True, False)


async def test_a_document_whose_outcome_is_unknown_is_parked_with_its_documents_kept(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    breaking = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "recepcionfc")
    code, output = await run(
        fake, environ, "emit", "--type", "32", "--sequence", "10", clock=clock, transport=breaking
    )
    assert code == 1
    assert "parked: do_ecf_dgii_outcome_unknown (failure: broken; sent: it may be at DGII)" in output
    assert "never send E320000000010 again" in output
    out = out_of(environ)
    assert (out / f"{ISSUER_RNC}E320000000010.rfce.xml").read_bytes() == fake.received[-1].document
    assert '"sent": null' in (out / f"{ISSUER_RNC}E320000000010.json").read_text(encoding="utf-8")
    again, refusal = await run(fake, environ, "emit", "--type", "32", "--sequence", "10", clock=clock)
    assert (again, "E320000000010 was already used" in refusal) == (2, True)
    assert len(fake.received) == 1


async def test_dgii_unavailable_parks_the_document_and_exits_one(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    fake.script.outage = -1
    code, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "11", clock=clock)
    assert code == 1
    assert "parked: do_ecf_dgii_unavailable (failure: server_error;" in output
    assert (out_of(environ) / f"{ISSUER_RNC}E310000000011.xml").exists()
    auth, refused = await run(fake, environ, "auth", clock=clock)
    assert (auth, "error: do_ecf_dgii_unavailable" in refused) == (1, True)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "ecf"}, "never reaches the live environment"),
        ({"LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "prod"}, "is testecf or certecf"),
        ({"LEDGER_DO_ECF_SPIKE_RNC": ""}, "LEDGER_DO_ECF_SPIKE_RNC is required"),
        ({"LEDGER_DO_ECF_SPIKE_RNC": "12"}, "LEDGER_DO_ECF_SPIKE_RNC"),
        ({"LEDGER_DO_ECF_SPIKE_BUYER_RNC": "12"}, "BUYER_"),
        ({"LEDGER_DO_ECF_SPIKE_SEQUENCE_EXPIRES": "2028-12-31"}, "dd-mm-yyyy"),
        ({"LEDGER_DO_ECF_SPIKE_SEQUENCE_EXPIRES": "31-02-2028"}, "dd-mm-yyyy"),
        ({"LEDGER_DO_ECF_SPIKE_ECF_HOST": "http://dgii.example.com"}, "LEDGER_DO_ECF_SPIKE_ECF_HOST"),
        ({"LEDGER_DO_ECF_SPIKE_P12": "certificate.p12"}, "outside a .dgii directory"),
        ({"LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": "certificate.password"}, "outside a .dgii directory"),
        ({"LEDGER_DO_ECF_SPIKE_OUT": "out"}, "LEDGER_DO_ECF_SPIKE_OUT names a directory outside"),
    ],
    ids=[
        "live",
        "unknown",
        "no-rnc",
        "bad-rnc",
        "bad-buyer",
        "bad-expiry",
        "no-such-day",
        "bad-host",
        "bundle-outside",
        "password-outside",
        "out-outside",
    ],
)
async def test_settings_are_checked_and_named_never_echoed(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, change: dict[str, str], expected: str
) -> None:
    code, output = await run(fake, {**environ, **change}, "auth", clock=clock)
    assert (code, expected in output) == (2, True)
    assert_no_secret(output)
    assert fake.received == []


@pytest.mark.parametrize("variable", ["LEDGER_DO_ECF_SPIKE_P12", "LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE"])
async def test_a_secret_file_that_does_not_read_is_named(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, home: Path, variable: str
) -> None:
    code, output = await run(fake, {**environ, variable: str(home / "missing")}, "auth", clock=clock)
    assert (code, f"{variable} names no readable file" in output) == (2, True)


async def test_a_wrong_password_or_an_incomplete_bundle_is_reported_without_either(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock, home: Path, issued: Issued
) -> None:
    (home / "wrong.password").write_text("not it", encoding="utf-8")
    wrong = {**environ, "LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": str(home / "wrong.password")}
    code, output = await run(fake, wrong, "auth", clock=clock)
    assert (code, "does not open" in output, "not it" in output) == (2, True, False)
    (home / "certificate-only.p12").write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"only", None, issued.certificate, None, BestAvailableEncryption(PASSWORD.encode())
        )
    )
    incomplete = {**environ, "LEDGER_DO_ECF_SPIKE_P12": str(home / "certificate-only.p12")}
    code, output = await run(fake, incomplete, "auth", clock=clock)
    assert (code, "holds no private key and certificate pair" in output) == (2, True)
    assert_no_secret(output)


async def test_documents_the_draft_rules_refuse_exit_two(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    no_buyer = {
        key: value for key, value in environ.items() if not key.startswith("LEDGER_DO_ECF_SPIKE_BUYER")
    }
    code, output = await run(fake, no_buyer, "emit", "--type", "31", "--sequence", "12", clock=clock)
    assert (code, "error: do_ecf_" in output) == (2, True)
    zero, zero_output = await run(fake, environ, "emit", "--type", "31", "--sequence", "0", clock=clock)
    assert (zero, "error: do_ecf_" in zero_output) == (2, True)
    assert fake.received == []
    assert not (out_of(environ) / spike.USED_FILE).exists()


async def test_an_output_directory_that_cannot_be_written_stops_before_sending(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    out_of(environ).write_text("a file where the directory goes", encoding="utf-8")
    code, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "13", clock=clock)
    assert (
        code,
        "LEDGER_DO_ECF_SPIKE_OUT names a directory where a file could not be written" in output,
    ) == (2, True)
    assert fake.received == []


async def test_arguments_refused_exit_two(
    fake: FakeDgii, environ: dict[str, str], clock: ManualClock
) -> None:
    code, _ = await run(fake, environ, "emit", "--type", "33", "--sequence", "1", clock=clock)
    assert code == 2
    nothing, _ = await run(fake, environ, clock=clock)
    assert nothing == 2
    helped, _ = await run(fake, environ, "--help", clock=clock)
    assert helped == 0
    assert fake.received == []


def test_main_runs_the_cli_and_exits_with_its_code(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]
) -> None:
    for key, value in {**environ, "LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "ecf"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as caught:
        spike.main(["auth"])
    assert caught.value.code == 2


def test_the_default_wiring_tells_utc_time() -> None:
    assert spike.DEFAULT_WIRING.clock().utcoffset() == timedelta(0)
    assert spike.DEFAULT_WIRING.transport is None
