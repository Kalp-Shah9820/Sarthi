"""Market basket analysis: exact support, confidence and lift over baskets.

The catalogue is small, so every antecedent set up to `max_len` items is enumerated exactly; an
approximate miner (FP-Growth) would only be needed for thousands of SKUs.
"""

from itertools import combinations

import numpy as np
import pandas as pd

TOP_RULES = 8
MIN_LIFT = 1.1
BUSIEST_AISLE_HEAT = 0.92


def _matrix(baskets: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Boolean basket x SKU matrix."""
    skus = sorted(baskets["sku_id"].unique())
    col = {s: i for i, s in enumerate(skus)}
    codes, _ = pd.factorize(baskets["basket_id"])
    m = np.zeros((codes.max() + 1 if len(codes) else 0, len(skus)), dtype=bool)
    m[codes, baskets["sku_id"].map(col).to_numpy()] = True
    return m, skus


def mine_rules(baskets: pd.DataFrame, names: dict[str, str], min_support: float = 0.01,
               min_confidence: float = 0.4, max_len: int = 2) -> list[dict]:
    """Association rules A -> c from (basket_id, sku_id) rows, strongest first.

    support = P(A and c), confidence = P(c | A), lift = confidence / P(c).
    """
    if baskets.empty:
        return []
    m, skus = _matrix(baskets)
    n = len(m)
    item_prob = m.mean(axis=0)
    rules = []
    for size in range(1, max_len + 1):
        for antecedent in combinations(range(len(skus)), size):
            has_a = m[:, antecedent].all(axis=1)
            count_a = int(has_a.sum())
            if count_a == 0:
                continue
            for c in range(len(skus)):
                if c in antecedent:
                    continue
                both = int((has_a & m[:, c]).sum())
                support, confidence = both / n, both / count_a
                lift = confidence / item_prob[c] if item_prob[c] > 0 else 0.0
                if support >= min_support and confidence >= min_confidence and lift > MIN_LIFT:
                    rules.append({
                        "antecedent": [names.get(skus[i], skus[i]) for i in antecedent],
                        "consequent": names.get(skus[c], skus[c]),
                        "confidence": round(confidence, 2), "lift": round(lift, 2), "support": round(support, 2),
                        "antecedent_ids": [skus[i] for i in antecedent], "consequent_id": skus[c],
                    })
    rules.sort(key=lambda r: (-r["confidence"], -r["lift"], r["antecedent_ids"], r["consequent_id"]))
    return rules[:TOP_RULES]


def aisle_pairs(rules: list[dict], sku_aisle: dict[str, str]) -> list[dict]:
    """Strongest rule confidence between each pair of aisles, as {from, to, strength 0-100}."""
    best: dict[tuple[str, str], float] = {}
    for rule in rules:
        target = sku_aisle.get(rule["consequent_id"])
        for sku_id in rule["antecedent_ids"]:
            source = sku_aisle.get(sku_id)
            if source and target and source != target:
                best[(source, target)] = max(best.get((source, target), 0.0), rule["confidence"])
    pairs = [{"from": a.lower(), "to": b.lower(), "strength": round(conf * 100)} for (a, b), conf in best.items()]
    return sorted(pairs, key=lambda p: (-p["strength"], p["from"], p["to"]))


def aisle_connections(pairs: list[dict]) -> dict[str, list[str]]:
    """Aisle id -> aisles it is co-purchased with (either direction)."""
    out: dict[str, set[str]] = {}
    for p in pairs:
        a, b = p["from"].upper(), p["to"].upper()
        out.setdefault(a, set()).add(b)
        out.setdefault(b, set()).add(a)
    return {aisle: sorted(linked) for aisle, linked in out.items()}


def aisle_heat(baskets: pd.DataFrame, sku_aisle: dict[str, str]) -> dict[str, float]:
    """Share of baskets that include each aisle, scaled so the busiest aisle is 0.92."""
    if baskets.empty:
        return {}
    tagged = baskets.assign(aisle=baskets["sku_id"].map(sku_aisle)).dropna(subset=["aisle"])
    share = tagged.groupby("aisle")["basket_id"].nunique() / baskets["basket_id"].nunique()
    if share.empty or share.max() == 0:
        return {}
    return {aisle: round(float(v / share.max() * BUSIEST_AISLE_HEAT), 2) for aisle, v in share.items()}
