"""One sync run: fetch both systems → reconcile → (optionally) push safe updates → report → alert."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .http import ApiClient, ApiError, TokenProvider
from .reconcile import Plan, reconcile
from .report import alert_text, write_reports
from .sources import ShopClient, WarehouseClient

log = logging.getLogger(__name__)


def build_clients(settings: Settings) -> tuple[WarehouseClient, ShopClient]:
    wh_http = httpx.Client(base_url=settings.warehouse_base_url, timeout=30)
    token = TokenProvider(wh_http, "oauth/token", settings.warehouse_client_id, settings.warehouse_client_secret)
    shop_http = httpx.Client(base_url=settings.shop_base_url, timeout=30)
    return (
        WarehouseClient(ApiClient(wh_http, auth=token)),
        ShopClient(ApiClient(shop_http, headers={"X-Api-Key": settings.shop_api_key})),
    )


def idempotency_key(day: str, sku: str, target: int) -> str:
    """Same day + SKU + target value → same key, so a retried or re-run update is applied only once."""
    return hashlib.sha256(f"{day}:{sku}:{target}".encode()).hexdigest()[:32]


def run_sync(
    settings: Settings,
    *,
    apply: bool = False,
    warehouse: WarehouseClient | None = None,
    shop: ShopClient | None = None,
    reports_dir: Path | None = None,
    post_alert: Callable[[str, str], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[dict[str, Any], Plan]:
    if warehouse is None or shop is None:
        warehouse, shop = build_clients(settings)
    started = now()

    stock = warehouse.fetch_stock()
    inventory = shop.fetch_inventory()
    plan = reconcile(
        {sku: row.available_units for sku, row in stock.items()},
        inventory,
        review_ratio=settings.review_ratio,
        review_min_units=settings.review_min_units,
    )

    updated, failed = 0, []
    if apply:
        day = started.date().isoformat()
        for line in plan.updates:
            assert line.warehouse_units is not None
            try:
                shop.set_available(line.sku, line.warehouse_units, idempotency_key(day, line.sku, line.warehouse_units))
                updated += 1
            except ApiError as exc:
                log.error("update failed for %s: %s", line.sku, exc)
                failed.append({"sku": line.sku, "error": str(exc)})

    summary = plan.summary()
    summary["needs_attention"] = summary["needs_review"] + len(failed)
    finished = now()
    result: dict[str, Any] = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "applied_mode": apply,
        "summary": summary,
        "updated": updated,
        "failed": failed,
        "api_retries": warehouse.api.retries + shop.api.retries,
        "reports": {},
    }
    if reports_dir is not None:
        result["reports"] = write_reports(plan, result, reports_dir / finished.strftime("%Y%m%dT%H%M%SZ"))

    if settings.alert_webhook_url and summary["needs_attention"]:
        (post_alert or send_webhook)(settings.alert_webhook_url, alert_text(result))
    return result, plan


def send_webhook(url: str, text: str) -> None:
    """Post to a Slack- or Discord-compatible incoming webhook. Alert failures never break the run."""
    try:
        httpx.post(url, json={"text": text, "content": text}, timeout=10).raise_for_status()
    except httpx.HTTPError as exc:
        log.error("could not send alert: %s", exc)
