"""The signer's role put together: its callers, its KMS, its use cases, its service and its handlers.

``build`` makes the use cases over signer's units and custody (the local backend over the KMS, the remote one
over a certification entity when there is one) and what serves them: the gRPC servicer, the facade bound in
the signer's own process, the expiry notices. The assembly calls it with the real dependencies; tests call it
with stand-ins. It refuses to offer the remote backend without an entity (``entity_not_configured``): until a
real entity's adapter exists (spec §3.7), the signer offers ``remote`` nowhere but in a test that hands one.

What the signer's role checks before it starts, in this order (each a defect of the deployment):

1. every caller's token (``caller_tokens``): present, at least 32 visible ASCII characters, none shared by two
   callers, a previous one only beside a current one (``CallerTokens``: ``grpc_caller_token_refused``);
2. the local-file KMS (``local_kms``): an environment that accepts it, said first (``local`` or ``test``,
   else ``kms_local_refused``), its key file named (``signer_kms_key_file_missing``), the file there or made
   in ``local`` (``kms_key_file_unusable``): it is read now, when the app is made;
3. the backends: ``remote`` only with an entity (``entity_not_configured``);
4. the port: any free one (``0``, the tests') only in ``local`` and ``test`` (``signer_grpc_port_refused``).

A job or an event the handlers cannot read (no company's scope, a notice without its certificate and days) is
``signer_work_unreadable`` (``INVALID``): the runners end it dead at once rather than retry it.

The consumer retires a deactivated company's certificates inside its inbox row's unit, so the retirements
commit with the row's deletion; a notice job announces its certificate as expiring if it is still active.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final
lazy from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from ledger_kernel.errors import InvalidInputError, LedgerError
from ledger_kernel.tenancy import Tenant
from ledger_platform.grpc import CallerPolicy, CallerTokens
from ledger_platform.jobs.outcomes import Done
from ledger_signer.adapters.custody.backends import Backends, EntityNotConfiguredError
from ledger_signer.adapters.custody.kms_local import ACCEPTED_IN, KmsLocalRefusedError, LocalFileKms
from ledger_signer.adapters.grpc.server import CALLER_METHODS, SignerServicer
from ledger_signer.adapters.postgres.unit import PgUnits, bind
from ledger_signer.application.certificates import Describing, Retiring, retire_company
from ledger_signer.application.facade import SignerFacade
from ledger_signer.application.importing import Importing
from ledger_signer.application.notices import EXPIRY_NOTICE, Notices
from ledger_signer.application.ports import SignerRules
from ledger_signer.application.signing import Signing
from ledger_signer.domain.certificates import BackendKind, CertificateId
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.config.settings import Environment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import RawEvent
lazy from ledger_platform.jobs.outcomes import Job, JobHandler, JobOutcome
lazy from ledger_signer.adapters.settings import SignerSettings
lazy from ledger_signer.application.ports import CertificationEntity, KeyManagement


class SignerWorkUnreadableError(InvalidInputError):
    """A job or an event the signer cannot read: no retry would read it, so it ends dead at once.

    ``INVALID``: the runners make the job or the inbox row dead with this code on its first attempt.
    """

    code: ClassVar[str] = "signer_work_unreadable"

    def __init__(self, work: str) -> None:
        super().__init__(f"a {work} the signer cannot read")


class SignerPortRefusedError(LedgerError):
    """Any free port (``LEDGER_SIGNER_GRPC_PORT=0``) where keys are real: no caller could find the signer."""

    code: ClassVar[str] = "signer_grpc_port_refused"

    def __init__(self) -> None:
        super().__init__(
            "the signer serves on a port of its own outside local and test: set LEDGER_SIGNER_GRPC_PORT"
        )


class KmsKeyFileMissingError(LedgerError):
    """The signer's role without its local-file KMS's key file named: a defect of the deployment."""

    code: ClassVar[str] = "signer_kms_key_file_missing"

    def __init__(self) -> None:
        super().__init__("the signer needs LEDGER_SIGNER_KMS_KEY_FILE: the local-file KMS's key file")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Notice:
    """What a ``signer.expiry_notice`` job carries."""

    certificate: UUID
    threshold_days: int


_NOTICE: Final = TypeAdapter(_Notice)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signer:
    """The signer's role's parts, over one set of use cases (one signing pool)."""

    servicer: SignerServicer
    """What the gRPC server serves."""
    facade: SignerFacade
    """``SignerPort`` and ``SignerApi`` in the signer's own process."""
    notices: Notices
    """What the expiry notice jobs announce with."""


def caller_tokens(settings: SignerSettings) -> CallerTokens:
    """Each caller's tokens and its allowlist (see the module, check 1)."""
    return CallerTokens(
        [
            CallerPolicy(name=caller, tokens=settings.tokens_of(caller), methods=methods)
            for caller, methods in CALLER_METHODS.items()
        ]
    )


def local_kms(settings: SignerSettings, environment: Environment) -> LocalFileKms:
    """The local-file KMS the settings name (see the module, check 2)."""
    if environment not in ACCEPTED_IN:  # said first: where keys are real, no key file makes it acceptable
        raise KmsLocalRefusedError
    path = settings.kms_key_file
    if path is None:
        raise KmsKeyFileMissingError
    return LocalFileKms(path, environment=environment)


def grpc_port(settings: SignerSettings, environment: Environment) -> int:
    """The port the signer serves on: any free one (``0``) only in ``local`` and ``test`` (check 4)."""
    if settings.grpc_port == 0 and environment not in ACCEPTED_IN:
        raise SignerPortRefusedError
    return settings.grpc_port


def rules_of(settings: SignerSettings) -> SignerRules:
    """The rules the use cases follow, as the settings say."""
    return SignerRules(
        backends=frozenset(BackendKind(name) for name in settings.backends),
        skew=timedelta(seconds=settings.signing_skew_seconds),
        concurrency=settings.signing_concurrency,
        import_concurrency=settings.import_concurrency,
        notice_days=settings.expiry_notice_days,
    )


def build(
    database: BoundDatabase,
    *,
    settings: SignerSettings,
    kms: KeyManagement,
    entity: CertificationEntity | None,
    clock: Clock,
) -> Signer:
    """The use cases over signer's units in ``database`` and custody, and what serves them (check 3)."""
    rules = rules_of(settings)
    if BackendKind.REMOTE in rules.backends and entity is None:
        raise EntityNotConfiguredError
    custody = Backends(kms, entity=entity)
    units = PgUnits(database)
    signing = Signing(units=units, custody=custody, rules=rules, clock=clock)
    describing = Describing(units)
    servicer = SignerServicer(
        importing=Importing(units=units, custody=custody, rules=rules, clock=clock),
        describing=describing,
        retiring=Retiring(units),
        signing=signing,
        import_seconds=settings.import_deadline_seconds,
    )
    return Signer(
        servicer=servicer, facade=SignerFacade(signing=signing, describing=describing), notices=Notices(units)
    )


def _company_of(scope: object, owner: str) -> Tenant:
    """``scope`` as a company's tenant; anything else is work the signer never reads."""
    if not isinstance(scope, Tenant):
        raise SignerWorkUnreadableError(owner)
    return scope


async def retire_deactivated(event: RawEvent, uow: UnitOfWork) -> None:
    """``signer.company_deactivations``: the company's active certificates retired in the row's unit."""
    company = _company_of(event.scope, event.type)
    await retire_company(bind(uow), company.company)


def expiry_notices(notices: Notices) -> JobHandler:
    """``signer.expiry_notice``: its certificate announced as expiring, if it is still active."""

    async def notify(job: Job) -> JobOutcome:
        tenant = _company_of(job.scope, EXPIRY_NOTICE)
        try:
            notice = _NOTICE.validate_python(dict(job.payload))
        except ValidationError:
            raise SignerWorkUnreadableError(EXPIRY_NOTICE) from None
        _ = await notices.notify(
            tenant, CertificateId(notice.certificate), threshold_days=notice.threshold_days
        )
        return Done()

    return notify
