# mdh-picker-with-exact-preselect

Use this part when you need to show a full dropdown pick-list to the user but also want to auto-select the best match when one is found. A typical use case is GL coding: cost centers, spend categories, or department codes where all valid options should be visible but the system should pre-populate the most likely value. The part works by performing an exact match first, then using two `$setWindowFields` + `$cond` stages to decide whether to retain a synthetic "please select" placeholder. If an exact match exists, the placeholder is removed and the matched record sits at the top; if no match is found, the placeholder is retained, forcing the user to make a choice. The full collection is always appended below via a second `$unionWith`.

## Params

- `dataset` — the MDH collection backing the dropdown (e.g. `workday_cost_center`). Referenced twice: as `source.dataset` and as the `coll` in the second `$unionWith`.
- `exact_field` — the document field to match exactly (e.g. `Organization_Data.Organization_Code`). The same field holds the placeholder value when no match is found.
- `label_template` — the display string shown per option in the Rossum UI (e.g. `{Organization_Data.Organization_Code} {Organization_Data.Organization_Name}`). Uses MDH label template syntax with curly-brace field references.
- `placeholder` — the text shown when no exact match is found; default `"Please select"`. This string is injected directly into the synthetic `$documents` record.

## Produces / Consumes

- Produces: `selected_key` — the chosen record's key, written to `mapping.target_schema_id`. `selected_label` is deliberately *not* listed: `mapping.label_template` only formats what the picker displays, so it is no second schema write.
- Consumes: the field value extracted from the document, injected as an MDH placeholder into the `$match` and `$unionWith` exclusion filter.

## Adapt

If this config shares a target field with another MDH config (e.g. an auto-match config that runs first), this populator must be ordered last and gated on `target == ''` via the `action_condition`. A later no-match result from the populator will overwrite a successful auto-match result from the earlier config, so sequencing is critical.

The source example included an active-record filter (`$match` on `Organization_Data.Organization_Active: true`) before the final `$project`; this fragment **drops it** because the flag name is collection-specific. Re-add an active/inactive `$match` for your collection so the pick-list does not surface retired records.

The `$project` and `mapping.dataset_key` use the example's literal field names (`Organization_Data.ID` for the key, `Organization_Data.Organization_Name` for the label). These are not parameterized — replace them with your collection's actual key and label fields. The `$project` must always return whatever field `mapping.dataset_key` selects on, plus any field the `label_template` reads, or the match returns no usable result.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
