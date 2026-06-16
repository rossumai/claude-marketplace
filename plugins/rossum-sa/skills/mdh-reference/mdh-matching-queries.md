# MDH Matching Query Guide

Complete reference for building MongoDB aggregation queries in Rossum Master Data Hub matching configurations. Use this when creating or modifying MDH hook configurations that match extracted document data against master data records.

---

## Configuration Schema

### Top-Level Structure

```json
{
  "name": "Human-readable config name",
  "source": { },
  "mapping": { },
  "additional_mappings": [ ],
  "result_actions": { },
  "default": { },
  "action_condition": "Python-like expression, e.g. \"True\"",
  "queue_ids": [ ]
}
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `source` | object | yes | Dataset and query logic |
| `mapping` | object | yes | Winner-to-document field mapping |
| `additional_mappings` | array | no | Extra fields populated from the winner record |
| `result_actions` | object | yes | UI behavior by match count |
| `default` | object | no | Fallback value when no match found |
| `action_condition` | string | no | Python-like condition expression |
| `queue_ids` | array | no | Queue IDs where this config is active |

### `source`

```json
"source": {
  "dataset": "collection_name",
  "queries": [
    { "aggregate": [ /* pipeline stages */ ] },
    { "aggregate": [ /* fallback pipeline */ ] }
  ]
}
```

- `dataset`: MDH dataset/collection identifier.
- `queries`: Ordered array of query objects. Execution stops after the first query that returns a valid result.
- Each query object **must** contain `aggregate` (MongoDB aggregation pipeline). Always use `aggregate`-only queries.

### `mapping`

```json
"mapping": {
  "target_schema_id": "vendor_match",
  "dataset_key": "internal_id",
  "label_template": "{\"name\"} - {\"city\"}"
}
```

- `target_schema_id`: Rossum field (enum type) that stores the result.
- `dataset_key`: Dataset field used as the technical key value.
- `label_template`: UI display format. Use escaped double quotes around field names.
- `label_keys`: Legacy alternative to `label_template`.

### `additional_mappings`

Populate multiple Rossum fields from one winner record:

```json
"additional_mappings": [
  { "dataset_key": "name", "target_schema_id": "vendor_name_match" },
  { "dataset_key": "VAT_CODE", "target_schema_id": "vat_code_supplier" }
]
```

### `result_actions`

```json
"result_actions": {
  "no_match_found": {
    "select": "default",
    "message": { "type": "error", "content": "No match found" }
  },
  "one_match_found": {
    "select": "best_match"
  },
  "multiple_matches_found": {
    "select": "best_match",
    "message": { "type": "warning", "content": "Multiple matches found" }
  }
}
```

- `select`: `"best_match"`, `"best"`, or `"default"`
- `message.type`: `"error"`, `"warning"`, or `"info"`

### Placeholders and Filters

Values from extracted document fields are injected via placeholders:

| Syntax | Description |
|--------|-------------|
| `{schema_id}` | Basic placeholder — replaced with the extracted field value |
| `{schema_id \| re}` | Regex-safe — escapes special characters for `$regex` |
| `{schema_id \| split(' ')}` | Split — turns string into array of words |
| `{secrets.api_key}` | Secret reference — accesses stored credentials |

Schema IDs come from queue schema fields where `category` is `"datapoint"`. Only the `id` value of datapoint-category fields should be used as placeholders.

#### Placeholder field type restrictions

MDH placeholders resolve to **stringifiable** values. The following types are **not supported** as placeholder sources and will fail the query with:

```
Error in configuration: Matching using a Rossum field of type 'DatapointType.<TYPE>' is not supported
```

| Schema `type` | Supported as `{placeholder}`? | Workaround if it isn't |
|---|---|---|
| `string` | ✅ | — |
| `number` | ✅ | — |
| `enum` | ✅ (the value's `id`) | — |
| `date` | ❌ **NOT supported** | Add a string formula proxy (see below) |
| `button` | ❌ | n/a (no value to inject) |

##### Workaround: string formula proxy for date fields

If you need to feed a date value into an MDH query (for example, a duplicate-detection window keyed off `date_issue`), add a hidden string formula field that materialises the ISO string from the date field, and point the query at the proxy:

```jsonc
// schema.json — add alongside the date field
{
  "id": "date_issue_iso",
  "label": "Date issue (ISO string for MDH)",
  "category": "datapoint",
  "type": "string",
  "hidden": true,
  "disable_prediction": true,
  "ui_configuration": {"type": "formula", "edit": "disabled"},
  "formula": "def to_iso(d):\n    if d is None:\n        return ''\n    if isinstance(d, datetime.datetime):\n        d = d.date()\n    return d.strftime('%Y-%m-%d')\n\nto_iso(field.date_issue)"
}
```

Then in the MDH query:

```jsonc
// ❌ errors: 'DatapointType.DATE is not supported'
{"$dateFromString": {"dateString": "{date_issue}"}}

// ✅ works
{"$dateFromString": {"dateString": "{date_issue_iso}"}}
```

The proxy is `hidden: true` so reviewers don't see it; it exists purely to feed MDH.

##### Annotation-side duplicate detection still works on date fields

This restriction only affects **MDH placeholders** (the `{schema_id}` syntax that interpolates the value into a MongoDB query against an MDH dataset). Querying annotations via Rossum's built-in MongoDB index (e.g. `{"field.date_issue.date": {"$gte": ..., "$lte": ...}}` from a custom hook) reads the date directly and does NOT need a string proxy.

#### Placeholder value types — check the schema first

> **Before you "fix" an MDH query that looks buggy: STOP.** If a placeholder comparison appears wrong (e.g. a quoted placeholder against a numeric key), do **not** rewrite it on assumption. First (1) read the source field's `type` / `enum_value_type` in the queue `schema.json` to learn the **actual** injected JSON type, and (2) verify any change against a real hook run (or the MDH **Try** button) before deploying. The original query is often correct, and an unverified "fix" — adding `$toString`, re-quoting — typically *introduces* a silent zero-match bug rather than removing one. This is a real failure mode that has shipped to customers.

A `{placeholder}`'s effective **JSON type** in the comparison follows the **source** schema field, and the dataset key you compare it against must be the **same JSON type** (number vs. string). Comparing across types silently returns **zero matches** — no error, the hook log reads `status: completed`.

The injected type depends on the source field's `type`, and for enums on its **`enum_value_type`**:

| Source field | Injected type |
|---|---|
| `type: "string"` | string |
| `type: "number"` | number |
| `type: "enum"`, `enum_value_type: "string"` (the default) | string |
| `type: "enum"`, `enum_value_type: "number"` | number |

`enum_value_type` is the same hint that controls how an MDH-matched value is *stored* in an enum result field (see "Numeric enum results" in `mdh-api-reference.md`) — and a value stored as a number is injected as a number when that field is used as a placeholder.

**Worked example (a real bug).** Field `sender_match` is `type: "enum"` with `enum_value_type: "number"`; the dataset's `supplier.id` is numeric:

```jsonc
// ✅ correct — the number enum injects a number, compared to a numeric key
{"supplier.id": "{sender_match}"}

// ❌ silently matches nothing — coerces the numeric key to a string and
//    compares it against the (still numeric) injected value
{"$eq": [{"$toString": "$supplier.id"}, "{sender_match}"]}
```

Note that in this case the `number` enum was injected as a number **even though the placeholder was written quoted** (`"{sender_match}"`). Do not rely on quoting to control the injected type — verify it (see below) rather than assume.

**Before writing or changing any placeholder comparison:** read the field's definition in the queue `schema.json` (its `type`, and for enums its `enum_value_type`) and confirm it matches the JSON type of the dataset key it is compared against. Never add `$toString` / `$toDouble` or re-quote a placeholder to "fix" a mismatch before you have confirmed the real injected type — the coercion usually *creates* the mismatch rather than removing it.

> **Don't "validate" substitution with a hand-typed literal.** Running `data_storage_aggregate` with a literal you typed yourself (e.g. `"9"` as a string) does **not** replicate MDH's type-aware placeholder injection — it tests your *guess* about the type, not what MDH actually sends. Confirm against the queue schema and a real hook run, or the MDH **Try** button (which substitutes real field values).

---

## Query Design Rules

### Matching Order (mandatory)

1. **Exact identifiers first** — VAT/tax ID, normalized PO reference, ERP IDs
2. **Exact reference combinations second** — supplier + order reference
3. **Fuzzy search last** — name/address/description combinations

### DO

1. Prefer exact matching before fuzzy matching. Never do exact matching on names/addresses — use fuzzy for those.
2. **Run the Atlas Search Index Pre-flight before shipping any `$search` query.** Confirm the named index exists on the collection and that every field referenced in the `$search` stage is mapped in the index. A missing or misnamed index makes `$search` return zero results silently — no error, hook log reads `status: completed`. See [Atlas Search Index Pre-flight](#atlas-search-index-pre-flight-run-before-shipping-any-search-query).
3. When `$search` is used:
   - Always follow with `$limit` (default 20 unless use case requires otherwise).
   - Capture score via `$addFields: { "__score": { "$meta": "searchScore" } }`.
   - Filter low-confidence matches with a score threshold.
   - Combine multiple strategies: use `phrase` and `text` searches with appropriate `slop` and `fuzzy` settings.
4. In fuzzy search, combine relevant parameters in `compound` queries using `must`, `should`, and `filter`.
5. Boost only `must` clauses in compound search — never boost `should`.
6. If regex behavior is needed, use `$search` with `regex` operator and a keyword analyzer index.
7. Use `$project` to return only attributes needed for document mapping.
8. Use JSON-compatible syntax with double-quoted keys and string values.
9. Place `$match` or `$search` early to reduce the candidate set quickly.
10. Keep pipelines deterministic.

### DON'T

1. Do not convert data types for matching logic (exception: after `$match` has already reduced the dataset significantly).
2. Do not use case-insensitive regex (`$regex` with `"$options": "i"`).
3. Do not use unanchored case-sensitive regex.
4. Do not use `$facet` — use sequential queries or `$unionWith` instead.
5. Do not use deprecated or JavaScript operators (`$function`, `$where`).
6. Do not use `$expr` with nested `$and` / `$or`.
7. Never deploy configuration to remote without user confirmation.
8. Default result window is 20 for fuzzy/search stages. Runtime guardrail cap is 50 records for interactive previews.
9. Never dump full datasets in user-facing responses.
10. Do not rely on key order in multi-key `$sort` stages without checking key lengths. Rossum's JSON serialization sorts object keys by key length (shortest first), which silently reorders `$sort` keys and changes sort priority. Always ensure the primary sort key is shorter than (or equal in length to) secondary keys. Example: `{"__priority": 1, "id.poLineId": 1}` works because `__priority` (12 chars) < `id.poLineId` (12 chars — tied, so original order is preserved). If the primary key is longer, rename it with a shorter alias (prefix with `__`).
11. Don't assume a placeholder's JSON type. Check the source field's `type` / `enum_value_type` in the queue `schema.json`: a `number` enum (or `number` field) injects a number, a `string` enum/field injects a string. Comparing across types silently returns zero matches. See [Placeholder value types — check the schema first](#placeholder-value-types--check-the-schema-first).
12. Don't validate MDH substitution by running `data_storage_aggregate` with a hand-typed literal — that bypasses MDH's type-aware injection and only tests your guess about the type. Verify against the schema and/or a real hook run (or the MDH "Try" button).

---

## Score Normalization Pattern

When fuzzy matching by name or address, raw `searchScore` can vary widely. Use length-ratio normalization to penalize matches where the candidate is much longer or shorter than the query:

```json
{
  "$addFields": {
    "__score": { "$meta": "searchScore" }
  }
},
{
  "$addFields": {
    "__new_score": {
      "$divide": [
        "$__score",
        {
          "$add": [
            1,
            {
              "$abs": {
                "$subtract": [
                  1,
                  { "$divide": [
                    { "$strLenCP": "$FIELD_NAME" },
                    { "$strLenCP": "{placeholder_value}" }
                  ]}
                ]
              }
            }
          ]
        }
      ]
    }
  }
},
{
  "$addFields": {
    "__normalized_score": {
      "$divide": [
        "$__new_score",
        { "$add": [1, "$__new_score"] }
      ]
    }
  }
},
{ "$sort": { "__normalized_score": -1 } },
{ "$match": { "__normalized_score": { "$gt": 0.8 } } }
```

- The `__new_score` divides raw score by the length ratio deviation, penalizing mismatched lengths.
- The `__normalized_score` applies a sigmoid-like normalization to bound values between 0 and 1.
- Threshold `0.8` is typical for name-only matching; use `0.9` when combining name + address.

---

## Unique-Result Filter Pattern (`$setWindowFields`)

Use `$setWindowFields` to count matches and conditionally filter. This is useful for ensuring only single-match results are returned (auto-select) or for combining exact matches with a "please select" default record:

```json
{
  "$setWindowFields": {
    "output": {
      "__mainMatch": { "$count": {} }
    }
  }
},
{
  "$match": { "__mainMatch": 1 }
}
```

This keeps results only when exactly one record matched — useful for auto-selecting exact matches.

---

## GL Coding / Dropdown Pre-selection Pattern

When all options should be shown but the best match should be pre-selected:

1. Exact match the target value
2. Count matches with `$setWindowFields`
3. `$unionWith` a synthetic "Please select" empty record
4. Count again to detect whether exact match existed
5. If exact match exists, remove the empty placeholder
6. `$unionWith` all remaining records from the collection
7. Use `multiple_matches_found: { select: "best_match" }` — exact match sits on top

This pattern ensures: if exact match found, it's pre-selected; otherwise, the empty placeholder forces user selection.

---

## Query Examples

### Example 1: Supplier Match — VAT First, Name Fallback

**Scenario:** VAT is the strongest key. Supplier name is fuzzy fallback.

```json
{
  "source": {
    "dataset": "vendors_master_list",
    "queries": [
      {
        "aggregate": [
          {
            "$match": {
              "vatin": "{sender_vat}",
              "status": "active"
            }
          },
          {
            "$project": {
              "_id": 0, "internal_id": 1, "name": 1, "city": 1, "vatin": 1
            }
          }
        ]
      },
      {
        "aggregate": [
          {
            "$search": {
              "index": "vendor_name_idx",
              "compound": {
                "must": [
                  {
                    "phrase": {
                      "path": "name",
                      "query": "{sender_name}",
                      "slop": 1,
                      "score": { "boost": { "value": 3 } }
                    }
                  }
                ],
                "filter": [
                  { "equals": { "path": "status", "value": "active" } }
                ]
              }
            }
          },
          { "$addFields": { "__score": { "$meta": "searchScore" } } },
          { "$match": { "__score": { "$gte": 7 } } },
          { "$limit": 20 },
          {
            "$project": {
              "_id": 0, "internal_id": 1, "name": 1, "city": 1, "vatin": 1, "__score": 1
            }
          }
        ]
      }
    ]
  }
}
```

**Why this order:**
- VAT exact match gives highest precision with lowest false positives.
- Name fallback is fuzzy and score-filtered, reducing weak matches.

### Example 2: PO Match — Exact Reference + Fuzzy Fallback

**Scenario:** PO reference can be noisy. First try normalized reference, then compound fuzzy on supplier and reference text.

```json
{
  "source": {
    "dataset": "purchase_orders",
    "queries": [
      {
        "aggregate": [
          {
            "$match": {
              "order_id_normalized": "{order_id_normalized}",
              "supplier_id": "{supplier_id}",
              "status": "open"
            }
          },
          {
            "$project": {
              "_id": 0, "po_internal_id": 1, "order_id_normalized": 1,
              "supplier_id": 1, "currency": 1
            }
          }
        ]
      },
      {
        "aggregate": [
          {
            "$search": {
              "index": "po_search_idx",
              "compound": {
                "must": [
                  {
                    "text": {
                      "path": "supplier_name",
                      "query": "{supplier_name}",
                      "fuzzy": { "maxEdits": 1, "prefixLength": 2 },
                      "score": { "boost": { "value": 2 } }
                    }
                  }
                ],
                "should": [
                  {
                    "phrase": {
                      "path": "order_reference",
                      "query": "{order_reference}",
                      "slop": 1
                    }
                  }
                ],
                "filter": [
                  { "equals": { "path": "status", "value": "open" } }
                ]
              }
            }
          },
          { "$addFields": { "__score": { "$meta": "searchScore" } } },
          { "$match": { "__score": { "$gte": 6 } } },
          { "$limit": 20 },
          {
            "$project": {
              "_id": 0, "po_internal_id": 1, "order_id_normalized": 1,
              "supplier_id": 1, "order_reference": 1, "__score": 1
            }
          }
        ]
      }
    ]
  }
}
```

### Example 3: Lookup-Based Delivery Address Resolution

**Scenario:** Resolve supplier by exact ID, then join delivery locations and match delivery code.

```json
{
  "source": {
    "dataset": "suppliers",
    "queries": [
      {
        "aggregate": [
          {
            "$match": {
              "supplier_id": "{supplier_id}",
              "status": "active"
            }
          },
          {
            "$lookup": {
              "from": "delivery_addresses",
              "localField": "supplier_id",
              "foreignField": "supplier_id",
              "as": "delivery_locations"
            }
          },
          { "$unwind": "$delivery_locations" },
          {
            "$match": {
              "delivery_locations.address_code": "{delivery_address_code}"
            }
          },
          {
            "$project": {
              "_id": 0, "supplier_id": 1, "supplier_name": 1,
              "delivery_code": "$delivery_locations.address_code",
              "delivery_name": "$delivery_locations.address_name",
              "delivery_city": "$delivery_locations.city"
            }
          }
        ]
      }
    ]
  }
}
```

### Example 4: Advanced Supplier Matching — Multi-Stage with Score Normalization

**Scenario:** Four-stage cascade: (1) exact VAT with non-empty guard, (2) regex search on VAT in supplier name via keyword index, (3) fuzzy name with phrase+text and normalized scoring, (4) name+address compound with higher threshold.

```json
{
  "source": {
    "dataset": "SUPPLIERS",
    "queries": [
      {
        "comment": "Stage 1: Exact VAT match with country prefix variants",
        "aggregate": [
          {
            "$match": {
              "$and": [
                { "$or": [
                  { "VAT_REG_NO": "GB{sender_vat_id_normalized}" },
                  { "VAT_REG_NO": "{sender_vat_id_normalized}" }
                ]},
                { "VAT_REG_NO": { "$ne": "" } },
                { "KCO": "{kco}" },
                { "DORMANT": false }
              ]
            }
          },
          {
            "$group": {
              "_id": "$SUPPLIER_REF",
              "name": { "$first": "$SUPPLIER_NAME" },
              "VAT_CODE": { "$first": "$VAT_CODE" }
            }
          },
          {
            "$project": {
              "id": "$_id", "name": "$name", "VAT_CODE": 1
            }
          },
          {
            "$setWindowFields": {
              "output": { "__mainMatch": { "$count": {} } }
            }
          },
          { "$match": { "__mainMatch": 1 } }
        ]
      },
      {
        "comment": "Stage 2: Regex search on VAT in supplier name (keyword index)",
        "aggregate": [
          {
            "$search": {
              "index": "default_kw",
              "regex": {
                "path": "SUPPLIER_NAME",
                "query": ".*{sender_vat_id_normalized}"
              }
            }
          },
          { "$limit": 15 },
          { "$match": { "KCO": "{kco}", "DORMANT": false } },
          {
            "$project": {
              "id": "$SUPPLIER_REF", "name": "$SUPPLIER_NAME", "VAT_CODE": 1
            }
          }
        ]
      },
      {
        "comment": "Stage 3: Fuzzy name match with phrase+text and score normalization",
        "aggregate": [
          {
            "$search": {
              "compound": {
                "filter": [
                  { "equals": { "path": "DORMANT", "value": false } },
                  { "in": { "path": "KCO", "value": ["{kco}"] } }
                ],
                "should": [
                  {
                    "phrase": {
                      "path": ["SUPPLIER_NAME"], "slop": 2,
                      "query": "{sender_name}"
                    }
                  },
                  {
                    "text": {
                      "path": ["SUPPLIER_NAME"],
                      "fuzzy": { "maxEdits": 1 },
                      "query": "{sender_name}"
                    }
                  }
                ]
              }
            }
          },
          { "$limit": 15 },
          { "$addFields": { "__score": { "$meta": "searchScore" } } },
          {
            "$addFields": {
              "__new_score": {
                "$divide": ["$__score", {
                  "$add": [1, { "$abs": { "$subtract": [1, {
                    "$divide": [
                      { "$strLenCP": "$SUPPLIER_NAME" },
                      { "$strLenCP": "{sender_name}" }
                    ]
                  }]}}]
                }]
              }
            }
          },
          {
            "$addFields": {
              "__normalized_score": {
                "$divide": ["$__new_score", { "$add": [1, "$__new_score"] }]
              }
            }
          },
          { "$sort": { "__normalized_score": -1 } },
          { "$match": { "__normalized_score": { "$gt": 0.8 } } },
          {
            "$project": {
              "id": "$SUPPLIER_REF", "name": "$SUPPLIER_NAME", "VAT_CODE": 1
            }
          }
        ]
      },
      {
        "comment": "Stage 4: Name (must) + address (should) with higher threshold",
        "aggregate": [
          {
            "$search": {
              "compound": {
                "must": [
                  {
                    "text": {
                      "path": ["SUPPLIER_NAME"],
                      "fuzzy": { "maxEdits": 1 },
                      "query": "{sender_name}",
                      "score": { "boost": { "value": 2 } }
                    }
                  }
                ],
                "filter": [
                  { "equals": { "path": "DORMANT", "value": false } },
                  { "in": { "path": "KCO", "value": ["{kco}"] } }
                ],
                "should": [
                  {
                    "text": {
                      "path": ["ADDRESS_1", "ADDRESS_2", "ADDRESS_3", "ADDRESS_4", "POSTCODE"],
                      "fuzzy": { "maxEdits": 1 },
                      "query": "{sender_address}",
                      "score": { "boost": { "value": 0.75 } }
                    }
                  }
                ]
              }
            }
          },
          { "$limit": 15 },
          { "$addFields": { "__score": { "$meta": "searchScore" } } },
          {
            "$addFields": {
              "__new_score": {
                "$divide": ["$__score", {
                  "$add": [1, { "$abs": { "$subtract": [1, {
                    "$divide": [
                      { "$strLenCP": {
                        "$concat": [
                          { "$ifNull": ["$SUPPLIER_NAME", ""] },
                          { "$ifNull": ["$ADDRESS_1", ""] },
                          { "$ifNull": ["$ADDRESS_2", ""] },
                          { "$ifNull": ["$ADDRESS_3", ""] },
                          { "$ifNull": ["$ADDRESS_4", ""] },
                          { "$ifNull": ["$POSTCODE", ""] }
                        ]
                      }},
                      { "$strLenCP": "{sender_name} {sender_address}" }
                    ]
                  }]}}]
                }]
              }
            }
          },
          {
            "$addFields": {
              "__normalized_score": {
                "$divide": ["$__new_score", { "$add": [1, "$__new_score"] }]
              }
            }
          },
          { "$sort": { "__normalized_score": -1 } },
          { "$match": { "__normalized_score": { "$gt": 0.9 } } },
          {
            "$project": {
              "id": "$SUPPLIER_REF", "name": "$SUPPLIER_NAME", "VAT_CODE": 1
            }
          }
        ]
      }
    ]
  }
}
```

**Key techniques:**
- Stage 1 uses `$setWindowFields` + `__mainMatch: 1` to auto-select only when exactly one result.
- Stage 2 uses `$search` with `regex` on a keyword index for VAT-in-name matching.
- Stages 3-4 use length-ratio score normalization to penalize mismatched candidate lengths.
- Stage 4 uses a higher threshold (0.9) because address adds more signal.
- `must` gets boosted, `should` does not (per compound search rules).

### Example 5: PO Line Item Matching with Amount Comparison

**Scenario:** Match PO line items by order number and supplier, then compare line amounts. Data type conversion is acceptable here because `$match` has already reduced the dataset.

```json
{
  "name": "PO by order number on line items",
  "source": {
    "dataset": "workday_purchase_order",
    "queries": [
      {
        "aggregate": [
          {
            "$match": {
              "Document_Number": "{item_order_id_mod}",
              "Supplier_Reference.ID.type": "Supplier_ID",
              "Supplier_Reference.ID._value_1": "{supplier_wd}"
            }
          },
          {
            "$unwind": {
              "path": "$Goods_Line_Data",
              "preserveNullAndEmptyArrays": true
            }
          },
          {
            "$match": {
              "Goods_Line_Data.Resource_Category_Reference.ID": {
                "$not": {
                  "$elemMatch": {
                    "type": "Spend_Category_ID",
                    "_value_1": "CONVERSION"
                  }
                }
              }
            }
          },
          { "$addFields": { "__convertedPrice": { "$toDecimal": "{item_total_base}" } } },
          { "$addFields": { "__convertedAmount": { "$toDecimal": "$Goods_Line_Data.Extended_Amount" } } },
          {
            "$match": {
              "$expr": { "$eq": ["$__convertedAmount", "$__convertedPrice"] }
            }
          }
        ]
      }
    ]
  },
  "action_condition": "'{supplier_invoice_any_wd}' != 'True'",
  "mapping": {
    "dataset_key": "Goods_Line_Data.Line_Number",
    "label_template": "{Document_Number} - Line: {Goods_Line_Data.Line_Number}",
    "target_schema_id": "item_order_id_wd"
  },
  "result_actions": {
    "no_match_found": {
      "select": "default",
      "message": { "type": "error", "content": "PO line match not found." }
    },
    "one_match_found": { "select": "best_match" },
    "multiple_matches_found": {
      "select": "best_match",
      "message": { "type": "warning", "content": "Multiple PO line matches found. (best match)" }
    }
  },
  "additional_mappings": [
    { "dataset_key": "Document_Number", "target_schema_id": "item_document_number_po_wd" },
    { "dataset_key": "Goods_Line_Data.Line_Number", "target_schema_id": "item_order_line_nr_wd" },
    { "dataset_key": "Goods_Line_Data.Extended_Amount", "target_schema_id": "item_order_line_amount" },
    { "dataset_key": "Goods_Line_Data.Unit_Cost", "target_schema_id": "item_po_unit_cost" }
  ]
}
```

### Example 6: GL Coding — Dropdown Pre-Selection with Full List

**Scenario:** Cost center matching. If exact match found, pre-select it at the top. Otherwise show a "Please select" placeholder. Always show all available cost centers below.

```json
{
  "name": "Cost center matching",
  "source": {
    "dataset": "workday_cost_center",
    "queries": [
      {
        "aggregate": [
          {
            "$match": {
              "Organization_Data.Organization_Code": "{item_cc_distributed}"
            }
          },
          {
            "$setWindowFields": {
              "output": { "__mainMatch": { "$count": {} } }
            }
          },
          {
            "$unionWith": {
              "pipeline": [
                {
                  "$documents": [
                    {
                      "Organization_Data": {
                        "ID": "",
                        "Organization_Code": "Please select",
                        "Organization_Name": ""
                      }
                    }
                  ]
                }
              ]
            }
          },
          {
            "$setWindowFields": {
              "output": { "__mainMatchWithDefault": { "$count": {} } }
            }
          },
          {
            "$match": {
              "$expr": {
                "$cond": {
                  "if": {
                    "$and": [
                      { "$gt": ["$__mainMatchWithDefault", "$__mainMatch"] },
                      { "$gt": ["$__mainMatchWithDefault", 1] }
                    ]
                  },
                  "then": { "$gt": ["$__mainMatch", 0] },
                  "else": { "$eq": [1, 1] }
                }
              }
            }
          },
          {
            "$unionWith": {
              "coll": "workday_cost_center",
              "pipeline": [
                {
                  "$match": {
                    "Organization_Data.Organization_Code": {
                      "$ne": "{item_cc_distributed}"
                    }
                  }
                }
              ]
            }
          },
          {
            "$match": {
              "Organization_Data.Organization_Active": true
            }
          },
          {
            "$project": {
              "Organization_Data.ID": 1,
              "Organization_Data.Organization_Code": 1,
              "Organization_Data.Organization_Name": 1
            }
          }
        ]
      }
    ]
  },
  "mapping": {
    "dataset_key": "Organization_Data.ID",
    "label_template": "{Organization_Data.Organization_Code} {Organization_Data.Organization_Name}",
    "target_schema_id": "item_cost_center_wd"
  },
  "result_actions": {
    "no_match_found": {
      "select": "default",
      "message": { "type": "error", "content": "Cost Center match not found." }
    },
    "one_match_found": { "select": "best_match" },
    "multiple_matches_found": { "select": "best_match" }
  }
}
```

**Key technique:** The double `$setWindowFields` + `$cond` logic removes the "Please select" placeholder only when an exact match exists. Combined with `multiple_matches_found: best_match`, the exact match auto-selects when found; otherwise the empty placeholder is selected, forcing user choice.

---

## Atlas Search Index Pre-flight (run before shipping any `$search` query)

A `$search` stage against a missing or misnamed Atlas Search index does **not** error out. It returns zero results, and the hook log shows `status: completed`. A query that "works" in your editor will silently return nothing in production, and the failure mode looks like "no matches found" rather than "broken query" — the hardest class of MDH bug to diagnose. This historically cost real time on Eurofins and other deployments; the pre-flight closes it at build time.

**Run this every time you author, modify, or debug a query that contains `$search`.** It is two API calls and prevents hours of downstream debugging.

### Step 1: List the search indexes on the collection

```
data_storage_list_search_indexes(collection_name="<your-collection>")
```

Confirm each `index` name referenced in your `$search` stages appears in the response. Index names are case-sensitive. Typos (`vendor_name_idx` vs `vendor_name_index`) silently fall through.

### Step 2: Verify field mappings

For each named index, inspect its `mappings.fields` (returned by the same `data_storage_list_search_indexes` call):

- Every field you reference in `$search` (under `path`, `compound.must.path`, `compound.should.path`, etc.) **must** appear in `mappings.fields`.
- Field names are case-sensitive — `Supplier_Name` and `SUPPLIER_NAME` are different indexes.
- For `$search` with `regex`, the field must be mapped with a keyword analyzer (`"analyzer": "lucene.keyword"`). The default analyzer will silently no-op on regex queries.
- For `text` and `phrase` queries, the default analyzer is usually correct — but verify rather than assume.

### Step 3: If anything is missing, create or update the index

If the index doesn't exist or the field mapping is wrong, use `data_storage_create_search_index` to create one with the right mappings, or drop and recreate (`data_storage_drop_search_index` + `data_storage_create_search_index`) if the existing one is wrong. Creation is asynchronous — wait until the index status is `READY` before re-running the query.

### Step 4: Smoke-test the query

Run the full aggregation pipeline against the collection with a known-good input. Confirm non-empty results. If results are empty:

- Re-check that the index analyzer matches the query type (`regex` needs keyword; `text`/`phrase` needs default).
- Re-check that the field path in the query matches the indexed field name exactly (case + spelling).
- Try the same query without `$search` (substitute `$match` on the same field) to verify the data exists.
- Try `$search` with `compound.should` only and no threshold to see the raw hit set.

### When Atlas Search shouldn't be used at all

If you only ever do exact matching, no index is needed — use `$match` directly. `$search` is for fuzzy/scored matching. Don't pull in `$search` just because it's there.

---

## Atlas Search Index Recommendations

When using `$search` with `regex`, create a keyword analyzer index:

```json
{
  "mappings": {
    "fields": {
      "SUPPLIER_NAME": { "type": "string", "analyzer": "lucene.keyword" },
      "VAT_REG_NO": { "type": "string", "analyzer": "lucene.keyword" }
    }
  }
}
```

For `text` and `phrase` queries, the default analyzer is usually sufficient. Create a named index (e.g., `vendor_name_idx`, `po_search_idx`) and reference it in the `$search` stage.

---

## Required Input from Solution Architect

Before building a matching configuration, gather:

1. **Base URL and Bearer token** for MDH API authentication
2. **Matching entity** — supplier, purchase order, delivery address, product, GL code, etc.
3. **MDH collection name**
4. **Schema IDs** to use as query placeholders (from the queue schema, `category: "datapoint"` fields only)
5. **Attributes to return** from MongoDB — used in `$project` stages
6. **Optional lookup details** — collection name, local/foreign keys, attributes from joined collection

## Output Requirements

When generating a matching configuration, always provide:

1. **Complete JSON configuration** — ready to deploy
2. **Technical explanation** — matching order rationale, fuzzy logic, tuning points, and score thresholds
