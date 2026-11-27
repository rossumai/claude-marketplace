# MongoDB Query Reference for Rossum Master Data Hub

## Contents

- Query Types
- Find Query Operators
- Regex Patterns
- Aggregation Pipeline
- `$function` — Custom JavaScript
- `$lookup` — Cross-Collection Joins
- `$unionWith` — Combine Multiple Collections
- Atlas Search (`$search`)
- Practical Rossum Matching Patterns
- Data Type Handling
- Performance Tips
- Debugging Queries

MongoDB query syntax reference for building MDH matching configurations. For how matching works (cascade model, placeholders, pipe modifiers, result actions), see [mdh-api-reference.md](../mdh-reference/mdh-api-reference.md) and [mdh-matching-queries.md](../mdh-reference/mdh-matching-queries.md). This file covers the MongoDB operators, stages, and patterns available in queries.

---

## Query Types

Rossum supports two MongoDB query types:

### 1. Find Queries

Simple document matching. Returns all documents that match the filter criteria.

```json
{ "find": { <filter> } }
```

### 2. Aggregate Pipelines

Multi-stage document processing pipelines for complex matching logic — fuzzy search, scoring, joining collections, JavaScript transformations.

```json
{ "aggregate": [ <stage1>, <stage2>, ... ] }
```

### 3. HTTP Requests

External API calls for matching against third-party systems (not MongoDB, but supported by the matching engine).

```json
{ "http": { "url": "...", "method": "POST", "headers": {...}, "body": {...}, "result_path": "..." } }
```

---

## Find Query Operators

### Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$eq` | Equal to (implicit when using `{ field: value }`) | `{ "status": { "$eq": "active" } }` |
| `$ne` | Not equal to | `{ "status": { "$ne": "inactive" } }` |
| `$gt` | Greater than | `{ "amount": { "$gt": 1000 } }` |
| `$gte` | Greater than or equal to | `{ "amount": { "$gte": 1000 } }` |
| `$lt` | Less than | `{ "amount": { "$lt": 5000 } }` |
| `$lte` | Less than or equal to | `{ "amount": { "$lte": 5000 } }` |
| `$in` | Matches any value in array | `{ "country": { "$in": ["CZ", "SK", "DE"] } }` |
| `$nin` | Matches none of the values in array | `{ "country": { "$nin": ["US", "UK"] } }` |

### Logical Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$and` | All conditions must match (implicit for multiple fields) | `{ "$and": [ { "a": 1 }, { "b": 2 } ] }` |
| `$or` | At least one condition must match | `{ "$or": [ { "vat": "{sender_vat}" }, { "tax_id": "{sender_vat}" } ] }` |
| `$not` | Inverts the effect of a query expression | `{ "status": { "$not": { "$eq": "deleted" } } }` |
| `$nor` | None of the conditions must match | `{ "$nor": [ { "status": "deleted" }, { "status": "archived" } ] }` |

### Element Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$exists` | Field exists (or not) in document | `{ "email": { "$exists": true } }` |
| `$type` | Field is of specified BSON type | `{ "zip": { "$type": "string" } }` |

### Evaluation Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$regex` | Regular expression match | `{ "name": { "$regex": "pattern", "$options": "i" } }` |
| `$expr` | Use aggregation expressions in find | `{ "$expr": { "$gt": ["$amount", "$threshold"] } }` |
| `$mod` | Modulo operation | `{ "qty": { "$mod": [4, 0] } }` |
| `$text` | Full-text search (requires text index) | `{ "$text": { "$search": "rossum" } }` |
| `$where` | JavaScript expression (slow, avoid) | `{ "$where": "this.a > this.b" }` |

### Array Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `$all` | Array contains all specified elements | `{ "tags": { "$all": ["invoice", "verified"] } }` |
| `$elemMatch` | Array element matches all conditions | `{ "items": { "$elemMatch": { "qty": { "$gt": 5 } } } }` |
| `$size` | Array has exact number of elements | `{ "items": { "$size": 3 } }` |

---

## Regex Patterns

Regex is critical for flexible matching in the Master Data Hub. The `$regex` operator supports Perl-compatible regular expressions (PCRE).

### Syntax

```json
{ "field": { "$regex": "pattern", "$options": "flags" } }
```

### Flags (`$options`)

| Flag | Description |
|------|-------------|
| `i` | Case-insensitive matching |
| `m` | Multiline — `^` and `$` match line boundaries |
| `s` | Dot (`.`) matches newline characters |
| `x` | Extended — ignores whitespace and allows comments |

### Common Patterns for Rossum

**Exact match, case-insensitive:**
```json
{ "find": { "vendor_name": { "$regex": "^{sender_name | re}$", "$options": "i" } } }
```

**Substring match (contains):**
```json
{ "find": { "description": { "$regex": "{item_description | re}", "$options": "i" } } }
```

**Starts with:**
```json
{ "find": { "code": { "$regex": "^{item_code | re}", "$options": "i" } } }
```

**Match with optional whitespace/punctuation:**
```json
{ "find": { "vat_number": { "$regex": "^\\s*{sender_vat | re}\\s*$" } } }
```

**Match either of two fields in the dataset:**
```json
{
  "find": {
    "$or": [
      { "vat_number": { "$regex": "^{sender_vat | re}$", "$options": "i" } },
      { "tax_id": { "$regex": "^{sender_vat | re}$", "$options": "i" } }
    ]
  }
}
```

> **Important**: Always use the `| re` (or `| regex`) pipe modifier when inserting schema values into `$regex` patterns. This escapes special regex characters (`.`, `*`, `+`, `(`, `)`, etc.) in the extracted value to prevent query errors or unintended matches.

---

## Aggregation Pipeline

Aggregation pipelines process documents through a sequence of stages. Each stage transforms the documents as they pass through.

```json
{ "aggregate": [ <stage1>, <stage2>, <stage3>, ... ] }
```

### Pipeline Stages

#### Filtering & Matching

| Stage | Description |
|-------|-------------|
| `$match` | Filter documents (same syntax as find queries). Place early for performance. |
| `$limit` | Restrict output to first N documents. |
| `$skip` | Skip first N documents. |
| `$sample` | Randomly select N documents. |

#### Reshaping

| Stage | Description |
|-------|-------------|
| `$project` | Include, exclude, rename fields, or compute new fields. |
| `$addFields` / `$set` | Add new fields or overwrite existing ones (keeping all other fields). |
| `$unset` | Remove specified fields. |
| `$replaceRoot` / `$replaceWith` | Replace the document with a specified embedded document or expression. |

#### Grouping & Sorting

| Stage | Description |
|-------|-------------|
| `$group` | Group documents by key and apply accumulators (`$sum`, `$avg`, `$first`, `$last`, `$push`, `$addToSet`, `$min`, `$max`, `$count`). |
| `$sort` | Sort documents. `1` ascending, `-1` descending. |
| `$sortByCount` | Group by value and sort by count descending. |
| `$count` | Count documents and output as a named field. |

#### Joining & Combining

| Stage | Description |
|-------|-------------|
| `$lookup` | Left outer join with another collection. |
| `$unionWith` | Combine results from multiple collections (union). |
| `$graphLookup` | Recursive lookup for tree/graph structures. |

#### Array Processing

| Stage | Description |
|-------|-------------|
| `$unwind` | Deconstruct an array field into one document per element. |

#### Windowing

| Stage | Description |
|-------|-------------|
| `$setWindowFields` | Apply window functions (running totals, ranks, moving averages) across a partition. |

#### Branching

| Stage | Description |
|-------|-------------|
| `$facet` | Run multiple sub-pipelines in parallel on the same input documents. |
| `$bucket` | Group documents into buckets by value ranges. |
| `$bucketAuto` | Automatically determine bucket boundaries. |

#### Output

| Stage | Description |
|-------|-------------|
| `$out` | Write results to a collection (replaces if exists). |
| `$merge` | Write results to a collection with merge/update options. |

---

### Expression Operators (used within pipeline stages)

#### Arithmetic

| Operator | Description |
|----------|-------------|
| `$add` | Add numbers or add to date |
| `$subtract` | Subtract numbers or subtract from date |
| `$multiply` | Multiply numbers |
| `$divide` | Divide numbers |
| `$mod` | Modulo |
| `$abs` | Absolute value |
| `$ceil` | Ceiling (round up) |
| `$floor` | Floor (round down) |
| `$round` | Round to specified decimal places |
| `$sqrt` | Square root |
| `$pow` | Raise to power |
| `$log` | Logarithm |
| `$log10` | Base-10 logarithm |
| `$ln` | Natural logarithm |
| `$exp` | Euler's number raised to power |
| `$trunc` | Truncate to integer |

#### String

| Operator | Description |
|----------|-------------|
| `$concat` | Concatenate strings |
| `$substr` / `$substrBytes` / `$substrCP` | Substring extraction |
| `$toLower` | Convert to lowercase |
| `$toUpper` | Convert to uppercase |
| `$trim` / `$ltrim` / `$rtrim` | Trim whitespace or characters |
| `$split` | Split string into array |
| `$strLenBytes` / `$strLenCP` | String length |
| `$indexOfBytes` / `$indexOfCP` | Find substring position |
| `$regexFind` / `$regexFindAll` / `$regexMatch` | Regex operations in expressions |
| `$replaceOne` / `$replaceAll` | String replacement |
| `$toString` | Convert to string |

#### Array

| Operator | Description |
|----------|-------------|
| `$arrayElemAt` | Element at index |
| `$arrayToObject` | Convert array of key-value pairs to object |
| `$concatArrays` | Concatenate arrays |
| `$filter` | Filter array elements by condition |
| `$first` / `$last` | First/last element |
| `$in` | Check if value is in array |
| `$indexOfArray` | Find element position |
| `$isArray` | Check if value is array |
| `$map` | Transform each array element |
| `$objectToArray` | Convert object to array of key-value pairs |
| `$range` | Generate integer array |
| `$reduce` | Reduce array to single value |
| `$reverseArray` | Reverse array |
| `$size` | Array length |
| `$slice` | Subset of array |
| `$zip` | Merge arrays element-wise |

#### Date

| Operator | Description |
|----------|-------------|
| `$dateFromString` | Parse date string |
| `$dateToString` | Format date as string |
| `$dateFromParts` | Construct date from parts |
| `$dateToParts` | Decompose date to parts |
| `$year` / `$month` / `$dayOfMonth` | Extract date components |
| `$hour` / `$minute` / `$second` / `$millisecond` | Extract time components |
| `$dayOfWeek` / `$dayOfYear` | Day positions |
| `$isoWeek` / `$isoWeekYear` / `$isoDayOfWeek` | ISO date components |
| `$dateAdd` / `$dateSubtract` / `$dateDiff` | Date arithmetic |
| `$toDate` | Convert to date |

#### Comparison (in expressions)

| Operator | Description |
|----------|-------------|
| `$cmp` | Compare two values (-1, 0, 1) |
| `$eq` / `$ne` | Equal / not equal |
| `$gt` / `$gte` | Greater than / greater than or equal |
| `$lt` / `$lte` | Less than / less than or equal |

#### Conditional

| Operator | Description |
|----------|-------------|
| `$cond` | If-then-else: `{ "$cond": { "if": expr, "then": val1, "else": val2 } }` |
| `$ifNull` | Return first non-null value: `{ "$ifNull": [expr, fallback] }` |
| `$switch` | Multi-branch conditional (like switch/case) |

#### Type

| Operator | Description |
|----------|-------------|
| `$type` | Return BSON type string |
| `$convert` | Convert between types with error handling |
| `$toBool` / `$toInt` / `$toLong` / `$toDouble` / `$toDecimal` / `$toString` / `$toDate` / `$toObjectId` | Type conversion shortcuts |
| `$isNumber` | Check if value is numeric |

#### Set (for array-as-set operations)

| Operator | Description |
|----------|-------------|
| `$setEquals` | Sets are equal |
| `$setIntersection` | Common elements |
| `$setUnion` | All elements from all sets |
| `$setDifference` | Elements in first set not in second |
| `$setIsSubset` | First set is subset of second |
| `$anyElementTrue` / `$allElementsTrue` | Boolean checks on array |

#### Object

| Operator | Description |
|----------|-------------|
| `$mergeObjects` | Merge multiple objects into one |
| `$getField` | Get field value by name (supports names with dots/dollars) |
| `$setField` | Set field value by name |

#### Accumulators (used in `$group` and `$setWindowFields`)

| Operator | Description |
|----------|-------------|
| `$sum` | Sum of values |
| `$avg` | Average |
| `$min` / `$max` | Minimum / maximum |
| `$first` / `$last` | First / last value in group |
| `$push` | Collect all values into array |
| `$addToSet` | Collect unique values into array |
| `$count` | Count of documents |
| `$stdDevPop` / `$stdDevSamp` | Standard deviation |

#### Special

| Operator | Description |
|----------|-------------|
| `$meta` | Access metadata (e.g., text search score, Atlas Search score) |
| `$literal` | Return a value without parsing (escape `$` prefixed strings) |
| `$rand` | Random float between 0 and 1 |
| `$function` | Custom JavaScript function |

---

## `$function` — Custom JavaScript

The `$function` operator runs custom JavaScript within an aggregation pipeline. Useful for complex string normalization or business logic that MongoDB operators cannot express.

### Syntax

```json
{
  "$function": {
    "body": "function(arg1, arg2) { return result; }",
    "args": ["$fieldName", "literal_value"],
    "lang": "js"
  }
}
```

### Rossum Example — Normalize Vendor Name

```json
{
  "aggregate": [
    {
      "$addFields": {
        "__normalized_name": {
          "$function": {
            "body": "function(name) { return name.replace(/[^a-zA-Z0-9]/g, '').toLowerCase(); }",
            "args": ["$Vendor Name"],
            "lang": "js"
          }
        }
      }
    },
    {
      "$match": {
        "__normalized_name": {
          "$regex": "^{sender_name | re}$",
          "$options": "i"
        }
      }
    },
    {
      "$project": {
        "_id": 0,
        "Vendor Name": 1,
        "vat_number": 1,
        "vendor_id": 1
      }
    }
  ]
}
```

> **Performance note**: JavaScript functions are slower than native MongoDB operators. Prefer native operators when possible. Use `$function` only for logic that cannot be expressed with built-in operators (e.g., complex string sanitization).

---

## `$lookup` — Cross-Collection Joins

Join documents from another dataset/collection.

### Basic Syntax

```json
{
  "$lookup": {
    "from": "other_collection",
    "localField": "field_in_current",
    "foreignField": "field_in_other",
    "as": "joined_results"
  }
}
```

### Pipeline Syntax (more powerful)

```json
{
  "$lookup": {
    "from": "other_collection",
    "let": { "local_var": "$local_field" },
    "pipeline": [
      { "$match": { "$expr": { "$eq": ["$foreign_field", "$$local_var"] } } }
    ],
    "as": "joined_results"
  }
}
```

### Rossum Example — Enrich Vendor Match with Addresses

```json
{
  "aggregate": [
    { "$match": { "vat_number": "{sender_vat}" } },
    {
      "$lookup": {
        "from": "vendor_addresses",
        "localField": "vendor_id",
        "foreignField": "vendor_id",
        "as": "addresses"
      }
    },
    { "$unwind": "$addresses" },
    { "$match": { "addresses.country": "{sender_country}" } },
    {
      "$project": {
        "_id": 0,
        "vendor_id": 1,
        "vat_number": 1,
        "addresses.street": 1,
        "addresses.city": 1,
        "addresses.country": 1
      }
    }
  ]
}
```

---

## `$unionWith` — Combine Multiple Collections

Merge results from multiple datasets into a single result set.

```json
{
  "aggregate": [
    { "$match": { "vat_number": "{sender_vat}" } },
    {
      "$unionWith": {
        "coll": "secondary_vendors",
        "pipeline": [
          { "$match": { "vat_number": "{sender_vat}" } }
        ]
      }
    }
  ]
}
```

---

## Atlas Search (`$search`)

MongoDB Atlas Search provides full-text search with fuzzy matching, scoring, and compound queries. This is what powers fuzzy matching in the Master Data Hub.

### Basic Text Search

```json
{
  "aggregate": [
    {
      "$search": {
        "text": {
          "query": "{sender_name}",
          "path": "Vendor Name"
        }
      }
    }
  ]
}
```

### Fuzzy Text Search

```json
{
  "aggregate": [
    {
      "$search": {
        "text": {
          "query": "{sender_name}",
          "path": "Vendor Name",
          "fuzzy": {
            "maxEdits": 1,
            "prefixLength": 3,
            "maxExpansions": 50
          }
        }
      }
    }
  ]
}
```

**Fuzzy parameters:**

| Parameter | Description | Default |
|-----------|-------------|---------|
| `maxEdits` | Maximum Levenshtein edits (1 or 2) | 2 |
| `prefixLength` | Number of leading characters that must match exactly | 0 |
| `maxExpansions` | Maximum number of variations generated | 50 |

### Compound Queries

Combine multiple search criteria with boolean logic and boosting.

```json
{
  "aggregate": [
    {
      "$search": {
        "compound": {
          "must": [
            {
              "text": {
                "query": "{sender_country}",
                "path": "country"
              }
            }
          ],
          "should": [
            {
              "text": {
                "query": "{sender_name}",
                "path": "Vendor Name",
                "fuzzy": { "maxEdits": 1 },
                "score": { "boost": { "value": 3 } }
              }
            },
            {
              "text": {
                "query": "{sender_vat}",
                "path": "vat_number",
                "score": { "boost": { "value": 5 } }
              }
            }
          ],
          "minimumShouldMatch": 1
        }
      }
    }
  ]
}
```

**Compound clauses:**

| Clause | Description |
|--------|-------------|
| `must` | All conditions must match (AND). Contributes to score. |
| `mustNot` | No conditions may match (NOT). Does not affect score. |
| `should` | At least one should match (OR). Each match boosts score. |
| `filter` | Must match, but does not contribute to score. |
| `minimumShouldMatch` | Minimum number of `should` clauses that must match. |

### Search with Score Extraction and Filtering

```json
{
  "aggregate": [
    {
      "$search": {
        "text": {
          "query": "{sender_name}",
          "path": "Vendor Name",
          "fuzzy": { "maxEdits": 1 }
        }
      }
    },
    { "$limit": 10 },
    {
      "$addFields": {
        "__searchScore": { "$meta": "searchScore" }
      }
    },
    {
      "$match": {
        "__searchScore": { "$gt": 0.1 }
      }
    },
    {
      "$project": {
        "_id": 0,
        "Vendor Name": 1,
        "vat_number": 1,
        "vendor_id": 1
      }
    }
  ]
}
```

### Search Score Filtering with Dynamic Thresholds

Raw search scores are not normalized — they depend on index size, document frequency, and query complexity. A static threshold often fails: too high and you miss valid matches, too low and you get false positives. The recommended approach uses **statistical analysis** of the result set to compute a dynamic threshold.

The technique uses `$setWindowFields` to compute count, average, max, and standard deviation across all results, then applies different threshold strategies depending on result count:

- **Few results (≤ 2)**: Use 90% of max score — almost certainly a good match
- **Many results (> 2)**: Use mean + 1.5× standard deviation (capped at max) — filters statistical outliers

Additionally, enforce an **absolute minimum score** to reject results that are too weak regardless of distribution.

```json
{
  "aggregate": [
    {
      "$search": {
        "index": "default",
        "compound": {
          "must": [
            {
              "text": {
                "path": "VE_NAME",
                "query": "{sender_name} "
              }
            }
          ],
          "should": [
            {
              "text": {
                "path": ["VE_STREET", "VE_CITY", "VE_ZIPCODE"],
                "query": "{sender_address} "
              }
            }
          ]
        }
      }
    },
    { "$limit": 50 },
    {
      "$addFields": {
        "__searchScore": { "$meta": "searchScore" }
      }
    },
    {
      "$setWindowFields": {
        "output": {
          "__docCount": { "$count": {} },
          "__avgSearchScore": { "$avg": "$__searchScore" },
          "__maxSearchScore": { "$max": "$__searchScore" },
          "__stdDevSearchScore": { "$stdDevPop": "$__searchScore" }
        }
      }
    },
    {
      "$addFields": {
        "__dynamicSearchScoreThreshold": {
          "$cond": {
            "if": { "$lte": ["$__docCount", 2] },
            "then": { "$multiply": ["$__maxSearchScore", 0.9] },
            "else": {
              "$min": [
                "$__maxSearchScore",
                {
                  "$add": [
                    "$__avgSearchScore",
                    { "$multiply": ["$__stdDevSearchScore", 1.5] }
                  ]
                }
              ]
            }
          }
        }
      }
    },
    {
      "$match": {
        "$expr": {
          "$gte": ["$__searchScore", "$__dynamicSearchScoreThreshold"]
        },
        "__searchScore": { "$gte": 1.08 }
      }
    },
    { "$limit": 10 },
    {
      "$project": {
        "_id": 0,
        "VE_CITY": 1,
        "VE_IBAN": 1,
        "VE_NAME": 1,
        "VE_STREET": 1,
        "VE_COUNTRY": 1,
        "VE_ZIPCODE": 1,
        "VE_TAX_ID_NO": 1,
        "VE_VAT_ID_NO": 1,
        "VE_VENDOR_NO": 1
      }
    }
  ]
}
```

**Key elements of this pattern:**

| Element | Purpose |
|---------|---------|
| `"$limit": 50` | Cap initial results to prevent expensive window computations |
| `$setWindowFields` | Compute statistics (count, avg, max, stddev) across the entire result set |
| `$cond` on `__docCount` | Switch threshold strategy based on result set size |
| `0.9 × max` | For ≤ 2 results: accept if score is within 90% of the best match |
| `avg + 1.5 × stddev` | For > 2 results: accept only statistical outliers (high scorers) |
| `$min` with max | Cap the dynamic threshold at the maximum score |
| `__searchScore >= 1.08` | Absolute minimum — reject weak matches regardless of distribution |
| `$project` | Return only the fields needed for mapping |

**Tuning the parameters:**
- Increase the `1.5` multiplier on stddev to be more selective (fewer results)
- Decrease it to be more permissive (more results)
- Adjust the absolute minimum (`1.08`) based on your data — test with the "Try" button
- The `0.9` factor for few-result sets can be lowered if near-matches are acceptable

### Other Search Operators

| Operator | Description |
|----------|-------------|
| `text` | Full-text search with optional fuzzy matching |
| `phrase` | Search for exact phrases with optional slop (word distance) |
| `wildcard` | Wildcard pattern matching (`?` single char, `*` multiple) |
| `regex` | Regular expression search against Atlas Search index |
| `range` | Numeric or date range search |
| `near` | Proximity-based scoring (closer values score higher) |
| `exists` | Documents where field exists in the search index |
| `autocomplete` | Prefix-based search for typeahead (requires autocomplete index) |
| `equals` | Exact match on boolean, objectId, or date fields |
| `moreLikeThis` | Find documents similar to input documents |
| `queryString` | Lucene-style query string syntax |
| `embeddedDocument` | Search within nested/embedded documents |

### Embedded Document Search with `returnScope`

When documents contain arrays of nested objects (e.g., invoice line items inside an invoice), use `embeddedDocument` field mappings with `returnScope` to search and return individual embedded documents rather than the entire parent. This is useful for matching against specific line items, positions, or other nested records.

Documentation: https://www.mongodb.com/docs/atlas/atlas-search/return-scope/

**Required index definition:**

The field containing embedded documents must be mapped as `type: "embeddedDocuments"` with `storedSource` listing the fields to return. Parent-level fields used for filtering need their own mappings.

```json
{
  "collectionName": "invoices_v1",
  "indexName": "search_invoice_embedded_line_items",
  "mappings": {
    "dynamic": false,
    "fields": {
      "invoice-number": {
        "type": "string",
        "analyzer": "lucene.keyword"
      },
      "invoice-lines": {
        "type": "embeddedDocuments",
        "fields": {
          "description": {
            "type": "string",
            "analyzer": "lucene.standard"
          }
        },
        "storedSource": {
          "include": [
            "id",
            "description",
            "line-num",
            "type",
            "total",
            "uom.code",
            "order-header-num",
            "order-line-num"
          ]
        }
      }
    }
  }
}
```

Key index elements:
- `type: "embeddedDocuments"` — tells Atlas Search to index nested array elements individually
- `storedSource.include` — which embedded fields to store and return (avoids returning the entire parent document)
- Parent fields (e.g., `invoice-number`) use `lucene.keyword` for exact matching in `filter`/`hasRoot`

**Example query — fuzzy match on line item description, filtered by invoice number:**

```json
{
  "aggregate": [
    {
      "$search": {
        "index": "search_invoice_embedded_line_items",
        "returnScope": {
          "path": "invoice-lines"
        },
        "returnStoredSource": true,
        "compound": {
          "must": [
            {
              "text": {
                "path": "invoice-lines.description",
                "query": "{item_description}",
                "fuzzy": {
                  "maxEdits": 2
                }
              }
            }
          ],
          "filter": [
            {
              "hasRoot": {
                "operator": {
                  "text": {
                    "path": "invoice-number",
                    "query": "{invoice_id}"
                  }
                }
              }
            }
          ]
        }
      }
    },
    { "$limit": 10 }
  ]
}
```

Key query elements:

| Element | Purpose |
|---------|---------|
| `returnScope.path` | Return individual embedded documents from this array field instead of the parent document |
| `returnStoredSource: true` | Return the stored source fields defined in `storedSource.include` |
| `text` on `invoice-lines.description` | Fuzzy search within embedded documents (use the dot-path `arrayField.nestedField`) |
| `hasRoot` | Filter embedded results by a condition on the parent document (e.g., match only line items belonging to a specific invoice) |
| `filter` clause | `hasRoot` goes inside `filter` so it restricts results without affecting relevance scores |

This pattern is useful for line item matching — finding the best-matching line in a specific PO or invoice by description, while scoping results to a known document number.

---

## Practical Rossum Matching Patterns

### Pattern 1: Exact Vendor Match by VAT

```json
{ "find": { "vat_number": "{sender_vat}" } }
```

### Pattern 2: Case-Insensitive Vendor Name Match

```json
{ "find": { "Vendor Name": { "$regex": "^{sender_name | re}$", "$options": "i" } } }
```

### Pattern 3: Match by Multiple Identifiers (OR)

Try VAT first, fall back to IBAN, then name.

```json
{
  "find": {
    "$or": [
      { "vat_number": "{sender_vat}" },
      { "iban": "{iban}" },
      { "name": { "$regex": "^{sender_name | re}$", "$options": "i" } }
    ]
  }
}
```

### Pattern 4: Purchase Order Line Item Match

```json
{
  "find": {
    "po_number": "{order_id}",
    "line_number": "{item_po_line}"
  }
}
```

### Pattern 5: Fuzzy Vendor Name with Dynamic Score Threshold

Uses compound search with `must`/`should` and statistical score filtering. See the "Search Score Filtering with Dynamic Thresholds" section above for the full pattern with `$setWindowFields`, dynamic threshold computation (mean + 1.5× stddev), and absolute minimum score.

Simplified version with static threshold for simpler use cases:

```json
{
  "aggregate": [
    {
      "$search": {
        "index": "default",
        "compound": {
          "must": [
            {
              "text": {
                "path": "Vendor Name",
                "query": "{sender_name} "
              }
            }
          ],
          "should": [
            {
              "text": {
                "path": ["Street", "City", "Zipcode"],
                "query": "{sender_address} "
              }
            }
          ]
        }
      }
    },
    { "$limit": 50 },
    {
      "$addFields": {
        "__searchScore": { "$meta": "searchScore" }
      }
    },
    {
      "$match": { "__searchScore": { "$gte": 1.0 } }
    },
    { "$limit": 10 },
    {
      "$project": {
        "_id": 0,
        "Vendor Name": 1,
        "Street": 1,
        "City": 1,
        "Zipcode": 1,
        "vat_number": 1,
        "vendor_id": 1
      }
    }
  ]
}
```

### Pattern 6: GL Code Match with Role Code

```json
{
  "find": {
    "role_code": { "$regex": "^{item_role | re}$", "$options": "i" }
  }
}
```

### Pattern 7: Compound Search — Vendor by Name + Country

```json
{
  "aggregate": [
    {
      "$search": {
        "compound": {
          "must": [
            {
              "text": {
                "query": "{sender_country}",
                "path": "country"
              }
            }
          ],
          "should": [
            {
              "text": {
                "query": "{sender_name}",
                "path": "Vendor Name",
                "fuzzy": { "maxEdits": 1 },
                "score": { "boost": { "value": 3 } }
              }
            }
          ]
        }
      }
    },
    { "$limit": 5 },
    {
      "$project": {
        "_id": 0,
        "Vendor Name": 1,
        "country": 1,
        "vat_number": 1,
        "vendor_id": 1
      }
    }
  ]
}
```

### Pattern 8: Deduplicate Results

When a query returns duplicate vendor records (e.g., same vendor with multiple addresses):

```json
{
  "aggregate": [
    { "$match": { "vat_number": "{sender_vat}" } },
    {
      "$group": {
        "_id": "$vendor_id",
        "doc": { "$first": "$$ROOT" }
      }
    },
    { "$replaceRoot": { "newRoot": "$doc" } },
    {
      "$project": {
        "_id": 0,
        "vendor_id": 1,
        "vat_number": 1,
        "name": 1
      }
    }
  ]
}
```

### Pattern 9: Normalize and Match

Strip non-alphanumeric characters before comparing:

```json
{
  "aggregate": [
    {
      "$addFields": {
        "__clean_vat": {
          "$function": {
            "body": "function(v) { return v ? v.replace(/[^a-zA-Z0-9]/g, '').toUpperCase() : ''; }",
            "args": ["$vat_number"],
            "lang": "js"
          }
        }
      }
    },
    {
      "$match": {
        "__clean_vat": {
          "$regex": "^{sender_vat | re}$",
          "$options": "i"
        }
      }
    },
    {
      "$project": {
        "_id": 0,
        "vat_number": 1,
        "vendor_id": 1,
        "name": 1
      }
    }
  ]
}
```

### Pattern 10: Dynamic Dataset Name

The dataset key supports placeholders for multi-country/multi-entity setups:

```json
{
  "name": "PO Match",
  "source": {
    "dataset": "PurchaseOrders_{queue_country}_v1",
    "queries": [
      { "find": { "po_number": "{order_id}" } }
    ]
  }
}
```

### Pattern 11: Preferred Vendor Prioritization

Sort matches to prefer flagged/preferred vendors:

```json
{
  "aggregate": [
    { "$match": { "vat_number": "{sender_vat}" } },
    {
      "$addFields": {
        "__preferred_sort": {
          "$cond": {
            "if": { "$eq": ["$is_preferred", true] },
            "then": 0,
            "else": 1
          }
        }
      }
    },
    { "$sort": { "__preferred_sort": 1 } },
    {
      "$project": {
        "_id": 0,
        "vendor_id": 1,
        "vat_number": 1,
        "name": 1,
        "is_preferred": 1
      }
    }
  ]
}
```

### Pattern 12: Substring/Contains Match

When the dataset field may contain the extracted value as a substring:

```json
{
  "find": {
    "description": {
      "$regex": "{item_description | re}",
      "$options": "i"
    }
  }
}
```

### Pattern 13: Numeric Range Match

Match when extracted amount falls within a range defined in the dataset:

```json
{
  "find": {
    "min_amount": { "$lte": { "$toDouble": "{amount_total}" } },
    "max_amount": { "$gte": { "$toDouble": "{amount_total}" } }
  }
}
```

### Pattern 14: Count Records in Dataset

Useful for diagnostics:

```json
{
  "aggregate": [
    { "$count": "total_records" }
  ]
}
```

---

## Data Type Handling

### `enum_value_type`

When matching produces numeric values that need to be compared as numbers (not strings), set the schema field's `enum_value_type`:

```json
{
  "type": "enum",
  "enum_value_type": "number",
  "options": []
}
```

This ensures proper type conversion when matched values are used in subsequent numeric comparisons or cross-configuration lookups.

### Type Coercion in Queries

- String values from annotations are the default — extracted values are strings
- Use `$toDouble`, `$toInt`, `$toLong` in aggregation expressions for numeric comparison
- Date comparisons require `$dateFromString` to parse date strings

---

## Performance Tips

1. **Put `$match` first** — filters reduce the working set for subsequent stages
2. **Use `$limit` after `$search`** — prevents processing thousands of fuzzy results
3. **Prefer native operators over `$function`** — JavaScript execution is significantly slower
4. **Use exact match queries first** in the query sequence — they are fastest; fall back to fuzzy/regex only when exact match fails
5. **Always use `$project` as the final stage** — return only the fields needed for mapping; large documents slow down the matching engine and waste bandwidth
6. **Avoid unnecessary `$sort` by search score** — `$search` already returns results sorted by relevance; adding `{ "$sort": { "__searchScore": -1 } }` is redundant unless you re-score or filter results after the initial search
7. **Avoid `$where`** — JavaScript-based filtering is the slowest query option
8. **Use indexes** — ensure frequently queried fields are indexed in the dataset
9. **Score thresholds** — always filter fuzzy results by score to prevent false positives

---

## Debugging Queries

### Using the "Try" Button

The Master Data Hub UI provides a "Try" button to test queries against the dataset with sample values substituted for `{schema_id}` placeholders.

### Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| No results from exact match | Case mismatch | Add `$regex` with `"$options": "i"` |
| No results from exact match | Whitespace in data | Trim values or use regex with `\\s*` |
| Query syntax error | Special characters in extracted value | Use `{field \| re}` pipe modifier |
| Too many fuzzy results | Low score threshold or high `maxEdits` | Lower `maxEdits` to 1, increase `prefixLength`, add `$match` on score |
| Wrong data type comparison | Comparing string to number | Use `$toDouble` / `$toInt` or set `enum_value_type` |
| Cross-config value not available | Earlier config didn't match | Add default values to earlier configuration |
| Slow query performance | `$function` or `$where` usage | Replace with native operators where possible |
