"""Write run results as JSON (machines), CSV (spreadsheets) and Markdown (humans)."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .reconcile import Action, Plan


def write_reports(plan: Plan, result: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    flagged = [line for line in plan.lines if line.action is not Action.NONE]

    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(
            {**result, "lines": [{**asdict(line), "diff": line.diff} for line in flagged]},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    csv_path = out_dir / "discrepancies.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sku", "warehouse_units", "shop_units", "diff", "status", "action", "reason"])
        for line in flagged:
            writer.writerow(
                [
                    line.sku,
                    line.warehouse_units,
                    line.shop_units,
                    line.diff,
                    line.status.value,
                    line.action.value,
                    line.reason,
                ]
            )

    md_path = out_dir / "summary.md"
    md_path.write_text(render_markdown(plan, result), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def render_markdown(plan: Plan, result: dict[str, Any], limit: int = 15) -> str:
    s = result["summary"]
    mode = "APPLY" if result["applied_mode"] else "DRY RUN"
    out = [
        f"# Inventory sync — {result['finished_at']} ({mode})",
        "",
        "| SKUs | In sync | Mismatch | Missing in shop | Unknown to warehouse | Auto updates | Needs review |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {s['skus_total']} | {s['in_sync']} | {s['mismatch']} | {s['missing_in_shop']} | "
        f"{s['unknown_to_warehouse']} | {s['auto_updates']} | {s['needs_review']} |",
        "",
        f"Updates pushed: **{result['updated']}** · failed: **{len(result['failed'])}** · "
        f"API retries: {result['api_retries']}",
    ]
    if plan.reviews:
        out += ["", "## Needs review", "", "| SKU | Warehouse | Shop | Diff | Reason |", "|---|---:|---:|---:|---|"]
        for line in plan.reviews[:limit]:
            wh = "—" if line.warehouse_units is None else line.warehouse_units
            sh = "—" if line.shop_units is None else line.shop_units
            out.append(f"| {line.sku} | {wh} | {sh} | {line.diff:+d} | {line.reason} |")
        if len(plan.reviews) > limit:
            out.append(f"\n…and {len(plan.reviews) - limit} more in `discrepancies.csv`.")
    return "\n".join(out) + "\n"


def alert_text(result: dict[str, Any]) -> str:
    s = result["summary"]
    return (
        f"Inventory sync ({'apply' if result['applied_mode'] else 'dry run'}): "
        f"{s['needs_review']} SKU(s) need review, {result['updated']} updated, "
        f"{len(result['failed'])} failed. Report: {result['reports'].get('markdown', '-')}"
    )
