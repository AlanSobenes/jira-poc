from __future__ import annotations

import netrc
import os
from typing import Optional


class AuthError(RuntimeError):
    """Raised when authentication configuration cannot be resolved."""


def _token_from_env(token_env_var: str) -> Optional[str]:
    token = os.getenv(token_env_var, "").strip()
    return token or None


def _token_from_netrc(hostname: str) -> Optional[str]:
    try:
        netrc_data = netrc.netrc()
    except (FileNotFoundError, netrc.NetrcParseError):
        return None

    auth = netrc_data.authenticators(hostname)
    if not auth:
        return None

    _, _, password = auth
    token = (password or "").strip()
    return token or None


def resolve_jira_pat(token_env_var: str, hostname: str) -> str:
    token = _token_from_env(token_env_var)
    if token:
        return token

    token = _token_from_netrc(hostname)
    if token:
        return token

    raise AuthError(
        f"Jira PAT not found. Set {token_env_var} in environment/.env or add token as password in ~/.netrc for {hostname}."
    )
