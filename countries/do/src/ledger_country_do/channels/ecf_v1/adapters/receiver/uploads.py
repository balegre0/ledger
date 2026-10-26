"""The one file a receiver's request carries: the multipart field ``xml`` (DGII's one input, DT-EMI).

The app's body limit refuses a body over the route's before anything reads it (413). The form is parsed with
one file at most and no other field; a body that is no such form, or carries no file ``xml``, is a bare 400
(``HTTPException``: nothing of the request is answered back). A file over ``limit`` bytes is 413.
"""

from http import HTTPStatus
from typing import Final

from fastapi import HTTPException
lazy from fastapi import Request

from ledger_platform.http.problems import PayloadTooLargeError

FIELD: Final = "xml"


async def uploaded(request: Request, *, limit: int) -> bytes:
    """The file ``xml`` of ``request``'s form, at most ``limit`` bytes (see the module)."""
    async with request.form(max_files=1, max_fields=0) as form:
        found = form.get(FIELD)
        if found is None or isinstance(found, str):  # no field, or a field that is no file
            raise HTTPException(HTTPStatus.BAD_REQUEST)
        content = await found.read(limit + 1)
    if len(content) > limit:
        raise PayloadTooLargeError(limit)
    return content
