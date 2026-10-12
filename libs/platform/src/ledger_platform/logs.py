"""Structured logs: JSON in production, readable in a terminal; standard-library loggers included.

Three rules keep a record safe and complete wherever it comes from. A traceback never carries local
variables, in either format: a frame that failed to connect holds the database password. What is true of
the whole process (``service``, ``environment``) is stamped by a processor, not bound in a context variable:
those belong to the calling context, which a thread or an executor does not share.

And an exception whose text can quote data is logged without it, wherever it sits in the chain of causes
and contexts, groups included. Such text is what PostgreSQL answers a statement (``Key (tax_id)=(...)
already exists``, ``invalid input syntax for type uuid: "..."``), what psycopg says reading a value, and what
pydantic and FastAPI say of an input (``input_value=...``). The log shows instead:

* ``psycopg.Error``: the SQLSTATE and the names of the schema, table, column and constraint, never the
  message. An error of a connection attempt keeps libpq's words: they name the server and the role, never a
  row, and they are what an operator needs.
* ``sqlalchemy.exc.StatementError`` (``DBAPIError`` among them): the driver error's type and what the rule
  above shows of it, and the statement (never its parameters).
* pydantic's ``ValidationError`` and FastAPI's request and response validation errors: how many problems
  there are, and where and of which type the first twenty are.

Any other exception keeps its message: it is ours, or a library's that carries no data.
"""

import io
import logging
import re
import sys
from dataclasses import dataclass
from typing import Final, TypedDict, final
lazy from collections.abc import Iterator, Sequence
lazy from typing import TextIO

import psycopg
import structlog
from fastapi.exceptions import ValidationException
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from sqlalchemy.exc import StatementError
lazy from structlog.typing import EventDict, ExcInfo, Processor

from ledger_kernel.errors import error_type, truncate
from ledger_platform.settings import LogFormat
lazy from ledger_platform.settings import ServiceSettings

WITHHELD: Final = "(message withheld: it can quote values)"
"""What a log says in place of the message of an error of the database."""
_MAX_ISSUES: Final = 20
"""How many problems of a validation error a log names; the rest are counted."""
_LOCATION_PART: Final = 40
"""A location names fields, but a key a client chose is a field name too: it is cut at this length."""
_STATEMENT_LIMIT: Final = 300
"""How much of a SQL statement a log keeps: enough to find the code that ran it."""


class _Issue(TypedDict):
    """A problem of a validation error, as far as a log may tell: where, and of which type."""

    loc: tuple[str | int, ...]
    type: str


@with_config(ConfigDict(extra="allow"))
class _Stack(TypedDict):
    """One exception of a chain as structlog renders it to JSON, as far as redaction needs it.

    The rest (the frames, the notes, whether it is a cause) passes through as it is.
    """

    exc_value: str
    exceptions: list[list[_Stack]]
    """The members of a group, each as a chain of its own; empty for any other exception."""


_ISSUES: Final = TypeAdapter(list[_Issue])
_STACKS: Final = TypeAdapter(list[_Stack])


def _location(loc: Sequence[str | int]) -> str:
    return ".".join(truncate(str(part), _LOCATION_PART) for part in loc)


def _validation(count: int, issues: Sequence[_Issue], title: str | None = None) -> str:
    """``count`` problems, the first of them named by location and type: never the input."""
    named = ", ".join(f"{_location(issue['loc'])} ({issue['type']})" for issue in issues[:_MAX_ISSUES])
    more = f", and {count - _MAX_ISSUES} more" if count > _MAX_ISSUES else ""
    subject = "" if title is None else f" for {title}"
    return f"{count} validation errors{subject}" + (f": {named}{more}" if named else "")


def _readable(problems: Sequence[object]) -> list[_Issue]:
    """The first problems of a FastAPI validation error, or none if they are not pydantic's."""
    try:
        return _ISSUES.validate_python(problems[:_MAX_ISSUES])
    except ValidationError:
        return []


def _of_the_database(error: psycopg.Error) -> str | None:
    """The SQLSTATE and the names an error of the database carries; ``None`` (keep it) for a connection's."""
    if error.pgconn is not None:
        return None
    diag = error.diag
    names = {
        "schema": diag.schema_name,
        "table": diag.table_name,
        "column": diag.column_name,
        "constraint": diag.constraint_name,
    }
    named = "".join(f" {key}={value}" for key, value in names.items() if value)
    return f"sqlstate={error.sqlstate}{named} {WITHHELD}"


def _of_a_statement(error: StatementError) -> str:
    cause = error.orig
    driver = "(no driver error)" if cause is None else f"({error_type(cause)}) {_shown(cause)}"
    return f"{driver} [SQL: {truncate(error.statement or '', _STATEMENT_LIMIT)}]"


def _withheld(error: BaseException) -> str | None:
    """What a log shows in place of the text of ``error``; ``None`` when its text may be shown."""
    match error:
        case psycopg.Error():
            return _of_the_database(error)
        case StatementError():
            return _of_a_statement(error)
        case ValidationError():
            issues = error.errors(include_url=False, include_context=False, include_input=False)
            return _validation(error.error_count(), issues, error.title)
        case ValidationException():
            problems = error.errors()
            return _validation(len(problems), _readable(problems))
        case _:
            return None


def _shown(error: BaseException) -> str:
    """The text of ``error`` as a log may show it."""
    withheld = _withheld(error)
    return structlog.tracebacks.safe_str(error) if withheld is None else withheld


def _chain(error: BaseException) -> Iterator[BaseException]:
    """``error`` and every exception it leads to, once each.

    Causes, contexts (even suppressed ones) and the members of groups: whatever a renderer may print.
    """
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(linked for linked in (current.__cause__, current.__context__) if linked is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


type _Replacement = tuple[re.Pattern[str], str]
"""Where a renderer printed the text of a withheld exception, and what the log shows there instead."""


def _printed(said: str) -> re.Pattern[str]:
    """``said`` as a renderer prints it.

    Verbatim, except that a traceback puts the margin of an exception group (spaces and ``|``) before each
    line after the first.
    """
    first, *rest = said.split("\n")
    return re.compile(re.escape(first) + "".join(rf"\n[ |]*{re.escape(line)}" for line in rest))


def _replacements(error: BaseException) -> list[_Replacement]:
    """Each exception of the chain whose text is withheld, as ``_printed`` finds it, and its replacement.

    Longest first: the text of a ``StatementError`` contains the text of its driver error, and is replaced as
    a whole before the shorter one could break it up.
    """
    replaced: dict[str, str] = {}
    for each in _chain(error):
        withheld, said = _withheld(each), structlog.tracebacks.safe_str(each)
        if withheld is not None and said:
            replaced[said] = withheld
    longest_first = sorted(replaced.items(), key=lambda pair: len(pair[0]), reverse=True)
    return [(_printed(said), withheld) for said, withheld in longest_first]


def _redact(text: str, replacements: Sequence[_Replacement]) -> str:
    for printed, withheld in replacements:
        text = printed.sub(withheld.replace("\\", r"\\"), text)  # a replacement, not a template
    return text


def _rewrite(stacks: list[_Stack], replacements: Sequence[_Replacement]) -> None:
    for stack in stacks:
        stack["exc_value"] = _redact(stack["exc_value"], replacements)
        for member in stack["exceptions"]:
            _rewrite(member, replacements)


@final
class _WithholdingDictTransformer:
    """structlog's exception dictionaries, without locals, every withheld text replaced."""

    __slots__ = ("_extract",)

    def __init__(self) -> None:
        self._extract = structlog.tracebacks.ExceptionDictTransformer(show_locals=False)

    def __call__(self, exc_info: ExcInfo) -> list[_Stack]:
        stacks = _STACKS.validate_python(self._extract(exc_info))
        _rewrite(stacks, _replacements(exc_info[1]))
        return stacks


def _withholding_traceback(sio: TextIO, exc_info: ExcInfo) -> None:
    """A plain-text traceback without locals, every withheld text replaced."""
    traceback = io.StringIO()
    structlog.dev.plain_traceback(traceback, exc_info)
    sio.write(_redact(traceback.getvalue(), _replacements(exc_info[1])))


@final
@dataclass(frozen=True, slots=True)
class _ProcessFields:
    """Stamps every record with the fields of the process. A value given on the call itself wins."""

    service: str
    environment: str

    def __call__(self, logger: object, method_name: str, event_dict: EventDict) -> EventDict:
        del logger, method_name
        event_dict.setdefault("service", self.service)
        event_dict.setdefault("environment", self.environment)
        return event_dict


def _shared_processors(settings: ServiceSettings) -> list[Processor]:
    """What both structlog records and standard-library records go through, in this order."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ProcessFields(settings.service_name, settings.environment),
    ]


def _rendering_processors(log_format: LogFormat) -> list[Processor]:
    """How a record becomes text.

    Exceptions are plain data or plain text, without their locals, and without what the text of an error of
    the database or of a validation can quote.
    """
    rendering: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if log_format is LogFormat.JSON:
        rendering += [
            structlog.processors.ExceptionRenderer(_WithholdingDictTransformer()),
            structlog.processors.JSONRenderer(),
        ]
    else:
        rendering.append(
            structlog.dev.ConsoleRenderer(colors=False, exception_formatter=_withholding_traceback)
        )
    return rendering


def configure_logging(settings: ServiceSettings) -> None:
    """Route structlog and every standard-library logger to stdout in ``settings.log_format``.

    The caller's context variables are left alone: they carry request-scoped values, merged into each record.
    """
    shared = _shared_processors(settings)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared, processors=_rendering_processors(settings.log_format)
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
