"""Channels as fiscal sees them: their definition, the registry over the assembled ones, what they answer."""

import inspect
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Final, final, override
from uuid import UUID
lazy from collections.abc import Sequence
lazy from datetime import timedelta

import pytest

from ledger_fiscal_kit.channels import (
    Channel,
    ChannelDef,
    ChannelRegistry,
    EnrollmentId,
    EnrollmentState,
    Issued,
    Objection,
    Representation,
    Requirements,
    ResumeReason,
    StatusUpdate,
)
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_fiscal_kit.status import (
    MAX_MESSAGE_CODE,
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    AuthorityMessage,
    FiscalStatus,
    Severity,
)
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.regimes import Direction, DocumentKind, FieldDef, FieldKind, Locale, RegimeKey, Source
from ledger_kernel.tenancy import Mode
from ledger_platform.config.manifest import ChannelEntry
from ledger_platform.runtime.contexts import AssembledChannels, ChannelsNotAssembledError, ChannelUnknownError
from ledger_platform.runtime.keys import BindingTypeError
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.runtime.calls import SystemCall

ORGANIZATION: Final = OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001"))
COMPANY: Final = CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002"))
DOCUMENT: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-000000000003"))
ENTRY: Final = ChannelEntry.model_validate(
    {
        "key": "do-ecf-v1",
        "entry_point": "ledger_country_do.channels.ecf_v1:CHANNEL",
        "schema": "do_ecf",
        "valid_from": date(2026, 1, 1),
        "valid_until": date(2030, 12, 31),
        "environments": ["testecf", "certecf", "ecf"],
        "live_environments": ["ecf"],
        "document_kinds": ["invoice", "credit_note", "debit_note"],
        "directions": ["sales", "purchase"],
        "tier": "gold",
    }
)
FIELD: Final = FieldDef(name="income_type", labels={Locale("es"): "Tipo de ingresos"}, kind=FieldKind.CODE)
SOURCE: Final = Source(
    title="Formato e-CF v1.0", url="https://dgii.gov.do/", version="1.0", retrieved_on=date(2026, 10, 1)
)
LABELS: Final = {Locale("es"): "Comprobante fiscal electrónico", Locale("en"): "Electronic fiscal receipt"}
DEFINITION: Final = ChannelDef.of(
    ENTRY, regime=RegimeKey("do"), fields=(FIELD,), labels=LABELS, sources=(SOURCE,)
)
OCCURRED_AT: Final = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)
UPDATE: Final = StatusUpdate(
    document=DOCUMENT,
    channel="do-ecf-v1",
    environment="testecf",
    state="accepted",
    status=FiscalStatus.ACCEPTED,
    severity=Severity.INFO,
    version=4,
    authority_number="E310000000001",
    messages=(AuthorityMessage(code="0", message="Aceptado", lang="es"),),
    details={"do": {"encf": "E310000000001", "security_code": "A1b2C3"}},
    occurred_at=OCCURRED_AT,
)


@final
class Stub(Channel):
    """A channel, as far as the registry can tell: what it answers is never asked."""

    @property
    @override
    def definition(self) -> ChannelDef:
        return DEFINITION

    @override
    async def requirements(self, call: SystemCall, state: EnrollmentState) -> Requirements:
        return Requirements(currently_due=())

    @override
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]:
        return ()

    @override
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        return Issued(status=UPDATE, signed=sign_within is not None)

    @override
    async def status(self, call: SystemCall, document: DocumentRef) -> StatusUpdate | None:
        return None

    @override
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None:
        return None

    @override
    async def representation(self, call: SystemCall, document: DocumentRef) -> Representation:
        return Representation(media_type="application/pdf", content=b"", filename="a.pdf")


def assembled(**channels: object) -> AssembledChannels:
    found = AssembledChannels()
    for key, channel in channels.items():
        found.add(key.replace("_", "-"), channel)
    found.seal()
    return found


def test_a_definition_copies_its_manifest_entry() -> None:
    assert DEFINITION.key == "do-ecf-v1"
    assert DEFINITION.regime == "do"
    assert (DEFINITION.valid_from, DEFINITION.valid_until) == (date(2026, 1, 1), date(2030, 12, 31))
    assert DEFINITION.environments == ("testecf", "certecf", "ecf")
    assert DEFINITION.live_environments == ("ecf",)
    assert DEFINITION.document_kinds == (
        DocumentKind.INVOICE,
        DocumentKind.CREDIT_NOTE,
        DocumentKind.DEBIT_NOTE,
    )
    assert DEFINITION.directions == (Direction.SALES, Direction.PURCHASE)
    assert DEFINITION.fields == (FIELD,)
    assert DEFINITION.labels == LABELS
    assert DEFINITION.sources == (SOURCE,)


def test_a_definition_s_labels_cannot_be_changed_by_whoever_built_it() -> None:
    labels = {Locale("es"): "e-CF"}
    definition = replace(DEFINITION, labels=labels)
    labels[Locale("es")] = "otro"
    assert definition.labels == {Locale("es"): "e-CF"}


def test_an_environment_implies_its_mode() -> None:
    assert DEFINITION.mode_of("ecf") is Mode.LIVE
    assert DEFINITION.mode_of("testecf") is Mode.TEST
    assert DEFINITION.mode_of("certecf") is Mode.TEST
    with pytest.raises(InvalidValueError, match=r"channel_def.environment must be one of the channel's"):
        DEFINITION.mode_of("sandbox")


def test_a_mode_uses_the_environments_of_its_own_mode_only() -> None:
    assert DEFINITION.allows("ecf", Mode.LIVE)
    assert not DEFINITION.allows("ecf", Mode.TEST)
    assert DEFINITION.allows("testecf", Mode.TEST)
    assert DEFINITION.allows("certecf", Mode.TEST)
    assert not DEFINITION.allows("testecf", Mode.LIVE)
    assert not DEFINITION.allows("sandbox", Mode.TEST)
    assert not DEFINITION.allows("sandbox", Mode.LIVE)


def test_a_definition_covers_its_kinds_and_directions() -> None:
    sales_invoices = replace(
        DEFINITION, document_kinds=(DocumentKind.INVOICE,), directions=(Direction.SALES,)
    )
    assert sales_invoices.covers(DocumentKind.INVOICE, Direction.SALES)
    assert not sales_invoices.covers(DocumentKind.CREDIT_NOTE, Direction.SALES)
    assert not sales_invoices.covers(DocumentKind.INVOICE, Direction.PURCHASE)


def test_a_live_environment_is_one_of_the_environments() -> None:
    with pytest.raises(
        InvalidValueError, match=r"channel_def.live_environments must be some of the channel's environments"
    ):
        replace(DEFINITION, live_environments=("production",))


def test_a_definition_ends_after_it_starts() -> None:
    with pytest.raises(
        InvalidValueError, match=r"channel_def.valid_until must be no earlier than valid_from"
    ):
        replace(DEFINITION, valid_until=date(2025, 12, 31))
    assert replace(DEFINITION, valid_until=None).valid_until is None


def test_the_registry_answers_the_assembled_channels() -> None:
    channel = Stub()
    registry = ChannelRegistry(assembled(do_ecf_v1=channel))
    assert registry.get("do-ecf-v1") is channel
    assert registry.get("do-ecf-v1").definition is DEFINITION
    assert registry.all() == (channel,)


def test_a_channel_s_members_take_a_system_call_and_issue_names_its_budget() -> None:
    channel = Stub()
    members = (
        channel.requirements,
        channel.check,
        channel.issue,
        channel.status,
        channel.resume,
        channel.representation,
    )
    assert {member.__name__ for member in members} | {"definition"} == Channel.__abstractmethods__
    assert all(next(iter(inspect.signature(member).parameters)) == "call" for member in members)
    budget = inspect.signature(channel.issue).parameters["sign_within"]
    assert budget.kind is inspect.Parameter.KEYWORD_ONLY


def test_an_enrollment_state_names_whose_enrollment_it_is() -> None:
    state = EnrollmentState(
        enrollment=EnrollmentId(UUID("0199e3a0-0000-7000-8000-000000000009")),
        organization=ORGANIZATION,
        company=COMPANY,
        mode=Mode.TEST,
        environment="testecf",
        active=False,
    )
    assert (state.enrollment, state.organization, state.company, state.mode, state.environment) == (
        UUID("0199e3a0-0000-7000-8000-000000000009"),
        ORGANIZATION,
        COMPANY,
        Mode.TEST,
        "testecf",
    )
    assert not state.active
    requirements = Requirements(currently_due=("do_ecf.range",))
    assert (requirements.currently_due, requirements.disabled_reason) == (("do_ecf.range",), None)


def test_what_is_not_a_channel_is_refused_by_the_registry() -> None:
    registry = ChannelRegistry(assembled(do_ecf_v1=object()))
    with pytest.raises(BindingTypeError, match="'do-ecf-v1'"):
        registry.get("do-ecf-v1")
    with pytest.raises(BindingTypeError):
        registry.all()


def test_the_registry_is_read_at_call_time_only() -> None:
    channels = AssembledChannels()
    registry = ChannelRegistry(channels)
    with pytest.raises(ChannelsNotAssembledError):
        registry.get("do-ecf-v1")
    with pytest.raises(ChannelsNotAssembledError):
        registry.all()
    channels.seal()
    assert registry.all() == ()
    with pytest.raises(ChannelUnknownError):
        registry.get("do-ecf-v1")


def test_the_resume_reasons_are_words() -> None:
    assert {reason: reason.value for reason in ResumeReason} == {
        ResumeReason.CERTIFICATE_INSTALLED: "certificate_installed",
        ResumeReason.RANGE_REGISTERED: "range_registered",
        ResumeReason.OPERATOR: "operator",
    }


def test_an_objection_names_a_rule_by_its_code() -> None:
    objection = Objection(code="do_ecf_range_missing", field=None, message="no usable range for type 31")
    assert (objection.code, objection.field, objection.message) == (
        "do_ecf_range_missing",
        None,
        "no usable range for type 31",
    )
    with pytest.raises(InvalidValueError, match=r"objection.code must be a snake_case code"):
        Objection(code="Range Missing", field="lines[0].quantity", message="too many places")


def test_an_issued_answer_says_whether_it_signed() -> None:
    issued = Issued(status=UPDATE, signed=True)
    assert issued.status.authority_number == "E310000000001"
    assert issued.signed


def test_a_status_update_keeps_its_details_from_whoever_built_it() -> None:
    nested: dict[str, JsonValue] = {"encf": "E310000000001"}
    details: dict[str, JsonValue] = {"do": nested, "other": 1}
    update = replace(UPDATE, details=details)
    details["other"] = None
    nested["encf"] = "E310000000002"
    assert update.details == {"do": {"encf": "E310000000001"}, "other": 1}
    assert (update.state, update.status, update.severity) == (
        "accepted",
        FiscalStatus.ACCEPTED,
        Severity.INFO,
    )
    assert (update.channel, update.environment, update.occurred_at) == ("do-ecf-v1", "testecf", OCCURRED_AT)


def test_a_status_update_holds_at_most_the_kept_messages() -> None:
    message = AuthorityMessage(code="613", message="x", lang="es")
    assert len(replace(UPDATE, messages=(message,) * MAX_MESSAGES).messages) == MAX_MESSAGES
    rule = r"status_update.messages must be kept: at most 20, each within its bounds"
    with pytest.raises(InvalidValueError, match=rule):
        replace(UPDATE, messages=(message,) * (MAX_MESSAGES + 1))
    with pytest.raises(InvalidValueError, match=rule):
        replace(UPDATE, messages=(replace(message, message="x" * (MAX_MESSAGE_TEXT + 1)),))
    with pytest.raises(InvalidValueError, match=rule):
        replace(UPDATE, messages=(replace(message, code="c" * (MAX_MESSAGE_CODE + 1)),))


def test_a_status_update_is_versioned_and_dated() -> None:
    with pytest.raises(InvalidValueError, match=r"status_update.version must be 1 or more"):
        replace(UPDATE, version=0)
    with pytest.raises(InvalidValueError, match=r"status_update.occurred_at must be an aware datetime"):
        replace(UPDATE, occurred_at=OCCURRED_AT.replace(tzinfo=None))


def test_a_representation_shows_no_content() -> None:
    printed = Representation(media_type="application/pdf", content=b"%PDF-1.7 secret", filename="a.pdf")
    assert "secret" not in repr(printed)
    assert (printed.media_type, printed.filename, printed.content[:4]) == (
        "application/pdf",
        "a.pdf",
        b"%PDF",
    )
