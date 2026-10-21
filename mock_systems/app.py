"""Two fake systems behaving like real-world APIs, including their annoying parts.

Warehouse (3PL) at /warehouse
    POST /oauth/token            OAuth2 client credentials, short-lived tokens
    GET  /v1/stock               offset/limit pagination (max 50), stock in *cartons*
Shop at /shop
    GET  /v1/inventory           page/per_page pagination with `next_page`, stock in *units*
    PUT  /v1/inventory/{sku}     honours `Idempotency-Key`

Chaos (tunable via env): every Nth stock call answers 429 with Retry-After, random 503s,
and tokens that expire quickly. Data is seeded, so every run starts from the same state.
"""

from __future__ import annotations

import os
import random
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

CLIENT_ID = "local-demo-client"
CLIENT_SECRET = "local-demo-secret"
SHOP_API_KEY = "local-demo-shop-key"


@dataclass
class State:
    warehouse: dict[str, dict] = field(default_factory=dict)
    shop: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, float] = field(default_factory=dict)
    idempotency: dict[str, dict] = field(default_factory=dict)
    stock_calls: int = 0
    shop_writes: int = 0


def seed_state(seed: int = 42, n_skus: int = 120) -> State:
    rng = random.Random(seed)
    state = State()
    for i in range(1, n_skus + 1):
        sku = f"SKU-{i:04d}"
        upc = rng.choice([6, 12, 24])
        cartons = rng.randint(0, 40)
        reserved = rng.randint(0, min(30, cartons * upc))
        state.warehouse[sku] = {"sku": sku, "cartons": cartons, "units_per_carton": upc, "reserved_units": reserved}
        available = cartons * upc - reserved
        roll = rng.random()
        if roll < 0.70:
            state.shop[sku] = available  # in sync
        elif roll < 0.90:
            state.shop[sku] = max(0, available + rng.choice([-1, 1]) * rng.randint(1, 9))  # small drift
        elif roll < 0.97:
            state.shop[sku] = available + rng.randint(60, 200)  # big jump → needs review
        # else: listing missing in the shop
    for j in range(1, 3):
        state.shop[f"SKU-9{j:03d}"] = rng.randint(5, 30)  # sold in shop, unknown to 3PL
    return state


class InventoryUpdate(BaseModel):
    available: int


def create_app(
    *,
    seed: int = 42,
    n_skus: int = 120,
    rate_limit_every: int = 7,
    token_ttl: int = 60,
    fail_rate: float = 0.03,
) -> FastAPI:
    state = seed_state(seed, n_skus)
    chaos = random.Random(seed + 1)
    app = FastAPI(title="mock systems")
    app.state.data = state
    warehouse = APIRouter(prefix="/warehouse")
    shop = APIRouter(prefix="/shop")

    @warehouse.post("/oauth/token")
    def token(grant_type: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)) -> dict:
        if grant_type != "client_credentials" or (client_id, client_secret) != (CLIENT_ID, CLIENT_SECRET):
            raise HTTPException(status_code=401, detail="invalid_client")
        value = secrets.token_urlsafe(24)
        state.tokens[value] = time.time() + token_ttl
        return {"access_token": value, "token_type": "Bearer", "expires_in": token_ttl}

    @warehouse.get("/v1/stock")
    def stock(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=50)):
        auth = request.headers.get("Authorization", "")
        token_value = auth.removeprefix("Bearer ")
        if state.tokens.get(token_value, 0) < time.time():
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        state.stock_calls += 1
        if rate_limit_every and state.stock_calls % rate_limit_every == 0:
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "1"})
        if chaos.random() < fail_rate:
            return JSONResponse({"error": "temporarily unavailable"}, status_code=503)
        rows = list(state.warehouse.values())
        return {"items": rows[offset : offset + limit], "total": len(rows)}

    def check_key(x_api_key: str | None) -> None:
        if x_api_key != SHOP_API_KEY:
            raise HTTPException(status_code=401, detail="invalid api key")

    @shop.get("/v1/inventory")
    def inventory(
        page: int = Query(1, ge=1),
        per_page: int = Query(100, ge=1, le=100),
        x_api_key: str | None = Header(None),
    ) -> dict:
        check_key(x_api_key)
        items = [{"sku": sku, "available": qty} for sku, qty in sorted(state.shop.items())]
        start = (page - 1) * per_page
        has_more = start + per_page < len(items)
        return {"data": items[start : start + per_page], "next_page": page + 1 if has_more else None}

    @shop.put("/v1/inventory/{sku}")
    def update(
        sku: str,
        body: InventoryUpdate,
        x_api_key: str | None = Header(None),
        idempotency_key: str | None = Header(None),
    ) -> dict:
        check_key(x_api_key)
        if sku not in state.shop:
            raise HTTPException(status_code=404, detail="unknown sku")
        if idempotency_key and idempotency_key in state.idempotency:
            return state.idempotency[idempotency_key]  # replay: do not apply twice
        if body.available < 0:
            raise HTTPException(status_code=422, detail="available must be >= 0")
        state.shop[sku] = body.available
        state.shop_writes += 1
        result = {"sku": sku, "available": body.available}
        if idempotency_key:
            state.idempotency[idempotency_key] = result
        return result

    app.include_router(warehouse)
    app.include_router(shop)
    return app


app = create_app(
    rate_limit_every=int(os.environ.get("MOCK_RATE_LIMIT_EVERY", 7)),
    token_ttl=int(os.environ.get("MOCK_TOKEN_TTL", 60)),
    fail_rate=float(os.environ.get("MOCK_FAIL_RATE", 0.03)),
)
