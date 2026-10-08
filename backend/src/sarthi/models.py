"""All database tables. Dates are `datetime.date`; timestamps are timezone-aware UTC (SQLModel rejects naive ones)."""

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


# ── Master data ──────────────────────────────────────────────────────────────

class Sku(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    category: str
    cogs: float
    price: float
    shelf_life_days: int = 365
    holding_cost_pct: float = 0.03  # share of COGS per month
    moq: int = 6
    aisle_id: str = "F"


class Location(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    city: str
    kind: str = "warehouse"  # store | warehouse
    lat: float = 0.0
    lon: float = 0.0
    capacity: int = 0


class Supplier(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    tier: str = "Silver"
    city: str = ""
    lat: float = 0.0
    lon: float = 0.0
    email: str = ""
    capacity_limit: int = 0
    defect_rate: float = 0.0
    esg_score: float = 70.0  # 0-100
    incentive_text: str = ""
    incentive_min_qty: int = 0
    incentive_pct: float = 0.0
    max_discount_pct: float = 0.0


class SkuSupplier(SQLModel, table=True):
    sku_id: str = Field(primary_key=True)
    supplier_id: str = Field(primary_key=True)
    unit_price: float
    is_primary: bool = False


class Aisle(SQLModel, table=True):
    id: str = Field(primary_key=True)
    label: str
    x: int
    y: int
    items: list = Field(default_factory=list, sa_column=Column(JSON))
    heat: float = 0.5
    connections: list = Field(default_factory=list, sa_column=Column(JSON))
    zone: str = "sweet"


# ── History ──────────────────────────────────────────────────────────────────

class Sale(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(index=True)
    sku_id: str = Field(index=True)
    location_id: str = "STORE-01"
    qty: int
    price: float
    basket_id: str = Field(index=True)


class StockDaily(SQLModel, table=True):
    day: date = Field(primary_key=True)
    sku_id: str = Field(primary_key=True)
    location_id: str = Field(primary_key=True)
    on_hand: int


class Inbound(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sku_id: str = Field(index=True)
    location_id: str
    supplier_id: str
    qty: int
    ordered_on: date
    expected_on: date
    received_on: date | None = None


class Delivery(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    supplier_id: str = Field(index=True)
    sku_id: str
    ordered_on: date
    expected_days: float
    actual_days: float
    qty_ordered: int
    qty_received: int
    mode: str = "multimodal"  # air | sea | multimodal


class PriceHistory(SQLModel, table=True):
    sku_id: str = Field(primary_key=True)
    month: str = Field(primary_key=True)  # YYYY-MM
    unit_price: float


# ── Signals and runs ─────────────────────────────────────────────────────────

class RiskSignal(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    type: str  # WEATHER | LOGISTICS | COMMODITY | TRANSPORT
    severity: str  # HIGH | MEDIUM | LOW
    region_key: str = ""
    msg: str = ""
    msg_key: str | None = None
    icon: str = ""
    lead_modifier: float = 1.0
    demand_multiplier: float = 1.0
    categories: list = Field(default_factory=list, sa_column=Column(JSON))
    skus_at_risk: int = 0
    source: str = "feed"
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    trigger: str
    dry_run: bool = False
    scenario: dict = Field(default_factory=dict, sa_column=Column(JSON))
    strategy: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "running"  # running | done | failed
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class SkuSnapshot(SQLModel, table=True):
    run_id: int = Field(primary_key=True)
    as_of: date = Field(primary_key=True)
    sku_id: str = Field(primary_key=True)
    zone: str
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    ts: datetime = Field(default_factory=utcnow)
    phase: str = ""
    agent: str = ""
    kind: str = ""
    key: str | None = None
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))
    text: str | None = None
    sku_id: str | None = None


# ── Decisions and actions ────────────────────────────────────────────────────

class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    sku_id: str
    sku_label: str
    zone: str
    type: str
    risk: int = 0
    confidence: int = 0
    impact_value: float = 0.0
    msg: str = ""
    action: str = ""
    impact: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    routed: str = "review"  # auto | review
    status: str = "open"  # open | approved | dismissed
    feedback: str | None = None
    txid: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


class PurchaseOrder(SQLModel, table=True):
    id: str = Field(primary_key=True)  # PO-…
    sku_id: str
    supplier_id: str
    qty: int
    unit_price: float
    mode: str = "multimodal"
    status: str = "draft"  # draft | confirmed
    source: str = "user"  # user | alert | auto
    created_at: datetime = Field(default_factory=utcnow)


class TransferOrder(SQLModel, table=True):
    id: str = Field(primary_key=True)  # TRF-…
    sku_id: str
    from_location: str
    to_location: str
    qty: int
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=utcnow)


class Campaign(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    type: str  # markdown | bundle | flash
    target_zone: str
    sku_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    discount_pct: float = 0.0
    est_impact_value: float = 0.0
    status: str = "proposed"  # proposed | live
    created_at: datetime = Field(default_factory=utcnow)


class Negotiation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    po_id: str = ""
    supplier_id: str
    rounds: list = Field(default_factory=list, sa_column=Column(JSON))
    agreed_price: float | None = None
    status: str = "open"


class OutboxEmail(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    to_addr: str
    subject: str
    body: str
    ref: str = ""
    status: str = "draft"  # draft | queued
    created_at: datetime = Field(default_factory=utcnow)


# ── Policy and learning ──────────────────────────────────────────────────────

class StrategyPolicy(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    mode: str = "Balanced"
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    source_text: str = ""
    expires_on: date | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class BanditArm(SQLModel, table=True):
    key: str = Field(primary_key=True)
    alpha: float = 2.0
    beta: float = 2.0


class Preference(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    scope: str
    target: str = ""
    directive: str
    value: float | None = None
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    active: bool = True


class Upload(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    type: str
    filename: str
    records: int = 0
    status: str = "done"
    errors: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class LlmCache(SQLModel, table=True):
    key: str = Field(primary_key=True)  # sha256 of the prompt
    response: str
    created_at: datetime = Field(default_factory=utcnow)


def local_today() -> date:
    """Today's date in the machine's local timezone: the business day the store is operating in."""
    return datetime.now(UTC).astimezone().date()
