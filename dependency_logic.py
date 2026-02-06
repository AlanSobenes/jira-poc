from __future__ import annotations

import json
from dataclasses import dataclass
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
    add_label: bool = False
    remove_label: bool = False


def _is_authoritative_link(link: dict, config: AppConfig) -> bool:
    link_type = (link.get("type", {}) or {}).get("name", "").strip().lower()
    inward = (link.get("type", {}) or {}).get("inward", "").strip().lower()
    outward = (link.get("type", {}) or {}).get("outward", "").strip().lower()

    allowed = set(config.authoritative_link_types)
    return link_type in allowed or inward in allowed or outward in allowed


def _linked_issue_keys(issue: dict, client: JiraClient, config: AppConfig) -> Set[str]:
    keys: Set[str] = set()

    for link in client.get_issue_links(issue):
        if not _is_authoritative_link(link, config):
            continue

        outward_key = client.extract_issue_key(link.get("outwardIssue"))
        inward_key = client.extract_issue_key(link.get("inwardIssue"))

        if outward_key:
            keys.add(outward_key)
        if inward_key:
            keys.add(inward_key)

    return keys


def _allowed_by_scope(issue: dict, config: AppConfig) -> bool:
    issue_type = JiraClient.get_issue_type(issue)
    status = JiraClient.get_status_name(issue)

    return issue_type in config.core_issue_types and status not in set(config.ignored_statuses)


def load_core_scope(client: JiraClient, config: AppConfig) -> List[dict]:
    core_jql = client.build_core_jql()
    fields = ["issuetype", "status", "issuelinks", "labels"]
    result = client.search_issues(core_jql, fields=fields)
    return [issue for issue in result.issues if _allowed_by_scope(issue, config)]


def build_changes(client: JiraClient, config: AppConfig) -> Tuple[List[PlannedChange], RunStats]:
    stats = RunStats()
    changes: List[PlannedChange] = []

    core_issues = load_core_scope(client, config)
    core_keys = {issue["key"] for issue in core_issues}

    stats.issues_scanned = len(core_issues)

    for core_issue in core_issues:
        linked_keys = _linked_issue_keys(core_issue, client, config)
        for linked_key in linked_keys:
            if linked_key in core_keys:
                continue

            stats.dependencies_found += 1

            linked_issue = client.get_issue(linked_key, fields=["labels", "status"])
            linked_status = JiraClient.get_status_name(linked_issue)
            if linked_status in set(config.ignored_statuses):
                continue

            labels = set(JiraClient.get_labels(linked_issue))
            if config.dependency_label not in labels:
                changes.append(PlannedChange(issue_key=linked_key, add_label=True))

    label_jql = client.labeled_issues_jql(config.dependency_label)
    labeled = client.search_issues(label_jql, fields=["issuelinks", "labels", "status"]).issues

    for issue in labeled:
        issue_key = issue["key"]

        if issue_key in core_keys:
            continue

        status = JiraClient.get_status_name(issue)
        if status in set(config.ignored_statuses):
            continue

        still_depends_on_core = bool(_linked_issue_keys(issue, client, config) & core_keys)
        if not still_depends_on_core:
            labels = set(JiraClient.get_labels(issue))
            if config.dependency_label in labels:
                changes.append(PlannedChange(issue_key=issue_key, remove_label=True))

    deduped: Dict[str, PlannedChange] = {}
    for change in changes:
        existing = deduped.get(change.issue_key)
        if not existing:
            deduped[change.issue_key] = change
            continue
        existing.add_label = existing.add_label or change.add_label
        existing.remove_label = existing.remove_label or change.remove_label

    final_changes = [c for c in deduped.values() if c.add_label != c.remove_label]
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
        labels_to_add = {config.dependency_label} if change.add_label else set()
        labels_to_remove = {config.dependency_label} if change.remove_label else set()

        action_parts = []
        if change.add_label:
            action_parts.append(f"add {config.dependency_label}")
        if change.remove_label:
            action_parts.append(f"remove {config.dependency_label}")
        action_text = " and ".join(action_parts)

        if apply:
            client.update_issue_labels(change.issue_key, labels_to_add=labels_to_add, labels_to_remove=labels_to_remove)
            print(f"APPLY: {change.issue_key}: {action_text}")
        else:
            print(f"DRY-RUN: {change.issue_key}: {action_text}")

        if change.add_label:
            stats.labels_added += 1
        if change.remove_label:
            stats.labels_removed += 1

        applied_changes.append(
            {
                "issue_key": change.issue_key,
                "add_label": change.add_label,
                "remove_label": change.remove_label,
                "action": action_text,
            }
        )

    if apply:
        audit_path = _write_audit_file(config, stats, applied_changes)
        print(f"AUDIT: wrote {audit_path}")
