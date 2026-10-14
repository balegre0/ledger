"""Names the platform owns: its schema, and the one rule every schema name follows (§4.4, §6.2).

A schema name is also its roles' prefix (``<schema>_owner``, ``<schema>_rw``): 2 to 31 lower-case letters,
digits and underscores, led by a letter, never ``pg``, ``pg_*``, ``public``, ``information_schema`` or one of
PostgreSQL's reserved words. The manifest model reads ``[data] schema`` with it, the roles builder makes roles
with it.
"""

import re
from typing import Final

PLATFORM: Final = "platform"
"""The platform's package name and its schema: its ``rw`` role is the signer's too (schedule leases); the
relay reads its ``routing``."""
_SCHEMA: Final = re.compile(r"[a-z][a-z0-9_]{1,30}", re.ASCII)
"""2 to 31 characters, so that ``<schema>_owner`` stays a short identifier."""
RESERVED_WORDS: Final = frozenset(
    [
        "all",
        "analyse",
        "analyze",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "asymmetric",
        "authorization",
        "binary",
        "both",
        "case",
        "cast",
        "check",
        "collate",
        "collation",
        "column",
        "concurrently",
        "constraint",
        "create",
        "cross",
        "current_catalog",
        "current_date",
        "current_role",
        "current_schema",
        "current_time",
        "current_timestamp",
        "current_user",
        "default",
        "deferrable",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "false",
        "fetch",
        "for",
        "foreign",
        "freeze",
        "from",
        "full",
        "grant",
        "group",
        "having",
        "ilike",
        "in",
        "initially",
        "inner",
        "intersect",
        "into",
        "is",
        "isnull",
        "join",
        "lateral",
        "leading",
        "left",
        "like",
        "limit",
        "localtime",
        "localtimestamp",
        "natural",
        "not",
        "notnull",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "outer",
        "overlaps",
        "placing",
        "primary",
        "references",
        "returning",
        "right",
        "select",
        "session_user",
        "similar",
        "some",
        "symmetric",
        "system_user",
        "table",
        "tablesample",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "variadic",
        "verbose",
        "when",
        "where",
        "window",
        "with",
    ]
)
"""PostgreSQL's reserved key words (``pg_get_keywords()``, categories R and T; a test keeps them equal)."""
_REFUSED: Final = RESERVED_WORDS | {"pg", "public", "information_schema"}


def is_schema_name(text: str) -> bool:
    """Whether ``text`` can name a schema and prefix its roles (see the module)."""
    return _SCHEMA.fullmatch(text) is not None and text not in _REFUSED and not text.startswith("pg_")
