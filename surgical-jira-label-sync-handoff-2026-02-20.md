# Surgical Handoff: Jira Label Sync Updates

Date: 2026-02-20
Source plan: `planned-jira-label-sync-2026-02-19.md`

## What I verified

- The plan is valid and coherent.
- Your local repo already contains uncommitted implementation changes in:
  - `config.py`
  - `dependency_logic.py`
  - `jira_client.py`
- To fully align with the plan outcomes, you should also update:
  - `main.py` (enable dry-run diagnostics + print pagination summary)
  - `.env` (add alias env var and ensure filter/JQL precedence is used correctly)
  - `README.md` (optional but recommended docs updates)

## How to send this to your leader

1. Send each patch block below.
2. He applies the edits in this order:
   1. `config.py`
   2. `dependency_logic.py`
   3. `jira_client.py`
   4. `main.py`
   5. `.env`
   6. `README.md` (optional)
3. Run `--dry-run` first to validate behavior before `--apply`.

---

## Patch A: Sync your current logic changes

Apply the exact diff below on his machine.

```diff
diff --git a/config.py b/config.py
index a07230a..b6aeaf9 100644
--- a/config.py
+++ b/config.py
@@ -2,7 +2,7 @@ from __future__ import annotations
 
 import os
 from dataclasses import dataclass
-from typing import List, Optional
+from typing import List, Optional, Set
 
 CANONICAL_LABEL = "DFS_CORE_Dependencies"
 DEFAULT_BASE_URL = "https://jira-sandbox.atlassian.net"  # YOUR JIRA ENDPOINT
@@ -51,6 +51,7 @@ class AppConfig:
     jira_auth_mode: str
     jira_email_env_var: str
     dependency_label: str
+    dependency_label_aliases: List[str]
     core_issue_types: List[str]
     authoritative_link_types: List[str]
     ignored_statuses: List[str]
@@ -62,8 +63,15 @@ def load_config() -> AppConfig:
     _load_dotenv()
 
     jira_base_url = os.getenv("JIRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
-    jira_core_filter_id = os.getenv("JIRA_CORE_FILTER_ID", DEFAULT_CORE_FILTER_ID).strip() or None
     jira_core_jql = os.getenv("JIRA_CORE_JQL", "").strip() or None
+    jira_core_filter_raw = os.getenv("JIRA_CORE_FILTER_ID")
+
+    # If explicit JQL is provided, do not auto-inject a default filter id.
+    if jira_core_jql:
+        jira_core_filter_id = (jira_core_filter_raw or "").strip() or None
+    else:
+        fallback_filter = jira_core_filter_raw if jira_core_filter_raw is not None else DEFAULT_CORE_FILTER_ID
+        jira_core_filter_id = (fallback_filter or "").strip() or None
 
     if not jira_core_filter_id and not jira_core_jql:
         raise ConfigError("Set JIRA_CORE_FILTER_ID or JIRA_CORE_JQL.")
@@ -84,6 +92,19 @@ def load_config() -> AppConfig:
     if jira_auth_mode not in VALID_AUTH_MODES:
         raise ConfigError("JIRA_AUTH_MODE must be one of: auto, basic, bearer.")
 
+    dependency_label = os.getenv("JIRA_DEPENDENCY_LABEL", CANONICAL_LABEL).strip() or CANONICAL_LABEL
+    dependency_label_aliases_raw = _csv_env("JIRA_DEPENDENCY_LABEL_ALIASES", [])
+    dependency_label_aliases: List[str] = []
+    seen_aliases: Set[str] = set()
+    canonical_normalized = dependency_label.casefold()
+
+    for alias in dependency_label_aliases_raw:
+        normalized = alias.casefold()
+        if normalized == canonical_normalized or normalized in seen_aliases:
+            continue
+        seen_aliases.add(normalized)
+        dependency_label_aliases.append(alias)
+
     return AppConfig(
         jira_base_url=jira_base_url,
         jira_core_filter_id=jira_core_filter_id,
@@ -91,7 +112,8 @@ def load_config() -> AppConfig:
         jira_token_env_var=os.getenv("JIRA_TOKEN_ENV_VAR", "JIRA_PAT"),
         jira_auth_mode=jira_auth_mode,
         jira_email_env_var=os.getenv("JIRA_EMAIL_ENV_VAR", "JIRA_EMAIL"),
-        dependency_label=os.getenv("JIRA_DEPENDENCY_LABEL", CANONICAL_LABEL),
+        dependency_label=dependency_label,
+        dependency_label_aliases=dependency_label_aliases,
         core_issue_types=_csv_env("JIRA_CORE_ISSUE_TYPES", DEFAULT_ISSUE_TYPES),
         authoritative_link_types=[s.lower() for s in _csv_env("JIRA_LINK_TYPES", DEFAULT_LINK_TYPES)],
         ignored_statuses=_csv_env("JIRA_IGNORED_STATUSES", DEFAULT_IGNORED_STATUSES),
diff --git a/dependency_logic.py b/dependency_logic.py
index 7c33f2c..a365f60 100644
--- a/dependency_logic.py
+++ b/dependency_logic.py
@@ -1,7 +1,7 @@
 from __future__ import annotations
 
 import json
-from dataclasses import dataclass
+from dataclasses import dataclass, field
 from datetime import datetime, timezone
 from pathlib import Path
 from typing import Dict, Iterable, List, Set, Tuple
@@ -21,8 +21,13 @@ class RunStats:
 @dataclass
 class PlannedChange:
     issue_key: str
-    add_label: bool = False
-    remove_label: bool = False
+    labels_to_add: Set[str] = field(default_factory=set)
+    labels_to_remove: Set[str] = field(default_factory=set)
+    reasons: List[str] = field(default_factory=list)
+
+
+def _normalized_set(values: Iterable[str]) -> Set[str]:
+    return {value.casefold() for value in values}
 
 
 def _is_authoritative_link(link: dict, config: AppConfig) -> bool:
@@ -59,21 +64,54 @@ def _allowed_by_scope(issue: dict, config: AppConfig) -> bool:
     return issue_type in config.core_issue_types and status not in set(config.ignored_statuses)
 
 
+def _has_label(existing_labels: Iterable[str], label: str) -> bool:
+    target = label.casefold()
+    return any(existing.casefold() == target for existing in existing_labels)
+
+
+def _matching_labels(existing_labels: Iterable[str], target_labels: Iterable[str]) -> Set[str]:
+    targets = _normalized_set(target_labels)
+    if not targets:
+        return set()
+    return {label for label in existing_labels if label.casefold() in targets}
+
+
+def _upsert_change(
+    changes_by_key: Dict[str, PlannedChange],
+    issue_key: str,
+    labels_to_add: Set[str],
+    labels_to_remove: Set[str],
+    reason: str,
+) -> None:
+    if not labels_to_add and not labels_to_remove:
+        return
+
+    change = changes_by_key.setdefault(issue_key, PlannedChange(issue_key=issue_key))
+    change.labels_to_add.update(labels_to_add)
+    change.labels_to_remove.update(labels_to_remove)
+
+    if reason and reason not in change.reasons:
+        change.reasons.append(reason)
+
+
 def load_core_scope(client: JiraClient, config: AppConfig) -> List[dict]:
     core_jql = client.build_core_jql()
     fields = ["issuetype", "status", "issuelinks", "labels"]
-    result = client.search_issues(core_jql, fields=fields)
+    result = client.search_issues(core_jql, fields=fields, query_name="core-scope")
     return [issue for issue in result.issues if _allowed_by_scope(issue, config)]
 
 
-def build_changes(client: JiraClient, config: AppConfig) -> Tuple[List[PlannedChange], RunStats]:
+def build_changes(client: JiraClient, config: AppConfig, include_diagnostics: bool = False) -> Tuple[List[PlannedChange], RunStats]:
     stats = RunStats()
-    changes: List[PlannedChange] = []
+    changes_by_key: Dict[str, PlannedChange] = {}
+    ignored_statuses = _normalized_set(config.ignored_statuses)
+    alias_labels = list(config.dependency_label_aliases)
+    tracked_labels = [config.dependency_label, *alias_labels]
 
     core_issues = load_core_scope(client, config)
     core_keys = {issue["key"] for issue in core_issues}
-
     stats.issues_scanned = len(core_issues)
+    inspected_dependency_issues: Set[str] = set()
 
     for core_issue in core_issues:
         linked_keys = _linked_issue_keys(core_issue, client, config)
@@ -83,17 +121,41 @@ def build_changes(client: JiraClient, config: AppConfig) -> Tuple[List[PlannedCh
 
             stats.dependencies_found += 1
 
-            linked_issue = client.get_issue(linked_key, fields=["labels", "status"])
-            linked_status = JiraClient.get_status_name(linked_issue)
-            if linked_status in set(config.ignored_statuses):
+            if linked_key in inspected_dependency_issues:
                 continue
+            inspected_dependency_issues.add(linked_key)
 
-            labels = set(JiraClient.get_labels(linked_issue))
-            if config.dependency_label not in labels:
-                changes.append(PlannedChange(issue_key=linked_key, add_label=True))
+            linked_issue = client.get_issue(linked_key, fields=["labels", "status", "issuelinks"])
+            linked_status = JiraClient.get_status_name(linked_issue).casefold()
+            if linked_status in ignored_statuses:
+                continue
 
-    label_jql = client.labeled_issues_jql(config.dependency_label)
-    labeled = client.search_issues(label_jql, fields=["issuelinks", "labels", "status"]).issues
+            labels = JiraClient.get_labels(linked_issue)
+            canonical_missing = not _has_label(labels, config.dependency_label)
+            aliases_present = _matching_labels(labels, alias_labels)
+            labels_to_add = {config.dependency_label} if canonical_missing else set()
+            labels_to_remove = set(aliases_present)
+
+            reason_parts: List[str] = ["Still depends on CORE"]
+            if canonical_missing:
+                reason_parts.append("canonical label missing")
+            if aliases_present:
+                reason_parts.append(f"alias labels present: {sorted(aliases_present)}")
+
+            _upsert_change(
+                changes_by_key,
+                issue_key=linked_key,
+                labels_to_add=labels_to_add,
+                labels_to_remove=labels_to_remove,
+                reason="; ".join(reason_parts),
+            )
+
+    label_jql = client.labeled_issues_jql(tracked_labels)
+    labeled = client.search_issues(
+        label_jql,
+        fields=["issuelinks", "labels", "status"],
+        query_name="cleanup-scan",
+    ).issues
 
     for issue in labeled:
         issue_key = issue["key"]
@@ -101,26 +163,72 @@ def build_changes(client: JiraClient, config: AppConfig) -> Tuple[List[PlannedCh
         if issue_key in core_keys:
             continue
 
-        status = JiraClient.get_status_name(issue)
-        if status in set(config.ignored_statuses):
+        status = JiraClient.get_status_name(issue).casefold()
+        if status in ignored_statuses:
             continue
 
-        still_depends_on_core = bool(_linked_issue_keys(issue, client, config) & core_keys)
-        if not still_depends_on_core:
-            labels = set(JiraClient.get_labels(issue))
-            if config.dependency_label in labels:
-                changes.append(PlannedChange(issue_key=issue_key, remove_label=True))
-
-    deduped: Dict[str, PlannedChange] = {}
-    for change in changes:
-        existing = deduped.get(change.issue_key)
-        if not existing:
-            deduped[change.issue_key] = change
+        labels = JiraClient.get_labels(issue)
+        canonical_present = _matching_labels(labels, [config.dependency_label])
+        aliases_present = _matching_labels(labels, alias_labels)
+        tracked_present = canonical_present | aliases_present
+
+        if not tracked_present:
+            continue
+
+        linked_keys = _linked_issue_keys(issue, client, config)
+        core_intersection = linked_keys & core_keys
+        still_depends_on_core = bool(core_intersection)
+
+        if still_depends_on_core:
+            labels_to_add = {config.dependency_label} if not canonical_present else set()
+            labels_to_remove = set(aliases_present)
+
+            reason_parts = [f"Still depends on CORE via: {sorted(core_intersection)}"]
+            if labels_to_add:
+                reason_parts.append("add canonical label")
+            if labels_to_remove:
+                reason_parts.append(f"remove aliases: {sorted(labels_to_remove)}")
+
+            _upsert_change(
+                changes_by_key,
+                issue_key=issue_key,
+                labels_to_add=labels_to_add,
+                labels_to_remove=labels_to_remove,
+                reason="; ".join(reason_parts),
+            )
             continue
-        existing.add_label = existing.add_label or change.add_label
-        existing.remove_label = existing.remove_label or change.remove_label
 
-    final_changes = [c for c in deduped.values() if c.add_label != c.remove_label]
+        labels_to_remove = set(tracked_present)
+        reason = "No authoritative links to CORE remain"
+
+        if include_diagnostics and labels_to_remove:
+            print(
+                f"REMOVE-DIAGNOSTIC: issue={issue_key} "
+                f"linked_keys={sorted(linked_keys)} "
+                f"core_intersection={sorted(core_intersection)} "
+                f"current_labels={sorted(labels)} "
+                f"reason={reason}"
+            )
+
+        _upsert_change(
+            changes_by_key,
+            issue_key=issue_key,
+            labels_to_add=set(),
+            labels_to_remove=labels_to_remove,
+            reason=reason,
+        )
+
+    final_changes: List[PlannedChange] = []
+    for change in changes_by_key.values():
+        remove_normalized = {label.casefold() for label in change.labels_to_remove}
+        if remove_normalized:
+            change.labels_to_add = {
+                label for label in change.labels_to_add if label.casefold() not in remove_normalized
+            }
+
+        if change.labels_to_add or change.labels_to_remove:
+            final_changes.append(change)
+
     return final_changes, stats
 
 
@@ -141,6 +249,7 @@ def _write_audit_file(config: AppConfig, stats: RunStats, applied_changes: List[
         "core_filter_id": config.jira_core_filter_id,
         "core_jql": config.jira_core_jql,
         "dependency_label": config.dependency_label,
+        "dependency_label_aliases": config.dependency_label_aliases,
         "summary": {
             "issues_scanned": stats.issues_scanned,
             "dependencies_found": stats.dependencies_found,
@@ -158,14 +267,17 @@ def apply_changes(client: JiraClient, config: AppConfig, changes: Iterable[Plann
     applied_changes: List[Dict[str, object]] = []
 
     for change in sorted(changes, key=lambda c: c.issue_key):
-        labels_to_add = {config.dependency_label} if change.add_label else set()
-        labels_to_remove = {config.dependency_label} if change.remove_label else set()
-
-        action_parts = []
-        if change.add_label:
-            action_parts.append(f"add {config.dependency_label}")
-        if change.remove_label:
-            action_parts.append(f"remove {config.dependency_label}")
+        labels_to_add = set(change.labels_to_add)
+        labels_to_remove = set(change.labels_to_remove)
+
+        if not labels_to_add and not labels_to_remove:
+            continue
+
+        action_parts: List[str] = []
+        if labels_to_add:
+            action_parts.append(f"add {sorted(labels_to_add)}")
+        if labels_to_remove:
+            action_parts.append(f"remove {sorted(labels_to_remove)}")
         action_text = " and ".join(action_parts)
 
         if apply:
@@ -173,18 +285,19 @@ def apply_changes(client: JiraClient, config: AppConfig, changes: Iterable[Plann
             print(f"APPLY: {change.issue_key}: {action_text}")
         else:
             print(f"DRY-RUN: {change.issue_key}: {action_text}")
+            for reason in change.reasons:
+                print(f"  reason: {reason}")
 
-        if change.add_label:
-            stats.labels_added += 1
-        if change.remove_label:
-            stats.labels_removed += 1
+        stats.labels_added += len(labels_to_add)
+        stats.labels_removed += len(labels_to_remove)
 
         applied_changes.append(
             {
                 "issue_key": change.issue_key,
-                "add_label": change.add_label,
-                "remove_label": change.remove_label,
+                "labels_to_add": sorted(labels_to_add),
+                "labels_to_remove": sorted(labels_to_remove),
                 "action": action_text,
+                "reasons": change.reasons,
             }
         )
 
diff --git a/jira_client.py b/jira_client.py
index 08d8ef1..224e48a 100644
--- a/jira_client.py
+++ b/jira_client.py
@@ -16,6 +16,17 @@ from config import AppConfig
 @dataclass
 class SearchResult:
     issues: List[dict]
+    diagnostics: "SearchDiagnostics"
+
+
+@dataclass
+class SearchDiagnostics:
+    query_name: str
+    api_mode: str
+    pages_fetched: int
+    issues_fetched: int
+    reported_total: Optional[int]
+    ended_by: str
 
 
 class JiraClient:
@@ -23,6 +34,7 @@ class JiraClient:
         self.config = config
         hostname = urlparse(config.jira_base_url).hostname or ""
         self.is_cloud = hostname.endswith(".atlassian.net")
+        self.search_diagnostics: List[SearchDiagnostics] = []
 
         token = resolve_jira_pat(config.jira_token_env_var, hostname)
         auth_mode = self._resolve_auth_mode(config.jira_auth_mode, hostname)
@@ -81,14 +93,18 @@ class JiraClient:
                 return {}
             return response.json()
 
-    def search_issues(self, jql: str, fields: List[str]) -> SearchResult:
+    def search_issues(self, jql: str, fields: List[str], query_name: str = "search") -> SearchResult:
         if self.is_cloud:
-            return self._search_issues_cloud(jql, fields)
-        return self._search_issues_legacy(jql, fields)
+            return self._search_issues_cloud(jql, fields, query_name=query_name)
+        return self._search_issues_legacy(jql, fields, query_name=query_name)
 
-    def _search_issues_legacy(self, jql: str, fields: List[str]) -> SearchResult:
+    def _search_issues_legacy(self, jql: str, fields: List[str], query_name: str) -> SearchResult:
         issues: List[dict] = []
         start_at = 0
+        page_index = 0
+        pages_fetched = 0
+        reported_total: Optional[int] = None
+        ended_by = "unknown"
 
         while True:
             payload = {
@@ -101,16 +117,50 @@ class JiraClient:
             page_issues = data.get("issues", [])
             issues.extend(page_issues)
 
-            total = data.get("total", 0)
+            raw_total = data.get("total")
+            if isinstance(raw_total, int):
+                reported_total = raw_total
+
+            pages_fetched += 1
+            print(
+                f"PAGINATION[{query_name}][legacy] page={page_index} "
+                f"size={len(page_issues)} cumulative={len(issues)} total={reported_total}"
+            )
+
             start_at += len(page_issues)
-            if start_at >= total or not page_issues:
+            if not page_issues:
+                ended_by = "empty_page"
+                break
+            if reported_total is not None and start_at >= reported_total:
+                ended_by = "reported_total_reached"
                 break
+            page_index += 1
+
+        diagnostics = SearchDiagnostics(
+            query_name=query_name,
+            api_mode="legacy",
+            pages_fetched=pages_fetched,
+            issues_fetched=len(issues),
+            reported_total=reported_total,
+            ended_by=ended_by,
+        )
+        self.search_diagnostics.append(diagnostics)
+
+        if diagnostics.reported_total is not None and diagnostics.reported_total != diagnostics.issues_fetched:
+            print(
+                f"PAGINATION[{query_name}][legacy] mismatch: fetched={diagnostics.issues_fetched} "
+                f"reported_total={diagnostics.reported_total}"
+            )
 
-        return SearchResult(issues=issues)
+        return SearchResult(issues=issues, diagnostics=diagnostics)
 
-    def _search_issues_cloud(self, jql: str, fields: List[str]) -> SearchResult:
+    def _search_issues_cloud(self, jql: str, fields: List[str], query_name: str) -> SearchResult:
         issues: List[dict] = []
         next_page_token: Optional[str] = None
+        page_index = 0
+        pages_fetched = 0
+        reported_total: Optional[int] = None
+        ended_by = "unknown"
 
         while True:
             params: Dict[str, object] = {
@@ -125,11 +175,42 @@ class JiraClient:
             page_issues = data.get("issues", [])
             issues.extend(page_issues)
 
+            raw_total = data.get("total")
+            if isinstance(raw_total, int):
+                reported_total = raw_total
+
             next_page_token = data.get("nextPageToken")
-            if not next_page_token or not page_issues:
+            pages_fetched += 1
+            print(
+                f"PAGINATION[{query_name}][cloud] page={page_index} "
+                f"size={len(page_issues)} cumulative={len(issues)} next_token={'yes' if next_page_token else 'no'}"
+            )
+
+            if not page_issues:
+                ended_by = "empty_page"
                 break
+            if not next_page_token:
+                ended_by = "next_page_token_exhausted"
+                break
+            page_index += 1
+
+        diagnostics = SearchDiagnostics(
+            query_name=query_name,
+            api_mode="cloud",
+            pages_fetched=pages_fetched,
+            issues_fetched=len(issues),
+            reported_total=reported_total,
+            ended_by=ended_by,
+        )
+        self.search_diagnostics.append(diagnostics)
 
-        return SearchResult(issues=issues)
+        if diagnostics.reported_total is not None and diagnostics.reported_total != diagnostics.issues_fetched:
+            print(
+                f"PAGINATION[{query_name}][cloud] mismatch: fetched={diagnostics.issues_fetched} "
+                f"reported_total={diagnostics.reported_total}"
+            )
+
+        return SearchResult(issues=issues, diagnostics=diagnostics)
 
     def get_issue(self, issue_key: str, fields: List[str]) -> dict:
         fields_query = ",".join(fields)
@@ -156,13 +237,49 @@ class JiraClient:
         assert self.config.jira_core_jql is not None
         return self.config.jira_core_jql
 
-    def labeled_issues_jql(self, label: str) -> str:
-        escaped = label.replace('"', '\\"')
+    def labeled_issues_jql(self, labels: Iterable[str]) -> str:
+        unique_labels: List[str] = []
+        seen: Set[str] = set()
+
+        if isinstance(labels, str):
+            raw_labels = [labels]
+        else:
+            raw_labels = list(labels)
+
+        for label in raw_labels:
+            normalized = label.casefold()
+            if normalized in seen:
+                continue
+            seen.add(normalized)
+            unique_labels.append(label.replace('"', '\\"'))
+
+        if not unique_labels:
+            raise RuntimeError("At least one label is required for cleanup JQL.")
+
+        if len(unique_labels) == 1:
+            label_clause = f'labels = "{unique_labels[0]}"'
+        else:
+            in_list = ", ".join(f'"{label}"' for label in unique_labels)
+            label_clause = f"labels IN ({in_list})"
+
         ignored = self.config.ignored_statuses
         if ignored:
             statuses = ", ".join(f'"{s}"' for s in ignored)
-            return f'labels = "{escaped}" AND status NOT IN ({statuses})'
-        return f'labels = "{escaped}"'
+            return f"{label_clause} AND status NOT IN ({statuses})"
+        return label_clause
+
+    def pagination_summary(self) -> Dict[str, int]:
+        mismatch_count = sum(
+            1
+            for item in self.search_diagnostics
+            if item.reported_total is not None and item.reported_total != item.issues_fetched
+        )
+        return {
+            "queries_executed": len(self.search_diagnostics),
+            "pages_fetched": sum(item.pages_fetched for item in self.search_diagnostics),
+            "issues_fetched": sum(item.issues_fetched for item in self.search_diagnostics),
+            "reported_total_mismatches": mismatch_count,
+        }
 
     @staticmethod
     def extract_issue_key(issue_ref: Optional[dict]) -> Optional[str]:
```

---

## Patch B: Complete planned output wiring (`main.py`)

```diff
diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@
 def main() -> int:
     args = parse_args()
 
     try:
         config = load_config()
         client = JiraClient(config)
-        changes, stats = build_changes(client, config)
+        changes, stats = build_changes(client, config, include_diagnostics=args.dry_run)
 
         apply_changes(client, config, changes, apply=args.apply, stats=stats)
+        pagination = client.pagination_summary()
 
         print("----- SUMMARY -----")
         print(f"Issues scanned: {stats.issues_scanned}")
         print(f"Dependencies found: {stats.dependencies_found}")
         print(f"Labels added: {stats.labels_added}")
         print(f"Labels removed: {stats.labels_removed}")
+        print(f"Search queries executed: {pagination['queries_executed']}")
+        print(f"Search pages fetched: {pagination['pages_fetched']}")
+        print(f"Search issues fetched: {pagination['issues_fetched']}")
+        print(f"Pagination mismatches: {pagination['reported_total_mismatches']}")
         print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
         return 0
```

---

## Patch C: `.env` additions/normalization

Do not share PAT values in chat. Only share non-secret keys.

```dotenv
# Keep token/email as already configured on his machine:
# JIRA_PAT=...
# JIRA_EMAIL=...   # needed for Jira Cloud

# Set exactly one scope selector:
JIRA_CORE_FILTER_ID=1244128
# JIRA_CORE_JQL=

# If using JQL instead, do this instead:
# JIRA_CORE_FILTER_ID=
# JIRA_CORE_JQL=project = DFS AND issuetype in (Initiative, Epic, Story)

JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies
JIRA_DEPENDENCY_LABEL_ALIASES=DFS_CORE_Dependecies,DFS_CORE_Dependency,DFS_CORE_dependency
```

---

## Patch D: README update (optional, recommended)

```diff
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@
 Defaults already match your final inputs:
 - `JIRA_BASE_URL=https://jira.kdc.capitalone.com`
 - `JIRA_CORE_FILTER_ID=1244128`
+- `JIRA_CORE_JQL=` (optional alternative to filter ID; set one or the other)
 - `JIRA_CORE_ISSUE_TYPES=Initiative,Epic,Story`
 - `JIRA_LINK_TYPES=blocks,is blocked by,depends on`
 - `JIRA_IGNORED_STATUSES=Canceled`
 - `JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies`
+- `JIRA_DEPENDENCY_LABEL_ALIASES=` (optional CSV for typo/legacy labels)
+
+Precedence rule:
+- If `JIRA_CORE_JQL` is set, leave `JIRA_CORE_FILTER_ID` unset/empty.
+- If `JIRA_CORE_JQL` is empty, default filter behavior applies.
@@
 # Optional overrides:
 # JIRA_BASE_URL=https://jira.kdc.capitalone.com
 # JIRA_AUTH_MODE=auto
 # JIRA_CORE_FILTER_ID=1244128
 # JIRA_CORE_JQL=
 # JIRA_CORE_ISSUE_TYPES=Initiative,Epic,Story
 # JIRA_LINK_TYPES=blocks,is blocked by,depends on
 # JIRA_IGNORED_STATUSES=Canceled
 # JIRA_DEPENDENCY_LABEL=DFS_CORE_Dependencies
+# JIRA_DEPENDENCY_LABEL_ALIASES=DFS_CORE_Dependecies,DFS_CORE_Dependency
```

---

## Validation run checklist for him

1. `py main.py --dry-run`
2. Confirm output includes:
   - `PAGINATION[core-scope]...`
   - `PAGINATION[cleanup-scan]...`
   - `REMOVE-DIAGNOSTIC: ...` lines for removals
   - summary pagination fields (`queries_executed`, `pages_fetched`, etc.)
3. If dry-run output looks correct, run: `py main.py --apply`

