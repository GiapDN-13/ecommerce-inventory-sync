"""Run the real pipeline against the mock systems, in-process (no network, no sleeping)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from inventory_sync.config import Settings
from inventory_sync.http import ApiClient, ApiError, RetryPolicy, TokenProvider
from inventory_sync.pipeline import run_sync
from inventory_sync.sources import ShopClient, WarehouseClient
from mock_systems.app import CLIENT_ID, CLIENT_SECRET, SHOP_API_KEY, create_app

NO_SLEEP = {"sleep": lambda _: None, "retry": RetryPolicy(max_attempts=6)}


def wire(app):
    wh_http = TestClient(app, base_url="http://testserver/warehouse")
    shop_http = TestClient(app, base_url="http://testserver/shop")
    token = TokenProvider(wh_http, "oauth/token", CLIENT_ID, CLIENT_SECRET)
    return (
        WarehouseClient(ApiClient(wh_http, auth=token, **NO_SLEEP)),
        ShopClient(ApiClient(shop_http, headers={"X-Api-Key": SHOP_API_KEY}, **NO_SLEEP)),
    )


@pytest.fixture
def app():
    # every 3rd stock call is rate-limited → pagination must survive retries
    return create_app(rate_limit_every=3, fail_rate=0.0)


def test_dry_run_changes_nothing(app, tmp_path):
    wh, shop = wire(app)
    before = dict(app.state.data.shop)
    result, plan = run_sync(Settings(), apply=False, warehouse=wh, shop=shop, reports_dir=tmp_path)

    assert app.state.data.shop == before
    assert app.state.data.shop_writes == 0
    s = result["summary"]
    assert s["skus_total"] == 122  # 120 warehouse SKUs + 2 shop-only
    assert s["auto_updates"] > 0 and s["needs_review"] > 0
    assert result["api_retries"] > 0  # the rate limiter was hit and handled
    report_dir = next(tmp_path.iterdir())
    assert {p.name for p in report_dir.iterdir()} == {"report.json", "discrepancies.csv", "summary.md"}


def test_apply_converges_and_is_idempotent(app):
    wh, shop = wire(app)
    first, plan = run_sync(Settings(), apply=True, warehouse=wh, shop=shop)
    assert first["updated"] == len(plan.updates) > 0
    assert first["failed"] == []

    # Second run: everything safe is already fixed; only items needing a human remain.
    second, plan2 = run_sync(Settings(), apply=True, warehouse=wh, shop=shop)
    assert second["summary"]["auto_updates"] == 0
    assert second["summary"]["needs_review"] == first["summary"]["needs_review"]
    assert second["updated"] == 0


def test_alert_is_sent_only_when_attention_needed(app):
    wh, shop = wire(app)
    sent = []
    settings = Settings(alert_webhook_url="https://hooks.example/abc")
    run_sync(settings, warehouse=wh, shop=shop, post_alert=lambda url, text: sent.append(text))
    assert len(sent) == 1 and "need review" in sent[0]


def test_incomplete_pagination_aborts_before_any_write():
    def handler(request):
        # API claims 3 rows but only ever returns 2 → partial snapshot
        offset = int(request.url.params.get("offset", 0))
        items = (
            [{"sku": "A", "cartons": 1, "units_per_carton": 6}, {"sku": "B", "cartons": 1, "units_per_carton": 6}]
            if offset == 0
            else []
        )
        return httpx.Response(200, json={"items": items, "total": 3})

    http = httpx.Client(base_url="https://wh.example", transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError, match="incomplete warehouse snapshot"):
        WarehouseClient(ApiClient(http)).fetch_stock()
