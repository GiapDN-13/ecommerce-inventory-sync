from inventory_sync.reconcile import Action, Status, reconcile
from inventory_sync.sources import WarehouseStock


def by_sku(plan):
    return {line.sku: line for line in plan.lines}


def test_in_sync_and_small_drift_is_auto_updated():
    plan = by_sku(reconcile({"A": 10, "B": 10}, {"A": 10, "B": 7}))
    assert plan["A"].status is Status.IN_SYNC and plan["A"].action is Action.NONE
    assert plan["B"].status is Status.MISMATCH and plan["B"].action is Action.UPDATE
    assert plan["B"].diff == 3


def test_big_jump_needs_review_not_auto_update():
    plan = by_sku(reconcile({"A": 5}, {"A": 180}))
    assert plan["A"].action is Action.REVIEW
    assert "safety threshold" in plan["A"].reason


def test_large_absolute_but_small_relative_change_is_auto_updated():
    # 30 units on a base of 1000 is only 3% → safe to push
    plan = by_sku(reconcile({"A": 1030}, {"A": 1000}))
    assert plan["A"].action is Action.UPDATE


def test_missing_in_shop_and_unknown_to_warehouse():
    plan = by_sku(reconcile({"A": 4}, {"Z": 12, "Y": 0}))
    assert plan["A"].status is Status.MISSING_IN_SHOP and plan["A"].action is Action.REVIEW
    assert plan["Z"].status is Status.UNKNOWN_TO_WAREHOUSE and plan["Z"].action is Action.REVIEW
    assert plan["Y"].action is Action.NONE  # shop already shows 0 → nothing to oversell


def test_summary_counts():
    summary = reconcile({"A": 1, "B": 2, "C": 3}, {"A": 1, "B": 3, "D": 9}).summary()
    assert summary == {
        "skus_total": 4,
        "in_sync": 1,
        "mismatch": 1,
        "missing_in_shop": 1,
        "unknown_to_warehouse": 1,
        "auto_updates": 1,
        "needs_review": 2,
    }


def test_available_units_never_negative():
    assert WarehouseStock("A", cartons=1, units_per_carton=6, reserved_units=10).available_units == 0
    assert WarehouseStock("A", cartons=3, units_per_carton=12, reserved_units=5).available_units == 31
