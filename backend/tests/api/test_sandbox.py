"""The What-If page: instant simulation and the agents' debate stream."""


import numpy as np
import pytest
from sqlmodel import col, select

from sarthi.analytics import montecarlo, whatif
from sarthi.db import session
from sarthi.models import Alert, Event, PurchaseOrder, Run, SkuSnapshot, TransferOrder
from tests.api.support import (
    ZONES,
    boot,
    count,
    sse,
)
from tests.conftest import must

# ── sandbox ──────────────────────────────────────────────────────────────────

def simulate(client, **change) -> dict:
    r = client.post("/api/sandbox/simulate", json={"lead": 5, "demand": 50, "stock": 300, "margin": 20, **change})
    assert r.status_code == 200, r.text
    return r.json()


def test_simulate_shape_and_direction(client):
    base = simulate(client)
    assert set(base) == {"risk", "par", "safetyStock", "sensitivity"}
    assert [p["lt"] for p in base["sensitivity"]] == [f"{d}d" for d in range(1, 16)]
    curve = [p["risk"] for p in base["sensitivity"]]
    assert curve == sorted(curve) and curve[0] < curve[-1]              # longer lead time, more risk
    assert base["sensitivity"][4]["risk"] == base["risk"]               # the 5-day point is the headline number

    by_lead = [simulate(client, lead=lead)["risk"] for lead in (2, 5, 9, 14)]
    by_stock = [simulate(client, stock=stock)["risk"] for stock in (100, 300, 600, 1200)]
    assert by_lead == sorted(by_lead) and by_lead[0] < by_lead[-1]
    assert by_stock == sorted(by_stock, reverse=True) and by_stock[0] > by_stock[-1]
    assert simulate(client, margin=40)["par"] == pytest.approx(2 * base["par"], abs=2)
    assert simulate(client, stock=1500, lead=1) == {**simulate(client, stock=1500, lead=1), "risk": 0, "par": 0}


def test_simulate_agrees_with_the_agents_monte_carlo():
    k = 5.0
    for lead, demand, stock in ((5, 50, 300), (9, 80, 400), (3, 20, 50)):
        fast = whatif.simulate(lead, demand, stock, 20, k, 50.0)
        slow = montecarlo.simulate(stock, [], np.full(whatif.HORIZON_DAYS, float(demand)), k, whatif.lead_samples(lead),
                                   whatif.PATHS, whatif.DEMAND_SEED)
        assert fast["risk"] == round(100 * slow.stockout_prob)
        assert fast["par"] == round(slow.expected_lost_units * 0.20 * 50.0)


def test_simulate_validation_and_no_writes(client):
    runs = count(Run)
    assert client.post("/api/sandbox/simulate", json={"lead": 5, "demand": 50, "stock": 300}).status_code == 422
    assert client.post("/api/sandbox/simulate", json={"lead": -1, "demand": 50, "stock": 300, "margin": 20}).status_code == 422
    simulate(client)
    assert count(Run) == runs


def test_sandbox_debate_stream_is_a_dry_run(client):
    counts = (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot))
    shown = boot(client)["live"]["runId"]
    lines, done = sse(client, "/api/sandbox/debate/stream", lead=12, demand=80, stock=200, margin=20, lang="EN")
    assert done and lines
    assert all(set(x) == {"agentKey", "tone", "msg"} and x["tone"] in ZONES and x["msg"] for x in lines)
    assert {"rlhfArbiter"} <= {x["agentKey"] for x in lines}                    # the debate reaches a ruling
    assert (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot)) == counts
    assert boot(client)["live"]["runId"] == shown                               # the screens still show the real run
    with session() as s:
        run = s.exec(select(Run).where(Run.trigger == "sandbox")).one()
        assert run is not None
    assert run.dry_run and run.status == "done"
    assert run.scenario == {"lead_mult": 2.4, "demand_mult": 1.6}
    assert count(Event, Event.run_id == run.id) > 0

    hindi, done = sse(client, "/api/sandbox/debate/stream", lead=5, demand=50, stock=300, margin=20, lang="HI")
    assert done and any("ऀ" <= ch <= "ॿ" for ch in hindi[0]["msg"])
    with session() as s:
        latest = s.exec(select(Run).where(col(Run.trigger) == "sandbox").order_by(col(Run.id).desc())).first()
        assert latest is not None
    assert must(latest.id) > must(run.id) and latest.scenario == {"lead_mult": 1.0, "demand_mult": 1.0}
    assert count(Event, Event.run_id == run.id) == 0                            # earlier what-ifs are cleared away
    assert count(Event, Event.run_id == latest.id) > 0


def test_sandbox_stream_rejects_bad_sliders(client):
    assert client.get("/api/sandbox/debate/stream", params={"lead": 0}).status_code == 422
