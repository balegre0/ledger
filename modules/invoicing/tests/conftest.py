"""invoicing's tests: the worker's database migrated as production migrates it, invoicing served by ``api``.

invoicing is the module under test of ``ledger_testing``'s module fixtures; the template holds the platform's
schema, identity's (the module its manifest depends on: the app that serves invoicing assembles it, and the
company a document belongs to is identity's) and invoicing's, each brought to head by its revisions. Tests
reach invoicing's schema the way the apps do: as ``ledger_app``, through ``module_database``, a
``BoundDatabase`` in ``invoicing_rw``. What is invoicing's own is here: who its use cases act as, its
principals and a company.

The unit tests share a regime shaped like the Dominican one, built from the kernel only. Its ITBIS categories
are the Dominican pack's (18 %, 16 %, exempt at 0 %, rounded at each category and at the document to two
places, half up); ``do.isc.standard`` is a second tax at 10 %, charged on the same base as ITBIS (taxes on one
line do not compound, ``TaxLine``); ``do.itbis.old`` stopped on 2020-01-01, so no rate of it is in effect on a
later tax point.
"""

from datetime import date
from decimal import Decimal
from typing import Final
lazy from collections.abc import Iterator

import pytest

from ledger_invoicing._manifest import MANIFEST
from ledger_invoicing.adapters.postgres.schema import MIGRATIONS
from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import (
    FieldDef,
    FieldKind,
    Locale,
    RateValue,
    RegimeDef,
    RegimeKey,
    RoundingPoint,
    RoundingRule,
    Source,
    TaxCategoryDef,
    TaxCategoryKey,
    TaxCategoryKind,
)
from ledger_platform.data.audit import Actor, acting_as
from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import Template

UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS)
TEST_ACTOR: Final = Actor("system:test", "invoicing-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform``, ``identity`` and ``invoicing``, each at head, relay granted."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """invoicing, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


SOURCE: Final = Source(
    title="Test law", url="https://example.test/law", version="1", retrieved_on=date(2026, 1, 1)
)
LABELS: Final = {Locale("en"): "Test"}
SINCE: Final = date(2019, 1, 1)
BY_CATEGORY: Final = frozenset({RoundingPoint.CATEGORY, RoundingPoint.DOCUMENT})
"""Where the Dominican pack rounds: each category's tax, and the document's."""


def category(key: str, kind: TaxCategoryKind, percent: str, *, until: date | None = None) -> TaxCategoryDef:
    """A category at ``percent`` from 2019-01-01, until ``until`` if given."""
    rate = RateValue(percent=Decimal(percent), source=SOURCE)
    return TaxCategoryDef(
        key=TaxCategoryKey(key), labels=LABELS, kind=kind, rates=Timeline(Since(SINCE, rate, until))
    )


CATEGORIES: Final = (
    category("do.itbis.standard", TaxCategoryKind.STANDARD, "18"),
    category("do.itbis.reduced", TaxCategoryKind.REDUCED, "16"),
    category("do.itbis.exempt", TaxCategoryKind.EXEMPT, "0"),
    category("do.isc.standard", TaxCategoryKind.STANDARD, "10"),
    category("do.itbis.old", TaxCategoryKind.STANDARD, "12", until=date(2020, 1, 1)),
)
FIELDS: Final = (
    FieldDef(name="ecf_type", labels=LABELS, kind=FieldKind.CODE, required=True),
    FieldDef(name="note", labels=LABELS, kind=FieldKind.TEXT),
    FieldDef(name="replaced_on", labels=LABELS, kind=FieldKind.DATE),
    FieldDef(name="rate", labels=LABELS, kind=FieldKind.DECIMAL),
    FieldDef(name="replaces", labels=LABELS, kind=FieldKind.OBJECT),
)
"""Fields of every kind, one of them required: what a regime may declare under ``fiscal.<regime>``."""


def build_regime(
    points: frozenset[RoundingPoint] = BY_CATEGORY, fields: tuple[FieldDef, ...] = ()
) -> RegimeDef:
    """The test regime, rounding at ``points`` and declaring ``fields``; it lets books be kept in USD too."""
    return RegimeDef(
        key=RegimeKey("do"),
        jurisdiction=parse_jurisdiction("DO"),
        currency=Currency.of("DOP"),
        time_zone="America/Santo_Domingo",
        labels=LABELS,
        tax_id_schemes=(),
        tax_categories=CATEGORIES,
        document_types=(),
        rounding=RoundingRule(mode=RoundingMode.HALF_UP, points=points, places=2, line_tolerance=Decimal(1)),
        fields=fields,
        parameters={},
        sources=(SOURCE,),
        functional_currencies=(Currency.of("USD"),),
    )


@pytest.fixture(scope="session")
def regime() -> RegimeDef:
    """The test regime as the Dominican pack rounds: at each category and at the document."""
    return build_regime()


@pytest.fixture(scope="session")
def regime_with_fields() -> RegimeDef:
    """The test regime declaring ``FIELDS``."""
    return build_regime(fields=FIELDS)


@pytest.fixture(scope="session")
def rounding_variants() -> tuple[RegimeDef, ...]:
    """The test regime rounding where a regime may: at each category and the document, each line, nowhere."""
    return (build_regime(), build_regime(frozenset({RoundingPoint.LINE})), build_regime(frozenset()))
