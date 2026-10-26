"""Drawing a printed representation (spec §8.6.7; Decision 26): Letter pages, drawn with reportlab.

What is drawn is a ``Printed``, top to bottom: the issuer (name, RNC, address) beside the e-CF's type, e-NCF
and dates; the legend "e-CF emitido en modalidad de Contingencia" when it was signed in contingency; the
counterparty (the buyer, or on a purchase the supplier); the e-CF it modifies; its lines (number,
description, quantity, unit price, discount, ITBIS, amount), continued over as many pages as they take, the
table's head repeated; its totals and, in another currency, that currency, its rate and the total in it; then
its QR beside its security code and the moment of signing. Each page's foot names the e-NCF and the page.

The same ``Printed`` is the same bytes (Decision 26): reportlab's invariant mode (no random document id, no
clock), its creation and modification dates the moment of signing (UTC), nothing else variable. The text is
Helvetica, which every reader has and which holds Spanish's letters (WinAnsi); it is not embedded, and a
character beyond it prints as a box.

The QR (``qr_symbol``) is segno's, error correction M. The spec says version 8, which holds at most 152 bytes
at level M: an RFCE's ``consultatimbrefc`` URL fits it, but an e-CF's ``consultatimbre`` URL (seven
parameters, some 200 characters) does not. A QR is version 8, or the smallest version above it that holds its
URL. It is drawn one filled square per dark module (vector, sharp at any scale), clear of anything else for
its quiet zone of four modules.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from datetime import UTC
from io import BytesIO
from typing import Final, Protocol, final
lazy from collections.abc import Callable, Sequence
lazy from datetime import datetime

import segno
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen.canvas import Canvas

from ledger_country_do.channels.ecf_v1.domain.channel import KEY

CONTINGENCY_LEGEND: Final = "e-CF emitido en modalidad de Contingencia"
"""What a document signed in contingency says (spec §8.6.6)."""
QR_VERSION: Final = 8
"""The QR's version, unless its URL needs a larger one (see the module)."""
QR_ERROR: Final = "m"
"""The QR's error correction level: M (about 15 % of it may be lost)."""
QR_MODULE: Final = 1.6
"""A QR module's side, in points: a version-10 symbol is some 33 mm wide with its quiet zone."""
QUIET_ZONE: Final = 4
"""The modules of clear space around a QR."""
TOTAL: Final = "Monto total"
"""The label of the e-CF's total, printed in bold."""

_WIDTH, _HEIGHT = LETTER
_MARGIN: Final = 40.0
_TOP: Final = _HEIGHT - _MARGIN
_BOTTOM: Final = 56.0
"""The lowest baseline of a page's content: below it, its foot."""
_FOOT: Final = 28.0
_RIGHT: Final = _WIDTH - _MARGIN
_SPLIT: Final = 340.0
"""Where the e-CF's identification starts, right of the issuer."""
_REGULAR: Final = "Helvetica"
_BOLD: Final = "Helvetica-Bold"
_TEXT: Final = 9.0
_SMALL: Final = 7.5
_LEADING: Final = 1.25
"""A line's height, as a multiple of its font's size."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Party:
    """A party as printed: its name, what identifies it (``RNC: ...``), its address, each when it has one."""

    name: str | None
    identity: str | None
    address: str | None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PrintedLine:
    """A line as the e-CF states it: its amounts as written there, its ITBIS as a rate or ``Exento``."""

    number: str
    description: str
    quantity: str
    unit_price: str
    discount: str | None
    tax: str
    amount: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Printed:
    """Everything a printed representation shows (see the module), as it is printed."""

    title: str
    """The e-CF type's name."""
    encf: str
    dates: tuple[str, ...]
    """Its issue date and, for a dated type, the last day of its range, each with its label."""
    issuer: Party
    counterparty: Party | None
    counterparty_title: str
    references: tuple[str, ...]
    """What it modifies, a sentence each."""
    lines: tuple[PrintedLine, ...]
    totals: tuple[tuple[str, str], ...]
    """Each total's label and amount, in pesos."""
    currency: tuple[str, ...]
    """In another currency: the currency, the rate, the total in it, a sentence each; else empty."""
    security_code: str
    signed: str
    """The moment of signing, as the e-CF states it (``FechaHoraFirma``)."""
    qr_url: str
    contingency: bool
    created: datetime
    """The moment of signing: the PDF's creation date."""


@final
@dataclass(frozen=True, slots=True)
class QrSymbol:
    """A QR symbol: its version and its modules, row by row from the top (``True`` dark)."""

    version: int
    matrix: tuple[tuple[bool, ...], ...]


def qr_symbol(url: str) -> QrSymbol:
    """``url``'s QR: error correction M, version 8 or the smallest above that holds it (see the module)."""
    smallest = segno.make_qr(url, error=QR_ERROR, boost_error=False)
    version = max(QR_VERSION, int(smallest.version))
    code = segno.make_qr(url, error=QR_ERROR, version=version, boost_error=False)
    return QrSymbol(version, tuple(tuple(bool(module) for module in row) for row in code.matrix))


@final
class _Sheet:
    """The pages being drawn, and where the next line goes (``y``, its baseline)."""

    __slots__ = ("_canvas", "_encf", "_page", "y")

    def __init__(self, canvas: Canvas, encf: str) -> None:
        self._canvas = canvas
        self._encf = encf
        self._page = 1
        self.y = _TOP

    def width(self, text: str, *, size: float, bold: bool = False) -> float:
        return self._canvas.stringWidth(text, _BOLD if bold else _REGULAR, size)

    def wrapped(self, text: str, width: float, *, size: float, bold: bool = False) -> tuple[str, ...]:
        """``text`` in lines no wider than ``width``, broken between words (a word too wide, within it)."""
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}" if current else word
            if self.width(candidate, size=size, bold=bold) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = ""
            for character in word:
                if current and self.width(current + character, size=size, bold=bold) > width:
                    lines.append(current)
                    current = ""
                current += character
        return (*lines, current) if current else tuple(lines)

    def write(self, x: float, text: str, *, size: float, bold: bool = False, right: bool = False) -> None:
        """``text`` on the line ``y``: from ``x``, or ending at ``x`` when ``right``."""
        self._canvas.setFont(_BOLD if bold else _REGULAR, size)
        if right:
            self._canvas.drawRightString(x, self.y, text)
        else:
            self._canvas.drawString(x, self.y, text)

    def lines(self, x: float, texts: Sequence[str], *, size: float, bold: bool = False) -> None:
        """``texts`` from ``x``, one under another, from ``y`` down."""
        for text in texts:
            self.write(x, text, size=size, bold=bold)
            self.y -= size * _LEADING

    def rule(self) -> None:
        """A thin line across the page, under the last line written."""
        self._canvas.setLineWidth(0.5)
        self._canvas.line(_MARGIN, self.y + _TEXT * 0.6, _RIGHT, self.y + _TEXT * 0.6)
        self.y -= 4

    def room(self, height: float) -> bool:
        """Whether ``height`` more fits above the page's foot."""
        return self.y - height >= _BOTTOM

    def turn(self) -> None:
        """This page footed and ended; the next begins at its top."""
        self._foot()
        self._canvas.showPage()
        self._page += 1
        self.y = _TOP

    def end(self) -> None:
        self._foot()
        self._canvas.showPage()

    def squares(self, x: float, top: float, symbol: QrSymbol) -> None:
        """``symbol``'s dark modules, its top-left module's corner at (``x``, ``top``)."""
        for row, modules in enumerate(symbol.matrix):
            for column, dark in enumerate(modules):
                if dark:
                    bottom = top - (row + 1) * QR_MODULE
                    self._canvas.rect(x + column * QR_MODULE, bottom, QR_MODULE, QR_MODULE, stroke=0, fill=1)

    def _foot(self) -> None:
        self._canvas.setFont(_REGULAR, _SMALL)
        self._canvas.drawCentredString(_WIDTH / 2, _FOOT, f"{self._encf} - Página {self._page}")


_NUMBER_X: Final = _MARGIN
_DESCRIPTION_X: Final = 58.0
_DESCRIPTION_WIDTH: Final = 176.0
_QUANTITY_RIGHT: Final = 300.0
_PRICE_RIGHT: Final = 375.0
_DISCOUNT_RIGHT: Final = 440.0
_TAX_X: Final = 448.0
_AMOUNT_RIGHT: Final = _RIGHT
_TOTAL_LABEL_RIGHT: Final = 470.0


def _header(sheet: _Sheet, printed: Printed) -> None:
    """The issuer, left, beside the e-CF's type, number and dates, right; then the legend, if any."""
    issuer, width = printed.issuer, _SPLIT - _MARGIN - 10
    top = sheet.y
    sheet.lines(_MARGIN, sheet.wrapped(issuer.name or "", width, size=11, bold=True), size=11, bold=True)
    for text in (each for each in (issuer.identity, issuer.address) if each):
        sheet.lines(_MARGIN, sheet.wrapped(text, width, size=_TEXT), size=_TEXT)
    left = sheet.y
    sheet.y = top
    sheet.lines(_SPLIT, sheet.wrapped(printed.title, _RIGHT - _SPLIT, size=10, bold=True), size=10, bold=True)
    sheet.lines(_SPLIT, (f"e-NCF: {printed.encf}",), size=10, bold=True)
    sheet.lines(_SPLIT, printed.dates, size=_TEXT)
    sheet.y = min(left, sheet.y) - 8
    if printed.contingency:
        sheet.lines(_MARGIN, (CONTINGENCY_LEGEND,), size=10, bold=True)
        sheet.y -= 4


def _parties(sheet: _Sheet, printed: Printed) -> None:
    """The counterparty, if the e-CF names one, and what the e-CF modifies, if anything."""
    counterparty = printed.counterparty
    if counterparty is not None:
        sheet.lines(_MARGIN, (printed.counterparty_title,), size=_TEXT, bold=True)
        named = (counterparty.name, counterparty.identity, counterparty.address)
        for text in (each for each in named if each):
            sheet.lines(_MARGIN, sheet.wrapped(text, _RIGHT - _MARGIN, size=_TEXT), size=_TEXT)
        sheet.y -= 4
    if printed.references:
        sheet.lines(_MARGIN, ("Referencia",), size=_TEXT, bold=True)
        for reference in printed.references:
            sheet.lines(_MARGIN, sheet.wrapped(reference, _RIGHT - _MARGIN, size=_TEXT), size=_TEXT)
        sheet.y -= 4


def _table_head(sheet: _Sheet) -> None:
    sheet.write(_NUMBER_X, "#", size=_SMALL, bold=True)
    sheet.write(_DESCRIPTION_X, "Descripción", size=_SMALL, bold=True)
    sheet.write(_QUANTITY_RIGHT, "Cantidad", size=_SMALL, bold=True, right=True)
    sheet.write(_PRICE_RIGHT, "Precio unitario", size=_SMALL, bold=True, right=True)
    sheet.write(_DISCOUNT_RIGHT, "Descuento", size=_SMALL, bold=True, right=True)
    sheet.write(_TAX_X, "ITBIS", size=_SMALL, bold=True)
    sheet.write(_AMOUNT_RIGHT, "Monto", size=_SMALL, bold=True, right=True)
    sheet.y -= _SMALL * _LEADING
    sheet.rule()


def _lines(sheet: _Sheet, printed: Printed) -> None:
    """The lines' table, a page turned (the table's head repeated) when a line does not fit."""
    _table_head(sheet)
    for line in printed.lines:
        description = sheet.wrapped(line.description, _DESCRIPTION_WIDTH, size=_SMALL)
        height = max(len(description), 1) * _SMALL * _LEADING + 2
        if not sheet.room(height):
            sheet.turn()
            sheet.lines(_MARGIN, (f"{printed.title} {printed.encf} (continuación)",), size=_TEXT, bold=True)
            _table_head(sheet)
        y = sheet.y
        sheet.write(_NUMBER_X, line.number, size=_SMALL)
        sheet.write(_QUANTITY_RIGHT, line.quantity, size=_SMALL, right=True)
        sheet.write(_PRICE_RIGHT, line.unit_price, size=_SMALL, right=True)
        sheet.write(_DISCOUNT_RIGHT, line.discount or "", size=_SMALL, right=True)
        sheet.write(_TAX_X, line.tax, size=_SMALL)
        sheet.write(_AMOUNT_RIGHT, line.amount, size=_SMALL, right=True)
        sheet.lines(_DESCRIPTION_X, description, size=_SMALL)
        sheet.y = y - height
    sheet.rule()


def _closing(sheet: _Sheet, printed: Printed) -> None:
    """The totals, the currency and the QR with the security code, together (a page turned if need be)."""
    symbol = qr_symbol(printed.qr_url)
    side = (len(symbol.matrix) + 2 * QUIET_ZONE) * QR_MODULE
    rows = len(printed.totals) + len(printed.currency)
    if not sheet.room(rows * _TEXT * _LEADING + side + 8):
        sheet.turn()
    for label, amount in printed.totals:
        bold = label == TOTAL
        sheet.write(_TOTAL_LABEL_RIGHT, label, size=_TEXT, bold=bold, right=True)
        sheet.write(_AMOUNT_RIGHT, amount, size=_TEXT, bold=bold, right=True)
        sheet.y -= _TEXT * _LEADING
    for sentence in printed.currency:
        sheet.write(_AMOUNT_RIGHT, sentence, size=_TEXT, right=True)
        sheet.y -= _TEXT * _LEADING
    top = sheet.y
    sheet.squares(_MARGIN + QUIET_ZONE * QR_MODULE, top - QUIET_ZONE * QR_MODULE, symbol)
    sheet.y = top - QUIET_ZONE * QR_MODULE - _TEXT
    text_x = _MARGIN + side + 8
    sheet.lines(text_x, (f"Código de seguridad: {printed.security_code}",), size=10, bold=True)
    sheet.lines(text_x, (f"Fecha de firma digital: {printed.signed}",), size=_TEXT)
    sheet.lines(text_x, ("Consulte este e-CF en la DGII con el código QR.",), size=_SMALL)
    sheet.y = top - side


type _DateFormatter = Callable[[int, int, int, int, int, int], str]
"""What reportlab formats a PDF's dates with: the year, month, day, hour, minute and second, to a PDF date."""


class _Dated(Protocol, metaclass=ABCMeta):
    """A canvas whose PDF dates a formatter writes: reportlab's ``Canvas``, its formatter typed here.

    types-reportlab leaves ``setDateFormatter``'s parameter untyped; a ``Canvas`` is passed as a ``_Dated``,
    so the call is checked (the method's name is reportlab's: ruff's ``extend-ignore-names`` lists it).
    """

    @abstractmethod
    def setDateFormatter(self, formatter: _DateFormatter, /) -> None:
        """Write the PDF's creation and modification dates with ``formatter``."""


def _dated(target: _Dated, formatter: _DateFormatter) -> None:
    """``target``'s PDF dates written by ``formatter``, the call typed through ``_Dated``."""
    target.setDateFormatter(formatter)


def draw(printed: Printed) -> bytes:
    """``printed`` as a PDF (see the module)."""
    out = BytesIO()
    canvas = Canvas(out, pagesize=LETTER, pageCompression=1, invariant=1)
    stamp = f"D:{printed.created.astimezone(UTC):%Y%m%d%H%M%S}Z"

    def dated(*_: int) -> str:
        return stamp

    _dated(canvas, dated)
    canvas.setTitle(printed.encf)
    canvas.setSubject(printed.title)
    canvas.setCreator(KEY)
    sheet = _Sheet(canvas, printed.encf)
    _header(sheet, printed)
    _parties(sheet, printed)
    _lines(sheet, printed)
    _closing(sheet, printed)
    sheet.end()
    canvas.save()
    return out.getvalue()
