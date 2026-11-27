# Test invoice spec format

Input for `make_test_invoice.py`. A JSON object; every key is optional except `lines[].description`.
[example-spec.json](example-spec.json) is a complete example.

| Key | Type | Default | Printed as |
|---|---|---|---|
| `title` | string | `"INVOICE"` | Large heading (e.g. `"CREDIT NOTE"`, `"PROFORMA INVOICE"`) |
| `currency` | string | `""` | Appended to the total label: `Total EUR` |
| `number_format` | `"en"` · `"eu"` · `"plain"` | `"en"` | `1,234.56` · `1.234,56` · `1234.56` |
| `sender` | list of strings | `[]` | "From" block, one line each (name, address, `VAT ID: …`, IBAN…) |
| `recipient` | list of strings | `[]` | "Bill to" block |
| `fields` | list of `[label, value]` | `[]` | Header rows `Label: value`, in this order |
| `lines` | list of objects | `[]` | Line-item table (below) |
| `totals_override` | object `{label: value}` | `{}` | Replaces a printed totals value by its label |
| `notes` | list of strings | `[]` | Free text under the totals (bank details, terms) |

**`lines[]`:** `description` (required), `code`, `quantity` (default 1), `unit_price` (default 0),
`tax_rate` in percent (default 0). Numbers may be JSON numbers or strings; use strings for money
(`"86.50"`) to keep exact decimals. `amount` = quantity × unit price, rounded half-up to cents.

**Totals rows** (labels you can target with `totals_override`): `Subtotal`, one
`VAT <rate>% of <base>` row per tax rate, then `Total <currency>`. An unknown label is refused with
the list of real labels.

## Output

`out.pdf` plus `out.expected.json`:

```json
{
  "fields": {"Invoice number": "TEST-2026-0001", "PO number": "4500000001"},
  "sender": ["…"], "recipient": ["…"],
  "lines": [{"code": "NW-100", "description": "…", "quantity": "200", "unit_price": "0.35", "tax_rate": "19", "amount": "70.00"}],
  "computed": {"subtotal": "461.00", "total": "543.19", "taxes": [{"rate": "7", "base": "45.00", "tax": "3.15"}]},
  "printed_totals": {"Subtotal": "461.00", "VAT 7% of 45.00": "3.15", "Total EUR": "543.19"}
}
```

`computed` is the correct math; `printed_totals` is what is on the page — they differ only where
`totals_override` was used.
