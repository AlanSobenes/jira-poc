from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Set

CANONICAL_LABEL = "DFS_CORE_Dependencies"
DEFAULT_BASE_URL = "https://jira-sandbox.atlassian.net"  # YOUR JIRA ENDPOINT
DEFAULT_CORE_FILTER_ID = "1244128"
DEFAULT_ISSUE_TYPES = ["Initiative", "Epic", "Story"]
DEFAULT_LINK_TYPES = ["blocks", "is blocked by", "depends on"]
DEFAULT_IGNORED_STATUSES = ["Canceled"]
VALID_AUTH_MODES = {"auto", "basic", "bearer"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv(dotenv_path: str = ".env") -> None:
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value


def _csv_env(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class AppConfig:
    jira_base_url: str
    jira_core_filter_id: Optional[str]
    jira_core_jql: Optional[str]
    jira_token_env_var: str
    jira_auth_mode: str
    jira_email_env_var: str
    dependency_label: str
    dependency_label_aliases: List[str]
    core_issue_types: List[str]
    authoritative_link_types: List[str]
    ignored_statuses: List[str]
    page_size: int
    request_timeout_seconds: int


def load_config() -> AppConfig:
    _load_dotenv()

    jira_base_url = os.getenv("JIRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    jira_core_jql = os.getenv("JIRA_CORE_JQL", "").strip() or None
    jira_core_filter_raw = os.getenv("JIRA_CORE_FILTER_ID")

    # If explicit JQL is provided, do not auto-inject a default filter id.
    if jira_core_jql:
        jira_core_filter_id = (jira_core_filter_raw or "").strip() or None
    else:
        fallback_filter = jira_core_filter_raw if jira_core_filter_raw is not None else DEFAULT_CORE_FILTER_ID
        jira_core_filter_id = (fallback_filter or "").strip() or None

    if not jira_core_filter_id and not jira_core_jql:
        raise ConfigError("Set JIRA_CORE_FILTER_ID or JIRA_CORE_JQL.")

    if jira_core_filter_id and jira_core_jql:
        raise ConfigError("Set only one of JIRA_CORE_FILTER_ID or JIRA_CORE_JQL, not both.")

    page_size_raw = os.getenv("JIRA_PAGE_SIZE", "100")
    timeout_raw = os.getenv("JIRA_REQUEST_TIMEOUT_SECONDS", "30")

    try:
        page_size = int(page_size_raw)
        timeout_seconds = int(timeout_raw)
    except ValueError as exc:
        raise ConfigError("JIRA_PAGE_SIZE and JIRA_REQUEST_TIMEOUT_SECONDS must be integers.") from exc

    jira_auth_mode = os.getenv("JIRA_AUTH_MODE", "auto").strip().lower()
    if jira_auth_mode not in VALID_AUTH_MODES:
        raise ConfigError("JIRA_AUTH_MODE must be one of: auto, basic, bearer.")

    dependency_label = os.getenv("JIRA_DEPENDENCY_LABEL", CANONICAL_LABEL).strip() or CANONICAL_LABEL
    dependency_label_aliases_raw = _csv_env("JIRA_DEPENDENCY_LABEL_ALIASES", [])
    dependency_label_aliases: List[str] = []
    seen_aliases: Set[str] = set()
    canonical_normalized = dependency_label.casefold()

    for alias in dependency_label_aliases_raw:
        normalized = alias.casefold()
        if normalized == canonical_normalized or normalized in seen_aliases:
            continue
        seen_aliases.add(normalized)
        dependency_label_aliases.append(alias)

    return AppConfig(
        jira_base_url=jira_base_url,
        jira_core_filter_id=jira_core_filter_id,
        jira_core_jql=jira_core_jql,
        jira_token_env_var=os.getenv("JIRA_TOKEN_ENV_VAR", "JIRA_PAT"),
        jira_auth_mode=jira_auth_mode,
        jira_email_env_var=os.getenv("JIRA_EMAIL_ENV_VAR", "JIRA_EMAIL"),
        dependency_label=dependency_label,
        dependency_label_aliases=dependency_label_aliases,
        core_issue_types=_csv_env("JIRA_CORE_ISSUE_TYPES", DEFAULT_ISSUE_TYPES),
        authoritative_link_types=[s.lower() for s in _csv_env("JIRA_LINK_TYPES", DEFAULT_LINK_TYPES)],
        ignored_statuses=_csv_env("JIRA_IGNORED_STATUSES", DEFAULT_IGNORED_STATUSES),
        page_size=page_size,
        request_timeout_seconds=timeout_seconds,
    )
