## Architecture & Deployment (prd2)

This project is managed with **prd2**. Deploy with `prd2 push` / `prd2 deploy` to environments — **not** via git branches, worktrees, or pull requests. Git here is only a backup of the prd2 tree; isolation is **environment-level** (e.g. `uat` vs `prod`), not git-branch-level. Do **not** use `superpowers:using-git-worktrees` or `superpowers:finishing-a-development-branch` as a deployment/isolation mechanism on this project.

### Layout

`<org-dir>/<subdirectory>/{workspaces,hooks,rules,engines,labels}`

- Top-level directories map to **Rossum organizations** (e.g. `uat`, `prod`) — each has its own `org_id`, `api_base`, and `credentials.yaml`.
- **`subdirectories`** partition an org's objects by an optional regex; **`default`** (empty regex) is the catch-all and the normal choice. A project always has at least one subdirectory.
- Objects are named `<Name>_[<id>]`. Hook / rule / schema-formula code lives in **sidecar `.py` files** next to the JSON.

### Editing

- Edit local `.py` files. **Never** edit the `code` field in hook JSON or the `formula` property in `schema.json` — `prd2 push` syncs `.py` files into JSON automatically. Never use `rossum_patch_hook` / `rossum_patch_schema` to push code that belongs in a `.py` file.
- Keep schema field IDs stable across pushes — changing them breaks annotations.

### Commands

- `prd2 pull` — refresh local state from an environment (read-only; safe).
- `prd2 push --indexed-only -f` — push staged changes non-interactively (only after the diff is approved).
- `prd2 deploy -f <deploy-file>.yaml` — promote to another environment.
- `prd2 purge` — remove objects.

### Safety

Read-only is fine without confirmation: `prd2 pull`, all `rossum_list_*` / `rossum_get_*`, `data_storage_find` / `data_storage_aggregate`, `rossum_whoami`. **Every write requires explicit user approval before execution** — `prd2 push`, `prd2 deploy`, all `rossum_create_*` / `rossum_patch_*` / `rossum_delete_*`, and all Data Storage writes (`insert`, `update`, `delete`, `replace`, `bulk_write`, `drop`). Never batch multiple writes into one approval; describe each and wait for a clear yes.

### File placement

- New hooks → `<org>/<subdir>/hooks/<Name>_[].json` (+ `.py` sidecar). Reuse existing hooks before creating new ones.
- New formulas → `…/queues/<Queue>/formulas/<field_id>.py` (one per file).
- Deploy files → `deploy_files/`, named `deploy_<source>_to_<target>.yaml`.
