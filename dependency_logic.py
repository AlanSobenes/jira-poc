from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from config import AppConfig
from jira_client import JiraClient


@dataclass
class RunStats:
    issues_scanned: int = 0
    dependencies_found: int = 0
    labels_added: int = 0
    labels_removed: int = 0


@dataclass
class PlannedChange:
    issue_key: str
    labels_to_add: Set[str] = field(default_factory=set)
    labels_to_remove: Set[str] = field(default_factory=set)
    reasons: List[str] = field(default_factory=list)


def _normalized_set(values: Iterable[str]) -> Set[str]:
    return {value.casefold() for value in values}


def _link_type_parts(link: dict) -> Tuple[str, str, str, str]:
    link_type = link.get("type", {}) or {}
    link_type_id = str(link_type.get("id", "")).strip()
    link_name = link_type.get("name", "").strip().casefold()
    inward = link_type.get("inward", "").strip().casefold()
    outward = link_type.get("outward", "").strip().casefold()
    return link_type_id, link_name, inward, outward


def _is_ignored_link(link: dict, config: AppConfig) -> bool:
    link_type_id, link_name, inward, outward = _link_type_parts(link)
    ignored_type_ids = set(config.ignored_link_type_ids)
    if link_type_id and link_type_id in ignored_type_ids:
        return True

    ignored_names = _normalized_set(config.ignored_link_names)
    return link_name in ignored_names or inward in ignored_names or outward in ignored_names


def _is_authoritative_link_direction(link: dict, direction: str, config: AppConfig) -> bool:
    if direction not in _normalized_set(config.authoritative_link_directions):
        return False
    if _is_ignored_link(link, config):
        return False

    link_type_id, link_name, inward, outward = _link_type_parts(link)
    authoritative_type_ids = set(config.authoritative_link_type_ids)
    if authoritative_type_ids:
        return bool(link_type_id) and link_type_id in authoritative_type_ids

    allowed_names = _normalized_set(config.authoritative_link_types)
    direction_name = outward if direction == "outward" else inward
    return link_name in allowed_names or direction_name in allowed_names


def _linked_issue_keys(issue: dict, client: JiraClient, config: AppConfig) -> Set[str]:
    keys: Set[str] = set()

    for link in client.get_issue_links(issue):
        outward_key = client.extract_issue_key(link.get("outwardIssue"))
        inward_key = client.extract_issue_key(link.get("inwardIssue"))

        if outward_key and _is_authoritative_link_direction(link, "outward", config):
            keys.add(outward_key)
        if inward_key and _is_authoritative_link_direction(link, "inward", config):
            keys.add(inward_key)

    return keys


def _allowed_by_scope(issue: dict, config: AppConfig) -> bool:
    issue_type = JiraClient.get_issue_type(issue)
    status = JiraClient.get_status_name(issue)

    return issue_type in config.core_issue_types and status not in set(config.ignored_statuses)


def _has_label(existing_labels: Iterable[str], label: str) -> bool:
    target = label.casefold()
    return any(existing.casefold() == target for existing in existing_labels)


def _matching_labels(existing_labels: Iterable[str], target_labels: Iterable[str]) -> Set[str]:
    targets = _normalized_set(target_labels)
    if not targets:
        return set()
    return {label for label in existing_labels if label.casefold() in targets}


def _upsert_change(
    changes_by_key: Dict[str, PlannedChange],
    issue_key: str,
    labels_to_add: Set[str],
    labels_to_remove: Set[str],
    reason: str,
) -> None:
    if not labels_to_add and not labels_to_remove:
        return

    change = changes_by_key.setdefault(issue_key, PlannedChange(issue_key=issue_key))
    change.labels_to_add.update(labels_to_add)
    change.labels_to_remove.update(labels_to_remove)

    if reason and reason not in change.reasons:
        change.reasons.append(reason)


def load_core_scope(client: JiraClient, config: AppConfig) -> List[dict]:
    core_jql = client.build_core_jql()
    fields = ["issuetype", "status", "issuelinks", "labels"]
    result = client.search_issues(core_jql, fields=fields, query_name="core-scope")
    return [issue for issue in result.issues if _allowed_by_scope(issue, config)]


def build_changes(client: JiraClient, config: AppConfig, include_diagnostics: bool = False) -> Tuple[List[PlannedChange], RunStats]:
    stats = RunStats()
    changes_by_key: Dict[str, PlannedChange] = {}
    ignored_statuses = _normalized_set(config.ignored_statuses)
    alias_labels = list(config.dependency_label_aliases)
    tracked_labels = [config.dependency_label, *alias_labels]

    core_issues = load_core_scope(client, config)
    core_keys = {issue["key"] for issue in core_issues}
    stats.issues_scanned = len(core_issues)
    inspected_dependency_issues: Set[str] = set()

    for core_issue in core_issues:
        linked_keys = _linked_issue_keys(core_issue, client, config)
        for linked_key in linked_keys:
            if linked_key in core_keys:
                continue

            stats.dependencies_found += 1

            if linked_key in inspected_dependency_issues:
                continue
            inspected_dependency_issues.add(linked_key)

            linked_issue = client.get_issue(linked_key, fields=["labels", "status", "issuelinks"])
            linked_status = JiraClient.get_status_name(linked_issue).casefold()
            if linked_status in ignored_statuses:
                continue

            labels = JiraClient.get_labels(linked_issue)
            canonical_missing = not _has_label(labels, config.dependency_label)
            aliases_present = _matching_labels(labels, alias_labels)
            labels_to_add = {config.dependency_label} if canonical_missing else set()
            labels_to_remove = set(aliases_present)

            reason_parts: List[str] = ["Still depends on CORE"]
            if canonical_missing:
                reason_parts.append("canonical label missing")
            if aliases_present:
                reason_parts.append(f"alias labels present: {sorted(aliases_present)}")

            _upsert_change(
                changes_by_key,
                issue_key=linked_key,
                labels_to_add=labels_to_add,
                labels_to_remove=labels_to_remove,
                reason="; ".join(reason_parts),
            )

    label_jql = client.labeled_issues_jql(tracked_labels)
    labeled = client.search_issues(
        label_jql,
        fields=["issuelinks", "labels", "status"],
        query_name="cleanup-scan",
    ).issues

    for issue in labeled:
        issue_key = issue["key"]

        if issue_key in core_keys:
            continue

        status = JiraClient.get_status_name(issue).casefold()
        if status in ignored_statuses:
            continue

        labels = JiraClient.get_labels(issue)
        canonical_present = _matching_labels(labels, [config.dependency_label])
        aliases_present = _matching_labels(labels, alias_labels)
        tracked_present = canonical_present | aliases_present

        if not tracked_present:
            continue

        linked_keys = _linked_issue_keys(issue, client, config)
        core_intersection = linked_keys & core_keys
        still_depends_on_core = bool(core_intersection)

        if still_depends_on_core:
            labels_to_add = {config.dependency_label} if not canonical_present else set()
            labels_to_remove = set(aliases_present)

            reason_parts = [f"Still depends on CORE via: {sorted(core_intersection)}"]
            if labels_to_add:
                reason_parts.append("add canonical label")
            if labels_to_remove:
                reason_parts.append(f"remove aliases: {sorted(labels_to_remove)}")

            _upsert_change(
                changes_by_key,
                issue_key=issue_key,
                labels_to_add=labels_to_add,
                labels_to_remove=labels_to_remove,
                reason="; ".join(reason_parts),
            )
            continue

        labels_to_remove = set(tracked_present)
        reason = "No authoritative dependency links to CORE remain (clone/non-authoritative link types ignored)"

        if include_diagnostics and labels_to_remove:
            print(
                f"REMOVE-DIAGNOSTIC: issue={issue_key} "
                f"linked_keys={sorted(linked_keys)} "
                f"core_intersection={sorted(core_intersection)} "
                f"current_labels={sorted(labels)} "
                f"reason={reason}"
            )

        _upsert_change(
            changes_by_key,
            issue_key=issue_key,
            labels_to_add=set(),
            labels_to_remove=labels_to_remove,
            reason=reason,
        )

    final_changes: List[PlannedChange] = []
    for change in changes_by_key.values():
        remove_normalized = {label.casefold() for label in change.labels_to_remove}
        if remove_normalized:
            change.labels_to_add = {
                label for label in change.labels_to_add if label.casefold() not in remove_normalized
            }

        if change.labels_to_add or change.labels_to_remove:
            final_changes.append(change)

    return final_changes, stats


def _write_audit_file(config: AppConfig, stats: RunStats, applied_changes: List[Dict[str, object]]) -> Path:
    now = datetime.now(timezone.utc)
    run_timestamp = now.isoformat()
    run_id = now.strftime("%Y%m%dT%H%M%SZ")

    audit_dir = Path(__file__).resolve().parent / "audit_logs"
    audit_dir.mkdir(parents=True, exist_ok=True)

    audit_path = audit_dir / f"label_sync_audit_{run_id}.json"
    payload = {
        "run_id": run_id,
        "generated_at_utc": run_timestamp,
        "mode": "APPLY",
        "jira_base_url": config.jira_base_url,
        "core_filter_id": config.jira_core_filter_id,
        "core_jql": config.jira_core_jql,
        "dependency_label": config.dependency_label,
        "dependency_label_aliases": config.dependency_label_aliases,
        "summary": {
            "issues_scanned": stats.issues_scanned,
            "dependencies_found": stats.dependencies_found,
            "labels_added": stats.labels_added,
            "labels_removed": stats.labels_removed,
        },
        "changes": applied_changes,
    }

    audit_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return audit_path


def apply_changes(client: JiraClient, config: AppConfig, changes: Iterable[PlannedChange], apply: bool, stats: RunStats) -> None:
    applied_changes: List[Dict[str, object]] = []

    for change in sorted(changes, key=lambda c: c.issue_key):
        labels_to_add = set(change.labels_to_add)
        labels_to_remove = set(change.labels_to_remove)

        if not labels_to_add and not labels_to_remove:
            continue

        action_parts: List[str] = []
        if labels_to_add:
            action_parts.append(f"add {sorted(labels_to_add)}")
        if labels_to_remove:
            action_parts.append(f"remove {sorted(labels_to_remove)}")
        action_text = " and ".join(action_parts)

        if apply:
            client.update_issue_labels(change.issue_key, labels_to_add=labels_to_add, labels_to_remove=labels_to_remove)
            print(f"APPLY: {change.issue_key}: {action_text}")
        else:
            print(f"DRY-RUN: {change.issue_key}: {action_text}")
            for reason in change.reasons:
                print(f"  reason: {reason}")

        stats.labels_added += len(labels_to_add)
        stats.labels_removed += len(labels_to_remove)

        applied_changes.append(
            {
                "issue_key": change.issue_key,
                "labels_to_add": sorted(labels_to_add),
                "labels_to_remove": sorted(labels_to_remove),
                "action": action_text,
                "reasons": change.reasons,
            }
        )

    if apply:
        audit_path = _write_audit_file(config, stats, applied_changes)
        print(f"AUDIT: wrote {audit_path}")
