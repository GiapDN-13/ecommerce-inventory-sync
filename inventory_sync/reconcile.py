"""Pure reconciliation logic — no I/O, easy to test."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    IN_SYNC = "in_sync"
    MISMATCH = "mismatch"
    MISSING_IN_SHOP = "missing_in_shop"
    UNKNOWN_TO_WAREHOUSE = "unknown_to_warehouse"


class Action(StrEnum):
    NONE = "none"
    UPDATE = "update"  # safe to push automatically
    REVIEW = "review"  # needs a human before anything changes


@dataclass(frozen=True)
class Line:
    sku: str
    warehouse_units: int | None
    shop_units: int | None
    status: Status
    action: Action
    reason: str = ""

    @property
    def diff(self) -> int:
        """Units the shop must change by to match the warehouse (positive = shop shows too few)."""
        return (self.warehouse_units or 0) - (self.shop_units or 0)


@dataclass(frozen=True)
class Plan:
    lines: list[Line]

    @property
    def updates(self) -> list[Line]:
        return [line for line in self.lines if line.action is Action.UPDATE]

    @property
    def reviews(self) -> list[Line]:
        return [line for line in self.lines if line.action is Action.REVIEW]

    def summary(self) -> dict[str, int]:
        by_status = Counter(line.status.value for line in self.lines)
        return {
            "skus_total": len(self.lines),
            **{status.value: by_status.get(status.value, 0) for status in Status},
            "auto_updates": len(self.updates),
            "needs_review": len(self.reviews),
        }


def reconcile(
    warehouse: Mapping[str, int],
    shop: Mapping[str, int],
    *,
    review_ratio: float = 0.5,
    review_min_units: int = 20,
) -> Plan:
    """Compare sellable warehouse units with what the shop shows.

    A mismatch is pushed automatically unless the change is both large in absolute terms
    (``>= review_min_units``) and relative terms (``> review_ratio`` of the current shop value).
    Big jumps usually mean a data problem upstream, so a person should look first.
    """
    lines: list[Line] = []
    for sku in sorted(warehouse.keys() | shop.keys()):
        wh, sh = warehouse.get(sku), shop.get(sku)
        if wh is not None and sh is not None:
            if wh == sh:
                lines.append(Line(sku, wh, sh, Status.IN_SYNC, Action.NONE))
                continue
            change = abs(wh - sh)
            is_big = change >= review_min_units and change > review_ratio * max(sh, 1)
            if is_big:
                reason = f"change of {wh - sh:+d} units exceeds the safety threshold"
                lines.append(Line(sku, wh, sh, Status.MISMATCH, Action.REVIEW, reason))
            else:
                lines.append(Line(sku, wh, sh, Status.MISMATCH, Action.UPDATE))
        elif wh is not None:
            reason = "in warehouse but not listed in the shop"
            lines.append(Line(sku, wh, None, Status.MISSING_IN_SHOP, Action.REVIEW, reason))
        else:
            assert sh is not None
            if sh > 0:
                reason = "shop sells stock the warehouse does not have (oversell risk)"
                lines.append(Line(sku, None, sh, Status.UNKNOWN_TO_WAREHOUSE, Action.REVIEW, reason))
            else:
                lines.append(Line(sku, None, sh, Status.UNKNOWN_TO_WAREHOUSE, Action.NONE))
    return Plan(lines)
