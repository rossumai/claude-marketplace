# export-evaluate-guard

Use this recipe to add a pre-call guard at the front of any Request Processor stage. The guard checks two things: that a named schema field is non-empty (preventing API calls with incomplete data) and that a prior stage returned a success status code (preventing cascading failures when an upstream call failed). Any failed condition causes the entire stage to be skipped.

## Params

- `required_fields` — a comma-separated list of field schema IDs that must be present and non-empty before the stage runs (e.g. `invoice_number`, `vendor_id`); in the fragment, the first field in the list is shown as representative — expand to an `$and` block for multiple fields
- `prior_stage` — the name of an upstream stage whose success status must be confirmed; the fragment expects a schema field named `<prior_stage>_status_code` to exist and contain `200` or `201`; defaults to empty string (omit the condition if no prior stage applies)

## Produces / Consumes

- Produces: nothing — the evaluate block only gates execution, it does not write any fields.
- Consumes: `field.«required_fields».value` (existence check) and `field.«prior_stage»_status_code` (status check), both read from schema fields populated by upstream stages or the document itself.

## Adapt

The `evaluate` block shown here has two conditions joined implicitly by AND — the stage is skipped if either fails. To require multiple fields, replace the single `required_fields` condition with an `$and` block listing each field separately. To check a non-standard success code (e.g. `202`), extend the `$in` list in the `prior_stage` condition. If no prior stage guard is needed, remove the second evaluate entry entirely.

See `export-pipeline-reference` for the Request Processor stage model.
