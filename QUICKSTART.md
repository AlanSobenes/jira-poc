# Quickstart

## 1. Create and activate venv
```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure `.env`

### Jira Cloud (`*.atlassian.net`)
```dotenv
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_AUTH_MODE=auto
JIRA_EMAIL=you@company.com
JIRA_PAT=your_api_token
JIRA_CORE_FILTER_ID=1244128
```

### Jira Server / Data Center
```dotenv
JIRA_BASE_URL=https://jira.company.com
JIRA_AUTH_MODE=auto
JIRA_PAT=your_pat
JIRA_CORE_FILTER_ID=1244128
```

## 3. Dry run first
```bash
py main.py --dry-run
```

## 4. Apply changes
```bash
py main.py --apply
```

## 5. Common errors
- `JIRA auth mode is basic, but JIRA_EMAIL is not set`
  - Set `JIRA_EMAIL` for Cloud.
- `API ... removed ... migrate to /rest/api/3/search/jql`
  - Fixed in current code; pull latest local file changes.
- `Set JIRA_CORE_FILTER_ID or JIRA_CORE_JQL`
  - Provide one (not both).
