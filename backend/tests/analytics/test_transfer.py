"""The warehouse transfer plan."""


import pytest

from sarthi.analytics import (
    transfer,
)

# ── Transfers ────────────────────────────────────────────────────────────────

def test_transfer_fills_deficit_from_surplus():
    moves = transfer.plan_transfers({"DEL": 100}, {"MUM": 60}, {("DEL", "MUM"): 5.0}, {"MUM": 20.0})
    assert moves == [{"from": "DEL", "to": "MUM", "units": 60, "unit_cost": 5.0, "saving": 900.0}]


def test_transfer_respects_limits_and_prefers_the_cheaper_source():
    surplus, deficit = {"DEL": 80, "BLR": 500}, {"MUM": 120, "CHN": 40}
    cost = {("DEL", "MUM"): 6.0, ("DEL", "CHN"): 9.0, ("BLR", "MUM"): 4.0, ("BLR", "CHN"): 3.0}
    moves = transfer.plan_transfers(surplus, deficit, cost, {"MUM": 50.0, "CHN": 50.0})
    shipped_from, shipped_to = {}, {}
    for m in moves:
        shipped_from[m["from"]] = shipped_from.get(m["from"], 0) + m["units"]
        shipped_to[m["to"]] = shipped_to.get(m["to"], 0) + m["units"]
    assert all(shipped_from[s] <= surplus[s] for s in shipped_from)
    assert shipped_to == {"MUM": 120, "CHN": 40}
    assert set(shipped_from) == {"BLR"}  # cheaper to both destinations and has enough


def test_transfer_skips_moves_that_do_not_pay_or_are_tiny():
    assert transfer.plan_transfers({"DEL": 100}, {"MUM": 60}, {("DEL", "MUM"): 25.0}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({"DEL": 100}, {"MUM": 6}, {("DEL", "MUM"): 5.0}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({}, {"MUM": 60}, {}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({"MUM": 100}, {"MUM": 60}, {}, {"MUM": 20.0}) == []


def test_distance_cost_and_site_needs():
    km = transfer.haversine_km(19.08, 72.88, 28.61, 77.21)  # Mumbai -> Delhi
    assert 1100 < km < 1200
    assert transfer.unit_cost(1000) == pytest.approx(6.0)
    surplus, deficit = transfer.site_needs({"A": 640, "B": 20, "C": 110}, {"A": 100, "B": 100, "C": 100}, safety_stock=300)
    assert surplus == {"A": 490} and deficit == {"B": 80} and "C" not in surplus | deficit
