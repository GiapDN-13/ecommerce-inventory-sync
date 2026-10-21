"""Settings from environment variables (see .env.example). Defaults point at the local mock systems."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    warehouse_base_url: str = "http://localhost:9000/warehouse"
    warehouse_client_id: str = "local-demo-client"
    warehouse_client_secret: str = "local-demo-secret"
    shop_base_url: str = "http://localhost:9000/shop"
    shop_api_key: str = "local-demo-shop-key"
    alert_webhook_url: str | None = None
    review_ratio: float = 0.5
    review_min_units: int = 20
    reports_dir: str = "reports"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        d = cls()
        return cls(
            warehouse_base_url=env.get("WAREHOUSE_BASE_URL", d.warehouse_base_url),
            warehouse_client_id=env.get("WAREHOUSE_CLIENT_ID", d.warehouse_client_id),
            warehouse_client_secret=env.get("WAREHOUSE_CLIENT_SECRET", d.warehouse_client_secret),
            shop_base_url=env.get("SHOP_BASE_URL", d.shop_base_url),
            shop_api_key=env.get("SHOP_API_KEY", d.shop_api_key),
            alert_webhook_url=env.get("ALERT_WEBHOOK_URL") or None,
            review_ratio=float(env.get("REVIEW_RATIO", d.review_ratio)),
            review_min_units=int(env.get("REVIEW_MIN_UNITS", d.review_min_units)),
            reports_dir=env.get("REPORTS_DIR", d.reports_dir),
        )
