# Internal development prompts

Copy-paste prompts for developing, testing, and maintaining the plugin.

## Cross-pollinate knowledge

Extract reusable gotchas from a project's CLAUDE.md into plugin reference skills, then trim the project file to remove redundancy.

```
Read CLAUDE.md from <path-to-project> and extract any general Rossum
gotchas or knowledge into the appropriate plugin reference skills
(mdh-reference, rossum-reference, prd-reference, etc.). Then simplify
or remove lines from the project CLAUDE.md that are now redundant.
```

## Self-test

Verify all existing MCP tools work correctly.

```
Call rossum_set_token with the provided token and base URL, then systematically test every MCP tool
against the live API. For each tool:

1. Call it with valid arguments derived from real data (use IDs from list endpoints to feed into
   get endpoints; use existing collection names for Data Storage calls).
2. For write/destructive tools, create a temporary test resource, verify it exists, then clean it up:
   - Data Storage: create_index → list_indexes (verify) → drop_index;
     create_search_index → list_search_indexes (verify) → drop_search_index.
   - Hooks: create_hook → get_hook (verify) → patch_hook (change name/active) →
     get_hook (verify patch) → delete_hook.
   - Rules: create_rule (disabled, trivial trigger_condition like "False", attached to a real queue) →
     get_rule (verify) → patch_rule (change name/trigger_condition, keep disabled) →
     get_rule (verify patch) → delete_rule → get_rule (expect 404).
   - test_hook (read-shaped but executes): pick a FUNCTION hook (not a webhook — avoid external
     side effects), call test_hook with its event/action; auto-resolve or pass annotation_id.
     Confirm it returns the hook's response/messages without mutating the annotation.
   - update_annotation_content: on a to_review annotation, replace one datapoint value (start→ops→
     cancel is auto-managed), re-read via get_annotation_content to confirm it persisted, then revert
     the value to leave the document unchanged.
   - Users: create_user (inactive, with a throwaway username) → list_users (verify) →
     confirm the user appears. No delete endpoint exists, so set is_active=false on creation.
3. Verify that list endpoints handle API pagination correctly (the Rossum API returns paginated
   responses with `pagination.next` URLs — confirm multi-page results are auto-collected).
4. Record pass/fail for each tool.

If a tool fails, diagnose whether the bug is in the server code (wrong field names, incorrect API path,
bad request body shape) or a real API error. Fix server bugs in-place — update server.py
and README.md in the same pass.

After all tools pass, evaluate coverage gaps: are there Rossum API endpoints that would be high-value
additions for an SA debugging implementations? If so, add them (with README updates).

Token: <ROSSUM_API_TOKEN>
Base URL: https://elis.rossum.ai
```

## Add a new endpoint

Discover, implement, and verify a new MCP tool.

```
Call rossum_set_token with the provided token and base URL, then add a new MCP tool for:
<DESCRIBE THE ENDPOINT, e.g. "listing automation blockers on an annotation">

1. Discovery — figure out the correct Rossum API endpoint:
   a. Check the rossum-reference and data-storage-reference skills for documentation.
   b. Probe the live API: call related list/get tools to inspect response payloads for URLs,
      nested resources, or fields that hint at the right path.
   c. If still unclear, try candidate URLs directly (GET/POST) and observe the response.
2. Implementation — add the tool to server.py:
   a. Follow the existing patterns: use @_tool decorator, appropriate annotation
      (_READ_ONLY / _WRITE / _DESTRUCTIVE), and the matching helper (_rossum_get, _rossum_list,
      _rossum_post, _rossum_delete, _data_storage_call).
   b. Include filtering parameters where the API supports them.
   c. For list endpoints, use _rossum_list to handle pagination automatically.
3. Verification — test the new tool against the live API:
   a. Call it with valid arguments derived from real data.
   b. Confirm the response shape is useful (trim excessive fields with pick_fields if needed).
   c. For write/destructive tools, create a temporary resource, verify, then clean up.
4. Update README.md — add the tool to the correct table section with its description and
   appropriate icon (✏️ for write, ⚠️ for destructive).

Token: <ROSSUM_API_TOKEN>
Base URL: https://elis.rossum.ai
```

## init-claude-md

`/rossum-sa:init-claude-md` writes a project-specific `CLAUDE.md` from a local inspection of a pulled prd2 project. The auto-generated portion is bracketed by `<!-- BEGIN/END rossum-sa:init-claude-md auto-generated -->` markers — re-running the skill refreshes only that block.

If you add fields to `inspect.py`, also update the placeholder list and the rendering step in `SKILL.md` and the `template.md` file. Inspector schema must stay in sync with what the skill expects.
