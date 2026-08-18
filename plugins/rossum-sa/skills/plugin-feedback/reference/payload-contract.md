<!-- plugins/rossum-sa/skills/plugin-feedback/reference/payload-contract.md -->
# Plugin-feedback payload contract (frozen)

Metadata-only. This is the ONLY data that ever leaves the machine. The list
here MUST match `FEEDBACK_FIELDS` in `hooks/detect_friction.py`
(`tests/test_plugin_feedback_skill.py` enforces it).

| Field | Notes |
|---|---|
| `route` | `tool-request` \| `agent-bug` \| `knowledge-gap` |
| `signal` | which detector fired |
| `corroborators` | e.g. `reprompted_a_lot`, `frustration` |
| `tool_name` | e.g. `rossum_get_annotation` |
| `endpoint` | e.g. `/annotations/{id}` |
| `method` | e.g. `GET` |
| `error_class` | error class only, NOT the response body |
| `http_status` | numeric status only |
| `expected` | one line, no raw values |
| `got` | one line, no raw values |
| `reference_pack` | for knowledge-gap |
| `section` | for knowledge-gap |
| `counts` | `{errors, cycles, reprompts}` |
| `plugin_version` | from plugin.json |
| `description` | SA free text, scrubbed |

**Never:** raw payloads, field values, document content, annotation/org IDs,
emails, file contents.
