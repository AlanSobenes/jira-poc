from __future__ import annotations

import argparse
import sys

from config import ConfigError, load_config
from dependency_logic import apply_changes, build_changes
from jira_client import JiraClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch Jira dependency label sync for external issues.",
        add_help=True,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only.")
    parser.add_argument("--apply", action="store_true", help="Apply label mutations in Jira.")

    args = parser.parse_args()

    if args.dry_run == args.apply:
        parser.error("Pass exactly one flag: --dry-run or --apply")

    return args


def main() -> int:
    args = parse_args()

    try:
        config = load_config()
        client = JiraClient(config)
        changes, stats = build_changes(client, config, include_diagnostics=args.dry_run)

        apply_changes(client, config, changes, apply=args.apply, stats=stats)
        pagination = client.pagination_summary()

        print("----- SUMMARY -----")
        print(f"Issues scanned: {stats.issues_scanned}")
        print(f"Dependencies found: {stats.dependencies_found}")
        print(f"Labels added: {stats.labels_added}")
        print(f"Labels removed: {stats.labels_removed}")
        print(f"Search queries executed: {pagination['queries_executed']}")
        print(f"Search pages fetched: {pagination['pages_fetched']}")
        print(f"Search issues fetched: {pagination['issues_fetched']}")
        print(f"Pagination mismatches: {pagination['reported_total_mismatches']}")
        print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
        return 0

    except (ConfigError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
