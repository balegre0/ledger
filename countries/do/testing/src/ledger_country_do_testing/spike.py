"""``ledger-dgii-spike``: the spike's emission, run by hand against DGII's TesteCF or CerteCF, or the fake.

Read ``countries/do/docs/runbooks/testecf-spike.md`` first. Settings come from ``LEDGER_DO_ECF_SPIKE_*``
environment variables. The certificate (``P12``) and its password (``P12_PASSWORD_FILE``) are read from files
inside the project's ``.dgii`` directory (git-ignored), and so are the documents written (``OUT``): the
project is the directory the CLI runs in (the repository's root, as the runbook runs it), and a path anywhere
else, another ``.dgii`` included, is refused. The CLI prints what DGII answered and where the documents were
written; it never prints or logs a token, a password, a key or a certificate, and it refuses the live
environment (``ecf``) whatever the hosts.

``emit`` signs first, then writes what it signed (the e-CF, and the RFCE on that route) before anything is
sent, then sends once. Every e-NCF is recorded in ``OUT/used-encf.txt`` before it is signed and is never
signed or sent again: a document whose fate is unknown is *parked* (``parked:`` with why, and whether it
may be at DGII), and its e-NCF is asked about by hand, never resent (the runbook).

Every file is written crash-safe: to a temporary file in the same directory, flushed to disk, then renamed
over its name and the directory synced where the system allows it, so a crash leaves the old file or the new
one, never a part. One run at a time: ``emit`` holds ``OUT/used-encf.lock`` (created exclusively) from
before it reads the journal until it ends; a run that finds it refuses. A run that dies leaves it behind,
to remove by hand once no run is going.

Exit codes: 0 accepted (or accepted with observations; and, with ``--deliver``, received by the buyer or
delivery not applicable), 1 rejected, still pending, parked, DGII unavailable, or the buyer did not receive
it, 2 a setting or an argument refused.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum, auto
from pathlib import Path
from typing import Final, TextIO, final
lazy from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
lazy from datetime import date

from cryptography import x509
from cryptography.x509.oid import NameOID
lazy import httpx

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient, Delivered
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import system_resolve
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.emission import Emitter
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, Issuer
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import (
    OFFICIAL_ECF_HOST,
    OFFICIAL_FC_HOST,
    Environment,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import local_date
from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
from ledger_country_do.channels.ecf_v1.domain.status_map import DOCUMENTED, FiscalStatus
from ledger_country_do_testing.local_signer import BundleIncompleteError, KeyPairSigner
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Resolve
lazy from ledger_country_do.channels.ecf_v1.application.emission import Emitted, Signed
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf

PREFIX: Final = "LEDGER_DO_ECF_SPIKE_"
USED_FILE: Final = "used-encf.txt"
LOCK_FILE: Final = "used-encf.lock"
HOME: Final = ".dgii"
"""The git-ignored directory the secrets and the documents written live in."""
DEFAULT_OUT: Final = f"{HOME}/out"
DEFAULT_EXPIRY: Final = "31-12-2028"
_ALLOWED: Final = frozenset({Environment.TESTECF, Environment.CERTECF})
_ACCEPTED: Final = frozenset({FiscalStatus.ACCEPTED, FiscalStatus.ACCEPTED_WITH_OBSERVATIONS})
_USAGE: Final = 2
_NOT_ACCEPTED: Final = 1
_AUTH_PROBE: Final = "ledger-spike-authentication-check"
"""A track id that does not exist: ``consultaresultado`` needs a token, so asking about it proves one."""
_SENT: Final = {True: "DGII answered about it", False: "it never left", None: "it may be at DGII"}
_IDENTITY_ATTRIBUTES: Final = (
    (NameOID.SERIAL_NUMBER, "serialNumber"),
    (NameOID.ORGANIZATION_IDENTIFIER, "organizationIdentifier"),
)
_IDENTITY_PREFIX: Final = re.compile(r"[A-Z]{5}-")
"""ETSI EN 319 412-1's prefix of an identity attribute (``IDCDO-``, ``VATDO-``...): never the number."""


_IS_REQUIRED: Final = "is required"
_ENVIRONMENTS: Final = "is testecf or certecf"
_LIVE: Final = "is testecf or certecf: the spike never reaches the live environment"
_REFUSED: Final = "is refused"
_DATE: Final = "is a date written dd-mm-yyyy"
_OUTSIDE_FILE: Final = f"names a file outside the project's {HOME} directory"
_OUTSIDE_DIRECTORY: Final = f"names a directory outside the project's {HOME} directory"
_LOCKED: Final = (
    f"{LOCK_FILE} is held by another run, or could not be created; if no run is going, remove it by hand"
)
_JOURNAL_UNREADABLE: Final = f"{USED_FILE} does not read: repair it by hand, never by dropping an e-NCF"
_UNREADABLE: Final = "names no readable file"
_UNWRITABLE: Final = "names a directory where a file could not be written"
_NOT_OPEN: Final = "does not open with the password file's password"
_INCOMPLETE: Final = "holds no private key and certificate pair"
_ROOTS_REQUIRED: Final = "is required to deliver: a PEM file of the root CAs a buyer's receipt chains to"
_NO_PEM: Final = "holds no PEM certificate"
_EXPIRY: Final = re.compile(r"([0-9]{2})-([0-9]{2})-([0-9]{4})")


class Setting(StrEnum):
    """A setting, by its environment variable's name after ``PREFIX``."""

    ENVIRONMENT = auto()
    P12 = auto()
    P12_PASSWORD_FILE = auto()
    TRUSTED_ROOTS = auto()
    RNC = auto()
    LEGAL_NAME = auto()
    ADDRESS = auto()
    BUYER_RNC = auto()
    BUYER_NAME = auto()
    ECF_HOST = auto()
    FC_HOST = auto()
    OUT = auto()
    SEQUENCE_EXPIRES = auto()

    @property
    def variable(self) -> str:
        """The environment variable."""
        return PREFIX + self.name

    def read(self, environ: Mapping[str, str], default: str = "") -> str:
        """Its value in ``environ``, or ``default``."""
        return environ.get(self.variable, default)


_PARTIES: Final = (Setting.RNC, Setting.LEGAL_NAME, Setting.ADDRESS, Setting.BUYER_RNC, Setting.BUYER_NAME)
_HOSTS: Final = (Setting.ECF_HOST, Setting.FC_HOST)


class SettingsError(Exception):
    """A setting refused; the message names the variable, never its value (a pack error's message may follow).

    ``refused``: the setting, or the settings one of which is refused; ``reason`` what is wrong.
    """

    def __init__(
        self, refused: Setting | tuple[Setting, ...], reason: str, cause: EcfError | None = None
    ) -> None:
        names = [each.variable for each in (refused if isinstance(refused, tuple) else (refused,))]
        which = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
        detail = "" if cause is None else f": {cause}"
        super().__init__(f"{which} {reason}{detail}")


class JournalError(Exception):
    """The e-NCF journal (``used-encf.txt``) cannot be used safely: locked by another run, or unreadable."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{Setting.OUT.variable}: {reason}")


def _required(environ: Mapping[str, str], setting: Setting) -> str:
    value = setting.read(environ)
    if not value.strip():
        raise SettingsError(setting, _IS_REQUIRED)
    return value


def _inside(path: Path, home: Path, *, directory: bool) -> bool:
    """Whether ``path``, resolved, lies inside ``home`` (or is it, for a ``directory``)."""
    resolved = path.resolve()
    return resolved.is_relative_to(home) and (directory or resolved != home)


def _secret_file(environ: Mapping[str, str], setting: Setting, home: Path) -> Path:
    path = Path(_required(environ, setting))
    if not _inside(path, home, directory=False):
        raise SettingsError(setting, _OUTSIDE_FILE)
    return path


def _read(path: Path, setting: Setting) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise SettingsError(setting, _UNREADABLE) from error


def _expiry(text: str) -> date:
    """``dd-mm-yyyy`` read as a date: how DGII writes ``FechaVencimientoSecuencia``."""
    found = _EXPIRY.fullmatch(text.strip())
    if found is None:
        raise SettingsError(Setting.SEQUENCE_EXPIRES, _DATE)
    try:
        return date(int(found.group(3)), int(found.group(2)), int(found.group(1)))
    except ValueError as error:
        raise SettingsError(Setting.SEQUENCE_EXPIRES, _DATE) from error


def _environment(environ: Mapping[str, str]) -> Environment:
    try:
        environment = Environment(Setting.ENVIRONMENT.read(environ, Environment.TESTECF.value))
    except ValueError as error:
        raise SettingsError(Setting.ENVIRONMENT, _ENVIRONMENTS) from error
    if environment not in _ALLOWED:
        raise SettingsError(Setting.ENVIRONMENT, _LIVE)
    return environment


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SpikeSettings:
    """What the spike needs, read from the environment (see the module)."""

    environment: Environment
    bundle: Path
    password_file: Path
    trusted_roots: Path | None
    issuer: Issuer
    buyer: Buyer
    hosts: DgiiHosts
    out: Path
    sequence_expires_on: date

    @classmethod
    def from_environ(cls, environ: Mapping[str, str], *, root: Path) -> SpikeSettings:
        """The settings; ``SettingsError`` naming the first variable refused.

        ``root`` is the project's root: secrets and documents live in its ``.dgii`` directory only.
        """
        home = (root / HOME).resolve()
        environment = _environment(environ)
        try:
            issuer = Issuer(
                rnc=_required(environ, Setting.RNC),
                legal_name=_required(environ, Setting.LEGAL_NAME),
                address=_required(environ, Setting.ADDRESS),
            )
            buyer = Buyer(
                rnc=environ.get(Setting.BUYER_RNC.variable), name=environ.get(Setting.BUYER_NAME.variable)
            )
        except EcfError as error:
            raise SettingsError(_PARTIES, _REFUSED, error) from error
        try:
            hosts = DgiiHosts(
                ecf=Setting.ECF_HOST.read(environ, OFFICIAL_ECF_HOST),
                fc=Setting.FC_HOST.read(environ, OFFICIAL_FC_HOST),
            )
        except EcfError as error:
            raise SettingsError(_HOSTS, _REFUSED, error) from error
        out = Path(Setting.OUT.read(environ, DEFAULT_OUT))
        if not _inside(out, home, directory=True):
            raise SettingsError(Setting.OUT, _OUTSIDE_DIRECTORY)
        roots = Setting.TRUSTED_ROOTS.read(environ)
        return cls(
            environment=environment,
            bundle=_secret_file(environ, Setting.P12, home),
            password_file=_secret_file(environ, Setting.P12_PASSWORD_FILE, home),
            trusted_roots=Path(roots) if roots.strip() else None,
            issuer=issuer,
            buyer=buyer,
            hosts=hosts,
            out=out,
            sequence_expires_on=_expiry(Setting.SEQUENCE_EXPIRES.read(environ, DEFAULT_EXPIRY)),
        )

    def signer(self) -> KeyPairSigner:
        """The signer of the certificate the two files hold; ``SettingsError`` if they do not open."""
        data = _read(self.bundle, Setting.P12)
        password = _read(self.password_file, Setting.P12_PASSWORD_FILE).rstrip(b"\r\n")
        try:
            return KeyPairSigner.from_pkcs12(data, password)
        except BundleIncompleteError as error:
            raise SettingsError(Setting.P12, _INCOMPLETE) from error
        except ValueError as error:
            raise SettingsError(Setting.P12, _NOT_OPEN) from error

    def trust(self) -> CounterpartyTrust:
        """The roots a buyer's receipt must chain to, from ``TRUSTED_ROOTS``; ``SettingsError`` without."""
        if self.trusted_roots is None:
            raise SettingsError(Setting.TRUSTED_ROOTS, _ROOTS_REQUIRED)
        try:
            roots = x509.load_pem_x509_certificates(_read(self.trusted_roots, Setting.TRUSTED_ROOTS))
        except ValueError as error:
            raise SettingsError(Setting.TRUSTED_ROOTS, _NO_PEM) from error
        return CounterpartyTrust(tuple(roots))


@final
class _Options(argparse.Namespace):
    command: str = ""
    ecf_type: str = "31"
    sequence: int = 0
    large: bool = False
    named: bool = False
    deliver: bool = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger-dgii-spike", description="The DGII spike, run by hand.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="obtain a DGII token (never shown)")
    emit = commands.add_parser("emit", help="sign, write, then send one e-CF of a sample")
    emit.add_argument("--type", dest="ecf_type", choices=["31", "32"], required=True)
    emit.add_argument(
        "--sequence", type=int, required=True, help="the e-NCF's sequence: never one used before"
    )
    emit.add_argument("--large", action="store_true", help="type 32 of DOP 295,000.00 (sent in full)")
    emit.add_argument("--named", action="store_true", help="type 32 naming the configured buyer")
    emit.add_argument("--deliver", action="store_true", help="once accepted, deliver to the buyer's receiver")
    return parser


def _now() -> datetime:
    return datetime.now(UTC)


def _say(out: TextIO, line: str) -> None:
    out.write(line + "\n")


def sync_directory(path: Path) -> None:
    """Flush ``path``'s directory entries to disk where the system allows it (not on Windows: a no-op)."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, data: bytes) -> Path:
    """``data`` at ``path``, crash-safe (see the module); ``SettingsError`` if it cannot be written."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        try:
            with temporary.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        sync_directory(path.parent)
    except OSError as error:
        raise SettingsError(Setting.OUT, _UNWRITABLE) from error
    return path


@contextmanager
def _locked(out: Path) -> Generator[None]:
    """``out``'s journal held by this run alone, for the length of the block (see the module)."""
    lock = out / LOCK_FILE
    if not out.is_dir():
        try:
            out.mkdir(parents=True)
        except OSError as error:
            raise SettingsError(Setting.OUT, _UNWRITABLE) from error
        sync_directory(out.parent)
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError as error:
        raise JournalError(_LOCKED) from error
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _identity(certificate: x509.Certificate) -> str:
    """The identity attributes of ``certificate``'s subject, with their prefixes; never a number."""
    found: list[str] = []
    for oid, label in _IDENTITY_ATTRIBUTES:
        for attribute in certificate.subject.get_attributes_for_oid(oid):
            prefix = _IDENTITY_PREFIX.match(str(attribute.value))
            found.append(f"{label} {'(bare)' if prefix is None else prefix.group()}")
    return ", ".join(found) or "none"


def _report(emitted: Emitted, settings: SpikeSettings, out: TextIO) -> None:
    signed = emitted.signed
    encf = signed.draft.encf
    _say(out, f"e-NCF: {encf}  route: {signed.route.value}  track id: {emitted.track_id or 'none'}")
    outcome = emitted.outcome
    unknown = emitted.unknown
    if unknown is not None:
        failure = "none" if unknown.failure is None else unknown.failure.value
        _say(out, f"parked: {unknown.code} (failure: {failure}; sent: {_SENT[unknown.sent]})")
        _say(out, f"  never send {encf} again: ask DGII about it by hand (runbook, 'Parked documents')")
    elif outcome is None or not outcome.final:
        _say(
            out,
            "DGII: still pending after the polling schedule; ask consultaresultado for the track id later",
        )
    else:
        number = "" if outcome.number is None else f" (number {outcome.number.value})"
        words = DOCUMENTED[outcome.code.value]
        _say(out, f"DGII: codigo {outcome.code.value} {words} -> {outcome.status.value}{number}")
    messages = () if outcome is None else outcome.messages
    for message in messages:
        _say(out, f"  {message.code}: {message.text}")
    _say(out, f"security code: {signed.security_code}")
    _say(out, f"QR: {signed.qr}")
    parked = (
        None
        if unknown is None
        else {
            "code": unknown.code,
            "failure": None if unknown.failure is None else unknown.failure.value,
            "sent": unknown.sent,
        }
    )
    summary = {
        "encf": str(encf),
        "route": signed.route.value,
        "track_id": emitted.track_id,
        "parked": parked,
        "codigo": None if outcome is None else outcome.code.value,
        "status": None if outcome is None else outcome.status.value,
        "messages": [[message.code, message.text] for message in messages],
        "security_code": signed.security_code,
        "qr": signed.qr,
    }
    path = settings.out / f"{settings.issuer.rnc}{encf}.json"
    _say(out, f"written: {_write(path, json.dumps(summary, indent=2).encode())}")


def _draft(options: _Options, settings: SpikeSettings, clock: Callable[[], datetime]) -> EcfDraft:
    """The sample ``options`` ask for, issued today by the configured issuer (``EcfError`` if refused)."""
    if EcfType(options.ecf_type) is EcfType.CREDIT_INVOICE:
        return replace(
            credit_invoice(options.sequence, issuer=settings.issuer, buyer=settings.buyer),
            issued_on=local_date(clock()),
            sequence_expires_on=settings.sequence_expires_on,
        )
    buyer = settings.buyer if options.large or options.named else Buyer()
    return replace(
        consumer_invoice(options.sequence, large=options.large, buyer=buyer, issuer=settings.issuer),
        issued_on=local_date(clock()),
    )


def _recorded(settings: SpikeSettings, encf: Encf) -> bool:
    """``encf`` recorded as used, before anything is signed; ``False`` if it was already."""
    used = settings.out / USED_FILE
    try:
        already = used.read_text(encoding="utf-8").split() if used.exists() else []
    except (OSError, UnicodeDecodeError) as error:
        raise JournalError(_JOURNAL_UNREADABLE) from error
    if str(encf) in already:
        return False
    _write(used, "\n".join([*already, str(encf)]).encode() + b"\n")
    return True


def _written(signed: Signed, settings: SpikeSettings, out: TextIO) -> None:
    """What was signed, on disk before anything is sent."""
    stem = f"{settings.issuer.rnc}{signed.draft.encf}"
    _say(out, f"written: {_write(settings.out / f'{stem}.xml', signed.signed_ecf)}")
    if signed.signed_rfce is not None:
        _say(out, f"written: {_write(settings.out / f'{stem}.rfce.xml', signed.signed_rfce)}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Wiring:
    """What the CLI talks through: tests inject an in-process transport, a resolver, a sleep and a clock.

    ``transport`` ``None``: httpx's own, TLS verified; ``resolve`` the system's resolver; ``sleep``
    ``asyncio.sleep``; ``clock`` UTC now.
    """

    transport: httpx.AsyncBaseTransport | None = None
    resolve: Resolve = system_resolve
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], datetime] = _now


DEFAULT_WIRING: Final = Wiring()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Run:
    """One run's settings, signer, DGII's client, wiring and output."""

    settings: SpikeSettings
    signer: KeyPairSigner
    dgii: DgiiClient
    wiring: Wiring
    out: TextIO


async def _delivered(signed: Signed, trust: CounterpartyTrust, context: _Run) -> int:
    """``signed``, accepted, delivered to its buyer; the exit code (0: received, or not applicable)."""
    settings, out, draft = context.settings, context.out, signed.draft
    buyers = BuyerClient(
        context.signer,
        context.dgii.directory,
        trust=trust,
        transport=context.wiring.transport,
        resolve=context.wiring.resolve,
        clock=context.wiring.clock,
    )
    try:
        async with buyers.opened() as opened:
            delivery = await opened.deliver(settings.environment, draft, signed.signed_ecf)
    except EcfError as error:
        _say(out, f"buyer: {error.code}: {error}")
        return _NOT_ACCEPTED
    if not isinstance(delivery, Delivered):
        _say(out, f"buyer: not applicable: {delivery.reason}")
        return 0
    path = _write(settings.out / f"{settings.issuer.rnc}{draft.encf}.arecf.xml", delivery.arecf)
    state = "received" if delivery.received else f"not received (reason {delivery.reason})"
    _say(out, f"buyer: {state}; receipt certificate identity: {_identity(delivery.certificate)}")
    _say(out, f"written: {path}")
    return 0 if delivery.received else _NOT_ACCEPTED


async def _emit(options: _Options, context: _Run) -> int:
    settings = context.settings
    trust = settings.trust() if options.deliver else None
    draft = _draft(options, settings, context.wiring.clock)
    with _locked(settings.out):
        return await _emitted(draft, trust, context)


async def _emitted(draft: EcfDraft, trust: CounterpartyTrust | None, context: _Run) -> int:
    """``draft`` recorded, signed, written, sent and reported; delivered if ``trust`` (the journal held)."""
    settings, out = context.settings, context.out
    if not _recorded(settings, draft.encf):
        _say(out, f"error: {draft.encf} was already used; pick a sequence never signed")
        return _USAGE
    emitter = Emitter(
        xml=DgiiXml(),
        signer=context.signer,
        authority=context.dgii,
        environment=settings.environment,
        clock=context.wiring.clock,
        sleep=context.wiring.sleep,
        qr_hosts=QrHosts(ecf=settings.hosts.ecf, fc=settings.hosts.fc),
    )
    signed = await emitter.sign(draft)
    _written(signed, settings, out)
    emitted = await emitter.send(signed)
    _report(emitted, settings, out)
    if emitted.unknown is not None or emitted.outcome is None or emitted.outcome.status not in _ACCEPTED:
        return _NOT_ACCEPTED
    return 0 if trust is None else await _delivered(signed, trust, context)


async def run(
    argv: Sequence[str], environ: Mapping[str, str], *, out: TextIO, wiring: Wiring = DEFAULT_WIRING
) -> int:
    """The CLI (see the module); returns its exit code."""
    try:
        options = _parser().parse_args(argv, namespace=_Options())
    except SystemExit as refused:
        return _USAGE if refused.code else 0
    try:
        settings = SpikeSettings.from_environ(environ, root=Path.cwd())
        signer = settings.signer()
        _say(out, f"environment: {settings.environment.value}")
        dgii = DgiiClient(
            signer,
            hosts=settings.hosts,
            clock=wiring.clock,
            sleep=wiring.sleep,
            transport=wiring.transport,
        )
        async with dgii.opened():
            if options.command == "auth":
                answer = await dgii.result(settings.environment, _AUTH_PROBE)
                issued = f"authenticated with {settings.environment.value}: a token was issued (not shown)"
                _say(out, f"{issued}; consultaresultado answered codigo {answer.code}")
                return 0
            context = _Run(settings=settings, signer=signer, dgii=dgii, wiring=wiring, out=out)
            return await _emit(options, context)
    except (SettingsError, JournalError) as error:
        _say(out, f"error: {error}")
        return _USAGE
    except EcfError as error:
        _say(out, f"error: {error.code}: {error}")
        return _USAGE if error.kind == "invalid" else _NOT_ACCEPTED


def main(argv: Sequence[str] | None = None) -> None:
    """``ledger-dgii-spike``: run the CLI with this process's environment and exit with its code."""
    raise SystemExit(asyncio.run(run(sys.argv[1:] if argv is None else argv, os.environ, out=sys.stdout)))
