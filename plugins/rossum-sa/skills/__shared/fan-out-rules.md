# Fan-out Rules — Read Before Dispatching Subagents

Two things about the Rossum connection that only bite when work is split across
parallel agents. Both come from a real stall: a 14-schema fan-out that produced
10 identical failed calls before the user could intervene.

## 1. Connect before you dispatch

The connection lives in the MCP server, and **a subagent cannot obtain
credentials for it**. There is nobody there to answer the credential prompt, and
in a background agent that prompt is auto-denied. (A subagent shares the parent's
server process, so a `rossum_set_token` call from one would technically take
effect — which is the other reason not to: it would mutate the parent session's
connection as a side effect of a task.) So a fan-out dispatched into an
unauthenticated server does not fail once — every agent fails the same way at the
same moment, and none of them can fix it.

**Call `rossum_whoami` in the main session before dispatching anything that
touches Rossum.** If it reports no connection, connect first, then dispatch.

If you *are* the subagent and a Rossum call comes back not-connected: stop and
report that upward. Do not retry, do not call `rossum_set_token`, and do not go
looking for a token on disk. The plugin's auth-guard hook will tell you the same
thing — it is not a suggestion.

For unattended runs (`claude -p`, `--agent`, scheduled or cloud agents) there is
no main session to connect either: put `ROSSUM_TOKEN` and `ROSSUM_API_URL` in the
environment and the server connects on its own first tool call.

## 2. Prefer dumping to files over fanning out MCP calls

Most fan-outs are read-only — "check this formula across 14 schemas", "find every
hook that references X". Those do not need a connection per agent at all. Pull the
objects **once, in the main session**, then let the agents work on local files:

- `rossum_get_schema` with `out_file_path`
- `rossum_get_hook` with `out_file_path`

Each returns a small envelope (ids, counts, `content_sha256`, `written_to`) and
leaves the object in a file, in the same JSON shape as a prd2 `schema.json` /
`hook.json`. The agents then need only Grep and Read.

This is better on every axis that matters, not just auth:

- **Context.** Real schemas exceed 1,000 lines; hook `settings` reaches 8,000+.
  Fourteen of those through the parent's context is the actual constraint.
- **Auth.** No MCP call in the agent means the problem above cannot occur.
- **Reuse.** The same files feed the write side — `content_file_path` for
  `rossum_patch_schema`, `settings_file_path` for `rossum_patch_hook`.

Fan out MCP calls only when the agents genuinely write, or need a live query whose
result cannot be pulled up front.
