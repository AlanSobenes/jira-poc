# Authentication

This POC supports both Jira Cloud and Jira Server/Data Center styles.

## Token Sources (priority)
1. Environment variable (default: `JIRA_PAT`)
2. `.env` file in project root (`jira-poc/.env`)
3. `~/.netrc` password for the configured Jira host

## Auth modes
### `JIRA_AUTH_MODE=auto` (default)
- If host ends with `.atlassian.net`: use Basic auth with `JIRA_EMAIL` + token
- Otherwise: use Bearer token auth (`Authorization: Bearer <token>`)

### `JIRA_AUTH_MODE=basic`
Always use Basic auth with:
- username: `JIRA_EMAIL`
- password: token from env/`.env`/`.netrc`

### `JIRA_AUTH_MODE=bearer`
Always use Bearer token auth.

## Examples
### Cloud
```dotenv
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_AUTH_MODE=auto
JIRA_EMAIL=you@company.com
JIRA_PAT=your-atlassian-api-token
```

### Server/Data Center
```dotenv
JIRA_BASE_URL=https://jira.company.com
JIRA_AUTH_MODE=auto
JIRA_PAT=your-pat
```

## Required permissions
Credentials must allow:
- read issues/search
- update issue labels on issues that will be labeled/unlabeled

## Rotation
1. Create a new token in Jira.
2. Replace token in env/`.env`/`.netrc`.
3. Re-run in dry-run, then apply mode.
4. Revoke old token.
