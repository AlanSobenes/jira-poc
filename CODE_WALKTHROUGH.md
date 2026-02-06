# Jira POC Code Walkthrough

This document explains how the codebase works end-to-end, including startup flow, configuration, authentication, Jira API interaction, dependency labeling logic, and where to modify behavior safely.

## 1. Project Purpose

The script synchronizes one canonical label (`DFS_CORE_Dependencies`) on Jira issues that are external dependencies of a defined CORE scope.

Core behavior:
1. Load CORE issues from a saved filter (or custom JQL).
2. Inspect issue links on those CORE issues.
3. Add the canonical label to external linked issues.
4. Remove the canonical label from issues that are no longer linked to any CORE issue.

It supports dry-run mode for preview and apply mode for real updates.

## 2. File-by-File Responsibilities

- `main.py`
  - CLI entry point
  - Validates `--dry-run` xor `--apply`
  - Wires together config, Jira client, planning, and execution

- `config.py`
  - Loads `.env` values into process env if not already set
  - Parses and validates runtime settings
  - Defines the immutable `AppConfig` dataclass used by the rest of the app

- `auth/token_auth.py`
  - Resolves token from env first, fallback to `~/.netrc`
  - Throws auth error when no token source exists

- `jira_client.py`
  - Owns all HTTP calls to Jira
  - Selects Cloud vs non-Cloud API behavior
  - Handles auth mode (`auto`/`basic`/`bearer`)
  - Handles retryable errors and JSON decoding

- `dependency_logic.py`
  - Business rules for deciding label add/remove changes
  - Produces a deduplicated change plan
  - Applies plan (or prints plan in dry-run)

## 3. Execution Flow (from `py main.py --dry-run`)

1. `main.py::parse_args()`
   - Enforces exactly one of `--dry-run` or `--apply`.

2. `config.py::load_config()`
   - Loads `.env`.
   - Builds `AppConfig` with defaults + overrides.
   - Validates mutually exclusive JQL/filter settings and numeric values.

3. `JiraClient(config)`
   - Detects host type (`*.atlassian.net` => Cloud).
   - Resolves token.
   - Resolves auth mode.
   - Configures `requests.Session` headers/auth.

4. `build_changes(client, config)`
   - Loads filtered CORE issue scope.
   - Computes dependency-driven add operations.
   - Computes cleanup-driven remove operations.
   - Deduplicates contradictions.

5. `apply_changes(...)`
   - In dry-run: prints planned actions.
   - In apply: calls Jira update API and prints actions.
   - Updates run counters.

6. Summary printed to stdout.

## 4. Configuration Model (`config.py`)

### Defaults and constants
- `CANONICAL_LABEL = DFS_CORE_Dependencies`
- Default base URL points to Atlassian Cloud sandbox.
- Default CORE filter id `1244128`.
- Default included types: `Initiative, Epic, Story`.
- Default authoritative link types: `blocks, is blocked by, depends on`.
- Default ignored status: `Canceled`.

### `.env` loading behavior
- Reads local `.env` line by line.
- Ignores comments/invalid lines.
- Does **not** overwrite already-set environment variables.

### Important env variables
- `JIRA_BASE_URL`
- `JIRA_CORE_FILTER_ID` or `JIRA_CORE_JQL` (exactly one)
- `JIRA_TOKEN_ENV_VAR` (default `JIRA_PAT`)
- `JIRA_AUTH_MODE` (`auto`, `basic`, `bearer`)
- `JIRA_EMAIL_ENV_VAR` (default `JIRA_EMAIL`, needed for Basic auth)
- `JIRA_DEPENDENCY_LABEL`
- `JIRA_CORE_ISSUE_TYPES`
- `JIRA_LINK_TYPES`
- `JIRA_IGNORED_STATUSES`
- `JIRA_PAGE_SIZE`
- `JIRA_REQUEST_TIMEOUT_SECONDS`

### Validation rules
- Must provide one and only one of filter id or custom JQL.
- Page size and timeout must be integers.
- Auth mode must be valid.

## 5. Authentication and Host Strategy

`jira_client.py` resolves host and auth like this:

1. Determine Cloud vs non-Cloud:
   - Cloud if hostname ends with `.atlassian.net`.

2. Determine auth mode:
   - If configured explicitly (`basic`/`bearer`), use that.
   - If `auto`: Cloud => `basic`, non-Cloud => `bearer`.

3. Resolve token:
   - Env token variable first (`JIRA_PAT` unless overridden).
   - Fallback `~/.netrc` password for Jira host.

4. Configure requests:
   - `basic`: `HTTPBasicAuth(email, token)`; requires `JIRA_EMAIL`.
   - `bearer`: `Authorization: Bearer <token>` header.

## 6. Jira API Interaction Layer

### Unified request wrapper (`_request`)
- Builds full URL from base + path.
- Retries transient failures (`429`, `500`, `502`, `503`, `504`) up to 3 retries.
- Uses `Retry-After` when available; else exponential backoff.
- Raises a runtime error for non-success responses with truncated body for debugging.

### Cloud and non-Cloud endpoint differences
- Search:
  - Cloud: `GET /rest/api/3/search/jql` with token pagination (`nextPageToken`).
  - Non-Cloud: `POST /rest/api/2/search` with `startAt` pagination.

- Issue read/update:
  - Cloud: `/rest/api/3/issue/{key}`
  - Non-Cloud: `/rest/api/2/issue/{key}`

This split was added due to Cloud deprecating the old search endpoint.

## 7. Business Logic (`dependency_logic.py`)

### Data structures
- `RunStats`: counters for summary output.
- `PlannedChange`: one issue-level planned action (`add_label` and/or `remove_label`).

### Scope definition
`load_core_scope()`:
- Builds CORE query from filter or JQL.
- Fetches fields needed for processing.
- Filters to allowed issue types and excludes ignored statuses.

### Link eligibility
`_is_authoritative_link()`:
- A link is counted if its `name` or `inward` or `outward` text matches configured authoritative link types (case-insensitive).

### Dependency discovery
`_linked_issue_keys()`:
- Reads both inward and outward linked issue keys from eligible links.
- Returns unique key set.

### Build add-label actions
For each CORE issue:
- Collect linked issue keys.
- Skip links to other CORE issues.
- For each external linked issue:
  - Increment dependency counter.
  - Fetch current issue labels + status.
  - Skip ignored statuses.
  - If canonical label missing, plan add.

### Build remove-label actions (cleanup)
- Query all issues currently carrying canonical label (excluding ignored statuses via JQL).
- Skip CORE issues.
- If an issue has no authoritative link to any CORE key, plan remove.

### Deduplication and conflict handling
- Merge by issue key.
- Combine multiple add/remove intents from different passes.
- Drop contradictory final state (`add_label == remove_label`), leaving only actionable rows.

## 8. Apply Stage

`apply_changes()` sorts planned changes by issue key to keep output stable.

For each change:
- Build Jira update operations:
  - Add op: `{"add": "DFS_CORE_Dependencies"}`
  - Remove op: `{"remove": "DFS_CORE_Dependencies"}`
- In apply mode, send PUT update.
- In dry-run mode, print only.
- Increment `labels_added` / `labels_removed` counters for summary.

## 9. Safety Characteristics

- Deterministic outputs due to sorted application order and deduping.
- Does not alter labels other than the configured canonical label.
- CORE issues are excluded from labeling by design.
- Ignored statuses are excluded in both scope and action phases.
- Dry-run mode allows safe preview before write operations.

## 10. Known Constraints / Edge Cases

- Extra API calls: linked issues are fetched individually; can be slower on very large graphs.
- Duplicate dependency counting: `dependencies_found` can include repeated references across multiple CORE issues.
- Runtime errors are not typed by API domain (all become `RuntimeError` after request wrapper).
- Current README may lag behind code behavior if not updated after API/auth changes.

## 11. How To Extend Safely

1. Add support for more link semantics:
   - Update `JIRA_LINK_TYPES` env value or default list in `config.py`.

2. Change what counts as CORE:
   - Update filter/JQL inputs.
   - Optionally modify `_allowed_by_scope()` rules.

3. Add observability:
   - Emit JSON report of planned/applied changes in `apply_changes()`.

4. Improve performance:
   - Batch-fetch linked issue details where API allows.
   - Cache `get_issue()` responses by key.

5. Harden failure handling:
   - Introduce custom exception classes for auth, validation, transport, and Jira response errors.

## 12. Quick Operational Checklist

1. Set credentials in env or `.env`.
2. For Atlassian Cloud, ensure `JIRA_EMAIL` is set.
3. Ensure either `JIRA_CORE_FILTER_ID` or `JIRA_CORE_JQL` is set (not both).
4. Run dry-run first:
   - `py main.py --dry-run`
5. Validate output.
6. Run apply:
   - `py main.py --apply`

## 13. Suggested Next Improvements (small effort, high value)

- Add unit tests for:
  - link classification
  - dedupe/conflict resolution
  - cloud vs legacy search paginator
- Add a `--output-json` option for change auditing.
- Add optional log level control (`INFO/DEBUG`).

