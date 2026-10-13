"""Binding authority (§3.10; Decision 12): a module binds only what its manifest declares."""

import inspect
from typing import Protocol, TypeIs, final, runtime_checkable
lazy from collections.abc import Coroutine

import pytest

from ledger_platform.runtime.contexts import ModuleParts, api_view, bind_parts, extension_view
from ledger_platform.runtime.facade import (
    NotCoroutineFunctionError,
    UnguardedFacadeError,
    facade,
    is_facade,
    require_facades,
)
from ledger_platform.runtime.keys import (
    BindingMissingError,
    Directory,
    Key,
    UndeclaredBindingError,
    UndeclaredDependencyError,
)
from ledger_platform.runtime.manifest import load_manifest
lazy from ledger_platform.runtime.manifest import PackageManifest


@runtime_checkable
class IdentityApi(Protocol):
    async def company(self, name: str) -> str: ...


@runtime_checkable
class Policy(Protocol):
    async def check(self, document: str) -> bool: ...


@runtime_checkable
class SignerPort(Protocol):
    async def sign(self, payload: bytes) -> bytes: ...


def is_identity_api(value: object) -> TypeIs[IdentityApi]:
    return isinstance(value, IdentityApi)


def is_policy(value: object) -> TypeIs[Policy]:
    return isinstance(value, Policy)


def is_signer(value: object) -> TypeIs[SignerPort]:
    return isinstance(value, SignerPort)


IDENTITY_API = Key[IdentityApi]("identity", is_identity_api)
INVOICING_POLICY = Key[Policy]("invoicing.Policy", is_policy)
PAYMENTS_POLICY = Key[Policy]("payments.Policy", is_policy)
SIGNER_PORT = Key[SignerPort]("SignerPort", is_signer)


class Identity:
    @facade
    async def company(self, name: str) -> str:
        return f"company {name}"


class Approving:
    @facade
    async def check(self, document: str) -> bool:
        return bool(document)


class Signer:
    async def sign(self, payload: bytes) -> bytes:
        return payload


class Unguarded:
    async def company(self, name: str) -> str:
        return name


class UnguardedStatic:
    @facade
    async def company(self, name: str) -> str:
        return name

    @staticmethod
    async def lookup(name: str) -> str:
        return name


class UnguardedClassMethod:
    @classmethod
    async def lookup(cls, name: str) -> str:
        return f"{cls.__name__} {name}"


class GuardedClassMethod:
    @classmethod
    @facade
    async def lookup(cls, name: str) -> str:
        return f"{cls.__name__} {name}"

    @property
    def broken(self) -> str:
        raise AssertionError


class Quiet:
    """Private coroutines and plain methods are no entry points: only public coroutines must be guarded."""

    @facade
    async def company(self, name: str) -> str:
        return await self._fetch(name)

    async def _fetch(self, name: str) -> str:
        return name

    def describe(self) -> str:
        return "quiet"


def manifest(name: str, **extension_points: list[str]) -> PackageManifest:
    lines = [
        "[package]",
        f'name = "{name}"',
        'kind = "module"',
        'summary = "A probe."',
        'owners = ["@o"]',
        "[dependencies]",
        'modules = ["identity"]' if name != "identity" else "modules = []",
        "[extension_points]",
        *(f"{key} = {values!r}".replace("'", '"') for key, values in extension_points.items()),
    ]
    return load_manifest("\n".join(lines))


@final
class Bench:
    def __init__(self) -> None:
        self.apis = Directory("apis")
        self.extensions = Directory("extensions")
        self.ports = Directory("ports")

    def bind(self, declared: PackageManifest, parts: ModuleParts) -> None:
        bind_parts(declared, parts, apis=self.apis, extensions=self.extensions, ports=self.ports)


def test_a_module_binds_its_api_under_its_own_name_and_dependents_read_it() -> None:
    bench = Bench()
    bench.bind(manifest("identity"), ModuleParts(api=IDENTITY_API.bind(Identity())))
    assert isinstance(api_view(manifest("invoicing"), bench.apis).get(IDENTITY_API), Identity)
    with pytest.raises(UndeclaredDependencyError):
        api_view(manifest("identity"), bench.apis).get(IDENTITY_API)


def test_a_module_binding_its_api_under_another_name_is_refused() -> None:
    with pytest.raises(UndeclaredBindingError, match="apis 'identity' is bound by 'invoicing'"):
        Bench().bind(manifest("invoicing"), ModuleParts(api=IDENTITY_API.bind(Identity())))


def test_a_module_providing_a_port_it_does_not_declare_is_refused() -> None:
    bench = Bench()
    with pytest.raises(UndeclaredBindingError, match="ports 'SignerPort' is bound by 'invoicing'"):
        bench.bind(manifest("invoicing"), ModuleParts(provides=(SIGNER_PORT.bind(Signer()),)))
    bench.bind(
        manifest("signer", provides=["SignerPort"]), ModuleParts(provides=(SIGNER_PORT.bind(Signer()),))
    )
    assert isinstance(bench.ports.view(frozenset({"SignerPort"})).get(SIGNER_PORT), Signer)


def test_a_module_implementing_an_extension_point_it_does_not_declare_is_refused() -> None:
    with pytest.raises(UndeclaredBindingError, match=r"extensions 'invoicing\.Policy' is bound by 'fiscal'"):
        Bench().bind(manifest("fiscal"), ModuleParts(implements=(INVOICING_POLICY.bind(Approving()),)))


def test_two_modules_declaring_one_protocol_name_never_collide() -> None:
    bench = Bench()
    fiscal = manifest("fiscal", implements=["invoicing.Policy"])
    bench.bind(fiscal, ModuleParts(implements=(INVOICING_POLICY.bind(Approving()),)))
    invoicing = extension_view(manifest("invoicing", declares=["Policy"]), bench.extensions)
    payments = extension_view(manifest("payments", declares=["Policy"]), bench.extensions)
    assert isinstance(invoicing.get(INVOICING_POLICY), Approving)
    with pytest.raises(BindingMissingError, match=r"payments\.Policy"):
        payments.get(PAYMENTS_POLICY)
    with pytest.raises(UndeclaredDependencyError):
        payments.get(INVOICING_POLICY)


@pytest.mark.parametrize(
    "implementation",
    [Unguarded(), UnguardedStatic(), UnguardedClassMethod()],
    ids=["method", "static", "class"],
)
def test_an_api_or_extension_whose_public_coroutines_are_not_facades_is_refused(
    implementation: object,
) -> None:
    with pytest.raises(UnguardedFacadeError, match="Unguarded"):
        require_facades(implementation)
    with pytest.raises(UnguardedFacadeError):
        Bench().bind(
            manifest("identity"), ModuleParts(api=Key[object]("identity", is_anything).bind(implementation))
        )
    with pytest.raises(UnguardedFacadeError):
        Bench().bind(
            manifest("fiscal", implements=["invoicing.Policy"]),
            ModuleParts(implements=(Key[object]("invoicing.Policy", is_anything).bind(implementation),)),
        )


def is_anything(value: object) -> TypeIs[object]:
    del value
    return True


def test_only_public_coroutine_methods_must_be_guarded() -> None:
    require_facades(Quiet())
    require_facades(GuardedClassMethod())  # its property is never read
    assert is_facade(Quiet.company)
    assert not is_facade(Quiet.describe)


async def greeting() -> str:
    return "hello"


def not_async() -> Coroutine[object, object, str]:
    return greeting()


def test_only_an_async_method_can_be_a_facade() -> None:
    with pytest.raises(NotCoroutineFunctionError, match="not_async"):
        facade(not_async)


async def test_a_facade_is_a_coroutine_function_that_checks_when_called() -> None:
    guarded = facade(greeting)
    assert inspect.iscoroutinefunction(guarded)
    assert await guarded() == "hello"
