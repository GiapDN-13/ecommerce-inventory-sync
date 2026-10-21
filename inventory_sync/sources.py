"""Clients for the two systems being reconciled.

* Warehouse (3PL): OAuth2 bearer token, offset/limit pagination, stock reported in cartons.
* Shop: API key, page-number pagination with ``next_page``, stock reported in units.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from .http import ApiClient, ApiError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WarehouseStock:
    sku: str
    cartons: int
    units_per_carton: int
    reserved_units: int

    @property
    def available_units(self) -> int:
        """Sellable units: everything on hand minus units already reserved for open orders."""
        return max(0, self.cartons * self.units_per_carton - self.reserved_units)


class WarehouseClient:
    PAGE_SIZE = 50  # API maximum

    def __init__(self, api: ApiClient) -> None:
        self.api = api

    def iter_stock(self) -> Iterator[tuple[WarehouseStock, int]]:
        offset = 0
        while True:
            body = self.api.get("v1/stock", params={"offset": offset, "limit": self.PAGE_SIZE}).json()
            items = body.get("items", [])
            if not items:
                return
            for item in items:
                yield (
                    WarehouseStock(
                        sku=item["sku"],
                        cartons=int(item["cartons"]),
                        units_per_carton=int(item["units_per_carton"]),
                        reserved_units=int(item.get("reserved_units", 0)),
                    ),
                    int(body["total"]),
                )
            offset += len(items)

    def fetch_stock(self) -> dict[str, WarehouseStock]:
        """Fetch every page and verify we got exactly what the API says it has."""
        stock: dict[str, WarehouseStock] = {}
        reported_total = 0
        seen = 0
        for row, total in self.iter_stock():
            seen += 1
            reported_total = total
            if row.sku in stock:
                log.warning("duplicate SKU %s across pages; keeping the latest row", row.sku)
            stock[row.sku] = row
        if seen != reported_total:
            raise ApiError(
                f"incomplete warehouse snapshot: received {seen} rows but API reports {reported_total}. "
                "Data probably changed during pagination — rerun instead of syncing partial data."
            )
        log.info("warehouse: %d SKUs", len(stock))
        return stock


class ShopClient:
    PER_PAGE = 100

    def __init__(self, api: ApiClient) -> None:
        self.api = api

    def fetch_inventory(self) -> dict[str, int]:
        inventory: dict[str, int] = {}
        page: int | None = 1
        while page is not None:
            body = self.api.get("v1/inventory", params={"page": page, "per_page": self.PER_PAGE}).json()
            for item in body.get("data", []):
                inventory[item["sku"]] = int(item["available"])
            page = body.get("next_page")
        log.info("shop: %d SKUs", len(inventory))
        return inventory

    def set_available(self, sku: str, available: int, idempotency_key: str) -> None:
        self.api.put(
            f"v1/inventory/{sku}",
            json={"available": available},
            headers={"Idempotency-Key": idempotency_key},
        )
