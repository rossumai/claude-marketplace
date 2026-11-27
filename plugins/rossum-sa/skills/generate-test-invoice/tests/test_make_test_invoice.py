"""make_test_invoice: money math, deliberate total overrides, and a structurally valid PDF.

Local-file writer, attended — per docs/testing-skill-scripts.md: pin the pure functions that decide
what gets printed, plus one happy path and the refusal guards.
"""
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import make_test_invoice as M  # noqa: E402


def _spec(**over):
    spec = json.loads((HERE.parent / "example-spec.json").read_text())
    spec.update(over)
    return spec


def test_totals_are_consistent_per_tax_rate():
    calc = M.compute(_spec())
    assert [str(ln["amount"]) for ln in calc["lines"]] == ["70.00", "346.00", "45.00"]
    assert [(str(t["rate"]), str(t["base"]), str(t["tax"])) for t in calc["taxes"]] == [
        ("7", "45.00", "3.15"), ("19", "416.00", "79.04")]
    assert calc["subtotal"] == Decimal("461.00") and calc["total"] == Decimal("543.19")


def test_amounts_round_half_up():
    calc = M.compute({"lines": [{"description": "x", "quantity": 1, "unit_price": "0.005", "tax_rate": 0}]})
    assert str(calc["lines"][0]["amount"]) == "0.01"


@pytest.mark.parametrize("nf,expected", [("en", "1,234.50"), ("eu", "1.234,50"), ("plain", "1234.50")])
def test_number_formats(nf, expected):
    assert M.fmt(Decimal("1234.5"), nf) == expected


def test_short_numbers_keep_zero_and_integers():
    assert [M.short(Decimal(v), "en") for v in ("0", "19", "7.5", "100", "1000")] == ["0", "19", "7.5", "100", "1,000"]


def test_totals_override_prints_a_deliberately_wrong_total():
    rows = dict(M.totals_rows(_spec(totals_override={"Total EUR": "999.99"}), M.compute(_spec())))
    assert rows["Total EUR"] == "999.99" and rows["Subtotal"] == "461.00"


def test_totals_override_with_unknown_label_is_refused():
    with pytest.raises(M.SpecError, match="Grand total"):
        M.totals_rows(_spec(totals_override={"Grand total": "1"}), M.compute(_spec()))


def _assert_valid_pdf(pdf: bytes):
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    startxref = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
    assert pdf[startxref:].startswith(b"xref")
    offsets = [int(o) for o in re.findall(rb"^(\d{10}) 00000 n $", pdf, re.M)]
    for num, off in enumerate(offsets, 1):  # every xref entry points at its own object
        assert pdf[off:].startswith(b"%d 0 obj" % num)


def test_render_happy_path_writes_valid_pdf_and_expected_values(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec()))
    out = tmp_path / "inv.pdf"
    assert M.main(["x", str(spec_path), str(out)]) == 0
    pdf = out.read_bytes()
    _assert_valid_pdf(pdf)
    assert b"TEST DOCUMENT" in pdf and b"(TEST-2026-0001)" in pdf
    expected = json.loads(out.with_suffix(".expected.json").read_text())
    assert expected["fields"]["PO number"] == "4500000001"
    assert expected["printed_totals"]["Total EUR"] == "543.19"


def test_many_lines_break_onto_new_pages_with_valid_structure():
    lines = [{"description": f"Item {i}", "quantity": 1, "unit_price": "1", "tax_rate": 0} for i in range(120)]
    pdf, _ = M.render(_spec(lines=lines))
    _assert_valid_pdf(pdf)
    assert pdf.count(b"/Type /Page ") >= 3
    assert pdf.count(b"TEST DOCUMENT") == 2 * pdf.count(b"/Type /Page ")  # mark on top and bottom of every page


def test_special_characters_are_escaped_and_unencodable_ones_replaced():
    assert M.pdf_text("a (b) \\ c") == b"a \\(b\\) \\\\ c"
    assert M.pdf_text("Müller") == "Müller".encode("cp1252")
    assert M.pdf_text("Ř") == b"?"


def test_bad_spec_exits_2_without_writing(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"lines": [{"quantity": 1}]}))
    out = tmp_path / "inv.pdf"
    assert M.main(["x", str(spec_path), str(out)]) == 2
    assert not out.exists()
