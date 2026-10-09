"""Inventory Ikigai zone classification."""

ZONES = ("sweet", "chaos", "ghost", "money")
CHAOS_PROB = 0.5


def classify(m: dict, cfg) -> str:
    """`m` needs doc, lead_eff, stockout_prob, carry_roi; `cfg` needs overstock_cover_days. First match wins.

    `doc` is days of cover from stock on hand. `cover` (optional, defaults to `doc`) also counts stock already
    on order that will arrive within the lead time: a SKU with 4 days on the shelf and a delivery due
    tomorrow is not short. Overstock is judged on what is physically held, so it uses `doc`.
    """
    if m.get("cover", m["doc"]) < m["lead_eff"] or m["stockout_prob"] >= CHAOS_PROB:
        return "chaos"
    if m["doc"] > cfg.overstock_cover_days:
        return "money" if m["carry_roi"] < 0 else "ghost"
    return "sweet"


def with_hysteresis(new_zone: str, previous_zone: str | None, previous_raw: str | None) -> str:
    """Stop SKUs on a threshold from flapping between zones.

    A SKU changes zone only when the new classification has held for two consecutive days
    (`previous_raw` is yesterday's unsmoothed classification). Moves into chaos apply at once:
    risk must not be delayed.
    """
    if previous_zone is None or new_zone == previous_zone or new_zone == "chaos":
        return new_zone
    return new_zone if new_zone == previous_raw else previous_zone
