"""Price negotiation as a deterministic alternating-offers protocol.

The numbers are settled by code: each side starts from its preferred price and concedes toward its limit
over a fixed number of rounds, following a time-dependent concession curve. The language model is only
asked to word the opening email. The "supplier" here is a simulator standing in for the real counterparty.
"""

from dataclasses import asdict, dataclass

ROUNDS = 4
SUPPLIER_BETA = {"Gold": 0.6, "Silver": 1.0, "Bronze": 1.6}   # < 1 holds firm until late; > 1 concedes early
PRICE_STEP = 0.05


@dataclass
class Deal:
    list_price: float
    target: float               # the buyer's opening position
    reserve: float              # the most the buyer will pay (its best alternative)
    floor: float                # the least the supplier will accept
    agreed_price: float | None  # None when the limits do not overlap
    rounds: list[dict]
    agreed_round: int | None

    @property
    def unit_price(self) -> float:
        return self.agreed_price if self.agreed_price is not None else self.list_price

    def as_dict(self) -> dict:
        return asdict(self)


def negotiate(list_price: float, incentive: float, max_discount_pct: float, tier: str, urgency: float,
              alternative_price: float | None = None) -> Deal:
    """Run the protocol.

    incentive: volume discount fraction already earned at this quantity. urgency: 0 (plenty of cover) to
    1 (stockout imminent); an urgent buyer concedes faster and so pays more. alternative_price: unit price
    at the next-best supplier, which caps what the buyer will pay.
    """
    max_discount = max_discount_pct / 100
    floor = list_price * (1 - max_discount)
    reserve = min(list_price, alternative_price) if alternative_price else list_price
    target = min(list_price * (1 - incentive - 0.5 * max_discount), reserve)
    beta_buyer = 0.5 + 2 * min(1.0, max(0.0, urgency))
    beta_supplier = SUPPLIER_BETA.get(tier, 1.0)

    rounds, agreed, agreed_round = [], None, None
    for t in range(1, ROUNDS + 1):
        progress = t / ROUNDS
        offer = target + (reserve - target) * progress ** (1 / beta_buyer)
        ask = list_price - (list_price - floor) * progress ** (1 / beta_supplier)
        rounds.append({"round": t, "buyer_offer": round(offer, 2), "supplier_ask": round(ask, 2)})
        if offer >= ask:
            midpoint = round(((offer + ask) / 2) / PRICE_STEP) * PRICE_STEP
            agreed = round(min(reserve, max(floor, midpoint)), 2)
            agreed_round = t
            break
    return Deal(round(list_price, 2), round(target, 2), round(reserve, 2), round(floor, 2), agreed, rounds, agreed_round)
