"""Command line entry point: `inventory-sync [--apply]`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Settings
from .http import ApiError
from .pipeline import run_sync
from .report import render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile warehouse stock with the online shop.")
    parser.add_argument("--apply", action="store_true", help="push safe updates to the shop (default: dry run)")
    parser.add_argument("--reports-dir", type=Path, help="where to write reports (default: $REPORTS_DIR)")
    parser.add_argument("--no-reports", action="store_true", help="do not write report files")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    reports_dir = None if args.no_reports else (args.reports_dir or Path(settings.reports_dir))
    try:
        result, plan = run_sync(settings, apply=args.apply, reports_dir=reports_dir)
    except ApiError as exc:
        logging.getLogger("inventory_sync").error("sync aborted, nothing was changed: %s", exc)
        return 2
    print(render_markdown(plan, result))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
