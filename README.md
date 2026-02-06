# Jira External Dependency Auto-Labeling POC

## What this POC does
Batch script that synchronizes one canonical label on external dependency issues:
- Canonical label: `DFS_CORE_Dependencies`
- CORE source of truth: saved filter ID `1244128` (or optional custom JQL)
- Dependency links inspected: `blocks`, `is blocked by`, `depends on`
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
Defaults already match your final inputs:
- `JIRA_BASE_URL=https://jira.kdc.capitalone.com`
- `JIRA_CORE_FILTER_ID=1244128`
- `JIRA_CORE_ISSUE_TYPES=Initiative,Epic,Story`
- `JIRA_LINK_TYPES=blocks,is blocked by,depends on`
- `JIRA_IGNORED_STATUSES=Canceled`
- `JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies`

Set token via env / `.env` / `.netrc` (see `auth/README.md`).

Optional `.env` example:
```dotenv
JIRA_PAT=replace_me
# For Jira Cloud (.atlassian.net), also set:
# JIRA_EMAIL=you@company.com

# Optional overrides:
# JIRA_BASE_URL=https://jira.kdc.capitalone.com
# JIRA_AUTH_MODE=auto
# JIRA_CORE_FILTER_ID=1244128
# JIRA_CORE_JQL=
# JIRA_CORE_ISSUE_TYPES=Initiative,Epic,Story
# JIRA_LINK_TYPES=blocks,is blocked by,depends on
# JIRA_IGNORED_STATUSES=Canceled
# JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies
```

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
