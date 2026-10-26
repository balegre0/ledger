"""The one file a receiver's request carries: the multipart field ``xml`` (DGII's one input, DT-EMI).

The app's body limit refuses a body over the route's before anything reads it (413). The form is parsed with
one file at most and no other field; a body that is no such form, or carries no file ``xml``, is a bare 400
(``HTTPException``: nothing of the request is answered back). The file is no larger than the body the
route's limit let through.
"""

from http import HTTPStatus
from typing import Final

from fastapi import HTTPException
lazy from fastapi import Request

FIELD: Final = "xml"


async def uploaded(request: Request) -> bytes:
    """The file ``xml`` of ``request``'s form (see the module)."""
    async with request.form(max_files=1, max_fields=0) as form:
        found = form.get(FIELD)
        if found is None or isinstance(found, str):  # no field, or a field that is no file
            raise HTTPException(HTTPStatus.BAD_REQUEST)
        return await found.read()
