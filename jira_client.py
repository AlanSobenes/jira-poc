from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth

from auth.token_auth import resolve_jira_pat
from config import AppConfig


@dataclass
class SearchResult:
    issues: List[dict]
    diagnostics: "SearchDiagnostics"


@dataclass
class SearchDiagnostics:
    query_name: str
    api_mode: str
    pages_fetched: int
    issues_fetched: int
    reported_total: Optional[int]
    ended_by: str


class JiraClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        hostname = urlparse(config.jira_base_url).hostname or ""
        self.is_cloud = hostname.endswith(".atlassian.net")
        self.search_diagnostics: List[SearchDiagnostics] = []

        token = resolve_jira_pat(config.jira_token_env_var, hostname)
        auth_mode = self._resolve_auth_mode(config.jira_auth_mode, hostname)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        if auth_mode == "basic":
            email = os.getenv(config.jira_email_env_var, "").strip()
            if not email:
                raise RuntimeError(
                    f"JIRA auth mode is basic, but {config.jira_email_env_var} is not set. "
                    "Set your Jira account email for Cloud auth."
                )
            self.session.auth = HTTPBasicAuth(email, token)
        else:
            self.session.headers["Authorization"] = f"Bearer {token}"

    @staticmethod
    def _resolve_auth_mode(configured_mode: str, hostname: str) -> str:
        if configured_mode in ("basic", "bearer"):
            return configured_mode

        if hostname.endswith(".atlassian.net"):
            return "basic"

        return "bearer"

    def _issue_api_base(self) -> str:
        return "/rest/api/3" if self.is_cloud else "/rest/api/2"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.config.jira_base_url}{path}"
        timeout = kwargs.pop("timeout", self.config.request_timeout_seconds)

        attempts = 0
        while True:
            attempts += 1
            response = self.session.request(method=method, url=url, timeout=timeout, **kwargs)

            if response.status_code in (429, 500, 502, 503, 504) and attempts < 4:
                retry_after = response.headers.get("Retry-After")
                sleep_seconds = float(retry_after) if retry_after else (2 ** (attempts - 1))
                time.sleep(sleep_seconds)
                continue

            if response.status_code >= 400:
                raise RuntimeError(f"Jira API error {response.status_code} for {path}: {response.text[:400]}")

            if not response.text:
                return {}
            return response.json()

    def search_issues(self, jql: str, fields: List[str], query_name: str = "search") -> SearchResult:
        if self.is_cloud:
            return self._search_issues_cloud(jql, fields, query_name=query_name)
        return self._search_issues_legacy(jql, fields, query_name=query_name)

    def _search_issues_legacy(self, jql: str, fields: List[str], query_name: str) -> SearchResult:
        issues: List[dict] = []
        start_at = 0
        page_index = 0
        pages_fetched = 0
        reported_total: Optional[int] = None
        ended_by = "unknown"

        while True:
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": self.config.page_size,
                "fields": fields,
            }
            data = self._request("POST", "/rest/api/2/search", json=payload)
            page_issues = data.get("issues", [])
            issues.extend(page_issues)

            raw_total = data.get("total")
            if isinstance(raw_total, int):
                reported_total = raw_total

            pages_fetched += 1
            print(
                f"PAGINATION[{query_name}][legacy] page={page_index} "
                f"size={len(page_issues)} cumulative={len(issues)} total={reported_total}"
            )

            start_at += len(page_issues)
            if not page_issues:
                ended_by = "empty_page"
                break
            if reported_total is not None and start_at >= reported_total:
                ended_by = "reported_total_reached"
                break
            page_index += 1

        diagnostics = SearchDiagnostics(
            query_name=query_name,
            api_mode="legacy",
            pages_fetched=pages_fetched,
            issues_fetched=len(issues),
            reported_total=reported_total,
            ended_by=ended_by,
        )
        self.search_diagnostics.append(diagnostics)

        if diagnostics.reported_total is not None and diagnostics.reported_total != diagnostics.issues_fetched:
            print(
                f"PAGINATION[{query_name}][legacy] mismatch: fetched={diagnostics.issues_fetched} "
                f"reported_total={diagnostics.reported_total}"
            )

        return SearchResult(issues=issues, diagnostics=diagnostics)

    def _search_issues_cloud(self, jql: str, fields: List[str], query_name: str) -> SearchResult:
        issues: List[dict] = []
        next_page_token: Optional[str] = None
        page_index = 0
        pages_fetched = 0
        reported_total: Optional[int] = None
        ended_by = "unknown"

        while True:
            params: Dict[str, object] = {
                "jql": jql,
                "maxResults": self.config.page_size,
                "fields": ",".join(fields),
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token

            data = self._request("GET", "/rest/api/3/search/jql", params=params)
            page_issues = data.get("issues", [])
            issues.extend(page_issues)

            raw_total = data.get("total")
            if isinstance(raw_total, int):
                reported_total = raw_total

            next_page_token = data.get("nextPageToken")
            pages_fetched += 1
            print(
                f"PAGINATION[{query_name}][cloud] page={page_index} "
                f"size={len(page_issues)} cumulative={len(issues)} next_token={'yes' if next_page_token else 'no'}"
            )

            if not page_issues:
                ended_by = "empty_page"
                break
            if not next_page_token:
                ended_by = "next_page_token_exhausted"
                break
            page_index += 1

        diagnostics = SearchDiagnostics(
            query_name=query_name,
            api_mode="cloud",
            pages_fetched=pages_fetched,
            issues_fetched=len(issues),
            reported_total=reported_total,
            ended_by=ended_by,
        )
        self.search_diagnostics.append(diagnostics)

        if diagnostics.reported_total is not None and diagnostics.reported_total != diagnostics.issues_fetched:
            print(
                f"PAGINATION[{query_name}][cloud] mismatch: fetched={diagnostics.issues_fetched} "
                f"reported_total={diagnostics.reported_total}"
            )

        return SearchResult(issues=issues, diagnostics=diagnostics)

    def get_issue(self, issue_key: str, fields: List[str]) -> dict:
        fields_query = ",".join(fields)
        return self._request("GET", f"{self._issue_api_base()}/issue/{issue_key}?fields={fields_query}")

    def update_issue_labels(self, issue_key: str, labels_to_add: Set[str], labels_to_remove: Set[str]) -> None:
        operations: List[dict] = []

        for label in sorted(labels_to_add):
            operations.append({"add": label})

        for label in sorted(labels_to_remove):
            operations.append({"remove": label})

        if not operations:
            return

        self._request("PUT", f"{self._issue_api_base()}/issue/{issue_key}", json={"update": {"labels": operations}})

    def build_core_jql(self) -> str:
        if self.config.jira_core_filter_id:
            return f"filter = {self.config.jira_core_filter_id}"

        assert self.config.jira_core_jql is not None
        return self.config.jira_core_jql

    def labeled_issues_jql(self, labels: Iterable[str]) -> str:
        unique_labels: List[str] = []
        seen: Set[str] = set()

        if isinstance(labels, str):
            raw_labels = [labels]
        else:
            raw_labels = list(labels)

        for label in raw_labels:
            normalized = label.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_labels.append(label.replace('"', '\\"'))

        if not unique_labels:
            raise RuntimeError("At least one label is required for cleanup JQL.")

        if len(unique_labels) == 1:
            label_clause = f'labels = "{unique_labels[0]}"'
        else:
            in_list = ", ".join(f'"{label}"' for label in unique_labels)
            label_clause = f"labels IN ({in_list})"

        ignored = self.config.ignored_statuses
        if ignored:
            statuses = ", ".join(f'"{s}"' for s in ignored)
            return f"{label_clause} AND status NOT IN ({statuses})"
        return label_clause

    def pagination_summary(self) -> Dict[str, int]:
        mismatch_count = sum(
            1
            for item in self.search_diagnostics
            if item.reported_total is not None and item.reported_total != item.issues_fetched
        )
        return {
            "queries_executed": len(self.search_diagnostics),
            "pages_fetched": sum(item.pages_fetched for item in self.search_diagnostics),
            "issues_fetched": sum(item.issues_fetched for item in self.search_diagnostics),
            "reported_total_mismatches": mismatch_count,
        }

    @staticmethod
    def extract_issue_key(issue_ref: Optional[dict]) -> Optional[str]:
        if not issue_ref:
            return None
        return issue_ref.get("key")

    @staticmethod
    def get_labels(issue: dict) -> List[str]:
        return issue.get("fields", {}).get("labels", []) or []

    @staticmethod
    def get_status_name(issue: dict) -> str:
        return (issue.get("fields", {}).get("status", {}) or {}).get("name", "")

    @staticmethod
    def get_issue_type(issue: dict) -> str:
        return (issue.get("fields", {}).get("issuetype", {}) or {}).get("name", "")

    @staticmethod
    def get_issue_links(issue: dict) -> Iterable[dict]:
        return issue.get("fields", {}).get("issuelinks", []) or []
