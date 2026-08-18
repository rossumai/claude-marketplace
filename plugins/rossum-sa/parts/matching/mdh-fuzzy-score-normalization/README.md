# mdh-fuzzy-score-normalization

Use this part when raw Atlas Search scores produce unreliable rankings because candidate records vary widely in text length. A short query against a long candidate name gets a high raw score simply due to term frequency, not quality of match. This part adds a length-ratio normalisation step after the initial fuzzy search: the raw score is divided by the length deviation factor, then passed through a sigmoid-like bounding transform, yielding a `__normalized_score` between 0 and 1 that penalises length mismatch. Apply it to name fields, address strings, or any free-text field where candidate length varies.

## Params

- `dataset` — the MDH collection to search (e.g. `vendors_master_list`, `SUPPLIERS`)
- `search_field` — the document field to run the fuzzy text search against (e.g. `name`, `SUPPLIER_NAME`); also used in the `$strLenCP` normalisation expression
- `threshold` — normalised score floor (0–1); default `0.4`. The normalised score is bounded by `x/(1+x)`, so it saturates well below 1 (a length-adjusted raw score of ~1 maps to ~0.5). `0.4` is a permissive starting floor that admits realistic matches; calibrate upward against your own data. It is not comparable to a raw-score threshold.

## Produces / Consumes

- Produces: `matched_id`, `match_score` — the winner record key and its normalised score, surfaced via the configured `mapping` and an optional `additional_mapping` on `__normalized_score`.
- Consumes: the runtime placeholder `{search_value}` injected as the fuzzy query string and used again in the `$strLenCP` comparison.

## Adapt

The normalisation formula computes `raw / (1 + |1 - len(candidate)/len(query)|)` and then applies `x / (1 + x)`. If you want to compare candidate length against a combined field (e.g. name + address concatenated), replace the single `$strLenCP: "$«search_field»"` with a `$strLenCP` of a `$concat` expression, mirroring the Stage 4 pattern from Example 4 in `mdh-reference`. Raise the threshold to `0.9` when combining name and address signals.

**Atlas Search index is a prerequisite.** The `$search` runs against an index named `default` mapping `«search_field»`. If it does not exist the query *errors* — create it first (see the `mdh-reference` pre-flight).

**The default threshold is deliberately low — calibrate it.** The normalising transform `x / (1 + x)` saturates: a length-adjusted raw score of ~1 maps to ~0.50, and reaching `0.85` needs a raw score of ~6. With realistic raw scores (1–3, observed live), the previous `0.85` default *rejected legitimate matches* (a one-character-typo match normalised to 0.48). The default is now `0.4`; raise it only after calibrating against your own collection.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
