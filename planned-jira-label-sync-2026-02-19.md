# Jira Label Sync Investigation Plan

Date: 2026-02-19

Status: Planning only (no code changes applied yet)

## Goals

- Investigate why issues are being planned for removal unexpectedly.
- Verify pagination is traversing all pages for both core-scope and cleanup queries.
- Add safe cleanup for mislabeled dependency labels.
- Keep JQL mode deterministic when `JIRA_CORE_JQL` is provided.

## Planned Work

1. Confirm current behavior on one unexpected remove issue.
- Add temporary dry-run diagnostics for remove candidates:
  - linked keys discovered
  - intersection with `core_keys`
  - current labels
  - reason for planned remove

2. Verify pagination with explicit evidence.
- Instrument Jira search loops to log:
  - page index
  - page size returned
  - cumulative issues fetched
- Compare fetched counts to API pagination signals (`total` for legacy, token exhaustion for cloud).

3. Fix JQL vs default filter precedence.
- Ensure default filter ID is not auto-injected when `JIRA_CORE_JQL` is set.
- Prevent false "both set" conflicts caused by default filter fallback.

4. Add wrong-label cleanup support.
- Introduce `JIRA_DEPENDENCY_LABEL_ALIASES` (CSV env var).
- Include canonical label + aliases in cleanup scan.
- Rules:
  - If issue still depends on CORE: add canonical if missing, remove aliases.
  - If issue no longer depends on CORE: remove canonical + aliases.

5. Make label checks case-insensitive.
- Normalize label comparisons to avoid false add/remove actions due casing differences.

6. Refactor planned change model.
- Move from bool add/remove flags to per-issue sets:
  - `labels_to_add`
  - `labels_to_remove`
- Allow one issue update to perform add canonical + remove aliases together.

7. Validate in dry-run before apply.
- Confirm pagination summary matches expectations.
- Confirm unexpected remove cases show clear, correct reasons.
- Confirm alias-labeled issues are migrated/cleaned as expected.

## Files Planned To Be Updated

1. `config.py`
- Add `JIRA_DEPENDENCY_LABEL_ALIASES` config parsing.
- Update filter/JQL precedence behavior.

2. `dependency_logic.py`
- Add alias cleanup logic and case-insensitive label matching.
- Refactor planned change structure to add/remove label sets.
- Add diagnostics for remove rationale in dry-run mode.

3. `jira_client.py`
- Add pagination observability counters/log output.

4. `main.py`
- Print pagination/debug summary fields in run output.

5. `README.md` (optional but recommended)
- Document `JIRA_DEPENDENCY_LABEL_ALIASES` and updated JQL/filter behavior.

## New Files Planned

- None required for implementation.
- Optional tests if requested later:
  - `tests/test_dependency_logic.py`

## Env Source For Alias Labels

`JIRA_DEPENDENCY_LABEL_ALIASES` will come from `.env` (or exported shell env), as a comma-separated list.

Example:

```dotenv
JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies
JIRA_DEPENDENCY_LABEL_ALIASES=DFS_CORE_Dependecies,DFS_CORE_Dependency,DFS_CORE_dependency
```
