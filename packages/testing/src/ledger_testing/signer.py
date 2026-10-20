"""``SignerPort`` for tests: it signs with what the test hands it, records what it was asked, fails on demand.

The testing package imports no country (contract 13), so ``TestSigner`` signs nothing itself: a test gives it
``sign`` and ``sign_seed`` (the DO pack's tests, a real enveloped signature from
``ledger_country_do_testing``'s key in the process; by default the bytes unchanged). It answers as the signer
does: a request whose tenant has no certificate is ``SigningCertificateMissingError``, a document stating a
moment its certificate does not cover ``SigningCertificateNotValidError``, a seed signed when its certificate
does not cover the signer's clock (``clock``) too; and each call first raises the next of ``failures``, in
order, while any remain (``SignerUnavailableError``, ``SignatureRefusedError``, ``SigningClockSkewError``,
...). Every signing request is recorded (``calls``), the refused ones too: what a test asserts is what the
signer was asked.

``with_test_signer`` puts a test signer in an app: the installed module that provides ``SignerPort`` (the
signer module, whose stand-in answers ``signer_unavailable`` outside its own process) is assembled as usual,
and its port is the test's.
"""

from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import ClassVar, Final, final, override
from uuid import UUID
lazy from collections.abc import Callable, Sequence

from ledger_fiscal_kit.ports import (
    SIGNER_PORT,
    CertificateValidity,
    Signed,
    SignerPort,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
)
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.packs import installed_modules
from ledger_platform.runtime.registries import MODULES, Discovered
lazy from ledger_fiscal_kit.ports import SeedToSign, SignatureProfile, XmlToSign
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_platform.runtime.contexts import Module, ModuleContext, ModuleParts
lazy from ledger_platform.runtime.registries import Discover

DEFAULT_VALIDITY: Final = CertificateValidity(
    certificate=UUID("0199e3a0-0000-7000-8000-00000000c0de"),
    not_before=datetime(2000, 1, 1, tzinfo=UTC),
    not_after=datetime(2100, 1, 1, tzinfo=UTC),
)
"""The certificate a test signer has unless told otherwise: it covers any moment a test states."""


def _unchanged(content: bytes) -> bytes:
    return content


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignCall:
    """A signing request as the signer audits it: whose, how, a document's reference or a seed's audience."""

    tenant: Tenant
    """The company in its mode the signature was asked for."""
    profile: SignatureProfile
    reference: str | None = None
    """A document's (``sign_xml``)."""
    audience: str | None = None
    """A seed's (``sign_seed``)."""


class SignerProviderMissingError(LedgerError):
    """``with_test_signer`` for modules none of which provides ``SignerPort``: there is no port to replace."""

    code: ClassVar[str] = "testing_signer_provider_missing"

    def __init__(self) -> None:
        super().__init__(f"no installed module provides {SIGNER_PORT.name}: there is no signer to replace")


@final
class TestSigner(SignerPort):
    """``SignerPort`` signing with the test's functions (see the module)."""

    __test__: ClassVar[bool] = False
    """Not a test class, whatever pytest makes of its name."""
    __slots__ = ("_clock", "_failures", "_sign", "_sign_seed", "_validity", "calls")

    def __init__(
        self,
        *,
        sign: Callable[[bytes], bytes] = _unchanged,
        sign_seed: Callable[[bytes], bytes] = _unchanged,
        validity: CertificateValidity | None = DEFAULT_VALIDITY,
        failures: Sequence[LedgerError] = (),
        clock: Clock = utc_now,
    ) -> None:
        self._sign = sign
        self._sign_seed = sign_seed
        self._validity = validity
        self._failures = deque(failures)
        self._clock = clock
        self.calls: list[SignCall] = []
        """Every signing request, in order, refused ones too."""

    @facade
    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        del call
        self._record(SignCall(tenant=request.tenant, profile=request.profile, reference=request.reference))
        validity = self._certificate()
        if not validity.covers(request.signed_at):
            raise SigningCertificateNotValidError
        return Signed(document=self._sign(request.document), certificate=validity.certificate)

    @facade
    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        del call
        self._record(SignCall(tenant=request.tenant, profile=request.profile, audience=request.audience))
        validity = self._certificate()
        if not validity.covers(self._clock()):
            raise SigningCertificateNotValidError
        return Signed(document=self._sign_seed(request.seed), certificate=validity.certificate)

    @facade
    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        del call
        if tenant.mode is None:
            raise InvalidValueError(field="certificate_validity.tenant", requirement="a tenant in a mode")
        self._reach()
        return self._validity

    def _record(self, call: SignCall) -> None:
        self.calls.append(call)
        self._reach()

    def _reach(self) -> None:
        if self._failures:
            raise self._failures.popleft()

    def _certificate(self) -> CertificateValidity:
        if self._validity is None:
            raise SigningCertificateMissingError
        return self._validity


def _providing(module: Module, signer: SignerPort) -> Module:
    """``module`` as installed, its ``SignerPort`` the test's."""

    def assemble(context: ModuleContext) -> ModuleParts:
        parts = module.assemble(context)
        others = tuple(binding for binding in parts.provides if binding.name != SIGNER_PORT.name)
        return replace(parts, provides=(*others, SIGNER_PORT.bind(signer)))

    return replace(module, assemble=assemble)


def with_test_signer(discover: Discover, signer: SignerPort) -> Discover:
    """``discover``, the modules that provide ``SignerPort`` providing ``signer`` instead (see the module).

    ``SignerProviderMissingError`` when the modules are found and none provides it.
    """

    def found(group: str) -> Sequence[Discovered]:
        if group != MODULES:
            return discover(group)
        modules = installed_modules(discover)
        if not any(SIGNER_PORT.name in module.manifest.extension_points.provides for module in modules):
            raise SignerProviderMissingError
        return tuple(
            Discovered(
                module.manifest.package.name,
                _loader(
                    _providing(module, signer)
                    if SIGNER_PORT.name in module.manifest.extension_points.provides
                    else module
                ),
            )
            for module in modules
        )

    return found


def _loader(module: Module) -> Callable[[], object]:
    def load() -> object:
        return module

    return load
