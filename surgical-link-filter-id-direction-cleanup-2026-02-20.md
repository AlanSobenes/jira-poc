# Surgical Patch: Dependency Link Type ID + Direction + Clone Cleanup

Date: 2026-02-20

## Goal

Use only authoritative dependency links from `issuelinks`, based on:
- link `type.id` (preferred, when configured)
- direction (`inward` / `outward`)

And explicitly ignore clone links so mislabeled issues are cleaned up.

## Files to update

1. `config.py`
2. `dependency_logic.py`

## Patch 1: `config.py`

Add support for:
- `JIRA_LINK_TYPE_IDS` (CSV of Jira link type IDs)
- `JIRA_LINK_DIRECTIONS` (CSV: `inward,outward`)
- `JIRA_IGNORED_LINK_TYPE_IDS` (optional clone/wrong-type IDs)
- `JIRA_IGNORED_LINK_NAMES` (defaults include clone names)

Also expands default link names to include soft dependency wording.

## Patch 2: `dependency_logic.py`

Behavior added:
- Evaluate each link direction independently (`outwardIssue` vs `inwardIssue`).
- Exclude links matching clone ignore rules.
- If `JIRA_LINK_TYPE_IDS` is set, use `type.id + direction` filtering.
- Otherwise, fallback to name matching (`JIRA_LINK_TYPES`) with direction awareness.
- Cleanup removal now clearly states clone/non-authoritative links were ignored.

## `.env` settings to send

```dotenv
# Keep using JQL/filter setup already in use

# Preferred: strict ID + direction mode
JIRA_LINK_TYPE_IDS=<id_for_blocks>,<id_for_dependency_type>
JIRA_LINK_DIRECTIONS=inward,outward

# Explicitly ignore clone link types
JIRA_IGNORED_LINK_NAMES=clones,is cloned by
# Optional if known:
# JIRA_IGNORED_LINK_TYPE_IDS=<clone_type_id>

# Fallback name mode (used only if JIRA_LINK_TYPE_IDS is empty)
JIRA_LINK_TYPES=blocks,is blocked by,depends on,is dependent on,is a dependency of
```

## Expected behavior after patch

1. Issues linked via clone links do **not** count as dependencies.
2. Issues linked via non-authoritative link types do **not** count as dependencies.
3. Labeled issues with no remaining authoritative dependency links are scheduled for label removal.
4. Dry-run reason text will state clone/non-authoritative links were ignored for removals.
