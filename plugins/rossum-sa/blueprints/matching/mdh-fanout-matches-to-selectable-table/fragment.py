from txscript import TxScript, default_to


def rossum_hook_request_handler(payload):
    x = TxScript.from_payload(payload)

    # 1) Hash guard — skip the fan-out (and so preserve the operator's
    #    selections) when the match inputs have not changed since last run.
    recalculate_hash = f"{x.field.«hash_source_field»}"
    fan_out_needed = x.field.«hash_field» != recalculate_hash

    if fan_out_needed:
        options = default_to(x.field.«source_match_field».attr.options, [])
        matches = [o for o in options if o.value]

        rows = []
        for o in matches:
            rows.append({
                # hidden per-row key the export/PATCH targets (= option value)
                "«row_key_field»": o.value,
                # operator-facing display so candidates are distinguishable (= label)
                "«row_reference_field»": o.label,
                # default selection: NO when ambiguous (>1), YES for a lone match
                # so unambiguous single-match documents stay touchless.
                "«row_select_field»": "yes" if len(matches) == 1 else "no",
            })

        x.field.«target_table» = rows
        x.field.«hash_field» = recalculate_hash

    # 2) Always (cheap) recompute the filtered export list from the current
    #    selection flags — independent of the hash guard, so operator edits to
    #    the select flag take effect without re-fanning (and resetting) the table.
    x.field.«export_list_field» = [
        row.«row_key_field»
        for row in x.field.«target_table»
        if row.«row_select_field» == "yes"
    ]

    return x.hook_response()
