# mdh-fuzzy-score-normalization

Use this recipe when raw Atlas Search scores produce unreliable rankings because candidate records vary widely in text length. A short query against a long candidate name gets a high raw score simply due to term frequency, not quality of match. This recipe adds a length-ratio normalisation step after the initial fuzzy search: the raw score is divided by the length deviation factor, then passed through a sigmoid-like bounding transform, yielding a `__normalized_score` between 0 and 1 that penalises length mismatch. Apply it to name fields, address strings, or any free-text field where candidate length varies.

## Params

- `dataset` — the MDH collection to search (e.g. `vendors_master_list`, `SUPPLIERS`)
- `search_field` — the document field to run the fuzzy text search against (e.g. `name`, `SUPPLIER_NAME`); also used in the `$strLenCP` normalisation expression
- `threshold` — normalised score floor; default `0.85`. Because the normalised score is bounded between 0 and 1, this threshold is not comparable to raw score thresholds — `0.85` here is roughly equivalent to a well-calibrated raw threshold of several times higher.

## Produces / Consumes

- Produces: `matched_id`, `match_score` — the winner record key and its normalised score, surfaced via the configured `mapping` and an optional `additional_mapping` on `__normalized_score`.
- Consumes: the runtime placeholder `{search_value}` injected as the fuzzy query string and used again in the `$strLenCP` comparison.

## Adapt

The normalisation formula computes `raw / (1 + |1 - len(candidate)/len(query)|)` and then applies `x / (1 + x)`. If you want to compare candidate length against a combined field (e.g. name + address concatenated), replace the single `$strLenCP: "$«search_field»"` with a `$strLenCP` of a `$concat` expression, mirroring the Stage 4 pattern from Example 4 in `mdh-reference`. Raise the threshold to `0.9` when combining name and address signals.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
