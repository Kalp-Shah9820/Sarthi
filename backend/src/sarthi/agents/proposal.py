"""A proposal: the unit that flows from the RESOLVE agents to the Arbiter and on to execution."""

from dataclasses import asdict, dataclass, field

KINDS = ("purchase", "transfer", "campaign", "audit")
ALERT_TYPES = ("stockout_reorder", "supplier_switch", "transfer", "markdown", "bundle", "expiry_risk",
               "cannibalization", "phantom_inventory")


@dataclass
class Proposal:
    kind: str                  # purchase | transfer | campaign | audit
    alert_type: str            # one of ALERT_TYPES; selects the wording template
    sku_id: str
    author: str                # agent key that proposed it
    payload: dict              # everything needed to execute it
    cost: float                # cash out: order value + freight, transfer cost, or margin given away
    par_rescued: float         # rupees of Profit-at-Risk this removes
    co2_kg: float = 0.0
    alternatives: list[dict] = field(default_factory=list)   # other options considered (e.g. ranked suppliers)
    facts: dict = field(default_factory=dict)                # flat values for wording templates and grounding
    extra: dict = field(default_factory=dict)                # context for review: moq, shelf_cap, mode_risk, modes ...
    status: str = "proposed"   # proposed | revised | approved | rejected
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{self.kind}:{self.alert_type}:{self.sku_id}"

    @property
    def net(self) -> float:
        return self.par_rescued - self.cost

    def as_dict(self) -> dict:
        """Plain dict for the run state. Review context from `extra` is lifted to the top level for the rule checks."""
        data = asdict(self)
        return {**self.extra, **data, "net": round(self.net, 2)}
