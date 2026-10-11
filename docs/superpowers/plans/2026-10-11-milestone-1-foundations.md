# Milestone 1: Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The uv workspace, the quality gate, and the three shared libraries (`kernel`, `contracts`, `platform`) that every ledger service is built on, plus the local infrastructure and CI.

**Architecture:** One uv workspace. `libs/kernel` is standard-library only (ids, errors, money, Dominican tax ids). `libs/contracts` holds protobuf sources, their generated code and the event envelope. `libs/platform` is the service chassis: settings, logs, lifecycle, health, HTTP app, PostgreSQL unit of work with row-level security, transactional outbox and relay, inbox and consumers with dead letters, tracing, gRPC server, and `Service`, which starts and stops all of it as one. Import direction: platform → contracts → kernel, enforced by import-linter.

**Tech Stack:** Python 3.15, uv 0.13 workspace, FastAPI 0.143, SQLAlchemy 2.1 (async, psycopg 3.3), nats-py 2.16 (JetStream), grpcio 1.84 + mypy-protobuf 5.1, protobuf 7.36, structlog 26.1, OpenTelemetry 1.45, prometheus-client 0.26, pydantic-settings 2.15, pytest 9 + pytest-asyncio 1.4 + hypothesis + testcontainers 4.15 (PostgreSQL 18.6, NATS 2.12.15), buf 1.73.0.

**Spec:** `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` (milestone 1 of §16; services, flows and DGII come in later milestones).

## Global Constraints

- Python `>=3.15`; every package's `pyproject.toml` says `requires-python = ">=3.15"`.
- Code, comments, docs and messages in English.
- Zero `# type: ignore`, `# pyright: ignore`, `# noqa`, `# pragma: no cover` anywhere. Generated protobuf code (`libs/contracts/src/ledger/`) is excluded from linting and type-checking by configuration, not by comments.
- `uv run poe check` passes after every task: ruff format + ruff (`select = ["ALL"]`), basedpyright (`typeCheckingMode = "all"`, warnings fail), mypy `--strict` (+ the extra codes in Task 1), import-linter, deptry, typos, pytest with **100 % branch coverage** and warnings as errors.
- Raise errors with their message built inside the error class (`raise InvalidIdError(text)`), never `raise X("literal")` (ruff TRY003/EM101).
- Type-only imports use Python 3.15 `lazy from ... import ...`. Imports used in annotations that FastAPI or pydantic read at runtime (route signatures, models) stay ordinary imports.
- Never `float` for money. Never echo a whole untrusted input in an error message: use `excerpt()` (Task 1).
- Docker must be running: platform tests use real PostgreSQL `postgres:18.6` and NATS `nats:2.12.15` through testcontainers.
- On Windows, psycopg's async driver needs a selector event loop: tests get one from the root `conftest.py` (Task 1); services run in Linux containers.
- Work on branch `milestone-1-foundations`; one commit per task; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Hostile or huge text in every parser** (ids, amounts, tax ids, event headers): refused with a short message that never echoes the whole input; tests in Tasks 1-4.
2. **Money at the edges** (sums past 10^18, products needing more than 60 digits, mixed currencies): an explicit error, never silent rounding; tests in Task 2.
3. **No tenant in the transaction**: row-level security hides every row and refuses every write (fail closed); tests in Task 7.
4. **Duplicate and poison events**: a redelivered event is a no-op, an unreadable or permanently invalid one goes to dead letters at once instead of retrying ten times; tests in Task 10.
5. **NATS failing while events are pending**: the relay keeps the events, retries, and delivers each once when NATS works again; a crashed background worker makes `/readyz` fail; tests in Tasks 9 and 12.

## File map

```
ledger/
├── .gitattributes .gitignore .python-version _typos.toml conftest.py pyproject.toml uv.lock Tiltfile README.md
├── .github/workflows/ci.yml
├── deploy/local/compose.yaml
├── libs/kernel/
│   ├── pyproject.toml
│   ├── src/ledger_kernel/{__init__,errors,ids,clock,money}.py, py.typed
│   ├── src/ledger_kernel/countries/{__init__,do}.py
│   └── tests/test_{errors,ids,clock,money,do}.py
├── libs/contracts/
│   ├── pyproject.toml, buf.yaml
│   ├── proto/ledger/common/v1/money.proto
│   ├── src/ledger_contracts/{__init__,envelope}.py, py.typed
│   ├── src/ledger/common/v1/money_pb2{,_grpc}.py{,i}      (generated, committed)
│   └── tests/test_envelope.py
└── libs/platform/
    ├── pyproject.toml
    ├── src/ledger_platform/{__init__,settings,logs,lifecycle,health,web,database,tracing,streams,outbox,consumer,grpc_server,service}.py, py.typed
    └── tests/conftest.py, tests/test_{settings,logs,lifecycle,health,web,database,tracing,outbox,consumer,grpc_server,service}.py
```

---

### Task 1: Workspace, quality gate, kernel errors, ids and clock

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.gitattributes`, `_typos.toml`, `conftest.py`, `.github/workflows/ci.yml`
- Create: `libs/kernel/pyproject.toml`, `libs/kernel/src/ledger_kernel/__init__.py`, `py.typed`, `errors.py`, `ids.py`, `clock.py`
- Test: `libs/kernel/tests/test_errors.py`, `test_ids.py`, `test_clock.py`

**Interfaces:**
- Produces: `ErrorKind` (StrEnum: `INVALID`, `NOT_FOUND`, `CONFLICT`, `FORBIDDEN`, `UNAVAILABLE`, `INTERNAL`); `LedgerError(Exception)` with `kind: ClassVar[ErrorKind]`, `code: ClassVar[str]`; subclasses `InvalidInputError`, `NotFoundError`, `ConflictError`, `ForbiddenError`, `UnavailableError`; `excerpt(text: str, limit: int = 40) -> str`.
- Produces: `OrganizationId = NewType(UUID)`, `CompanyId = NewType(UUID)`, `Tenant(organization: OrganizationId, company: CompanyId)` (frozen dataclass), `new_id() -> UUID`, `parse_id(text: str) -> UUID`, `InvalidIdError(text)`.
- Produces: `type Clock = Callable[[], datetime]`, `utc_now() -> datetime`.

- [ ] **Step 1: Create the branch and the workspace files**

```bash
git switch -c milestone-1-foundations
```

`.python-version`:

```
3.15
```

`.gitattributes`:

```
* text=auto eol=lf
*.png binary
```

`.gitignore`:

```
__pycache__/
*.py[oc]
build/
dist/
*.egg-info
.venv
.mypy_cache/
.ruff_cache/
.pytest_cache/
.import_linter_cache/
.hypothesis/
.coverage
htmlcov/
.tilt-dev/
.env
```

`_typos.toml`:

```toml
[files]
extend-exclude = ["uv.lock", "libs/contracts/src/ledger/"]
```

`pyproject.toml` (workspace root; later tasks append to the marked lists):

```toml
[project]
name = "ledger-workspace"
version = "0.0.0"
description = "Workspace root: the shared tooling of every ledger package and service."
requires-python = ">=3.15"
dependencies = ["ledger-kernel"]

[dependency-groups]
dev = [
    "basedpyright>=1.40.2",
    "deptry>=0.25.1",
    "hypothesis>=6.168.5",
    "import-linter>=2.15",
    "mypy>=2.4.0",
    "poethepoet>=0.48.0",
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
    "pytest-cov>=7.1.0",
    "pytest-randomly>=5.0.0",
    "ruff>=0.17.0",
    "typos>=1.51.1",
]

[tool.uv]
required-version = ">=0.13.0"
package = false

[tool.uv.workspace]
members = ["libs/*"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

# ── Tasks: `uv run poe <task>` ──────────────────────────────────────────────────
[tool.poe.tasks]
fmt = { shell = "ruff format . && ruff check --fix .", help = "Format and autofix" }
format-check = { cmd = "ruff format --check .", help = "Verify formatting" }
lint = { cmd = "ruff check .", help = "Lint (every ruff rule enabled)" }
pyright = { cmd = "basedpyright", help = "Type check #1: basedpyright, mode 'all'" }
mypy-kernel = { cmd = "mypy libs/kernel" }
mypy = { sequence = ["mypy-kernel"], help = "Type check #2, one package at a time (each has its own tests)" }
types = { sequence = ["pyright", "mypy"], help = "Both type checkers" }
arch = { cmd = "lint-imports", help = "Architecture contracts (import-linter)" }
deps-kernel = { cmd = "deptry libs/kernel/src --config libs/kernel/pyproject.toml" }
deps = { sequence = ["deps-kernel"], help = "Unused / missing / transitive dependencies, per package" }
spell = { cmd = "typos", help = "Spell check" }
test = { cmd = "pytest", help = "Tests with coverage" }
check = { sequence = ["format-check", "lint", "types", "arch", "deps", "spell", "test"], help = "Everything CI runs" }

# ── Ruff ────────────────────────────────────────────────────────────────────────
[tool.ruff]
target-version = "py315"
line-length = 110
extend-exclude = ["libs/contracts/src/ledger"]

[tool.ruff.lint]
select = ["ALL"]
ignore = [
    "D203",    # conflicts with D211
    "D213",    # conflicts with D212
    "COM812",  # conflicts with the formatter
    "CPY001",  # no copyright header
    "D105",    # magic methods are self-documenting
    "D107",    # __init__ is documented on the class
]

[tool.ruff.lint.per-file-ignores]
"libs/*/tests/**" = [
    "S101",    # assert is the point of a test
    "D",       # test names are the documentation
    "PLR2004", # magic values are fine in assertions
    "S105",    # fake credentials are not secrets
    "S106",
    "INP001",  # tests are not a package: each package's tests/ is its own root
]
"conftest.py" = ["INP001"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.flake8-type-checking]
strict = true
runtime-evaluated-base-classes = [
    "pydantic.BaseModel",
    "pydantic_settings.BaseSettings",
    "sqlalchemy.orm.DeclarativeBase",
]

[tool.ruff.lint.isort]
known-first-party = ["ledger_kernel", "ledger_contracts", "ledger_platform", "ledger"]

[tool.ruff.format]
docstring-code-format = true

# ── Type checker #1: basedpyright ───────────────────────────────────────────────
[tool.basedpyright]
pythonVersion = "3.15"
include = ["libs", "conftest.py"]
exclude = ["libs/contracts/src/ledger", "**/.venv", "**/__pycache__"]
typeCheckingMode = "all"
failOnWarnings = true
reportImplicitOverride = "error"
reportImportCycles = "error"
reportUnnecessaryTypeIgnoreComment = "error"
reportUnusedCallResult = "none"

# ── Type checker #2: mypy --strict + every opt-in error code that applies ──────
[tool.mypy]
python_version = "3.15"
strict = true
warn_unreachable = true
disallow_any_explicit = true
disallow_any_unimported = true
extra_checks = true
local_partial_types = true
strict_bytes = true
exclude = ["libs/contracts/src/ledger/"]
enable_error_code = [
    "deprecated",
    "exhaustive-match",
    "explicit-override",
    "ignore-without-code",
    "mutable-override",
    "possibly-undefined",
    "redundant-expr",
    "redundant-self",
    "truthy-bool",
    "truthy-iterable",
    "unimported-reveal",
    "unused-awaitable",
]

# ── Tests ───────────────────────────────────────────────────────────────────────
[tool.pytest]
minversion = "9.0"
testpaths = ["libs"]
addopts = [
    "--strict-markers",
    "--strict-config",
    "--import-mode=importlib",
    "--cov",
    "--cov-report=term-missing:skip-covered",
]
xfail_strict = true
filterwarnings = ["error"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.coverage.run]
branch = true
source_pkgs = ["ledger_kernel"]
core = "ctrace"

[tool.coverage.report]
fail_under = 100

# ── Architecture contracts (import-linter) ──────────────────────────────────────
[tool.importlinter]
root_packages = ["ledger_kernel"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Production code never imports test tooling"
type = "forbidden"
source_modules = ["ledger_kernel"]
forbidden_modules = ["pytest", "hypothesis"]
```

`libs/kernel/pyproject.toml`:

```toml
[project]
name = "ledger-kernel"
version = "0.1.0"
description = "Primitives every ledger package shares: identifiers, errors, money, clocks, tax ids. Standard library only."
requires-python = ">=3.15"
dependencies = []

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`conftest.py` (workspace root):

```python
"""Test configuration shared by every package."""

import asyncio
import sys
lazy from collections.abc import Callable, Mapping

lazy import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg's async driver cannot run on Windows' default proactor loop: give every test a selector loop."""
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
```

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v10
        with:
          enable-cache: true
      - name: Install (locked)
        run: uv sync --locked
      - name: Format, lint, types x2, architecture, deps, spelling, tests
        run: uv run poe check
```

`libs/kernel/src/ledger_kernel/__init__.py`:

```python
"""Primitives every ledger package shares. Standard library only."""
```

`libs/kernel/src/ledger_kernel/py.typed`: empty file.

- [ ] **Step 2: Write the failing tests**

`libs/kernel/tests/test_errors.py`:

```python
from typing import ClassVar

import pytest

from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
    excerpt,
)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (LedgerError, ErrorKind.INTERNAL, "internal"),
        (InvalidInputError, ErrorKind.INVALID, "invalid_input"),
        (NotFoundError, ErrorKind.NOT_FOUND, "not_found"),
        (ConflictError, ErrorKind.CONFLICT, "conflict"),
        (ForbiddenError, ErrorKind.FORBIDDEN, "forbidden"),
        (UnavailableError, ErrorKind.UNAVAILABLE, "unavailable"),
    ],
)
def test_every_base_error_has_a_kind_and_a_stable_code(
    error: type[LedgerError], kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)


class DuplicateInvoiceError(ConflictError):
    code: ClassVar[str] = "duplicate_invoice"

    def __init__(self, number: int) -> None:
        super().__init__(f"invoice {number} exists")


def test_a_subclass_refines_the_code_and_keeps_the_kind() -> None:
    error = DuplicateInvoiceError(7)
    assert (error.kind, error.code, str(error)) == (ErrorKind.CONFLICT, "duplicate_invoice", "invoice 7 exists")


def test_an_excerpt_is_short_and_quoted() -> None:
    assert excerpt("abc") == "'abc'"
    assert excerpt("x" * 10_000) == "'" + "x" * 40 + "…'"
    assert excerpt("a\nb") == "'a\\nb'"
```

`libs/kernel/tests/test_ids.py`:

```python
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import CompanyId, InvalidIdError, OrganizationId, Tenant, new_id, parse_id

CANONICAL = "0192f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f"


def test_new_ids_are_version_7_unique_and_sorted_by_creation() -> None:
    ids = [new_id() for _ in range(1000)]
    assert all(value.version == 7 for value in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


@given(st.uuids())
def test_the_canonical_form_reads_back_in_any_case(value: UUID) -> None:
    assert parse_id(str(value)) == value
    assert parse_id(str(value).upper()) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not-an-id",
        "{" + CANONICAL + "}",
        "urn:uuid:" + CANONICAL,
        CANONICAL.replace("-", ""),
        "0192f7a46-b1e-7c3d-8e4f-5a6b7c8d9e0f",  # hyphen moved
        CANONICAL + "\n",
        "０１９２f7a4-6b1e-7c3d-8e4f-5a6b7c8d9e0f",  # full-width digits
    ],
    ids=["empty", "word", "braces", "urn", "no-hyphens", "moved-hyphen", "newline", "full-width"],
)
def test_every_other_form_is_refused(text: str) -> None:
    with pytest.raises(InvalidIdError, match="not an id"):
        parse_id(text)


def test_a_huge_input_is_not_echoed_back() -> None:
    with pytest.raises(InvalidIdError) as caught:
        parse_id("x" * 10_000)
    assert len(str(caught.value)) < 60
    assert caught.value.code == "invalid_id"


def test_a_tenant_is_an_immutable_value() -> None:
    organization, company = OrganizationId(new_id()), CompanyId(new_id())
    tenant = Tenant(organization, company)
    assert tenant == Tenant(organization, company)
    assert len({tenant, Tenant(organization, company)}) == 1
    field = "company"
    with pytest.raises(FrozenInstanceError):
        setattr(tenant, field, company)
```

(`setattr` with a variable name: assigning the attribute directly is a type error, and ruff B010 flags `setattr` with a literal name.)

`libs/kernel/tests/test_clock.py`:

```python
from datetime import UTC

from ledger_kernel.clock import utc_now


def test_utc_now_is_aware_and_in_utc() -> None:
    assert utc_now().tzinfo is UTC
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv lock && uv sync && uv run pytest libs/kernel -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_kernel.errors'`.

- [ ] **Step 4: Implement**

`libs/kernel/src/ledger_kernel/errors.py`:

```python
"""The error vocabulary of the platform.

Every error raised on purpose is a ``LedgerError``. Its ``kind`` decides how it crosses a boundary (HTTP
status, gRPC status, retry or not); its ``code`` is stable and machine-readable, so API clients can match
on it; its message is for humans and may change. Messages are built inside each error class.
"""

from enum import StrEnum
from typing import ClassVar, Final

_EXCERPT: Final = 40


class ErrorKind(StrEnum):
    """What went wrong, from the caller's point of view."""

    INVALID = "invalid"
    """The request can never succeed as sent."""
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    """The current state forbids it: a duplicate, a wrong state, a stale version."""
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    """A dependency failed; the same request may succeed later."""
    INTERNAL = "internal"


class LedgerError(Exception):
    """Base of every error the platform raises on purpose."""

    kind: ClassVar[ErrorKind] = ErrorKind.INTERNAL
    code: ClassVar[str] = "internal"


class InvalidInputError(LedgerError):
    """The input is malformed or breaks a rule; sending it again cannot work."""

    kind: ClassVar[ErrorKind] = ErrorKind.INVALID
    code: ClassVar[str] = "invalid_input"


class NotFoundError(LedgerError):
    """The thing asked for does not exist (or the caller may not know it exists)."""

    kind: ClassVar[ErrorKind] = ErrorKind.NOT_FOUND
    code: ClassVar[str] = "not_found"


class ConflictError(LedgerError):
    """The current state forbids the operation."""

    kind: ClassVar[ErrorKind] = ErrorKind.CONFLICT
    code: ClassVar[str] = "conflict"


class ForbiddenError(LedgerError):
    """The caller is known but not allowed to do this."""

    kind: ClassVar[ErrorKind] = ErrorKind.FORBIDDEN
    code: ClassVar[str] = "forbidden"


class UnavailableError(LedgerError):
    """A dependency is failing; retrying later may succeed."""

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "unavailable"


def excerpt(text: str, limit: int = _EXCERPT) -> str:
    """``text`` quoted for an error message, cut at ``limit`` characters so input is never echoed whole."""
    if len(text) <= limit:
        return repr(text)
    return repr(text[:limit] + "…")
```

`libs/kernel/src/ledger_kernel/ids.py`:

```python
"""Identifiers: UUIDv7 for every entity, a distinct type per entity, and the tenant."""

import re
import uuid
from dataclasses import dataclass
from typing import ClassVar, Final, NewType, final
from uuid import UUID

from ledger_kernel.errors import InvalidInputError, excerpt

OrganizationId = NewType("OrganizationId", UUID)
"""A customer organization (one Zitadel organization)."""
CompanyId = NewType("CompanyId", UUID)
"""A company (one RNC) inside an organization: the unit of tenancy."""

_CANONICAL: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII | re.IGNORECASE)


class InvalidIdError(InvalidInputError):
    """Text that is not an id in its canonical form."""

    code: ClassVar[str] = "invalid_id"

    def __init__(self, text: str) -> None:
        super().__init__(f"not an id: {excerpt(text)}")


@final
@dataclass(frozen=True, slots=True)
class Tenant:
    """Whose data a transaction, a request or an event is about."""

    organization: OrganizationId
    company: CompanyId


def new_id() -> UUID:
    """A new identifier: UUIDv7, so ids sort by creation time and index well."""
    return uuid.uuid7()


def parse_id(text: str) -> UUID:
    """Read an id in the canonical 8-4-4-4-12 form (either case); every other form is refused."""
    if _CANONICAL.fullmatch(text) is None:
        raise InvalidIdError(text)
    return UUID(text)
```

`libs/kernel/src/ledger_kernel/clock.py`:

```python
"""Time: always aware, always UTC. Code takes a ``Clock`` so tests can fix the time."""

from datetime import UTC, datetime
lazy from collections.abc import Callable

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest libs/kernel -q`
Expected: PASS, coverage 100 %.

- [ ] **Step 6: Run the whole gate**

Run: `uv run poe check`
Expected: every step passes. If `typos` flags a word, fix the spelling (do not add exceptions for English typos).

- [ ] **Step 7: Normalize line endings and commit**

```bash
git add --renormalize .
git add .
git commit -m "build: workspace, quality gate and kernel errors, ids, clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Kernel money

**Files:**
- Create: `libs/kernel/src/ledger_kernel/money.py`
- Test: `libs/kernel/tests/test_money.py`

**Interfaces:**
- Consumes: `InvalidInputError`, `excerpt` (Task 1).
- Produces: `Currency` (StrEnum `DOP`, `USD`, `EUR`); `Money(amount: Decimal, currency: Currency)` frozen; `Money.of(amount: str | int | Decimal, currency) -> Money`; `Money.zero(currency) -> Money`; `+`, `-`, unary `-`, `* (Decimal | int)`, `<`, `<=`, `>`, `>=`; `rounded() -> Money`; `InvalidAmountError(problem: AmountProblem, value: str)`; `CurrencyMismatchError(left, right)`; constants `MAX_INTEGER_DIGITS = 18`, `MAX_SCALE = 24`.

- [ ] **Step 1: Write the failing tests**

`libs/kernel/tests/test_money.py`:

```python
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.money import CurrencyMismatchError, Currency, InvalidAmountError, Money

amounts = st.decimals(min_value=Decimal("-1e15"), max_value=Decimal("1e15"), places=6)
pesos = st.builds(Money, amounts, st.just(Currency.DOP))


def dop(amount: str) -> Money:
    return Money.of(amount, Currency.DOP)


def test_amounts_are_read_exactly() -> None:
    assert dop("1250.50").amount == Decimal("1250.50")
    assert Money.of(7, Currency.USD) == Money(Decimal(7), Currency.USD)
    assert Money.of(Decimal("2.5"), Currency.EUR).amount == Decimal("2.5")
    assert dop("-0.01").amount == Decimal("-0.01")
    assert Money.zero(Currency.DOP) == dop("0")


@pytest.mark.parametrize(
    "text",
    ["", "abc", "1e3", "NaN", "Infinity", " 1", "1 ", "1_000", "١٢", "1.", ".5", "+1", "1" * 19, "0." + "1" * 25],
)
def test_text_that_is_not_a_plain_decimal_is_refused(text: str) -> None:
    with pytest.raises(InvalidAmountError, match="amount"):
        dop(text)


def test_a_huge_text_is_not_echoed_back() -> None:
    with pytest.raises(InvalidAmountError) as caught:
        dop("9" * 100_000)
    assert len(str(caught.value)) < 120


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("1e18"), Decimal("-1e18"), Decimal("1e-25"), 10**18],
    ids=["nan", "infinity", "too-large", "too-small", "too-precise", "huge-int"],
)
def test_values_out_of_range_are_refused(value: Decimal | int) -> None:
    with pytest.raises(InvalidAmountError):
        Money.of(value, Currency.DOP)


def test_a_boolean_is_not_an_amount() -> None:
    with pytest.raises(InvalidAmountError):
        Money.of(True, Currency.DOP)


def test_arithmetic_is_exact() -> None:
    assert dop("0.10") + dop("0.20") == dop("0.30")
    assert dop("10.50") - dop("0.50") == dop("10")
    assert -dop("3") == dop("-3")
    assert dop("10.50") * 3 == dop("31.50")
    assert dop("10.50") * Decimal("0.18") == dop("1.89")


def test_a_result_out_of_range_is_an_error_not_a_rounding() -> None:
    with pytest.raises(InvalidAmountError):
        dop("999999999999999999") + dop("1")
    with pytest.raises(InvalidAmountError):
        dop("0." + "1" * 24) * Decimal("0." + "1" * 40)


def test_currencies_never_mix() -> None:
    usd = Money.of("1", Currency.USD)
    for operation in (
        lambda: dop("1") + usd,
        lambda: dop("1") - usd,
        lambda: dop("1") < usd,
        lambda: dop("1") <= usd,
        lambda: dop("1") > usd,
        lambda: dop("1") >= usd,
    ):
        with pytest.raises(CurrencyMismatchError, match="DOP with USD"):
            operation()


def test_comparisons() -> None:
    assert dop("1") < dop("2")
    assert dop("2") <= dop("2")
    assert dop("3") > dop("2")
    assert dop("2") >= dop("2")
    assert not dop("2") < dop("2")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0.005", "0.01"), ("-0.005", "-0.01"), ("1.234", "1.23"), ("2.675", "2.68"), ("7", "7.00")],
)
def test_rounding_is_half_away_from_zero_to_the_minor_unit(amount: str, expected: str) -> None:
    assert dop(amount).rounded().amount.as_tuple() == Decimal(expected).as_tuple()


@given(pesos, pesos, pesos)
def test_addition_is_commutative_and_associative(a: Money, b: Money, c: Money) -> None:
    assert a + b == b + a
    assert (a + b) + c == a + (b + c)
    assert a - b == a + (-b)
    assert a + (-a) == Money.zero(Currency.DOP)


@given(pesos)
def test_rounding_is_idempotent_and_within_half_a_cent(a: Money) -> None:
    rounded = a.rounded()
    assert rounded.rounded() == rounded
    assert abs(rounded.amount - a.amount) <= Decimal("0.005")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest libs/kernel/tests/test_money.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_kernel.money'`.

- [ ] **Step 3: Implement**

`libs/kernel/src/ledger_kernel/money.py`:

```python
"""Money: exact decimal amounts in a currency. Never a float, never a silent rounding.

Amounts stay below 10^18 with at most 24 decimal places (a 12-place price times a 12-place quantity).
Arithmetic is exact: a result that would need rounding or leave that range is an ``InvalidAmountError``.
Rounding to the currency's minor unit happens only when asked, with ``rounded()``.
"""

import re
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping

from ledger_kernel.errors import InvalidInputError, excerpt

MAX_INTEGER_DIGITS: Final = 18
MAX_SCALE: Final = 24
_TEXT: Final = re.compile(rf"-?[0-9]{{1,{MAX_INTEGER_DIGITS}}}(?:\.[0-9]{{1,{MAX_SCALE}}})?", re.ASCII)
_EXACT: Final = Context(prec=60, rounding=ROUND_HALF_UP, traps=[InvalidOperation, Inexact, Overflow, DivisionByZero])


class Currency(StrEnum):
    """ISO 4217 currencies the platform handles."""

    DOP = "DOP"
    USD = "USD"
    EUR = "EUR"


_MINOR_UNIT: Final[Mapping[Currency, Decimal]] = {
    Currency.DOP: Decimal("0.01"),
    Currency.USD: Decimal("0.01"),
    Currency.EUR: Decimal("0.01"),
}


class AmountProblem(StrEnum):
    """Why a value is not an acceptable amount."""

    NOT_DECIMAL = "is not a plain decimal number"
    NOT_FINITE = "is not a finite number"
    TOO_LARGE = f"is not below 10^{MAX_INTEGER_DIGITS}"
    TOO_PRECISE = f"has more than {MAX_SCALE} decimal places"
    NOT_EXACT = "cannot be computed exactly"


class InvalidAmountError(InvalidInputError):
    """A value that cannot be an amount of money."""

    code: ClassVar[str] = "invalid_amount"

    def __init__(self, problem: AmountProblem, value: str) -> None:
        super().__init__(f"the amount {excerpt(value)} {problem}")


class CurrencyMismatchError(InvalidInputError):
    """Two amounts in different currencies were combined."""

    code: ClassVar[str] = "currency_mismatch"

    def __init__(self, left: Currency, right: Currency) -> None:
        super().__init__(f"cannot combine {left} with {right}")


def _checked(amount: Decimal) -> Decimal:
    if not amount.is_finite():
        raise InvalidAmountError(AmountProblem.NOT_FINITE, str(amount))
    if amount and amount.adjusted() >= MAX_INTEGER_DIGITS:
        raise InvalidAmountError(AmountProblem.TOO_LARGE, str(amount))
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_SCALE:
        raise InvalidAmountError(AmountProblem.TOO_PRECISE, str(amount))
    return amount


def _exact(operation: Callable[[Decimal, Decimal], Decimal], left: Decimal, right: Decimal) -> Decimal:
    try:
        return operation(left, right)
    except DecimalException as error:
        raise InvalidAmountError(AmountProblem.NOT_EXACT, f"{left} and {right}") from error


@final
@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in one currency."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        _checked(self.amount)

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: Currency) -> Money:
        """Read an amount. Text must be a plain decimal: ``"1250.50"``, ``"-3"`` (no exponent, sign or spaces)."""
        if isinstance(amount, bool):
            raise InvalidAmountError(AmountProblem.NOT_DECIMAL, str(amount))
        if isinstance(amount, str):
            if _TEXT.fullmatch(amount) is None:
                raise InvalidAmountError(AmountProblem.NOT_DECIMAL, amount)
            return cls(Decimal(amount), currency)
        return cls(Decimal(amount), currency)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """Nothing, in ``currency``."""
        return cls(Decimal(0), currency)

    def rounded(self) -> Money:
        """To the currency's minor unit, half away from zero (0.005 → 0.01, -0.005 → -0.01)."""
        return Money(self.amount.quantize(_MINOR_UNIT[self.currency], rounding=ROUND_HALF_UP), self.currency)

    def __add__(self, other: Money) -> Money:
        return Money(_exact(_EXACT.add, self.amount, self._same(other)), self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(_exact(_EXACT.subtract, self.amount, self._same(other)), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(_exact(_EXACT.multiply, self.amount, Decimal(factor)), self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._same(other)

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._same(other)

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._same(other)

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._same(other)

    def _same(self, other: Money) -> Decimal:
        if other.currency is not self.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return other.amount
```

Note: the length check in `_TEXT` runs before `Decimal(...)`, so a 100 000-digit string is refused by the regular expression without being converted.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest libs/kernel -q`
Expected: PASS, coverage 100 %.

- [ ] **Step 5: Gate and commit**

Run: `uv run poe check` — expected: passes.

```bash
git add libs/kernel
git commit -m "feat(kernel): exact money with currencies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Kernel Dominican tax ids (RNC and cédula)

**Files:**
- Create: `libs/kernel/src/ledger_kernel/countries/__init__.py`, `libs/kernel/src/ledger_kernel/countries/do.py`
- Test: `libs/kernel/tests/test_do.py`

**Interfaces:**
- Consumes: `InvalidInputError`, `excerpt`.
- Produces: `TaxIdKind` (StrEnum `RNC`, `CEDULA`); `TaxId(digits: str, kind: TaxIdKind)` frozen; `TaxId.parse(text: str) -> TaxId`; `TaxId.check_digit_matches -> bool`; `str(tax_id) == digits`; `InvalidTaxIdError(text)`.

Background for the implementer: an RNC has 9 digits, a cédula 11. The RNC check digit uses weights `7 9 8 6 5 4 3 2` over the first eight digits: `r = sum % 11`, check `= (10 - r) % 9 + 1`. The cédula uses Luhn. DGII has issued real numbers whose check digit does not match (python-stdnum keeps a whitelist of them), so the check digit is **reported, not enforced**; DGII's registry is the authority and is consulted in milestone 2. Valid examples: RNC `101850043`, `131098193`; cédula `00113918205`, `22400022111`. Invalid check digits: `101850042`, `00113918204`.

- [ ] **Step 1: Write the failing tests**

`libs/kernel/tests/test_do.py`:

```python
import pytest

from ledger_kernel.countries.do import InvalidTaxIdError, TaxId, TaxIdKind


@pytest.mark.parametrize(
    ("text", "digits", "kind"),
    [
        ("101850043", "101850043", TaxIdKind.RNC),
        ("1-01-85004-3", "101850043", TaxIdKind.RNC),
        ("131098193", "131098193", TaxIdKind.RNC),
        ("001-1391820-5", "00113918205", TaxIdKind.CEDULA),
        ("224 0002211 1", "22400022111", TaxIdKind.CEDULA),
    ],
)
def test_rnc_and_cedula_are_read_without_separators(text: str, digits: str, kind: TaxIdKind) -> None:
    tax_id = TaxId.parse(text)
    assert (tax_id.digits, tax_id.kind, str(tax_id)) == (digits, kind, digits)
    assert tax_id.check_digit_matches


@pytest.mark.parametrize("text", ["101850042", "00113918204"])
def test_a_wrong_check_digit_is_reported_not_refused(text: str) -> None:
    assert not TaxId.parse(text).check_digit_matches


@pytest.mark.parametrize(
    "text",
    ["", "12345678", "1234567890", "123456789012", "abcdefghi", "١٠١٨٥٠٠٤٣", "101850043\n", "1018.50043"],
    ids=["empty", "8-digits", "10-digits", "12-digits", "letters", "arabic-digits", "newline", "dot"],
)
def test_anything_else_is_refused(text: str) -> None:
    with pytest.raises(InvalidTaxIdError, match="not an RNC or cédula"):
        TaxId.parse(text)


def test_a_huge_input_is_refused_without_echo() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        TaxId.parse("1" * 100_000)
    assert len(str(caught.value)) < 80


@pytest.mark.parametrize(("digits", "kind"), [("123", TaxIdKind.RNC), ("101850043", TaxIdKind.CEDULA)])
def test_a_tax_id_cannot_be_built_inconsistent(digits: str, kind: TaxIdKind) -> None:
    with pytest.raises(InvalidTaxIdError):
        TaxId(digits, kind)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/kernel/tests/test_do.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_kernel.countries'`.

- [ ] **Step 3: Implement**

`libs/kernel/src/ledger_kernel/countries/__init__.py`:

```python
"""Country-specific identifiers that more than one service needs."""
```

`libs/kernel/src/ledger_kernel/countries/do.py`:

```python
"""Dominican Republic taxpayer identifiers: RNC (9 digits) and cédula (11 digits).

DGII accepts a cédula wherever an RNC is asked for. Check digits are reported, not enforced: DGII has
issued real numbers whose check digit does not match, so whether a number exists is for DGII's registry
to say, consulted by the services that need it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from ledger_kernel.errors import InvalidInputError, excerpt

_RNC_WEIGHTS: Final = (7, 9, 8, 6, 5, 4, 3, 2)
_MAX_TEXT: Final = 20
_LUHN_MAX_DIGIT: Final = 9


class TaxIdKind(StrEnum):
    """Which identifier."""

    RNC = "rnc"
    CEDULA = "cedula"


_LENGTH: Final[Mapping[TaxIdKind, int]] = {TaxIdKind.RNC: 9, TaxIdKind.CEDULA: 11}
_KIND_BY_LENGTH: Final[Mapping[int, TaxIdKind]] = {length: kind for kind, length in _LENGTH.items()}


class InvalidTaxIdError(InvalidInputError):
    """Text that is neither an RNC nor a cédula."""

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, text: str) -> None:
        super().__init__(f"not an RNC or cédula: {excerpt(text)}")


def _rnc_check_digit(first_eight: str) -> int:
    remainder = sum(weight * int(digit) for weight, digit in zip(_RNC_WEIGHTS, first_eight, strict=True)) % 11
    return (10 - remainder) % 9 + 1


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character)
        if position % 2 == 1:
            digit *= 2
            if digit > _LUHN_MAX_DIGIT:
                digit -= _LUHN_MAX_DIGIT
        total += digit
    return total % 10 == 0


@final
@dataclass(frozen=True, slots=True)
class TaxId:
    """An RNC or a cédula, digits only."""

    digits: str
    kind: TaxIdKind

    def __post_init__(self) -> None:
        if not (self.digits.isascii() and self.digits.isdigit()) or len(self.digits) != _LENGTH[self.kind]:
            raise InvalidTaxIdError(self.digits)

    @classmethod
    def parse(cls, text: str) -> TaxId:
        """Read an RNC or a cédula; hyphens and spaces between digits are ignored."""
        if len(text) > _MAX_TEXT:
            raise InvalidTaxIdError(text)
        digits = text.replace("-", "").replace(" ", "")
        kind = _KIND_BY_LENGTH.get(len(digits))
        if kind is None or not (digits.isascii() and digits.isdigit()):
            raise InvalidTaxIdError(text)
        return cls(digits, kind)

    @property
    def check_digit_matches(self) -> bool:
        """Whether the last digit is the one the number's algorithm predicts."""
        match self.kind:
            case TaxIdKind.RNC:
                return _rnc_check_digit(self.digits[:8]) == int(self.digits[8])
            case TaxIdKind.CEDULA:
                return _luhn_valid(self.digits)

    def __str__(self) -> str:
        return self.digits
```

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/kernel -q` — PASS, 100 %. Run: `uv run poe check` — passes.

```bash
git add libs/kernel
git commit -m "feat(kernel): Dominican RNC and cedula

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Contracts: protobuf toolchain and the event envelope

**Files:**
- Create: `libs/contracts/pyproject.toml`, `libs/contracts/buf.yaml`, `libs/contracts/proto/ledger/common/v1/money.proto`
- Create: `libs/contracts/src/ledger_contracts/__init__.py`, `py.typed`, `envelope.py`
- Generate (committed): `libs/contracts/src/ledger/common/v1/money_pb2.py`, `money_pb2.pyi`, `money_pb2_grpc.py`, `money_pb2_grpc.pyi`
- Modify: root `pyproject.toml`, `.github/workflows/ci.yml`
- Test: `libs/contracts/tests/test_envelope.py`

**Interfaces:**
- Consumes: `Tenant`, `OrganizationId`, `CompanyId`, `new_id`, `parse_id`, `InvalidIdError`, `Clock`, `utc_now`, `InvalidInputError`, `excerpt`.
- Produces: `ledger.common.v1.money_pb2.Money` (fields `amount: str`, `currency: str`).
- Produces: `Envelope(id: UUID, type: str, source: str, tenant: Tenant | None, time: datetime, data: bytes, traceparent: str | None = None)`; `Envelope.wrap(message: Message, *, source: str, tenant: Tenant | None, clock: Clock = utc_now) -> Envelope`; `envelope.unwrap(kind: type[M]) -> M`; `envelope.headers() -> dict[str, str]`; `Envelope.from_headers(headers: Mapping[str, str], data: bytes) -> Envelope`; `MalformedEventError(field: str)`; `UnexpectedEventError(expected: str, actual: str)`.

Background: generated imports are rooted at the proto package (`from ledger.common.v1 import money_pb2`), so the generated code lives in a top-level `ledger` namespace package shipped by `ledger-contracts` next to `ledger_contracts` (uv_build `module-name = [...]`, `namespace = true`; verified to build).

- [ ] **Step 1: Package, proto and buf configuration**

`libs/contracts/pyproject.toml`:

```toml
[project]
name = "ledger-contracts"
version = "0.1.0"
description = "Wire contracts of the ledger platform: protobuf messages, gRPC services and the event envelope."
requires-python = ">=3.15"
dependencies = ["ledger-kernel", "protobuf>=7.36.2", "grpcio>=1.84.0"]

[tool.uv.sources]
ledger-kernel = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = ["ledger_contracts", "ledger"]
namespace = true
```

`libs/contracts/buf.yaml`:

```yaml
version: v2
modules:
  - path: proto
lint:
  use:
    - STANDARD
breaking:
  use:
    - FILE
```

`libs/contracts/proto/ledger/common/v1/money.proto`:

```proto
syntax = "proto3";

package ledger.common.v1;

// An exact amount of money.
message Money {
  // A plain decimal ("1250.50", "-3"): never a float.
  string amount = 1;
  // ISO 4217 code, e.g. "DOP".
  string currency = 2;
}
```

Root `pyproject.toml` changes:

```toml
# [project]
dependencies = ["ledger-kernel", "ledger-contracts"]

# [dependency-groups] dev: add
    "grpcio-tools>=1.84.0",
    "mypy-protobuf>=5.1.0",
    "types-grpcio>=1.84.0.20260928",
    "types-protobuf>=7.35.1.20260906",

# [tool.uv.sources]: add
ledger-contracts = { workspace = true }

# [tool.poe.tasks]: add, and extend the sequences
mypy-contracts = { cmd = "mypy libs/contracts" }
deps-contracts = { cmd = "deptry libs/contracts/src --config libs/contracts/pyproject.toml" }
proto = { cmd = "python -m grpc_tools.protoc --proto_path=libs/contracts/proto --python_out=libs/contracts/src --mypy_out=libs/contracts/src --grpc_python_out=libs/contracts/src --mypy_grpc_out=libs/contracts/src libs/contracts/proto/ledger/**/*.proto", help = "Regenerate the code of every .proto" }
proto-lint = { cmd = "docker run --rm --volume ${POE_ROOT}/libs/contracts:/workspace --workdir /workspace bufbuild/buf:1.73.0 lint", help = "buf lint" }
# mypy = { sequence = ["mypy-kernel", "mypy-contracts"] }
# deps = { sequence = ["deps-kernel", "deps-contracts"] }
# check = { sequence = ["format-check", "lint", "types", "arch", "deps", "spell", "proto-lint", "test"] }

# [tool.mypy]: add after the main table
[[tool.mypy.overrides]]
module = ["ledger.*"]
ignore_errors = true   # generated by protoc; its .pyi stubs still type every use

# [tool.coverage.run]
source_pkgs = ["ledger_kernel", "ledger_contracts"]

# [tool.importlinter]
root_packages = ["ledger_kernel", "ledger_contracts"]

[[tool.importlinter.contracts]]
name = "Packages point down: contracts -> kernel"
type = "layers"
layers = ["ledger_contracts", "ledger_kernel"]

# extend the test-tooling contract
source_modules = ["ledger_kernel", "ledger_contracts"]
```

The `ignore_errors` override is configuration for generated files, allowed by the Global Constraints.

- [ ] **Step 2: Generate and check the code**

Run: `uv lock && uv sync && uv run poe proto`
Expected: `libs/contracts/src/ledger/common/v1/money_pb2.py`, `money_pb2.pyi`, `money_pb2_grpc.py`, `money_pb2_grpc.pyi` exist. If poe does not expand `**`, replace the glob in the task by `libs/contracts/proto/ledger/common/v1/money.proto` and add each new proto file to it explicitly from then on.

Run: `uv run poe proto-lint`
Expected: no output, exit 0.

- [ ] **Step 3: Write the failing tests**

`libs/contracts/src/ledger_contracts/__init__.py`:

```python
"""Wire contracts: generated protobuf code lives in the ``ledger`` package next to this one."""
```

`libs/contracts/src/ledger_contracts/py.typed`: empty.

`libs/contracts/tests/test_envelope.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope, MalformedEventError, UnexpectedEventError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
TENANT = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def wrapped(tenant: Tenant | None = TENANT) -> Envelope:
    return Envelope.wrap(Money(amount="10.50", currency="DOP"), source="invoicing", tenant=tenant, clock=lambda: NOW)


def test_wrapping_records_the_payload_type_and_cloudevents_attributes() -> None:
    envelope = wrapped()
    assert envelope.type == "ledger.common.v1.Money"
    assert envelope.id.version == 7
    assert envelope.headers() == {
        "ce-specversion": "1.0",
        "ce-id": str(envelope.id),
        "ce-type": "ledger.common.v1.Money",
        "ce-source": "invoicing",
        "ce-time": "2026-10-11T12:00:00+00:00",
        "ce-organizationid": str(TENANT.organization),
        "ce-subject": str(TENANT.company),
        "content-type": "application/protobuf",
        "Nats-Msg-Id": str(envelope.id),
    }


@pytest.mark.parametrize("tenant", [TENANT, None], ids=["tenant", "platform-wide"])
@pytest.mark.parametrize("traceparent", [TRACEPARENT, None], ids=["traced", "untraced"])
def test_an_envelope_survives_the_wire(tenant: Tenant | None, traceparent: str | None) -> None:
    original = replace(wrapped(tenant), traceparent=traceparent)
    received = Envelope.from_headers(original.headers(), original.data)
    assert received == original
    assert received.unwrap(Money) == Money(amount="10.50", currency="DOP")


@given(st.text(min_size=1, max_size=30))
def test_any_source_survives_the_wire(source: str) -> None:
    original = replace(wrapped(), source=source)
    assert Envelope.from_headers(original.headers(), original.data) == original


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"ce-specversion": "0.3"}, "ce-specversion"),
        ({"ce-id": "not-an-id"}, "ce-id"),
        ({"ce-type": ""}, "ce-type"),
        ({"ce-source": ""}, "ce-source"),
        ({"ce-time": "yesterday"}, "ce-time"),
        ({"ce-time": "2026-10-11T12:00:00"}, "ce-time"),
        ({"ce-subject": "not-an-id"}, "ce-subject"),
        ({"ce-organizationid": "not-an-id"}, "ce-organizationid"),
    ],
)
def test_a_malformed_header_is_named(change: dict[str, str], field: str) -> None:
    with pytest.raises(MalformedEventError, match=field):
        Envelope.from_headers(wrapped().headers() | change, b"")


@pytest.mark.parametrize("missing", ["ce-specversion", "ce-id", "ce-type", "ce-source", "ce-time"])
def test_a_missing_header_is_named(missing: str) -> None:
    headers = wrapped().headers()
    del headers[missing]
    with pytest.raises(MalformedEventError, match=missing):
        Envelope.from_headers(headers, b"")


def test_half_a_tenant_is_malformed() -> None:
    headers = wrapped().headers()
    del headers["ce-subject"]
    with pytest.raises(MalformedEventError, match="ce-subject"):
        Envelope.from_headers(headers, b"")


def test_an_invalid_traceparent_is_dropped_not_fatal() -> None:
    headers = wrapped().headers() | {"traceparent": "garbage"}
    assert Envelope.from_headers(headers, b"").traceparent is None


def test_unwrapping_checks_the_type_and_the_bytes() -> None:
    envelope = wrapped()
    with pytest.raises(UnexpectedEventError, match="expected ledger.common.v1.Money"):
        replace(envelope, type="ledger.other.v1.Thing").unwrap(Money)
    with pytest.raises(MalformedEventError, match="data"):
        replace(envelope, data=b"\x0a\xff").unwrap(Money)


def test_huge_header_values_are_not_echoed() -> None:
    with pytest.raises(MalformedEventError) as caught:
        Envelope.from_headers(wrapped().headers() | {"ce-id": "x" * 100_000}, b"")
    assert len(str(caught.value)) < 120
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest libs/contracts -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_contracts.envelope'`.

- [ ] **Step 5: Implement**

`libs/contracts/src/ledger_contracts/envelope.py`:

```python
"""The envelope of every integration event: CloudEvents 1.0 attributes carried as NATS headers.

The payload is a protobuf message; ``type`` is its full name (``ledger.events.invoicing.v1.DocumentIssued``),
so a consumer knows what it received before decoding. ``Nats-Msg-Id`` is the event id: JetStream drops a
duplicate published within its de-duplication window.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final, final
from uuid import UUID

from google.protobuf.message import DecodeError, Message

from ledger_kernel.clock import Clock, utc_now
from ledger_kernel.errors import InvalidInputError, excerpt
from ledger_kernel.ids import CompanyId, InvalidIdError, OrganizationId, Tenant, new_id, parse_id
lazy from collections.abc import Mapping

SPEC_VERSION: Final = "1.0"
CONTENT_TYPE: Final = "application/protobuf"
_TRACEPARENT: Final = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", re.ASCII)


class MalformedEventError(InvalidInputError):
    """An event whose envelope or payload cannot be read: retrying cannot fix it."""

    code: ClassVar[str] = "malformed_event"

    def __init__(self, field: str, value: str = "") -> None:
        super().__init__(f"event field {field!r} is missing or invalid: {excerpt(value)}")


class UnexpectedEventError(InvalidInputError):
    """An event of another type than the consumer expects."""

    code: ClassVar[str] = "unexpected_event"

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"expected {expected}, got {excerpt(actual)}")


def _required(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "")
    if not value:
        raise MalformedEventError(name)
    return value


def _id(headers: Mapping[str, str], name: str) -> UUID:
    value = _required(headers, name)
    try:
        return parse_id(value)
    except InvalidIdError as error:
        raise MalformedEventError(name, value) from error


def _time(headers: Mapping[str, str]) -> datetime:
    value = _required(headers, "ce-time")
    try:
        time = datetime.fromisoformat(value)
    except ValueError as error:
        raise MalformedEventError("ce-time", value) from error
    if time.tzinfo is None:
        raise MalformedEventError("ce-time", value)
    return time


def _tenant(headers: Mapping[str, str]) -> Tenant | None:
    if "ce-organizationid" not in headers and "ce-subject" not in headers:
        return None
    return Tenant(OrganizationId(_id(headers, "ce-organizationid")), CompanyId(_id(headers, "ce-subject")))


@final
@dataclass(frozen=True, slots=True)
class Envelope:
    """One event: who emitted it, about whom, when, and the serialized payload."""

    id: UUID
    type: str
    source: str
    tenant: Tenant | None
    time: datetime
    data: bytes
    traceparent: str | None = None

    @classmethod
    def wrap(cls, message: Message, *, source: str, tenant: Tenant | None, clock: Clock = utc_now) -> Envelope:
        """A new event carrying ``message``."""
        return cls(
            id=new_id(),
            type=message.DESCRIPTOR.full_name,
            source=source,
            tenant=tenant,
            time=clock(),
            data=message.SerializeToString(deterministic=True),
        )

    def unwrap[M: Message](self, kind: type[M]) -> M:
        """The payload as ``kind``; refuses another type or bytes that do not decode."""
        expected = kind.DESCRIPTOR.full_name
        if self.type != expected:
            raise UnexpectedEventError(expected, self.type)
        try:
            return kind.FromString(self.data)
        except DecodeError as error:
            raise MalformedEventError("data") from error

    def headers(self) -> dict[str, str]:
        """The NATS headers that carry this envelope (the payload travels as the message body)."""
        headers = {
            "ce-specversion": SPEC_VERSION,
            "ce-id": str(self.id),
            "ce-type": self.type,
            "ce-source": self.source,
            "ce-time": self.time.isoformat(),
            "content-type": CONTENT_TYPE,
            "Nats-Msg-Id": str(self.id),
        }
        if self.tenant is not None:
            headers["ce-organizationid"] = str(self.tenant.organization)
            headers["ce-subject"] = str(self.tenant.company)
        if self.traceparent is not None:
            headers["traceparent"] = self.traceparent
        return headers

    @classmethod
    def from_headers(cls, headers: Mapping[str, str], data: bytes) -> Envelope:
        """Read an envelope back; a missing or invalid attribute is a ``MalformedEventError``."""
        version = _required(headers, "ce-specversion")
        if version != SPEC_VERSION:
            raise MalformedEventError("ce-specversion", version)
        traceparent = headers.get("traceparent")
        return cls(
            id=_id(headers, "ce-id"),
            type=_required(headers, "ce-type"),
            source=_required(headers, "ce-source"),
            tenant=_tenant(headers),
            time=_time(headers),
            data=data,
            traceparent=traceparent if traceparent and _TRACEPARENT.fullmatch(traceparent) else None,
        )
```

- [ ] **Step 6: Verify, extend CI, gate, commit**

Run: `uv run pytest libs/contracts -q` — PASS. Run: `uv run poe check` — passes (coverage 100 % over `ledger_kernel` and `ledger_contracts`).

Add to `.github/workflows/ci.yml` after "Install (locked)":

```yaml
      - name: Generated code is up to date
        run: |
          uv run poe proto
          git diff --exit-code -- libs/contracts/src/ledger
      - name: Protobuf breaking changes against main
        if: github.event_name == 'pull_request'
        run: |
          if git cat-file -e origin/main:libs/contracts/buf.yaml 2>/dev/null; then
            docker run --rm --volume "$PWD:/repo" --workdir /repo/libs/contracts bufbuild/buf:1.73.0 \
              breaking --against '/repo/.git#branch=origin/main,subdir=libs/contracts'
          fi
```

```bash
git add pyproject.toml uv.lock libs/contracts .github
git commit -m "feat(contracts): protobuf toolchain and CloudEvents envelope

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Platform package, settings and logs

**Files:**
- Create: `libs/platform/pyproject.toml`, `libs/platform/src/ledger_platform/__init__.py`, `py.typed`, `settings.py`, `logs.py`
- Create: `libs/platform/tests/conftest.py`
- Modify: root `pyproject.toml`
- Test: `libs/platform/tests/test_settings.py`, `test_logs.py`

**Interfaces:**
- Produces: `LogFormat` (StrEnum `CONSOLE`, `JSON`); `ServiceSettings(BaseSettings)` with env prefix `LEDGER_`: `service_name: str` (pattern `^[a-z][a-z0-9-]{1,30}$`), `environment: Literal["local","test","staging","production"] = "local"`, `log_level: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"`, `log_format: LogFormat = CONSOLE`, `database_url: SecretStr`, `nats_url: str = "nats://localhost:4222"`, `http_port: int = 8000`, `grpc_port: int = 50051` (0 = any free port), `otlp_endpoint: str | None = None`.
- Produces: `configure_logging(settings: ServiceSettings) -> None`.

- [ ] **Step 1: Package and root configuration**

`libs/platform/pyproject.toml`:

```toml
[project]
name = "ledger-platform"
version = "0.1.0"
description = "The ledger service chassis: settings, logs, health, HTTP, PostgreSQL, events, tracing, gRPC."
requires-python = ">=3.15"
dependencies = [
    "ledger-kernel",
    "ledger-contracts",
    "fastapi>=0.143.0",
    "grpcio>=1.84.0",
    "nats-py>=2.16.0",
    "opentelemetry-api>=1.45.1",
    "opentelemetry-exporter-otlp-proto-grpc>=1.45.1",
    "opentelemetry-sdk>=1.45.1",
    "prometheus-client>=0.26.0",
    "psycopg[binary]>=3.3.6",
    "pydantic>=2.14.1",
    "pydantic-settings>=2.15.0",
    "sqlalchemy[asyncio]>=2.1.4",
    "structlog>=26.1.0",
]

[tool.uv.sources]
ledger-kernel = { workspace = true }
ledger-contracts = { workspace = true }

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[tool.deptry.package_module_name_map]
"nats-py" = "nats"
"opentelemetry-api" = "opentelemetry"
"opentelemetry-sdk" = "opentelemetry"
"opentelemetry-exporter-otlp-proto-grpc" = "opentelemetry"
"prometheus-client" = "prometheus_client"
"pydantic-settings" = "pydantic_settings"

[tool.deptry.per_rule_ignores]
DEP002 = ["psycopg", "grpcio", "opentelemetry-exporter-otlp-proto-grpc", "nats-py", "fastapi", "sqlalchemy", "opentelemetry-sdk", "opentelemetry-api", "prometheus-client"]
```

The `DEP002` list exists only until the module that imports each package lands; every later task removes the names it starts importing, and Task 12 leaves only `psycopg` (loaded by SQLAlchemy from the `postgresql+psycopg://` URL, never imported).

Root `pyproject.toml` changes:

```toml
# [project]
dependencies = ["ledger-kernel", "ledger-contracts", "ledger-platform"]
# [dependency-groups] dev: add
    "httpx>=0.28.1",
    "testcontainers>=4.15.0",
# [tool.uv.sources]: add
ledger-platform = { workspace = true }
# [tool.poe.tasks]: add and extend
mypy-platform = { cmd = "mypy libs/platform" }
deps-platform = { cmd = "deptry libs/platform/src --config libs/platform/pyproject.toml" }
# mypy = { sequence = ["mypy-kernel", "mypy-contracts", "mypy-platform"] }
# deps = { sequence = ["deps-kernel", "deps-contracts", "deps-platform"] }
# [tool.mypy]: add
plugins = ["pydantic.mypy"]
# [tool.coverage.run]
source_pkgs = ["ledger_kernel", "ledger_contracts", "ledger_platform"]
# [tool.importlinter]
root_packages = ["ledger_kernel", "ledger_contracts", "ledger_platform"]
# the "Packages point down" contract
layers = ["ledger_platform", "ledger_contracts", "ledger_kernel"]
# the test-tooling contract
source_modules = ["ledger_kernel", "ledger_contracts", "ledger_platform"]
forbidden_modules = ["pytest", "hypothesis", "testcontainers", "httpx"]

[[tool.importlinter.contracts]]
name = "Kernel is standard library only"
type = "forbidden"
source_modules = ["ledger_kernel"]
forbidden_modules = ["pydantic", "pydantic_settings", "sqlalchemy", "nats", "fastapi", "starlette", "structlog", "google", "grpc", "opentelemetry", "prometheus_client", "psycopg"]

[[tool.importlinter.contracts]]
name = "Contracts know the wire, not the infrastructure"
type = "forbidden"
source_modules = ["ledger_contracts"]
forbidden_modules = ["pydantic", "sqlalchemy", "nats", "fastapi", "starlette", "structlog", "opentelemetry", "prometheus_client", "psycopg"]

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

`libs/platform/src/ledger_platform/__init__.py`:

```python
"""The service chassis every ledger service is built on."""
```

`libs/platform/src/ledger_platform/py.typed`: empty.

Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/conftest.py`:

```python
"""Fixtures of the platform's tests. Later tasks add real PostgreSQL and NATS here."""

import logging
lazy from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """``configure_logging`` changes process-wide state: put it back after every test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
```

`libs/platform/tests/test_settings.py`:

```python
import pytest
from pydantic import ValidationError

from ledger_platform.settings import LogFormat, ServiceSettings

URL = "postgresql+psycopg://app:s3cret@db:5432/ledger"


def test_settings_come_from_ledger_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    monkeypatch.setenv("LEDGER_LOG_FORMAT", "json")
    monkeypatch.setenv("LEDGER_GRPC_PORT", "0")
    settings = ServiceSettings()
    assert (settings.service_name, settings.log_format, settings.grpc_port) == ("invoicing", LogFormat.JSON, 0)
    assert settings.database_url.get_secret_value() == URL
    assert (settings.environment, settings.http_port, settings.otlp_endpoint) == ("local", 8000, None)


def test_the_database_url_never_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    settings = ServiceSettings()
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings.model_dump())


@pytest.mark.parametrize(
    "environment",
    [
        {"LEDGER_SERVICE_NAME": "Invoicing", "LEDGER_DATABASE_URL": URL},
        {"LEDGER_SERVICE_NAME": "invoicing"},
        {"LEDGER_SERVICE_NAME": "invoicing", "LEDGER_DATABASE_URL": URL, "LEDGER_HTTP_PORT": "70000"},
        {"LEDGER_SERVICE_NAME": "invoicing", "LEDGER_DATABASE_URL": URL, "LEDGER_LOG_LEVEL": "TRACE"},
    ],
    ids=["uppercase-name", "no-database", "port-out-of-range", "unknown-level"],
)
def test_invalid_settings_stop_the_start(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        ServiceSettings()
```

`libs/platform/tests/test_logs.py`:

```python
import json
import logging
from typing import Literal

import pytest
import structlog
from pydantic import SecretStr

from ledger_platform.logs import configure_logging
from ledger_platform.settings import LogFormat, ServiceSettings

type Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def settings(log_format: LogFormat, level: Level = "INFO") -> ServiceSettings:
    return ServiceSettings(
        service_name="invoicing",
        database_url=SecretStr("postgresql+psycopg://x"),
        log_format=log_format,
        log_level=level,
    )


def lines(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines()]


def test_json_logs_carry_the_service_level_and_time(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    structlog.get_logger("ledger.test").info("invoice.issued", number=7)
    (line,) = lines(capsys.readouterr().out)
    assert line["event"] == "invoice.issued"
    assert line["number"] == 7
    assert (line["service"], line["environment"], line["level"]) == ("invoicing", "local", "info")
    assert str(line["timestamp"]).endswith("Z")


def test_standard_library_loggers_are_json_too(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    logging.getLogger("sqlalchemy.engine").warning("pool exhausted")
    (line,) = lines(capsys.readouterr().out)
    assert (line["event"], line["level"], line["logger"]) == ("pool exhausted", "warning", "sqlalchemy.engine")


def explode() -> None:
    raise RuntimeError


def test_exceptions_are_structured(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    try:
        explode()
    except RuntimeError:
        structlog.get_logger().exception("failed")
    (line,) = lines(capsys.readouterr().out)
    assert isinstance(line["exception"], list)


def test_lower_levels_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON, "WARNING"))
    structlog.get_logger().info("noise")
    logging.getLogger("lib").info("noise")
    assert capsys.readouterr().out == ""


def test_console_logs_are_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.CONSOLE))
    structlog.get_logger().info("invoice.issued", number=7)
    output = capsys.readouterr().out
    assert "invoice.issued" in output
    assert "number=7" in output
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest libs/platform -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.settings'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/settings.py`:

```python
"""Configuration every service shares, from ``LEDGER_*`` environment variables.

Invalid or missing values stop the start with every problem listed. Secrets are ``SecretStr``: never in
``repr``, logs or errors.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogFormat(StrEnum):
    """How logs are written."""

    CONSOLE = "console"
    JSON = "json"


class ServiceSettings(BaseSettings):
    """Settings of one service process. Services subclass it to add their own."""

    model_config = SettingsConfigDict(env_prefix="LEDGER_", extra="forbid", frozen=True, env_ignore_empty=True)

    service_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")]
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr
    """SQLAlchemy URL with the psycopg driver: ``postgresql+psycopg://user:password@host:5432/db``."""
    nats_url: str = "nats://localhost:4222"
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    grpc_port: Annotated[int, Field(ge=0, le=65535)] = 50051
    """0 binds any free port (tests)."""
    otlp_endpoint: str | None = None
    """OTLP/gRPC collector, e.g. ``http://otel-collector:4317``; traces are not exported without it."""
```

If basedpyright reports `model_config` as an unannotated class attribute, annotate it `model_config: ClassVar[SettingsConfigDict] = ...` (ShieldBot needed this for `ConfigDict`).

`libs/platform/src/ledger_platform/logs.py`:

```python
"""Structured logs: JSON in production, readable in a terminal; standard-library loggers included."""

import logging
import sys

import structlog

from ledger_platform.settings import LogFormat, ServiceSettings


def configure_logging(settings: ServiceSettings) -> None:
    """Route structlog and every standard-library logger to stdout in ``settings.log_format``."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    rendering: list[structlog.typing.Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if settings.log_format is LogFormat.JSON:
        rendering += [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        rendering.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processors=rendering))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    structlog.contextvars.bind_contextvars(service=settings.service_name, environment=settings.environment)
```

`cache_logger_on_first_use=False` is deliberate: module-level loggers must follow a reconfiguration (tests reconfigure per test).

- [ ] **Step 5: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check`. Remove from `DEP002` in `libs/platform/pyproject.toml` nothing yet (pydantic, pydantic-settings, structlog are imported now and were never listed). Expected: passes.

```bash
git add pyproject.toml uv.lock libs/platform
git commit -m "feat(platform): settings and structured logs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Platform lifecycle, health and the HTTP app

**Files:**
- Create: `libs/platform/src/ledger_platform/lifecycle.py`, `health.py`, `web.py`
- Test: `libs/platform/tests/test_lifecycle.py`, `test_health.py`, `test_web.py`

**Interfaces:**
- Produces: `Lifecycle` (async context manager) with `async enter(resource: AbstractAsyncContextManager[T]) -> T` and `on_close(callback: Callable[[], Awaitable[object]])`.
- Produces: `type Check = Callable[[], Awaitable[object]]`; `Health(*, timeout: float = 2.0)` with `add(name, check)`, `async run() -> dict[str, bool]`; `DuplicateCheckError(name)`.
- Produces: `PROBLEM_JSON = "application/problem+json"`; `create_app(*, title: str, version: str, health: Health, registry: CollectorRegistry, lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]], routers: Sequence[APIRouter] = ()) -> FastAPI` with `/livez`, `/readyz`, `/metrics`, RFC 9457 errors, and the histogram `http_request_duration_seconds{method,route,status}`.

- [ ] **Step 1: Write the failing tests**

`libs/platform/tests/test_lifecycle.py`:

```python
from contextlib import AbstractAsyncContextManager, asynccontextmanager
lazy from collections.abc import AsyncGenerator

import pytest

from ledger_platform.lifecycle import Lifecycle


def resource(name: str, log: list[str]) -> AbstractAsyncContextManager[str]:
    @asynccontextmanager
    async def managed() -> AsyncGenerator[str]:
        log.append(f"open {name}")
        try:
            yield name
        finally:
            log.append(f"close {name}")

    return managed()


async def test_resources_close_in_reverse_order() -> None:
    log: list[str] = []

    async def flush() -> None:
        log.append("flush")

    async with Lifecycle() as lifecycle:
        assert await lifecycle.enter(resource("pool", log)) == "pool"
        lifecycle.on_close(flush)
        await lifecycle.enter(resource("client", log))
    assert log == ["open pool", "open client", "close client", "flush", "close pool"]


def fail() -> None:
    raise RuntimeError


async def test_a_failed_start_still_releases_what_was_opened() -> None:
    log: list[str] = []
    with pytest.raises(RuntimeError):
        async with Lifecycle() as lifecycle:
            await lifecycle.enter(resource("pool", log))
            fail()
    assert log == ["open pool", "close pool"]
```

`libs/platform/tests/test_health.py`:

```python
import asyncio

import pytest

from ledger_platform.health import DuplicateCheckError, Health


async def healthy() -> None:
    return None


async def broken() -> None:
    raise ConnectionError


async def stuck() -> None:
    await asyncio.sleep(10)


async def test_every_check_is_reported() -> None:
    health = Health(timeout=0.05)
    health.add("database", healthy)
    health.add("nats", broken)
    health.add("slow", stuck)
    assert await health.run() == {"database": True, "nats": False, "slow": False}


async def test_no_checks_is_healthy() -> None:
    assert await Health().run() == {}


def test_a_name_is_used_once() -> None:
    health = Health()
    health.add("database", healthy)
    with pytest.raises(DuplicateCheckError, match="database"):
        health.add("database", healthy)
```

`libs/platform/tests/test_web.py`:

```python
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import ClassVar

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from prometheus_client import CollectorRegistry
from pydantic import BaseModel

from ledger_kernel.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
)
from ledger_platform.health import Health
from ledger_platform.web import PROBLEM_JSON, create_app
lazy from collections.abc import AsyncGenerator


class MissingItemError(NotFoundError):
    code: ClassVar[str] = "missing_item"

    def __init__(self, item: int) -> None:
        super().__init__(f"item {item} does not exist")


class Item(BaseModel):
    name: str
    quantity: int


router = APIRouter()
ERRORS: dict[str, type[LedgerError]] = {
    "invalid": InvalidInputError,
    "conflict": ConflictError,
    "forbidden": ForbiddenError,
    "unavailable": UnavailableError,
    "internal": LedgerError,
}


@router.get("/items/{item_id}")
async def read_item(item_id: int) -> dict[str, int]:
    if item_id == 404:
        raise MissingItemError(item_id)
    return {"id": item_id}


@router.post("/items")
async def create_item(item: Item) -> Item:
    return item


@router.get("/errors/{kind}")
async def fail(kind: str) -> None:
    raise ERRORS[kind]


@router.get("/crash")
async def crash() -> None:
    raise ZeroDivisionError


events: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    del app
    events.append("start")
    yield
    events.append("stop")


@pytest.fixture
def health() -> Health:
    return Health(timeout=0.05)


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def app(health: Health, registry: CollectorRegistry) -> FastAPI:
    return create_app(
        title="test", version="1.0", health=health, registry=registry, lifespan=lifespan, routers=(router,)
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_liveness_needs_nothing(client: httpx.AsyncClient) -> None:
    response = await client.get("/livez")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_readiness_reports_each_check(client: httpx.AsyncClient, health: Health) -> None:
    async def ok() -> None:
        return None

    health.add("database", ok)
    response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready", "checks": {"database": True}})

    async def down() -> None:
        raise ConnectionError

    health.add("nats", down)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": True, "nats": False}}


async def test_requests_are_measured_by_route_template(client: httpx.AsyncClient) -> None:
    await client.get("/items/7")
    await client.get("/nowhere")
    metrics = (await client.get("/metrics")).text
    assert 'http_request_duration_seconds_count{method="GET",route="/items/{item_id}",status="200"} 1.0' in metrics
    assert 'route="unmatched",status="404"' in metrics


async def test_a_ledger_error_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/items/404")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "urn:ledger:error:missing_item",
        "title": "Not Found",
        "status": 404,
        "code": "missing_item",
        "detail": "item 404 does not exist",
    }


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("invalid", HTTPStatus.UNPROCESSABLE_CONTENT),
        ("conflict", HTTPStatus.CONFLICT),
        ("forbidden", HTTPStatus.FORBIDDEN),
        ("unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
        ("internal", HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
async def test_each_error_kind_has_its_status(client: httpx.AsyncClient, kind: str, status: HTTPStatus) -> None:
    response = await client.get(f"/errors/{kind}")
    assert (response.status_code, response.json()["status"]) == (status, status)


async def test_a_body_that_breaks_the_schema_lists_every_problem(client: httpx.AsyncClient) -> None:
    response = await client.post("/items", json={"quantity": "many"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert {issue["location"] for issue in body["errors"]} == {"body.name", "body.quantity"}


async def test_a_bug_is_a_500_without_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/crash")
    assert response.status_code == 500
    assert response.json() == {
        "type": "urn:ledger:error:internal",
        "title": "Internal Server Error",
        "status": 500,
        "code": "internal",
    }


async def test_the_lifespan_is_the_one_given(app: FastAPI) -> None:
    events.clear()
    async with app.router.lifespan_context(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


async def test_operational_routes_are_not_in_the_public_schema(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert set(paths) == {"/items/{item_id}", "/items", "/errors/{kind}", "/crash"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/platform -q --no-cov`
Expected: FAIL with `ModuleNotFoundError` for `ledger_platform.lifecycle`, `health`, `web`.

- [ ] **Step 3: Implement**

`libs/platform/src/ledger_platform/lifecycle.py`: copy ShieldBot's `src/shieldbot/core/lifecycle.py` verbatim (shown in full here):

```python
"""Ordered ownership of everything that must be released at shutdown."""

from contextlib import AsyncExitStack
from typing import Self, final
lazy from collections.abc import Awaitable, Callable
lazy from contextlib import AbstractAsyncContextManager
lazy from types import TracebackType


@final
class Lifecycle:
    """Resources acquired while starting, released in reverse order exactly once.

    Release happens even when startup fails halfway, and a failing release does not stop the others
    (``AsyncExitStack`` semantics).
    """

    __slots__ = ("_stack",)

    def __init__(self) -> None:
        self._stack = AsyncExitStack()

    async def enter[T](self, resource: AbstractAsyncContextManager[T]) -> T:
        """Enter ``resource`` now; exit it at shutdown, before anything entered earlier."""
        return await self._stack.enter_async_context(resource)

    def on_close(self, callback: Callable[[], Awaitable[object]]) -> None:
        """Await ``callback`` at shutdown, before anything registered earlier."""
        self._stack.push_async_callback(callback)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._stack.__aexit__(exc_type, exc, traceback)
```

`libs/platform/src/ledger_platform/health.py`:

```python
"""Readiness: named checks run concurrently, each within a time limit."""

import asyncio
from typing import ClassVar, final
lazy from collections.abc import Awaitable, Callable

import structlog

from ledger_kernel.errors import LedgerError

type Check = Callable[[], Awaitable[object]]
"""Returns when healthy, raises when not."""

_log = structlog.get_logger(__name__)


class DuplicateCheckError(LedgerError):
    """Two checks registered under one name."""

    code: ClassVar[str] = "duplicate_check"

    def __init__(self, name: str) -> None:
        super().__init__(f"a health check named {name!r} already exists")


@final
class Health:
    """The checks ``/readyz`` runs."""

    __slots__ = ("_checks", "_timeout")

    def __init__(self, *, timeout: float = 2.0) -> None:
        self._checks: dict[str, Check] = {}
        self._timeout = timeout

    def add(self, name: str, check: Check) -> None:
        """Register ``check`` under ``name``."""
        if name in self._checks:
            raise DuplicateCheckError(name)
        self._checks[name] = check

    async def run(self) -> dict[str, bool]:
        """Every check's result; a check that raises or exceeds the timeout is unhealthy."""
        async with asyncio.TaskGroup() as group:
            tasks = {name: group.create_task(self._one(name, check)) for name, check in self._checks.items()}
        return {name: task.result() for name, task in tasks.items()}

    async def _one(self, name: str, check: Check) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                await check()
        except Exception:
            _log.exception("health.check_failed", check=name)
            return False
        return True
```

`libs/platform/src/ledger_platform/web.py`:

```python
"""The HTTP application of a service: its routers, operational routes, metrics and RFC 9457 errors."""

from http import HTTPStatus
from time import perf_counter
from typing import Final, TypedDict

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest
from pydantic import TypeAdapter
from starlette.routing import Route

from ledger_kernel.errors import ErrorKind, LedgerError
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

lazy from fastapi import APIRouter

lazy from ledger_platform.health import Health

PROBLEM_JSON: Final = "application/problem+json"
_STATUS: Final[Mapping[ErrorKind, HTTPStatus]] = {
    ErrorKind.INVALID: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorKind.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorKind.CONFLICT: HTTPStatus.CONFLICT,
    ErrorKind.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorKind.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorKind.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}
_log = structlog.get_logger(__name__)


class _Issue(TypedDict):
    loc: tuple[str | int, ...]
    msg: str


_ISSUES: Final = TypeAdapter(list[_Issue])


def _problem(
    status: HTTPStatus, code: str, detail: str | None = None, errors: list[dict[str, str]] | None = None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:ledger:error:{code}",
        "title": status.phrase,
        "status": status.value,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status.value, media_type=PROBLEM_JSON)


def create_app(
    *,
    title: str,
    version: str,
    health: Health,
    registry: CollectorRegistry,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A FastAPI application with ``routers`` plus ``/livez``, ``/readyz`` and ``/metrics``."""
    app = FastAPI(title=title, version=version, lifespan=lifespan)
    duration = Histogram(
        "http_request_duration_seconds",
        "Time to answer an HTTP request.",
        ["method", "route", "status"],
        registry=registry,
    )

    @app.middleware("http")
    async def measure(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = perf_counter()
        response = await call_next(request)
        scope: Mapping[str, object] = request.scope
        route = scope.get("route")
        template = route.path if isinstance(route, Route) else "unmatched"
        duration.labels(request.method, template, str(response.status_code)).observe(perf_counter() - started)
        return response

    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, error: LedgerError) -> JSONResponse:
        del request
        return _problem(_STATUS[error.kind], error.code, str(error) or None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        del request
        issues = _ISSUES.validate_python(error.errors())
        errors = [{"location": ".".join(map(str, issue["loc"])), "message": issue["msg"]} for issue in issues]
        return _problem(HTTPStatus.UNPROCESSABLE_CONTENT, "invalid_request", "The request does not match the schema.", errors)

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        _log.error("http.unhandled_error", path=request.url.path, exc_info=error)
        return _problem(HTTPStatus.INTERNAL_SERVER_ERROR, "internal")

    @app.get("/livez", include_in_schema=False)
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        checks = await health.run()
        ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    for router in routers:
        app.include_router(router)
    return app
```

Notes for the implementer:
- `test_a_ledger_error_is_a_problem_document` and `test_each_error_kind_has_its_status`: the kind tests raise bare classes with no message, so `str(error)` is `""` and `detail` is left out (`or None`).
- `ruff` will flag the inline handler functions as unused (`F841` does not apply to decorated functions; if `RUF029` or `ARG001` fires, use the arguments as shown with `del`).
- If basedpyright reports `error.errors()` as partially unknown, keep `TypeAdapter` validation exactly as written: it turns `Sequence[Any]` into typed issues.
- Remove `fastapi` and `prometheus-client` from `DEP002` in `libs/platform/pyproject.toml`.

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` — PASS. Run: `uv run poe check` — passes.

```bash
git add libs/platform
git commit -m "feat(platform): lifecycle, health and the HTTP app with problem details

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Platform database, unit of work and row-level security

**Files:**
- Create: `libs/platform/src/ledger_platform/database.py`
- Modify: `libs/platform/tests/conftest.py`
- Test: `libs/platform/tests/test_database.py`

**Interfaces:**
- Consumes: `Tenant`, `LedgerError`.
- Produces: `PlatformTable(DeclarativeBase)` (its `metadata` holds the chassis's tables; services add it to their Alembic target metadata); `UnitOfWork` with `connection: AsyncConnection` and `after_commit(callback: Callable[[], object])`; `Database` with `classmethod open(url: str, *, pool_size: int = 10) -> AbstractAsyncContextManager[Database]`, `engine: AsyncEngine`, `transaction(tenant: Tenant | None) -> AbstractAsyncContextManager[UnitOfWork]`, `async ping() -> None`; `tenant_isolation(table: Table) -> tuple[str, ...]` (DDL); `MissingTenantColumnError(table)`; settings names `ledger.organization_id`, `ledger.company_id`.

Background: PostgreSQL superusers bypass row-level security, and so do table owners unless the table has `FORCE ROW LEVEL SECURITY`. The fixtures therefore run the app as a non-superuser role `ledger_app` that owns the test database, and `tenant_isolation` forces RLS.

- [ ] **Step 1: Add the PostgreSQL fixtures**

Append to `libs/platform/tests/conftest.py`:

```python
import psycopg
from testcontainers.community.postgres import PostgresContainer

from ledger_platform.database import Database, PlatformTable
lazy from collections.abc import AsyncGenerator

POSTGRES_IMAGE = "postgres:18.6"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A real PostgreSQL; the app connects as ``ledger_app``, which owns the database but is no superuser."""
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        with psycopg.connect(container.get_connection_url(), autocommit=True) as admin:
            admin.execute("CREATE ROLE ledger_app LOGIN PASSWORD 'app-password'")
            admin.execute("CREATE DATABASE ledger OWNER ledger_app")
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        yield f"postgresql+psycopg://ledger_app:app-password@{host}:{port}/ledger"


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """The chassis's tables, created for one test and dropped after it."""
    async with Database.open(database_url, pool_size=2) as database:
        async with database.engine.begin() as connection:
            await connection.run_sync(PlatformTable.metadata.create_all)
        yield database
        async with database.engine.begin() as connection:
            await connection.run_sync(PlatformTable.metadata.drop_all)
```

(Move the new imports to the top of the file with the others.)

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_database.py`:

```python
import pytest
from sqlalchemy import Column, MetaData, Table, Text, Uuid, insert, select, text
from sqlalchemy.exc import DBAPIError

from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.database import Database, MissingTenantColumnError, tenant_isolation

metadata = MetaData()
notes = Table(
    "notes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("organization_id", Uuid, nullable=False),
    Column("company_id", Uuid, nullable=False),
    Column("body", Text, nullable=False),
)
ORGANIZATION = OrganizationId(new_id())
ALPHA = Tenant(ORGANIZATION, CompanyId(new_id()))
BETA = Tenant(ORGANIZATION, CompanyId(new_id()))


@pytest.fixture
async def isolated(database: Database) -> Database:
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        for statement in tenant_isolation(notes):
            await connection.execute(text(statement))
    return database


async def write(database: Database, tenant: Tenant | None, body: str) -> None:
    async with database.transaction(tenant) as unit:
        owner = tenant or ALPHA
        await unit.connection.execute(
            insert(notes).values(id=new_id(), organization_id=owner.organization, company_id=owner.company, body=body)
        )


async def read(database: Database, tenant: Tenant | None) -> list[str]:
    async with database.transaction(tenant) as unit:
        return list((await unit.connection.execute(select(notes.c.body).order_by(notes.c.body))).scalars())


async def test_each_company_sees_only_its_rows(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    await write(isolated, BETA, "beta 1")
    assert await read(isolated, ALPHA) == ["alpha 1"]
    assert await read(isolated, BETA) == ["beta 1"]


async def test_without_a_tenant_nothing_is_visible_or_writable(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    assert await read(isolated, None) == []
    with pytest.raises(DBAPIError, match="row-level security"):
        await write(isolated, None, "orphan")


async def test_a_company_cannot_write_rows_of_another(isolated: Database) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with isolated.transaction(ALPHA) as unit:
            await unit.connection.execute(
                insert(notes).values(id=new_id(), organization_id=ORGANIZATION, company_id=BETA.company, body="x")
            )


async def test_the_tenant_does_not_leak_into_the_next_transaction(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    assert await read(isolated, ALPHA) == ["alpha 1"]
    assert await read(isolated, None) == []


def abort() -> None:
    raise LookupError


async def test_after_commit_callbacks_run_only_on_commit(isolated: Database) -> None:
    calls: list[str] = []
    async with isolated.transaction(ALPHA) as unit:
        unit.after_commit(lambda: calls.append("committed"))
    assert calls == ["committed"]

    with pytest.raises(LookupError):
        async with isolated.transaction(ALPHA) as unit:
            unit.after_commit(lambda: calls.append("rolled back"))
            await unit.connection.execute(
                insert(notes).values(id=new_id(), organization_id=ORGANIZATION, company_id=ALPHA.company, body="gone")
            )
            abort()
    assert calls == ["committed"]
    assert await read(isolated, ALPHA) == []


async def test_ping(database: Database) -> None:
    await database.ping()


def test_isolation_needs_a_company_column() -> None:
    other = Table("other", MetaData(), Column("id", Uuid, primary_key=True))
    with pytest.raises(MissingTenantColumnError, match="other"):
        tenant_isolation(other)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_database.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'Database' from 'ledger_platform.database'` (module missing).

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/database.py`:

```python
"""PostgreSQL: one transaction per unit of work, with the tenant enforced by row-level security.

Every transaction sets ``ledger.organization_id`` and ``ledger.company_id`` (transaction-local). Tables
protected by ``tenant_isolation`` show and accept only the rows of that company; without a tenant they
show nothing and accept nothing (fail closed).
"""

from contextlib import asynccontextmanager
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Callable

from sqlalchemy import MetaData, Table, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.ids import Tenant

_SET_TENANT: Final = text(
    "SELECT set_config('ledger.organization_id', :organization, true), "
    "set_config('ledger.company_id', :company, true)"
)
_PING: Final = text("SELECT 1")


class PlatformTable(DeclarativeBase):
    """Tables the chassis adds to every service's database (outbox, inbox)."""

    metadata: ClassVar[MetaData] = MetaData()


class MissingTenantColumnError(LedgerError):
    """``tenant_isolation`` asked for a table without ``company_id``."""

    code: ClassVar[str] = "missing_tenant_column"

    def __init__(self, table: str) -> None:
        super().__init__(f"table {table!r} has no company_id column to isolate by")


def tenant_isolation(table: Table) -> tuple[str, ...]:
    """DDL (for a migration) that shows and accepts only the current company's rows of ``table``."""
    if "company_id" not in table.c:
        raise MissingTenantColumnError(table.name)
    current = "NULLIF(current_setting('ledger.company_id', true), '')::uuid"
    return (
        f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table.name}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY tenant_isolation ON "{table.name}" USING (company_id = {current}) '
        f"WITH CHECK (company_id = {current})",
    )


@final
class UnitOfWork:
    """One open transaction: state changes, outbox and inbox commit together or not at all."""

    __slots__ = ("_after_commit", "_connection")

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self._after_commit: list[Callable[[], object]] = []

    @property
    def connection(self) -> AsyncConnection:
        """For repositories: every statement runs inside the transaction."""
        return self._connection

    def after_commit(self, callback: Callable[[], object]) -> None:
        """Call ``callback`` once the transaction has committed; never if it rolls back."""
        self._after_commit.append(callback)

    def committed(self) -> None:
        """Called by ``Database`` after the commit."""
        for callback in self._after_commit:
            callback()


@final
class Database:
    """A service's PostgreSQL database."""

    __slots__ = ("_engine",)

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    @asynccontextmanager
    async def open(cls, url: str, *, pool_size: int = 10) -> AsyncGenerator[Database]:
        """A connection pool to ``url``, closed on exit."""
        engine = create_async_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True)
        try:
            yield cls(engine)
        finally:
            await engine.dispose()

    @property
    def engine(self) -> AsyncEngine:
        """For migrations and tests; services use ``transaction``."""
        return self._engine

    @asynccontextmanager
    async def transaction(self, tenant: Tenant | None) -> AsyncGenerator[UnitOfWork]:
        """A transaction scoped to ``tenant``: commits on normal exit, rolls back on an exception."""
        async with self._engine.begin() as connection:
            await connection.execute(
                _SET_TENANT,
                {
                    "organization": "" if tenant is None else str(tenant.organization),
                    "company": "" if tenant is None else str(tenant.company),
                },
            )
            unit = UnitOfWork(connection)
            yield unit
        unit.committed()

    async def ping(self) -> None:
        """Raises unless PostgreSQL answers."""
        async with self._engine.connect() as connection:
            await connection.execute(_PING)
```

Remove `sqlalchemy` from `DEP002` in `libs/platform/pyproject.toml`.

- [ ] **Step 5: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` — PASS (Docker running). Run: `uv run poe check` — passes.

```bash
git add libs/platform
git commit -m "feat(platform): PostgreSQL unit of work with fail-closed row-level security

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Platform tracing

**Files:**
- Create: `libs/platform/src/ledger_platform/tracing.py`
- Test: `libs/platform/tests/test_tracing.py`

**Interfaces:**
- Produces: `Tracing(provider: TracerProvider)`; `Tracing.create(*, service_name: str, exporter: SpanExporter | None) -> Tracing`; `tracing.tracer: Tracer`; `tracing.current_traceparent() -> str | None`; `tracing.consuming(name: str, traceparent: str | None) -> AbstractContextManager[Span]`; `tracing.shutdown() -> None`.

Background: the process-wide tracer provider can be set only once; `Tracing` holds its own provider so tests never touch global state. `Service` (Task 12) also sets it globally so later auto-instrumentation (milestone 2: FastAPI, SQLAlchemy, gRPC) uses it.

- [ ] **Step 1: Write the failing tests**

`libs/platform/tests/test_tracing.py`:

```python
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from ledger_platform.tracing import Tracing


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracing(exporter: InMemorySpanExporter) -> Tracing:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider)


def test_a_consumer_continues_the_producer_s_trace(tracing: Tracing, exporter: InMemorySpanExporter) -> None:
    with tracing.tracer.start_as_current_span("produce"):
        traceparent = tracing.current_traceparent()
    assert traceparent is not None
    with tracing.consuming("consume invoicing", traceparent):
        pass
    consume, produce = sorted(exporter.get_finished_spans(), key=lambda span: span.name)
    assert consume.context is not None
    assert produce.context is not None
    assert consume.context.trace_id == produce.context.trace_id
    assert consume.parent is not None
    assert consume.parent.span_id == produce.context.span_id
    assert consume.kind is SpanKind.CONSUMER


def test_without_an_active_span_there_is_no_traceparent(tracing: Tracing) -> None:
    assert tracing.current_traceparent() is None


@pytest.mark.parametrize("traceparent", [None, "garbage"])
def test_without_a_usable_parent_a_new_trace_starts(
    tracing: Tracing, exporter: InMemorySpanExporter, traceparent: str | None
) -> None:
    with tracing.consuming("consume", traceparent):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.parent is None


def test_create_names_the_service_and_exports_only_with_an_exporter(exporter: InMemorySpanExporter) -> None:
    exporting = Tracing.create(service_name="invoicing", exporter=exporter)
    with exporting.tracer.start_as_current_span("work"):
        pass
    exporting.shutdown()
    (span,) = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == "invoicing"

    silent = Tracing.create(service_name="invoicing", exporter=None)
    with silent.tracer.start_as_current_span("work"):
        pass
    silent.shutdown()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_tracing.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.tracing'`.

- [ ] **Step 3: Implement**

`libs/platform/src/ledger_platform/tracing.py`:

```python
"""Distributed tracing: one trace from a client's request through events and workers (W3C traceparent)."""

from contextlib import contextmanager
from typing import Final, final
lazy from collections.abc import Iterator

from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
lazy from opentelemetry.sdk.trace.export import SpanExporter
lazy from opentelemetry.trace import Span, Tracer

_PROPAGATOR: Final = TraceContextTextMapPropagator()
_TRACEPARENT: Final = "traceparent"


@final
class Tracing:
    """A tracer provider of the service's own, and the propagation of traces through events."""

    __slots__ = ("_provider", "_tracer")

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer("ledger_platform")

    @classmethod
    def create(cls, *, service_name: str, exporter: SpanExporter | None) -> Tracing:
        """Spans named for ``service_name``, exported in batches when an exporter is given."""
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        return cls(provider)

    @property
    def provider(self) -> TracerProvider:
        """For setting the process-wide provider once, at service start."""
        return self._provider

    @property
    def tracer(self) -> Tracer:
        """The platform's tracer."""
        return self._tracer

    def current_traceparent(self) -> str | None:
        """The W3C ``traceparent`` of the active span, to carry in an event; ``None`` outside any span."""
        carrier: dict[str, str] = {}
        _PROPAGATOR.inject(carrier)
        return carrier.get(_TRACEPARENT)

    @contextmanager
    def consuming(self, name: str, traceparent: str | None) -> Iterator[Span]:
        """A consumer span continuing ``traceparent``'s trace, or a new trace without a usable one."""
        parent = _PROPAGATOR.extract({_TRACEPARENT: traceparent} if traceparent else {})
        with self._tracer.start_as_current_span(name, context=parent, kind=SpanKind.CONSUMER) as span:
            yield span

    def shutdown(self) -> None:
        """Export what is pending and stop."""
        self._provider.shutdown()
```

Remove `opentelemetry-sdk` and `opentelemetry-api` from `DEP002`.

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check` — both pass.

```bash
git add libs/platform
git commit -m "feat(platform): tracing with W3C propagation through events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Platform streams, transactional outbox and relay

**Files:**
- Create: `libs/platform/src/ledger_platform/streams.py`, `outbox.py`
- Modify: `libs/platform/tests/conftest.py`
- Test: `libs/platform/tests/test_outbox.py`

**Interfaces:**
- Consumes: `Database`, `UnitOfWork`, `PlatformTable`, `Tracing`, `Envelope`.
- Produces: `StreamSpec(name: str, subjects: tuple[str, ...], duplicate_window: timedelta = 2 min, max_age: timedelta = 7 days)`; `DEAD_LETTERS: StreamSpec`; `async ensure_stream(jetstream: JetStreamContext, spec: StreamSpec) -> None`.
- Produces: `OutboxRecord` (table `outbox`); `Outbox(*, tracing: Tracing, registry: CollectorRegistry, batch: int = 100)` with `async add(unit: UnitOfWork, envelope: Envelope, *, subject: str) -> None`, `async relay_once(database: Database, jetstream: JetStreamContext) -> int`, `async run(database, jetstream, *, idle: float = 1.0, retry: float = 1.0) -> Never`; counter `ledger_outbox_published_total`.

Background: the relay publishes and waits for JetStream's ack inside the transaction that locks the rows (`FOR UPDATE SKIP LOCKED`), then deletes them. If publishing fails the transaction rolls back and the rows stay; a row published twice (crash between ack and commit) is dropped by JetStream's de-duplication (`Nats-Msg-Id`) within two minutes and by the consumer's inbox after that. A commit wakes the relay of the same process at once; rows written by other replicas are picked up within `idle` seconds by any relay.

- [ ] **Step 1: Add the NATS fixtures**

Append to `libs/platform/tests/conftest.py` (imports at the top):

```python
import nats
from nats.js import JetStreamContext
from testcontainers.community.nats import NatsContainer

NATS_IMAGE = "nats:2.12.15"


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with NatsContainer(NATS_IMAGE, jetstream=True) as container:
        yield f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(4222)}"


@pytest.fixture
async def jetstream(nats_url: str) -> AsyncGenerator[JetStreamContext]:
    """A JetStream client; every stream a test created is deleted after it."""
    connection = await nats.connect(nats_url)
    context = connection.jetstream()
    try:
        yield context
    finally:
        for name in await context.streams_info():
            await context.delete_stream(name.config.name)
        await connection.close()
```

If `streams_info()` returns `StreamInfo` objects with `config.name` typed `str | None`, guard with `if name.config.name is not None`.

- [ ] **Step 2: Write the failing tests**

`libs/platform/tests/test_outbox.py`:

```python
import asyncio
from datetime import timedelta

import pytest
from nats.js import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from sqlalchemy import func, select

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox, OutboxRecord
from ledger_platform.streams import DEAD_LETTERS, StreamSpec, ensure_stream
from ledger_platform.tracing import Tracing

STREAM = StreamSpec(name="TEST", subjects=("test.>",))


def event() -> Envelope:
    return Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=None)


@pytest.fixture
def tracing() -> Tracing:
    return Tracing(TracerProvider())


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def outbox(tracing: Tracing, registry: CollectorRegistry) -> Outbox:
    return Outbox(tracing=tracing, registry=registry, batch=10)


async def pending(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(OutboxRecord))).scalar_one()


async def stored(jetstream: JetStreamContext, stream: str = "TEST") -> int:
    return (await jetstream.stream_info(stream)).state.messages


async def test_streams_are_created_then_updated(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, STREAM)
    await ensure_stream(jetstream, StreamSpec(name="TEST", subjects=("test.>", "more.>"), max_age=timedelta(days=1)))
    info = await jetstream.stream_info("TEST")
    assert info.config.subjects == ["test.>", "more.>"]
    await ensure_stream(jetstream, DEAD_LETTERS)


async def test_a_committed_event_is_published_once_then_removed(
    database: Database, jetstream: JetStreamContext, outbox: Outbox, registry: CollectorRegistry
) -> None:
    await ensure_stream(jetstream, STREAM)
    envelope = event()
    async with database.transaction(None) as unit:
        await outbox.add(unit, envelope, subject="test.created")
    assert await pending(database) == 1

    assert await outbox.relay_once(database, jetstream) == 1
    assert await pending(database) == 0
    assert await outbox.relay_once(database, jetstream) == 0

    message = await jetstream.get_msg("TEST", seq=1)
    assert message.data == envelope.data
    assert message.headers is not None
    assert Envelope.from_headers(message.headers, message.data) == envelope
    assert registry.get_sample_value("ledger_outbox_published_total") == 1


def abort() -> None:
    raise LookupError


async def test_an_event_in_a_rolled_back_transaction_never_exists(database: Database, outbox: Outbox) -> None:
    with pytest.raises(LookupError):
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
            abort()
    assert await pending(database) == 0


async def test_the_active_trace_travels_with_the_event(database: Database, outbox: Outbox, tracing: Tracing) -> None:
    with tracing.tracer.start_as_current_span("request"):
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
    async with database.transaction(None) as unit:
        headers = (await unit.connection.execute(select(OutboxRecord.headers))).scalar_one()
    assert "traceparent" in headers


async def test_events_wait_while_publishing_fails_and_go_out_once_it_works(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
    relay = asyncio.create_task(outbox.run(database, jetstream, idle=0.05, retry=0.05))
    await asyncio.sleep(0.3)
    assert await pending(database) == 1  # no stream yet: every publish fails

    await ensure_stream(jetstream, STREAM)
    async with asyncio.timeout(5):
        while await pending(database):
            await asyncio.sleep(0.05)
    relay.cancel()
    assert await stored(jetstream) == 1


async def test_a_commit_wakes_the_relay(database: Database, jetstream: JetStreamContext, outbox: Outbox) -> None:
    await ensure_stream(jetstream, STREAM)
    relay = asyncio.create_task(outbox.run(database, jetstream, idle=60))
    await asyncio.sleep(0.1)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
    async with asyncio.timeout(2):
        while await stored(jetstream) == 0:
            await asyncio.sleep(0.02)
    relay.cancel()


async def test_more_events_than_a_batch_go_out_without_waiting(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    await ensure_stream(jetstream, STREAM)
    async with database.transaction(None) as unit:
        for _ in range(25):
            await outbox.add(unit, event(), subject="test.created")
    relay = asyncio.create_task(outbox.run(database, jetstream, idle=60))
    async with asyncio.timeout(3):
        while await stored(jetstream) < 25:
            await asyncio.sleep(0.02)
    relay.cancel()
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_outbox.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.outbox'`.

- [ ] **Step 4: Implement**

`libs/platform/src/ledger_platform/streams.py`:

```python
"""JetStream streams: declared by the services that publish to them, created or updated at start."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final

from nats.js.api import StorageType, StreamConfig
from nats.js.errors import NotFoundError
lazy from nats.js import JetStreamContext


@final
@dataclass(frozen=True, slots=True)
class StreamSpec:
    """A stream and the subjects it stores."""

    name: str
    subjects: tuple[str, ...]
    duplicate_window: timedelta = timedelta(minutes=2)
    max_age: timedelta = timedelta(days=7)


DEAD_LETTERS: Final = StreamSpec(
    name="DEAD_LETTERS",
    subjects=(
        "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.>",
        "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.>",
    ),
    max_age=timedelta(days=30),
)
"""JetStream's notices of events a consumer gave up on: each names the stream and sequence to inspect."""


async def ensure_stream(jetstream: JetStreamContext, spec: StreamSpec) -> None:
    """Create the stream, or bring an existing one to ``spec``."""
    config = StreamConfig(
        name=spec.name,
        subjects=list(spec.subjects),
        duplicate_window=spec.duplicate_window.total_seconds(),
        max_age=spec.max_age.total_seconds(),
        storage=StorageType.FILE,
    )
    try:
        await jetstream.stream_info(spec.name)
    except NotFoundError:
        await jetstream.add_stream(config)
        return
    await jetstream.update_stream(config)
```

`libs/platform/src/ledger_platform/outbox.py`:

```python
"""Transactional outbox: an event commits with the state change it announces, then a relay publishes it."""

import asyncio
import contextlib
from dataclasses import replace
from datetime import datetime
from typing import Never, final
from uuid import UUID

import structlog
from prometheus_client import CollectorRegistry, Counter
from sqlalchemy import DateTime, LargeBinary, delete, insert, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledger_platform.database import PlatformTable
lazy from nats.js import JetStreamContext

lazy from ledger_contracts.envelope import Envelope
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.tracing import Tracing

_log = structlog.get_logger(__name__)


class OutboxRecord(PlatformTable):
    """An event committed but not yet acknowledged by JetStream."""

    __tablename__ = "outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    subject: Mapped[str]
    headers: Mapped[dict[str, str]] = mapped_column(JSONB)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


@final
class Outbox:
    """Writes events inside units of work and relays them to JetStream."""

    __slots__ = ("_batch", "_published", "_tracing", "_wake")

    def __init__(self, *, tracing: Tracing, registry: CollectorRegistry, batch: int = 100) -> None:
        self._tracing = tracing
        self._batch = batch
        self._wake = asyncio.Event()
        self._published = Counter(
            "ledger_outbox_published", "Events the relay published to JetStream.", registry=registry
        )

    async def add(self, unit: UnitOfWork, envelope: Envelope, *, subject: str) -> None:
        """Store ``envelope`` for ``subject`` in ``unit``'s transaction; the relay wakes when it commits."""
        traced = envelope if envelope.traceparent else replace(envelope, traceparent=self._tracing.current_traceparent())
        await unit.connection.execute(
            insert(OutboxRecord).values(
                id=traced.id,
                subject=subject,
                headers=traced.headers(),
                data=traced.data,
                created_at=traced.time,
            )
        )
        unit.after_commit(self._wake.set)

    async def relay_once(self, database: Database, jetstream: JetStreamContext) -> int:
        """Publish up to one batch of pending events, oldest first; how many went out."""
        async with database.transaction(None) as unit:
            rows = (
                await unit.connection.execute(
                    select(OutboxRecord.id, OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data)
                    .order_by(OutboxRecord.created_at)
                    .limit(self._batch)
                    .with_for_update(skip_locked=True)
                )
            ).tuples().all()
            for _, subject, headers, data in rows:
                await jetstream.publish(subject, data, headers=headers)
            if rows:
                await unit.connection.execute(delete(OutboxRecord).where(OutboxRecord.id.in_([row[0] for row in rows])))
        self._published.inc(len(rows))
        return len(rows)

    async def run(self, database: Database, jetstream: JetStreamContext, *, idle: float = 1.0, retry: float = 1.0) -> Never:
        """Relay forever: at once after a local commit, every ``idle`` seconds otherwise."""
        while True:
            try:
                sent = await self.relay_once(database, jetstream)
            except Exception:
                _log.exception("outbox.relay_failed")
                await asyncio.sleep(retry)
                continue
            if sent < self._batch:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(idle):
                        await self._wake.wait()
                self._wake.clear()
```

Notes:
- prometheus-client appends `_total` to counter names: the sample is `ledger_outbox_published_total`.
- Remove `nats-py` from `DEP002`.

- [ ] **Step 5: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check` — both pass.

```bash
git add libs/platform
git commit -m "feat(platform): JetStream streams, transactional outbox and relay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Platform inbox, consumers and dead letters

**Files:**
- Create: `libs/platform/src/ledger_platform/consumer.py`
- Test: `libs/platform/tests/test_consumer.py`

**Interfaces:**
- Consumes: `Database`, `UnitOfWork`, `PlatformTable`, `Tracing`, `Envelope`, `MalformedEventError`, `LedgerError`, `ErrorKind`, `ensure_stream`, `StreamSpec`, `DEAD_LETTERS`, `Outbox` (tests only).
- Produces: `InboxRecord` (table `inbox`, primary key `(consumer, event_id)`); `type Handler = Callable[[Envelope, UnitOfWork], Awaitable[None]]`; `ConsumerSpec(*, name: str, stream: str, subject: str, handler: Handler, max_deliver: int = 10, backoff: tuple[float, ...] = (1.0, 10.0, 60.0, 300.0, 600.0))`; `Consumer(spec, *, database: Database, tracing: Tracing, registry: CollectorRegistry, clock: Clock = utc_now)` with `async run(jetstream: JetStreamContext, *, batch: int = 20, poll: float = 5.0) -> Never` and `async handle(message: Msg) -> None`; counter `ledger_events_total{consumer, outcome}` with outcomes `handled`, `duplicate`, `failed`, `rejected`, `malformed`.

Behaviour: the handler runs in a transaction scoped to the event's tenant that also claims `(consumer, event id)` in the inbox; a claim that already exists means a duplicate (acked, handler not called). The message is acked after the commit. A handler exception naks with the delay `backoff[min(delivery - 1, len - 1)]`; after `max_deliver` deliveries JetStream stops and publishes a `MAX_DELIVERIES` advisory, which `DEAD_LETTERS` stores. An unreadable envelope, or a handler `LedgerError` of kind `INVALID` (the event can never succeed), terminates the message at once (`MSG_TERMINATED` advisory, also stored in `DEAD_LETTERS`).

- [ ] **Step 1: Write the failing tests**

`libs/platform/tests/test_consumer.py`:

```python
import asyncio
from typing import ClassVar

import pytest
from nats.js import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.consumer import Consumer, ConsumerSpec
from ledger_platform.database import Database, UnitOfWork
from ledger_platform.outbox import Outbox
from ledger_platform.streams import DEAD_LETTERS, StreamSpec, ensure_stream
from ledger_platform.tracing import Tracing
lazy from collections.abc import Awaitable, Callable

STREAM = StreamSpec(name="EVENTS", subjects=("events.>",))
TENANT = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


class NeverValidError(InvalidInputError):
    code: ClassVar[str] = "never_valid"


class Recorder:
    def __init__(self, failures: int = 0, error: Exception | None = None) -> None:
        self.received: list[Envelope] = []
        self.calls = 0
        self.failures = failures
        self.error = error or RuntimeError()
        self.tenants: list[str] = []

    async def __call__(self, envelope: Envelope, unit: UnitOfWork) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        self.received.append(envelope)
        setting = await unit.connection.exec_driver_sql("SELECT current_setting('ledger.company_id')")
        self.tenants.append(setting.scalar_one())


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracing(exporter: InMemorySpanExporter) -> Tracing:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider)


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
async def streams(jetstream: JetStreamContext) -> JetStreamContext:
    await ensure_stream(jetstream, STREAM)
    await ensure_stream(jetstream, DEAD_LETTERS)
    return jetstream


def consumer(
    recorder: Recorder,
    database: Database,
    tracing: Tracing,
    registry: CollectorRegistry,
    *,
    max_deliver: int = 10,
) -> Consumer:
    spec = ConsumerSpec(
        name="test-consumer",
        stream="EVENTS",
        subject="events.>",
        handler=recorder,
        max_deliver=max_deliver,
        backoff=(0.05,),
    )
    return Consumer(spec, database=database, tracing=tracing, registry=registry)


def money() -> Envelope:
    return Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=TENANT)


async def publish(jetstream: JetStreamContext, envelope: Envelope) -> None:
    await jetstream.publish("events.created", envelope.data, headers=envelope.headers())


async def until(condition: Callable[[], Awaitable[bool]], seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not await condition():
            await asyncio.sleep(0.05)


async def dead_letters(jetstream: JetStreamContext) -> int:
    return (await jetstream.stream_info("DEAD_LETTERS")).state.messages


def outcome(registry: CollectorRegistry, name: str) -> float | None:
    return registry.get_sample_value("ledger_events_total", {"consumer": "test-consumer", "outcome": name})


async def test_an_event_is_handled_in_its_tenant_s_transaction(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder()
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))
    envelope = money()
    await publish(streams, envelope)

    async def handled() -> bool:
        return recorder.received == [envelope]

    await until(handled)
    worker.cancel()
    assert recorder.tenants == [str(TENANT.company)]
    assert outcome(registry, "handled") == 1


async def test_a_redelivered_event_is_handled_once(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder()
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))
    envelope = money()
    await publish(streams, envelope)
    await streams.publish("events.created", envelope.data, headers=envelope.headers() | {"Nats-Msg-Id": "replay"})

    async def both_seen() -> bool:
        return (outcome(registry, "handled") or 0) + (outcome(registry, "duplicate") or 0) == 2

    await until(both_seen)
    worker.cancel()
    assert recorder.received == [envelope]
    assert outcome(registry, "duplicate") == 1


async def test_a_failing_handler_is_retried_until_it_succeeds(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder(failures=2)
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))
    await publish(streams, money())

    async def handled() -> bool:
        return len(recorder.received) == 1

    await until(handled)
    worker.cancel()
    assert (recorder.calls, outcome(registry, "failed")) == (3, 2)


async def test_an_event_that_keeps_failing_ends_in_dead_letters(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder(failures=1000)
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry, max_deliver=2).run(streams, poll=0.2))
    await publish(streams, money())

    async def dead() -> bool:
        return await dead_letters(streams) == 1

    await until(dead)
    worker.cancel()
    assert recorder.calls == 2


async def test_a_permanently_invalid_event_goes_to_dead_letters_at_once(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder(failures=1000, error=NeverValidError())
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))
    await publish(streams, money())

    async def dead() -> bool:
        return await dead_letters(streams) == 1

    await until(dead)
    worker.cancel()
    assert (recorder.calls, outcome(registry, "rejected")) == (1, 1)


async def test_an_unreadable_message_goes_to_dead_letters_without_reaching_the_handler(
    database: Database, streams: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    recorder = Recorder()
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))
    await streams.publish("events.created", b"not an event")

    async def dead() -> bool:
        return await dead_letters(streams) == 1

    await until(dead)
    worker.cancel()
    assert recorder.calls == 0
    assert outcome(registry, "malformed") == 1


async def test_the_producer_s_trace_continues_in_the_consumer(
    database: Database,
    streams: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    exporter: InMemorySpanExporter,
) -> None:
    outbox = Outbox(tracing=tracing, registry=registry)
    with tracing.tracer.start_as_current_span("request"):
        async with database.transaction(TENANT) as unit:
            await outbox.add(unit, money(), subject="events.created")
    await outbox.relay_once(database, streams)
    recorder = Recorder()
    worker = asyncio.create_task(consumer(recorder, database, tracing, registry).run(streams, poll=0.2))

    async def handled() -> bool:
        return len(recorder.received) == 1

    await until(handled)
    worker.cancel()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    request, consumed = spans["request"], spans["consume test-consumer"]
    assert request.context is not None
    assert consumed.context is not None
    assert consumed.context.trace_id == request.context.trace_id
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_consumer.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.consumer'`.

- [ ] **Step 3: Implement**

`libs/platform/src/ledger_platform/consumer.py`:

```python
"""Event consumers: each event handled once, in its tenant's transaction, retried with backoff, then dead-lettered."""

from dataclasses import dataclass
from datetime import datetime
from typing import Never, final
from uuid import UUID

import nats.errors
import structlog
from nats.js.api import AckPolicy, ConsumerConfig
from prometheus_client import CollectorRegistry, Counter
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from ledger_contracts.envelope import Envelope, MalformedEventError
from ledger_kernel.clock import Clock, utc_now
from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.database import PlatformTable
lazy from collections.abc import Awaitable, Callable

lazy from nats.aio.msg import Msg
lazy from nats.js import JetStreamContext

lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.tracing import Tracing

type Handler = Callable[[Envelope, UnitOfWork], Awaitable[None]]

_ACK_WAIT = 30.0
_log = structlog.get_logger(__name__)


class InboxRecord(PlatformTable):
    """An event a consumer has handled: a second delivery finds it and does nothing."""

    __tablename__ = "inbox"

    consumer: Mapped[str] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsumerSpec:
    """A durable JetStream consumer and what it does with each event."""

    name: str
    stream: str
    subject: str
    handler: Handler
    max_deliver: int = 10
    backoff: tuple[float, ...] = (1.0, 10.0, 60.0, 300.0, 600.0)


@final
class Consumer:
    """Pulls events for one ``ConsumerSpec`` and handles them exactly once in effect."""

    __slots__ = ("_clock", "_database", "_events", "_spec", "_tracing")

    def __init__(
        self,
        spec: ConsumerSpec,
        *,
        database: Database,
        tracing: Tracing,
        registry: CollectorRegistry,
        clock: Clock = utc_now,
    ) -> None:
        self._spec = spec
        self._database = database
        self._tracing = tracing
        self._clock = clock
        self._events = Counter(
            "ledger_events", "Events consumed, by outcome.", ["consumer", "outcome"], registry=registry
        )

    async def run(self, jetstream: JetStreamContext, *, batch: int = 20, poll: float = 5.0) -> Never:
        """Consume forever."""
        subscription = await jetstream.pull_subscribe(
            self._spec.subject,
            durable=self._spec.name,
            stream=self._spec.stream,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT, max_deliver=self._spec.max_deliver, ack_wait=_ACK_WAIT
            ),
        )
        while True:
            try:
                messages = await subscription.fetch(batch, timeout=poll)
            except nats.errors.TimeoutError:
                continue
            for message in messages:
                await self.handle(message)

    async def handle(self, message: Msg) -> None:
        """Handle one delivery and settle it: ack, nak with a delay, or terminate."""
        try:
            envelope = Envelope.from_headers(message.headers or {}, message.data)
        except MalformedEventError:
            _log.exception("consumer.malformed_event", consumer=self._spec.name, subject=message.subject)
            self._count("malformed")
            await message.term()
            return
        with self._tracing.consuming(f"consume {self._spec.name}", envelope.traceparent):
            try:
                outcome = await self._process(envelope)
            except LedgerError as error:
                if error.kind is not ErrorKind.INVALID:
                    await self._retry(message, envelope)
                    return
                _log.exception("consumer.event_rejected", consumer=self._spec.name, event=str(envelope.id))
                self._count("rejected")
                await message.term()
                return
            except Exception:
                await self._retry(message, envelope)
                return
        self._count(outcome)
        await message.ack()

    async def _process(self, envelope: Envelope) -> str:
        async with self._database.transaction(envelope.tenant) as unit:
            claimed = await unit.connection.execute(
                insert(InboxRecord)
                .values(consumer=self._spec.name, event_id=envelope.id, processed_at=self._clock())
                .on_conflict_do_nothing()
                .returning(InboxRecord.event_id)
            )
            if claimed.first() is None:
                return "duplicate"
            await self._spec.handler(envelope, unit)
        return "handled"

    async def _retry(self, message: Msg, envelope: Envelope) -> None:
        delivery = message.metadata.num_delivered or 1
        _log.exception(
            "consumer.handler_failed",
            consumer=self._spec.name,
            event=str(envelope.id),
            type=envelope.type,
            delivery=delivery,
        )
        self._count("failed")
        backoff = self._spec.backoff
        await message.nak(delay=backoff[min(delivery, len(backoff)) - 1])

    def _count(self, outcome: str) -> None:
        self._events.labels(self._spec.name, outcome).inc()
```

Notes:
- `_retry` is called inside the `except` blocks, so `_log.exception` there logs the active exception.
- The inbox insert is outside row-level security on purpose (platform table); the handler's own tables are protected.
- If `message.metadata.num_delivered` is typed `int` (not optional), drop `or 1`.
- If the `MAX_DELIVERIES` advisory is not emitted for a nak'd last delivery on NATS 2.12, check the server's behaviour in its docs before changing the test: the test is the specification (an event that keeps failing must be visible in `DEAD_LETTERS`).
- Inbox rows are never deleted in this milestone; a retention job comes with the first service that produces volume (tracked in the README's "Not done yet").

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check` — both pass.

```bash
git add libs/platform
git commit -m "feat(platform): inbox, consumers with backoff and dead letters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Platform gRPC server

**Files:**
- Create: `libs/platform/src/ledger_platform/grpc_server.py`
- Test: `libs/platform/tests/test_grpc_server.py`

**Interfaces:**
- Consumes: `LedgerError`, `ErrorKind`.
- Produces: `ERROR_CODE_KEY = "ledger-error-code"`; `ErrorInterceptor(grpc.aio.ServerInterceptor)`; `serve_grpc(*, port: int, register: Callable[[grpc.aio.Server], None], grace: float = 10.0) -> AbstractAsyncContextManager[int]` (yields the bound port).

Error mapping: a `LedgerError` raised by a unary-unary method aborts the call with the status for its kind (`INVALID_ARGUMENT`, `NOT_FOUND`, `FAILED_PRECONDITION`, `PERMISSION_DENIED`, `UNAVAILABLE`, `INTERNAL`), the error's message as details and its `code` in the trailing metadata `ledger-error-code`. Other exceptions keep gRPC's default (`UNKNOWN`). Transport security is the mesh's job (Linkerd mTLS), so the server binds an insecure port.

- [ ] **Step 1: Write the failing tests**

`libs/platform/tests/test_grpc_server.py`:

```python
from typing import ClassVar

import grpc
import pytest

from ledger_kernel.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
)
from ledger_platform.grpc_server import ERROR_CODE_KEY, serve_grpc
lazy from collections.abc import AsyncIterator

ERRORS: dict[bytes, type[LedgerError]] = {
    b"invalid": InvalidInputError,
    b"missing": NotFoundError,
    b"conflict": ConflictError,
    b"forbidden": ForbiddenError,
    b"unavailable": UnavailableError,
    b"internal": LedgerError,
}


class MissingThingError(NotFoundError):
    code: ClassVar[str] = "missing_thing"

    def __init__(self) -> None:
        super().__init__("no such thing")


async def echo(request: bytes, context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    del context
    if request == b"thing":
        raise MissingThingError
    if request in ERRORS:
        raise ERRORS[request]
    if request == b"bug":
        raise ZeroDivisionError
    return request


def register(server: grpc.aio.Server) -> None:
    handler = grpc.unary_unary_rpc_method_handler(echo, request_deserializer=bytes, response_serializer=bytes)
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Echo", {"Say": handler}),))
    stream = grpc.unary_stream_rpc_method_handler(repeat, request_deserializer=bytes, response_serializer=bytes)
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Stream", {"Repeat": stream}),))


async def repeat(request: bytes, context: grpc.aio.ServicerContext[bytes, bytes]) -> AsyncIterator[bytes]:
    del context
    yield request
    yield request


async def call(port: int, payload: bytes) -> bytes:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        say = channel.unary_unary("/test.Echo/Say", request_serializer=bytes, response_deserializer=bytes)
        return await say(payload, timeout=5)


async def test_a_call_succeeds() -> None:
    async with serve_grpc(port=0, register=register) as port:
        assert await call(port, b"hello") == b"hello"


async def test_a_ledger_error_becomes_a_status_with_its_code() -> None:
    async with serve_grpc(port=0, register=register) as port:
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call(port, b"thing")
    error = caught.value
    assert (error.code(), error.details()) == (grpc.StatusCode.NOT_FOUND, "no such thing")
    assert (ERROR_CODE_KEY, "missing_thing") in tuple(error.trailing_metadata() or ())


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (b"invalid", grpc.StatusCode.INVALID_ARGUMENT),
        (b"missing", grpc.StatusCode.NOT_FOUND),
        (b"conflict", grpc.StatusCode.FAILED_PRECONDITION),
        (b"forbidden", grpc.StatusCode.PERMISSION_DENIED),
        (b"unavailable", grpc.StatusCode.UNAVAILABLE),
        (b"internal", grpc.StatusCode.INTERNAL),
    ],
)
async def test_each_kind_has_its_status(payload: bytes, status: grpc.StatusCode) -> None:
    async with serve_grpc(port=0, register=register) as port:
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call(port, payload)
    assert caught.value.code() == status


async def test_a_bug_stays_unknown() -> None:
    async with serve_grpc(port=0, register=register) as port:
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call(port, b"bug")
    assert caught.value.code() == grpc.StatusCode.UNKNOWN


async def test_streaming_methods_pass_through() -> None:
    async with serve_grpc(port=0, register=register) as port, grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        repeat_call = channel.unary_stream("/test.Stream/Repeat", request_serializer=bytes, response_deserializer=bytes)
        assert [item async for item in repeat_call(b"x", timeout=5)] == [b"x", b"x"]


async def test_unknown_methods_are_unimplemented() -> None:
    async with serve_grpc(port=0, register=register) as port, grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        missing = channel.unary_unary("/test.Echo/Nothing", request_serializer=bytes, response_deserializer=bytes)
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await missing(b"", timeout=5)
    assert caught.value.code() == grpc.StatusCode.UNIMPLEMENTED
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_grpc_server.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.grpc_server'`.

- [ ] **Step 3: Implement**

`libs/platform/src/ledger_platform/grpc_server.py`:

```python
"""The gRPC server of a service: domain errors become gRPC statuses with a stable code."""

from contextlib import asynccontextmanager
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import grpc

from ledger_kernel.errors import ErrorKind, LedgerError

ERROR_CODE_KEY: Final = "ledger-error-code"
_STATUS: Final[Mapping[ErrorKind, grpc.StatusCode]] = {
    ErrorKind.INVALID: grpc.StatusCode.INVALID_ARGUMENT,
    ErrorKind.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    ErrorKind.CONFLICT: grpc.StatusCode.FAILED_PRECONDITION,
    ErrorKind.FORBIDDEN: grpc.StatusCode.PERMISSION_DENIED,
    ErrorKind.UNAVAILABLE: grpc.StatusCode.UNAVAILABLE,
    ErrorKind.INTERNAL: grpc.StatusCode.INTERNAL,
}


@final
class ErrorInterceptor(grpc.aio.ServerInterceptor):
    """Turns a ``LedgerError`` from a unary-unary method into its status and ``ledger-error-code``."""

    @override
    async def intercept_service[Request, Response](
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler[Request, Response] | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Request, Response] | None:
        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        behavior = handler.unary_unary

        async def guarded(request: Request, context: grpc.aio.ServicerContext[Request, Response]) -> Response:
            try:
                return await behavior(request, context)
            except LedgerError as error:
                await context.abort(_STATUS[error.kind], str(error), trailing_metadata=((ERROR_CODE_KEY, error.code),))

        return grpc.unary_unary_rpc_method_handler(
            guarded,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


@asynccontextmanager
async def serve_grpc(
    *, port: int, register: Callable[[grpc.aio.Server], None], grace: float = 10.0
) -> AsyncGenerator[int]:
    """Serve the services ``register`` adds on ``port`` (0: any free port); yields the bound port."""
    server = grpc.aio.server(interceptors=(ErrorInterceptor(),))
    register(server)
    bound = server.add_insecure_port(f"[::]:{port}")
    await server.start()
    try:
        yield bound
    finally:
        await server.stop(grace)
```

Notes on the stubs (`types-grpcio`), to resolve without suppressions:
- `RpcMethodHandler.unary_unary` may be typed as returning `Response | Awaitable[Response]` (sync and async servers share the type). If so, write `result = behavior(request, context)` and `return await result if inspect.isawaitable(result) else result`, and add a test with a synchronous behavior function so both branches are covered.
- `ServicerContext` may not be generic in the installed stubs; then drop the type arguments everywhere.
- Remove `grpcio` from `DEP002`.

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check` — both pass.

```bash
git add libs/platform
git commit -m "feat(platform): gRPC server with domain error mapping

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `Service`: the chassis assembled

**Files:**
- Create: `libs/platform/src/ledger_platform/service.py`
- Modify: root `pyproject.toml` (import-linter layers for the platform)
- Test: `libs/platform/tests/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 5-11.
- Produces: `Runtime` (frozen dataclass: `settings`, `database`, `nats: Client`, `jetstream`, `outbox`, `tracing`, `registry`); `Service(settings: ServiceSettings, *, version: str, routers: Sequence[APIRouter] = (), streams: Sequence[StreamSpec] = (), consumers: Callable[[Runtime], Sequence[ConsumerSpec]] = no_consumers, grpc: Callable[[Runtime, grpc.aio.Server], None] | None = None)` with `app: FastAPI`, `runtime() -> Runtime` (a FastAPI dependency) and `grpc_port: int | None`; `ServiceNotRunningError`.

Start order (inside the app's lifespan): logs (at construction) → database pool → NATS connection → `DEAD_LETTERS` and the service's streams → tracing (global provider set once per process) → outbox relay task → one task per consumer → gRPC server. Shutdown is the reverse: gRPC stops, workers are cancelled, tracing flushes, NATS drains, the pool closes. Readiness checks: `database` (ping), `nats` (connected), `workers` (no background task has died).

- [ ] **Step 1: Write the failing tests**

`libs/platform/tests/test_service.py`:

```python
import asyncio
from typing import Annotated

import grpc
import httpx
import pytest
from fastapi import APIRouter, Depends
from pydantic import SecretStr

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_platform.consumer import ConsumerSpec
from ledger_platform.database import Database, PlatformTable, UnitOfWork
from ledger_platform.service import Runtime, Service, ServiceNotRunningError
from ledger_platform.settings import ServiceSettings
from ledger_platform.streams import StreamSpec
lazy from collections.abc import AsyncGenerator, Sequence

STREAM = StreamSpec(name="SERVICE_TEST", subjects=("service-test.>",))
received: list[Envelope] = []


def build(database_url: str, nats_url: str) -> Service:
    router = APIRouter()
    service: Service

    def current_runtime() -> Runtime:
        return service.runtime()

    @router.post("/payments", status_code=202)
    async def pay(runtime: Annotated[Runtime, Depends(current_runtime)]) -> dict[str, str]:
        envelope = Envelope.wrap(Money(amount="5", currency="DOP"), source="payments", tenant=None)
        async with runtime.database.transaction(None) as unit:
            await runtime.outbox.add(unit, envelope, subject="service-test.paid")
        return {"id": str(envelope.id)}

    async def on_paid(envelope: Envelope, unit: UnitOfWork) -> None:
        del unit
        received.append(envelope)

    def consumers(runtime: Runtime) -> Sequence[ConsumerSpec]:
        del runtime
        return (ConsumerSpec(name="payments-test", stream=STREAM.name, subject="service-test.>", handler=on_paid),)

    def register(runtime: Runtime, server: grpc.aio.Server) -> None:
        del runtime
        handler = grpc.unary_unary_rpc_method_handler(ping, request_deserializer=bytes, response_serializer=bytes)
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Ping", {"Ping": handler}),))

    settings = ServiceSettings(
        service_name="payments",
        environment="test",
        database_url=SecretStr(database_url),
        nats_url=nats_url,
        grpc_port=0,
    )
    service = Service(settings, version="1.0", routers=(router,), streams=(STREAM,), consumers=consumers, grpc=register)
    return service


async def ping(request: bytes, context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
    del context
    return request


@pytest.fixture
async def tables(database_url: str) -> AsyncGenerator[None]:
    async with Database.open(database_url, pool_size=1) as database:
        async with database.engine.begin() as connection:
            await connection.run_sync(PlatformTable.metadata.create_all)
        yield
        async with database.engine.begin() as connection:
            await connection.run_sync(PlatformTable.metadata.drop_all)


@pytest.mark.usefixtures("tables")
async def test_a_request_s_event_reaches_a_consumer_through_the_whole_chassis(database_url: str, nats_url: str) -> None:
    received.clear()
    service = build(database_url, nats_url)
    transport = httpx.ASGITransport(app=service.app)
    async with service.app.router.lifespan_context(service.app), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/readyz")
        assert ready.json() == {"status": "ready", "checks": {"database": True, "nats": True, "workers": True}}

        response = await client.post("/payments")
        assert response.status_code == 202
        async with asyncio.timeout(5):
            while not received:
                await asyncio.sleep(0.05)
        assert str(received[0].id) == response.json()["id"]

        assert service.grpc_port is not None
        async with grpc.aio.insecure_channel(f"127.0.0.1:{service.grpc_port}") as channel:
            call = channel.unary_unary("/test.Ping/Ping", request_serializer=bytes, response_deserializer=bytes)
            assert await call(b"ok", timeout=5) == b"ok"

    with pytest.raises(ServiceNotRunningError):
        service.runtime()


async def test_before_start_the_service_is_not_ready(database_url: str, nats_url: str) -> None:
    service = build(database_url, nats_url)
    transport = httpx.ASGITransport(app=service.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": False, "nats": False, "workers": False}


@pytest.mark.usefixtures("tables")
async def test_a_dead_worker_makes_the_service_not_ready(database_url: str, nats_url: str) -> None:
    service = build(database_url, nats_url)
    transport = httpx.ASGITransport(app=service.app)
    async with (
        service.app.router.lifespan_context(service.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        worker = service.workers[0]
        worker.cancel()
        await asyncio.sleep(0)
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["workers"] is False


async def test_without_otlp_and_grpc_the_service_still_runs(database_url: str, nats_url: str) -> None:
    settings = ServiceSettings(service_name="bare", database_url=SecretStr(database_url), nats_url=nats_url)
    service = Service(settings, version="1.0")
    async with service.app.router.lifespan_context(service.app):
        assert service.grpc_port is None
        assert service.runtime().settings is settings
```

Also add one test with `otlp_endpoint="http://127.0.0.1:4317"` (nothing listens; the batch exporter only logs failed exports at shutdown) asserting the service starts and stops cleanly, so the exporter branch is covered. If the exporter's failure log turns into a warning that `filterwarnings = ["error"]` rejects, point it at an `InMemorySpanExporter` instead by making the exporter factory a `Service` keyword (`exporter: Callable[[ServiceSettings], SpanExporter | None] = otlp_exporter`) and test `otlp_exporter` directly for both branches.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest libs/platform/tests/test_service.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_platform.service'`.

- [ ] **Step 3: Implement**

`libs/platform/src/ledger_platform/service.py`:

```python
"""A ledger service: database, events, tracing, health, HTTP and gRPC, started and stopped as one."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar, final

import nats
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry

from ledger_kernel.errors import UnavailableError
from ledger_platform.consumer import Consumer
from ledger_platform.database import Database
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.health import Health
from ledger_platform.lifecycle import Lifecycle
from ledger_platform.logs import configure_logging
from ledger_platform.outbox import Outbox
from ledger_platform.streams import DEAD_LETTERS, ensure_stream
from ledger_platform.tracing import Tracing
from ledger_platform.web import create_app
lazy from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence

lazy import grpc
lazy from fastapi import APIRouter, FastAPI
lazy from nats.aio.client import Client
lazy from nats.js import JetStreamContext

lazy from ledger_platform.consumer import ConsumerSpec
lazy from ledger_platform.settings import ServiceSettings
lazy from ledger_platform.streams import StreamSpec


class ServiceNotRunningError(UnavailableError):
    """The service is starting or stopping."""

    code: ClassVar[str] = "service_not_running"

    def __init__(self) -> None:
        super().__init__("the service is not running")


class NatsDisconnectedError(UnavailableError):
    """NATS is not connected."""

    code: ClassVar[str] = "nats_disconnected"

    def __init__(self) -> None:
        super().__init__("not connected to NATS")


class WorkerStoppedError(UnavailableError):
    """A background worker is not running."""

    code: ClassVar[str] = "worker_stopped"

    def __init__(self) -> None:
        super().__init__("a background worker stopped")


@final
@dataclass(frozen=True, slots=True)
class Runtime:
    """What a service's code uses while the service runs."""

    settings: ServiceSettings
    database: Database
    nats: Client
    jetstream: JetStreamContext
    outbox: Outbox
    tracing: Tracing
    registry: CollectorRegistry


def no_consumers(runtime: Runtime) -> Sequence[ConsumerSpec]:
    """For services that consume no events."""
    del runtime
    return ()


@final
class Service:
    """Builds the FastAPI app whose lifespan starts and stops everything else."""

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        version: str,
        routers: Sequence[APIRouter] = (),
        streams: Sequence[StreamSpec] = (),
        consumers: Callable[[Runtime], Sequence[ConsumerSpec]] = no_consumers,
        grpc: Callable[[Runtime, grpc.aio.Server], None] | None = None,
    ) -> None:
        configure_logging(settings)
        self._settings = settings
        self._streams = streams
        self._consumers = consumers
        self._grpc = grpc
        self._runtime: Runtime | None = None
        self._workers: list[asyncio.Task[object]] = []
        self.grpc_port: int | None = None
        self._registry = CollectorRegistry()
        health = Health()
        health.add("database", self._database_ready)
        health.add("nats", self._nats_ready)
        health.add("workers", self._workers_ready)
        self.app = create_app(
            title=settings.service_name,
            version=version,
            health=health,
            registry=self._registry,
            lifespan=self._lifespan,
            routers=routers,
        )

    def runtime(self) -> Runtime:
        """The running service's runtime; a FastAPI dependency for routes."""
        if self._runtime is None:
            raise ServiceNotRunningError
        return self._runtime

    @property
    def workers(self) -> Sequence[asyncio.Task[object]]:
        """The background tasks (outbox relay, consumers) of the running service."""
        return tuple(self._workers)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncGenerator[None]:
        del app
        settings = self._settings
        try:
            async with Lifecycle() as lifecycle:
                database = await lifecycle.enter(Database.open(settings.database_url.get_secret_value()))
                connection = await nats.connect(settings.nats_url, name=settings.service_name)
                lifecycle.on_close(connection.drain)
                jetstream = connection.jetstream()
                for stream in (DEAD_LETTERS, *self._streams):
                    await ensure_stream(jetstream, stream)
                tracing = self._tracing()
                lifecycle.on_close(_flush(tracing))
                outbox = Outbox(tracing=tracing, registry=self._registry)
                runtime = Runtime(settings, database, connection, jetstream, outbox, tracing, self._registry)
                self._start(lifecycle, outbox.run(database, jetstream))
                for spec in self._consumers(runtime):
                    consumer = Consumer(spec, database=database, tracing=tracing, registry=self._registry)
                    self._start(lifecycle, consumer.run(jetstream))
                if self._grpc is not None:
                    register = self._grpc
                    self.grpc_port = await lifecycle.enter(
                        serve_grpc(port=settings.grpc_port, register=lambda server: register(runtime, server))
                    )
                self._runtime = runtime
                yield
        finally:
            self._runtime = None
            self._workers.clear()
            self.grpc_port = None

    def _tracing(self) -> Tracing:
        endpoint = self._settings.otlp_endpoint
        tracing = Tracing.create(
            service_name=self._settings.service_name,
            exporter=None if endpoint is None else OTLPSpanExporter(endpoint=endpoint),
        )
        if not isinstance(trace.get_tracer_provider(), TracerProvider):
            trace.set_tracer_provider(tracing.provider)
        return tracing

    def _start(self, lifecycle: Lifecycle, work: Coroutine[object, object, object]) -> None:
        task: asyncio.Task[object] = asyncio.create_task(work)
        self._workers.append(task)

        async def stop() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        lifecycle.on_close(stop)

    async def _database_ready(self) -> None:
        await self.runtime().database.ping()

    async def _nats_ready(self) -> None:
        if not self.runtime().nats.is_connected:
            raise NatsDisconnectedError

    async def _workers_ready(self) -> None:
        self.runtime()
        if any(task.done() for task in self._workers):
            raise WorkerStoppedError


def _flush(tracing: Tracing) -> Callable[[], Coroutine[object, object, None]]:
    async def flush() -> None:
        await asyncio.to_thread(tracing.shutdown)

    return flush
```

Then:
- Remove `opentelemetry-exporter-otlp-proto-grpc` from `DEP002`; only `psycopg` remains there, with the comment `# the PostgreSQL driver SQLAlchemy loads from the URL; never imported`.
- Add the platform's internal layers to the root `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Platform layers: service -> adapters -> messaging -> infrastructure -> foundations"
type = "layers"
containers = ["ledger_platform"]
layers = [
    "service",
    "web | grpc_server | consumer",
    "outbox | streams",
    "database | tracing | health",
    "settings | logs | lifecycle",
]
```

`consumer` imports `database` and `tracing` (lower layers) and nothing from `outbox`; `logs` imports `settings` inside the same layer — if import-linter refuses a same-layer import with `|` (independent siblings), move `settings` to its own bottom layer: `"logs | lifecycle"`, then `"settings"`.

- [ ] **Step 4: Verify, gate, commit**

Run: `uv run pytest libs/platform -q` then `uv run poe check` — both pass, coverage 100 %.

```bash
git add pyproject.toml libs/platform
git commit -m "feat(platform): Service assembles the chassis with readiness and ordered shutdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Local infrastructure, CI, README and spec alignment

**Files:**
- Create: `deploy/local/compose.yaml`, `Tiltfile`, `README.md`
- Modify: `.github/workflows/ci.yml`, `docs/superpowers/specs/2026-10-11-ledger-platform-design.md`

- [ ] **Step 1: Local infrastructure**

`deploy/local/compose.yaml`:

```yaml
# Infrastructure for local development. Services join it as they are built (milestone 2 onwards).
name: ledger

services:
  postgres:
    image: postgres:18.6
    environment:
      POSTGRES_USER: ledger
      POSTGRES_PASSWORD: ledger
      POSTGRES_DB: ledger
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["postgres:/var/lib/postgresql"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ledger"]
      interval: 5s
      timeout: 3s
      retries: 10

  nats:
    image: nats:2.12.15
    command: ["--jetstream", "--store_dir=/data", "--http_port=8222"]
    ports: ["127.0.0.1:4222:4222", "127.0.0.1:8222:8222"]
    volumes: ["nats:/data"]

volumes:
  postgres: {}
  nats: {}
```

`Tiltfile`:

```python
# Local development. `tilt up` runs the infrastructure; each service adds its own build and resource.
docker_compose("deploy/local/compose.yaml")
```

Add `"Tiltfile"` to `extend-exclude` in `[tool.ruff]` (Starlark, not Python).

Run: `docker compose -f deploy/local/compose.yaml config --quiet && docker compose -f deploy/local/compose.yaml up -d --wait && docker compose -f deploy/local/compose.yaml down`
Expected: exit 0; both containers become healthy/running.

- [ ] **Step 2: CI**

Add to `.github/workflows/ci.yml`, after the gate step:

```yaml
      - name: Local infrastructure configuration
        run: docker compose -f deploy/local/compose.yaml config --quiet
```

- [ ] **Step 3: README**

`README.md`:

````markdown
# ledger

A business platform built to be sold, starting with Dominican Republic electronic invoicing (e-CF) as an
API. Design: [docs/superpowers/specs/2026-10-11-ledger-platform-design.md](docs/superpowers/specs/2026-10-11-ledger-platform-design.md).

Python 3.15 · uv workspace · strictly typed end to end · zero suppressions · 100 % branch coverage.

## Layout

| Path | What |
|---|---|
| `libs/kernel` | `ledger_kernel`: ids (UUIDv7), errors, money, clock, RNC/cédula. Standard library only |
| `libs/contracts` | `ledger_contracts` + generated `ledger.*`: protobuf messages, gRPC services, the CloudEvents envelope |
| `libs/platform` | `ledger_platform`: the service chassis (settings, logs, health, HTTP, PostgreSQL with RLS, outbox, consumers, tracing, gRPC, `Service`) |
| `services/` | one directory per service (from milestone 2) |
| `deploy/local` | PostgreSQL and NATS for local development (`tilt up` or `docker compose -f deploy/local/compose.yaml up`) |

## Develop

```bash
uv sync
uv run poe check     # format, lint, types x2, architecture, deps, spelling, buf lint, tests (Docker needed)
uv run poe proto     # after editing libs/contracts/proto
```

## The chassis in one paragraph

A service builds `Service(settings, version=..., routers=..., streams=..., consumers=..., grpc=...)` and
serves `service.app` with Granian. Routes get `Runtime` through `Depends(service.runtime)`; they change
state and add events in one `database.transaction(tenant)`, so an event exists only if its change
committed. The outbox relay publishes to JetStream; consumers handle each event once (inbox), in the
event's tenant, retry with backoff, and dead-letter what cannot succeed. Every tenant table uses
`tenant_isolation(table)` in its migration: no tenant, no rows.

## Not done yet

- Auto-instrumentation of FastAPI, SQLAlchemy and gRPC (milestone 2, with the first service).
- Idempotency keys for the public API and the per-principal authorization context (milestone 2).
- A retention job for inbox rows.
- gRPC client helpers mapping `ledger-error-code` back to domain errors (with the first gRPC client).
````

- [ ] **Step 4: Align the spec with what was built**

In `docs/superpowers/specs/2026-10-11-ledger-platform-design.md` make exactly these edits:

1. §4.1, Outbox paragraph — replace `PostgreSQL ``LISTEN/NOTIFY`` wakes the relay; a 1 s poll is the fallback.` with `A commit wakes the relay of the same process at once; rows from other replicas are picked up by any relay within a 1 s poll.` and add: `The relay deletes rows once JetStream acknowledges them.`
2. §4.1, Envelope bullet — replace `` `ce-subject` = company id`` with `` `ce-subject` = company id, `ce-organizationid` = organization id (absent for platform-wide events)``.
3. §4.1, after the `max_deliver` bullet, add: `- Events that can never succeed (unreadable envelope, or a handler error of kind INVALID) are terminated at once; JetStream's MAX_DELIVERIES and MSG_TERMINATED advisories are kept in the DEAD_LETTERS stream for 30 days.`
4. §4.2, errors bullet — replace the sentence about `google.rpc.Status` with `Errors use the status code of the error's kind and its stable code in the trailing metadata `ledger-error-code`, mapped back to domain exceptions by `platform`.`
5. §3, `kernel` row — replace `` `Rnc`/`Cedula` value objects with check digits`` with `` `TaxId` (RNC or cédula; check digit reported, not enforced, because DGII has issued numbers that fail it)``.
6. §11, add: `Kubernetes probes use each service's HTTP `/livez` and `/readyz` (gRPC health is not needed: every service serves HTTP).`

- [ ] **Step 5: Gate and commit**

Run: `uv run poe check` — passes.

```bash
git add deploy Tiltfile README.md .github pyproject.toml docs
git commit -m "build: local infrastructure, CI, README; align spec with the chassis

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes (for the plan author; already applied)

- Spec coverage for milestone 1 (§13 workspace and gate, §3 libraries, §4 events/outbox/inbox/gRPC, §7 RLS/ids/money/time, §11 logs/metrics/traces/health, §15 local development): Tasks 1-13. Deferred with a named milestone in the README: auto-instrumentation, idempotency keys, authorization context, gRPC client mapping, inbox retention.
- Names used across tasks: `Database.transaction`, `UnitOfWork.connection/after_commit/committed`, `Outbox.add/relay_once/run`, `ConsumerSpec`, `Consumer.run/handle`, `StreamSpec`, `DEAD_LETTERS`, `ensure_stream`, `Tracing.create/current_traceparent/consuming/shutdown/provider/tracer`, `serve_grpc`, `ERROR_CODE_KEY`, `Service.app/runtime/workers/grpc_port`, `Runtime` fields — consistent.
