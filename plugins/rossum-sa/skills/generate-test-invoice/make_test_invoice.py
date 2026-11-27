#!/usr/bin/env python3
"""Render a test invoice PDF from a JSON spec — stdlib only, no install needed.

    python3 make_test_invoice.py spec.json out.pdf

Writes `out.pdf` (a text PDF Rossum can read: labelled header fields, sender and
recipient blocks, a line-item table, totals) and `out.expected.json` (every value
printed on the document, with the computed amounts) to assert against after
extraction. Every page carries a TEST DOCUMENT mark so it is never mistaken for a
real invoice. Spec format: see spec-format.md next to this script.
"""
from __future__ import annotations

import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

PAGE_W, PAGE_H, MARGIN = 595, 842, 50  # A4 in points
BOTTOM = 80  # start a new page below this y
WATERMARK = "TEST DOCUMENT - generated for testing, not a real invoice"
CENT = Decimal("0.01")

# Helvetica advance widths (per 1000 units) for the characters that appear in
# right-aligned numbers; anything else is estimated at 556 (the digit width).
_WIDTHS = {**{d: 556 for d in "0123456789"}, ".": 278, ",": 278, "-": 333, " ": 278, "%": 889}


class SpecError(ValueError):
    """The spec is unusable; the message names the offending key."""


# --- values ------------------------------------------------------------------


def money(value, where: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise SpecError(f"{where}: not a number: {value!r}") from None


def fmt(amount: Decimal, number_format: str) -> str:
    """2-decimal amount in the spec's number format: en 1,234.56 · eu 1.234,56 · plain 1234.56."""
    text = f"{amount.quantize(CENT, ROUND_HALF_UP):,.2f}"
    if number_format == "eu":
        return text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    if number_format == "plain":
        return text.replace(",", "")
    return text


def short(amount: Decimal, number_format: str) -> str:
    """Like fmt, without trailing zero decimals: 19 · 7.5 · 0 (quantities and VAT rates)."""
    text = fmt(amount, number_format)
    return text.rstrip("0").rstrip(",.") or "0"


def compute(spec: dict) -> dict:
    """Line amounts, per-rate tax and totals — the numbers the document prints."""
    lines = spec.get("lines") or []
    if not isinstance(lines, list):
        raise SpecError("lines: must be a list")
    out_lines, bases = [], {}
    for i, line in enumerate(lines, 1):
        where = f"lines[{i}]"
        if "description" not in line:
            raise SpecError(f"{where}: missing 'description'")
        qty = money(line.get("quantity", 1), f"{where}.quantity")
        price = money(line.get("unit_price", 0), f"{where}.unit_price")
        rate = money(line.get("tax_rate", 0), f"{where}.tax_rate")
        amount = (qty * price).quantize(CENT, ROUND_HALF_UP)
        bases[rate] = bases.get(rate, Decimal(0)) + amount
        out_lines.append({**line, "quantity": qty, "unit_price": price, "tax_rate": rate, "amount": amount})
    taxes = [
        {"rate": rate, "base": base, "tax": (base * rate / 100).quantize(CENT, ROUND_HALF_UP)}
        for rate, base in sorted(bases.items())
    ]
    subtotal = sum((t["base"] for t in taxes), Decimal(0))
    total = subtotal + sum((t["tax"] for t in taxes), Decimal(0))
    return {"lines": out_lines, "taxes": taxes, "subtotal": subtotal, "total": total}


def totals_rows(spec: dict, calc: dict) -> list[tuple[str, str]]:
    """(label, printed value) rows; `totals_override` replaces a printed value by label on purpose."""
    nf, cur = spec.get("number_format", "en"), spec.get("currency", "")
    rows = [("Subtotal", fmt(calc["subtotal"], nf))]
    rows += [(f"VAT {short(t['rate'], nf)}% of {fmt(t['base'], nf)}", fmt(t["tax"], nf))
             for t in calc["taxes"]]
    rows.append((f"Total {cur}".strip(), fmt(calc["total"], nf)))
    override = spec.get("totals_override") or {}
    unknown = set(override) - {label for label, _ in rows}
    if unknown:
        raise SpecError(f"totals_override: no totals row labelled {sorted(unknown)}; rows are {[r[0] for r in rows]}")
    return [(label, str(override.get(label, value))) for label, value in rows]


# --- PDF ---------------------------------------------------------------------


# shortcut: the 14 built-in PDF fonts cover cp1252 only (no ř, ł, Cyrillic); embed a TrueType
# font if Central/Eastern European names ever need testing on generated documents.
def pdf_text(text: str) -> bytes:
    """WinAnsi bytes for a PDF string literal: unencodable characters become '?', \\ ( ) are escaped."""
    raw = str(text).encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def text_width(text: str, size: float) -> float:
    return sum(_WIDTHS.get(ch, 556) for ch in str(text)) * size / 1000


def wrap(text: str, max_chars: int) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + [cur] if cur else lines or [""]


class Canvas:
    """Minimal multi-page PDF writer: Helvetica / Helvetica-Bold text and lines."""

    def __init__(self):
        self.pages: list[list[bytes]] = []
        self.new_page()

    def new_page(self):
        self.pages.append([])
        self.y = PAGE_H - MARGIN - 20
        self.text(MARGIN, PAGE_H - 30, WATERMARK, 8, gray=0.45)
        self.text(MARGIN, 25, WATERMARK, 8, gray=0.45)

    def ensure(self, height: float) -> bool:
        """Start a new page if `height` no longer fits; True when it did."""
        if self.y - height < BOTTOM:
            self.new_page()
            return True
        return False

    def text(self, x, y, s, size=10, bold=False, gray=0.0, right=False):
        if right:
            x -= text_width(s, size)
        font = "F2" if bold else "F1"
        self.pages[-1].append(
            b"%.2f g BT /%s %d Tf %.2f %.2f Td (%s) Tj ET" % (gray, font.encode(), size, x, y, pdf_text(s))
        )

    def line(self, x1, y1, x2, y2):
        self.pages[-1].append(b"0.6 G 0.5 w %.2f %.2f m %.2f %.2f l S" % (x1, y1, x2, y2))

    def render(self) -> bytes:
        n = len(self.pages)
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (5 + 2 * i) for i in range(n)), n),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        ]
        for i, ops in enumerate(self.pages):
            stream = b"\n".join(ops)
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                        b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (PAGE_W, PAGE_H, 6 + 2 * i))
            objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for num, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (num, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        return bytes(out)


# Line-item table: (key, header, x position, right-aligned, wrap width in chars).
COLUMNS = [("code", "Code", MARGIN, False, 0), ("description", "Description", MARGIN + 70, False, 38),
           ("quantity", "Qty", 375, True, 0), ("unit_price", "Unit price", 445, True, 0),
           ("tax_rate", "VAT %", 490, True, 0), ("amount", "Amount", PAGE_W - MARGIN, True, 0)]


def draw_table_header(c: Canvas):
    for _, head, x, right, _ in COLUMNS:
        c.text(x, c.y, head, 9, bold=True, right=right)
    c.line(MARGIN, c.y - 4, PAGE_W - MARGIN, c.y - 4)
    c.y -= 16


def render(spec: dict) -> tuple[bytes, dict]:
    calc = compute(spec)
    nf = spec.get("number_format", "en")
    if nf not in ("en", "eu", "plain"):
        raise SpecError("number_format: use 'en', 'eu' or 'plain'")
    c = Canvas()
    c.text(MARGIN, c.y, spec.get("title", "INVOICE"), 20, bold=True)
    c.y -= 30

    blocks = [("From", spec.get("sender") or []), ("Bill to", spec.get("recipient") or [])]
    top = c.y
    for col, (label, lines) in enumerate(blocks):
        c.y = top
        x = MARGIN + col * 260
        c.text(x, c.y, label, 9, bold=True, gray=0.3)
        c.y -= 14
        for ln in lines:
            c.text(x, c.y, ln, 10)
            c.y -= 13
    c.y = min(c.y, top - 14 - 13 * max(len(b[1]) for b in blocks)) - 12

    for label, value in spec.get("fields") or []:
        c.ensure(14)
        c.text(MARGIN, c.y, f"{label}:", 10, bold=True)
        c.text(MARGIN + 130, c.y, value, 10)
        c.y -= 14
    c.y -= 12

    if calc["lines"]:
        draw_table_header(c)
        for line in calc["lines"]:
            cells = {"code": line.get("code", ""), "description": line["description"],
                     "quantity": short(line["quantity"], nf),
                     "unit_price": fmt(line["unit_price"], nf),
                     "tax_rate": short(line["tax_rate"], nf),
                     "amount": fmt(line["amount"], nf)}
            desc = wrap(cells["description"], COLUMNS[1][4])
            if c.ensure(13 * len(desc) + 4):
                draw_table_header(c)
            for key, _, x, right, _ in COLUMNS:
                if key != "description":
                    c.text(x, c.y, cells[key], 9, right=right)
            for i, part in enumerate(desc):
                c.text(COLUMNS[1][2], c.y - 12 * i, part, 9)
            c.y -= 12 * len(desc) + 4
        c.line(MARGIN, c.y + 2, PAGE_W - MARGIN, c.y + 2)
        c.y -= 14

    rows = totals_rows(spec, calc)
    for i, (label, value) in enumerate(rows):
        c.ensure(16)
        bold = i == len(rows) - 1
        c.text(400, c.y, label, 10, bold=bold, right=True)
        c.text(PAGE_W - MARGIN, c.y, value, 10, bold=bold, right=True)
        c.y -= 16

    if spec.get("notes"):
        c.y -= 10
        for note in spec["notes"]:
            for part in wrap(note, 95):
                c.ensure(13)
                c.text(MARGIN, c.y, part, 9)
                c.y -= 13

    expected = {
        "fields": dict(spec.get("fields") or []),
        "sender": spec.get("sender") or [],
        "recipient": spec.get("recipient") or [],
        "lines": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in ln.items()} for ln in calc["lines"]],
        "computed": {"subtotal": str(calc["subtotal"]), "total": str(calc["total"]),
                     "taxes": [{k: str(v) for k, v in t.items()} for t in calc["taxes"]]},
        "printed_totals": dict(rows),
    }
    return c.render(), expected


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    spec_path, out = Path(argv[1]), Path(argv[2])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        pdf, expected = render(spec)
    except (OSError, json.JSONDecodeError, SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out.write_bytes(pdf)
    exp_path = out.with_suffix(".expected.json")
    exp_path.write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({pdf.count(b'/Type /Page ')} page(s)) and {exp_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
