# Jira External Dependency Auto-Labeling POC

## What this POC does
Batch script that synchronizes one canonical label on external dependency issues:
- Canonical label: `DFS_CORE_Dependencies`
- CORE source of truth: saved filter ID `1244128` (or optional custom JQL)
- Dependency links inspected: `blocks`, `is blocked by`, `depends on`,`is a dependency of`
- Ignored status: `Canceled`

Behavior:
1. Fetch CORE issues.
2. Build CORE key set.
3. For each CORE issue, inspect dependency links.
4. If linked issue is external (not CORE), add canonical label.
5. Cleanup pass: for currently labeled issues, remove label if no remaining CORE dependency.

Safety guarantees:
- Never labels CORE issues.
- Never adds duplicate labels.
- Never removes unrelated labels.
- Re-runnable and deterministic.
- No listeners/webhooks/services.

## Repository structure
```text
jira-poc/
├── main.py
├── jira_client.py
├── dependency_logic.py
├── config.py
├── auth/
│   ├── token_auth.py
│   └── README.md
├── README.md
└── requirements.txt
```

## Prerequisites
- Python 3.10+
- Jira PAT/API token with issue read + edit label permissions

## Install
### Windows (PowerShell or CMD)
```bash
cd jira-poc
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
```

### macOS (Terminal)
```bash
cd jira-poc
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Configure
Set token via env / `.env` / `.netrc` (see `auth/README.md`).

### Required `.env` variables
```dotenv
# Jira endpoint
JIRA_BASE_URL=https://jira.kdc.capitalone.com

# Auth token
JIRA_PAT=replace_me
# For Jira Cloud (.atlassian.net), also set:
# JIRA_EMAIL=you@company.com

# Scope selector: set exactly one of the next two
JIRA_CORE_FILTER_ID=1244128
# JIRA_CORE_JQL=project = COREPM AND issuetype in (Initiative, Epic, Story)

# Canonical dependency label
JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies
```

### Optional `.env` variables (recommended)
```dotenv
# Label typo/legacy cleanup
JIRA_DEPENDENCY_LABEL_ALIASES=DFS_CORE_Dependecies,DFS_CORE_Dependency,DFS_CORE_dependency

# CORE scope filters
JIRA_CORE_ISSUE_TYPES=Initiative,Epic,Story
JIRA_IGNORED_STATUSES=Canceled

# Preferred dependency-link matching mode: strict by Jira link type IDs
# (if set, IDs are used instead of JIRA_LINK_TYPES names)
JIRA_LINK_TYPE_IDS=
JIRA_LINK_DIRECTIONS=inward,outward

# Always ignore clone link relationships
JIRA_IGNORED_LINK_NAMES=clones,is cloned by
JIRA_IGNORED_LINK_TYPE_IDS=

# Name-based fallback only when JIRA_LINK_TYPE_IDS is empty
JIRA_LINK_TYPES=blocks,is blocked by,depends on,is dependent on,is a dependency of

# Runtime tuning
JIRA_AUTH_MODE=auto
JIRA_PAGE_SIZE=100
JIRA_REQUEST_TIMEOUT_SECONDS=30
```

### Rules and precedence
- Set only one of `JIRA_CORE_FILTER_ID` or `JIRA_CORE_JQL`.
- If `JIRA_LINK_TYPE_IDS` is set, dependency matching uses link `type.id` + `JIRA_LINK_DIRECTIONS`.
- If `JIRA_LINK_TYPE_IDS` is empty, dependency matching falls back to `JIRA_LINK_TYPES`.
- Clone links (`clones`, `is cloned by`) are excluded from dependency logic and cleanup checks.

## Run
Required flag policy:
- must pass exactly one of `--dry-run` or `--apply`

### Windows
```bash
py main.py --dry-run
py main.py --apply
```

### macOS
```bash
python3 main.py --dry-run
python3 main.py --apply
```

## Audit trail (apply mode)
On every `--apply` run, the script writes a timestamped audit file:
- Folder: `audit_logs/`
- Filename pattern: `label_sync_audit_YYYYMMDDTHHMMSSZ.json`

Each file includes:
- UTC run timestamp
- Jira base URL and scope inputs
- summary counts (scanned/found/added/removed)
- per-issue actions performed

## Logging output
Script prints:
- issues scanned
- dependencies found
- labels added
- labels removed
- per-issue action lines
- audit file path (for apply runs)

No token/secret values are logged.

## How Peter runs this later (macOS)
1. Clone/copy this `jira-poc` folder.
2. Set token in env/`.env`/`.netrc` (and `JIRA_EMAIL` if Jira Cloud).
3. Run `python3 main.py --dry-run` and review output.
4. Run `python3 main.py --apply`.

## TODOs for Phase 2 (optional)
- Add scheduler wrapper (still batch execution).
- Add audit mode output to JSON/CSV for change tracking.
- Add richer retry policy and request metrics.

## Non-goals
- Jira Automation rules
- ScriptRunner
- webhooks/listeners/services
- databases/snowflake
- dashboards/UI
