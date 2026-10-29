"""Zone classification and its hysteresis."""

from types import SimpleNamespace

import pytest

from sarthi.analytics import (
    zones,
)

# ── Zones ────────────────────────────────────────────────────────────────────

CFG = SimpleNamespace(overstock_cover_days=30)


@pytest.mark.parametrize(("metrics", "zone"), [
    ({"doc": 8.0, "lead_eff": 2.4, "stockout_prob": 0.02, "carry_roi": 0.17}, "sweet"),
    ({"doc": 1.6, "lead_eff": 14.4, "stockout_prob": 0.97, "carry_roi": 0.30}, "chaos"),
    ({"doc": 9.0, "lead_eff": 6.0, "stockout_prob": 0.55, "carry_roi": 0.30}, "chaos"),   # risk alone is enough
    ({"doc": 102.0, "lead_eff": 8.4, "stockout_prob": 0.0, "carry_roi": 0.016}, "ghost"),
    ({"doc": 100.0, "lead_eff": 3.6, "stockout_prob": 0.0, "carry_roi": -0.06}, "money"),
])
def test_zone_classification(metrics, zone):
    assert zones.classify(metrics, CFG) == zone


def test_hysteresis_holds_for_a_blip_but_not_for_risk():
    assert zones.with_hysteresis("ghost", None, None) == "ghost"                # first day
    assert zones.with_hysteresis("ghost", "sweet", "sweet") == "sweet"          # one-day blip: stay
    assert zones.with_hysteresis("ghost", "sweet", "ghost") == "ghost"          # second day running: move
    assert zones.with_hysteresis("chaos", "sweet", "sweet") == "chaos"          # into chaos immediately
    assert zones.with_hysteresis("sweet", "chaos", "chaos") == "chaos"          # out of chaos needs two days
    assert zones.with_hysteresis("sweet", "sweet", "ghost") == "sweet"
