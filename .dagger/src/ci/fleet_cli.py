"""Fail-closed command-line entry point for the authoritative fleet scan."""

from __future__ import annotations

import argparse
import logging
import os

from ci.fleet import RepositoryResult, scan_fleet

LOGGER = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse the sole rollout-sensitive fleet option."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-central", action="store_true")
    return parser.parse_args()


def report(result: RepositoryResult) -> None:
    """Emit exact identity and actionable findings without credentials."""
    LOGGER.info("repo=%s sha=%s findings=%d", result.name, result.sha, len(result.findings))
    for item in result.findings:
        LOGGER.error("repo=%s code=%s path=%s %s", result.name, item.code, item.path, item.message)


def main() -> None:
    """Run the complete scan and fail if any evidence is inaccessible or invalid."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.environ["GITHUB_TOKEN"]
    results = scan_fleet(token, parse_arguments().include_central)
    for result in results:
        report(result)
    if any(result.findings for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
