"""Actions the manager takes directly on the Replenish page: an order, a transfer, a campaign."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process
from sqlmodel import select

from sarthi.agents import compliance_guardian
from sarthi.agents import debate as debate_lines
from sarthi.agents.execution_engine import execute_payload
from sarthi.analytics import esg, policy
from sarthi.api import presenters
from sarthi.api.routers import runs
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.models import (
    Alert,
    Location,
    PurchaseOrder,
    Sku,
    SkuSnapshot,
    SkuSupplier,
    Supplier,
    TransferOrder,
)
from sarthi.orchestrator.runner import active_strategy, latest_run_id

router = APIRouter(tags=["actions"])

NAME_MATCH = 80
BASE_MODE_DAYS = esg.MODES["multimodal"][0]      # quoted lead times assume the default shipping mode
OVERRIDE_NOTE = "recorded on the manager's instruction"


class OrderIn(BaseModel):
    skuId: str
    distributor: str
    units: int = Field(gt=0, le=1_000_000)
    price: float = Field(gt=0)
    esgIndex: int | None = Field(default=None, ge=0, le=2)


class TransferIn(BaseModel):
    skuId: str
    fromId: str
    toId: str
    units: int = Field(gt=0, le=1_000_000)


class CampaignIn(BaseModel):
    type: str


def _run_id() -> int:
    run_id = latest_run_id()
    if run_id is None:
        raise HTTPException(status_code=503, detail="warming up")
    return run_id


def _supplier(name: str, suppliers: list[Supplier]) -> Supplier | None:
    """A supplier by id, full name or the short name the charts show ("Reliance")."""
    wanted = name.strip().lower()
    for x in suppliers:
        if wanted in (x.id.lower(), x.name.lower(), x.name.split(" ")[0].lower()):
            return x
    found = process.extractOne(wanted, {x.id: x.name.lower() for x in suppliers}, scorer=fuzz.WRatio, score_cutoff=NAME_MATCH)
    return next((x for x in suppliers if found and x.id == found[2]), None)


@router.post("/orders")
def create_order(body: OrderIn) -> dict:
    """Place the manager's own order. It is always recorded; any rule it breaks is written to the audit trail."""
    run_id = _run_id()
    settings = get_settings()
    with session() as s:
        sku = s.get(Sku, body.skuId)
        if sku is None:
            raise HTTPException(status_code=404, detail=f"SKU {body.skuId} not found")
        suppliers = list(s.exec(select(Supplier)).all())
        supplier = _supplier(body.distributor, suppliers)
        if supplier is None:
            raise HTTPException(status_code=404, detail=f"distributor '{body.distributor}' not found")
        primary_id = s.exec(select(SkuSupplier.supplier_id).where(SkuSupplier.sku_id == sku.id, SkuSupplier.is_primary)).first()
        usual = next((x for x in suppliers if x.id == primary_id), None)
        snapshot = s.exec(select(SkuSnapshot).where(SkuSnapshot.run_id == run_id, SkuSnapshot.sku_id == sku.id)
                          .order_by(SkuSnapshot.as_of.desc())).first()
        suggested = s.exec(select(Alert).where(Alert.run_id == run_id, Alert.sku_id == sku.id).order_by(Alert.id.desc())).all()
        orders = s.exec(select(PurchaseOrder).where(PurchaseOrder.status == "confirmed")).all()

    if body.esgIndex is not None:
        mode = esg.MODE_ORDER[body.esgIndex]
    else:       # no choice made: the mode the agents recommended for this product, else the default
        mode = next((a.payload["mode"] for a in suggested if a.payload.get("kind") == "purchase" and a.payload.get("mode")),
                    "multimodal")
    promised = float(sku.lead_time_days)
    if usual and supplier.id != usual.id and usual.avg_tat_days and supplier.avg_tat_days:
        promised *= supplier.avg_tat_days / usual.avg_tat_days
    eta = max(1, round(promised + esg.MODES[mode][0] - BASE_MODE_DAYS))

    velocity = float(snapshot.metrics.get("velocity", 0)) if snapshot else 0.0
    envelope = compliance_guardian.build_envelope(settings, active_strategy())
    envelope["auto_max_order_value"] = None             # a person is placing this order, so no unattended limit applies
    proposal = {"kind": "purchase", "sku_id": sku.id, "moq": sku.moq,
                "payload": {"supplier_id": supplier.id, "qty": body.units, "unit_price": body.price, "mode": mode},
                "air_orders": sum(o.mode == "air" for o in orders), "total_orders": len(orders)}
    if velocity > 0:
        proposal["shelf_cap"] = int(velocity * sku.shelf_life_days * policy.SHELF_LIFE_SHARE)
    bb = Blackboard(run_id)
    for verdict in compliance_guardian.review(proposal, envelope):
        facts = {"sku": sku.name, "qty": body.units, **verdict.message_facts}
        try:
            text = debate_lines.render(f"object:{verdict.rule}", "EN", facts)
        except KeyError:
            text = f"Rule {verdict.rule} applies to this order."
        bb.act(verdict.voice, "execute", text, result=OVERRIDE_NOTE, sku_id=sku.id, rule=verdict.rule, severity=verdict.severity)

    result = execute_payload("purchase", {**proposal["payload"], "eta_days": eta}, sku_id=sku.id, run_id=run_id, source="user")
    presenters.bump()
    return {"orderId": result["txid"], "expectedDelivery": f"{eta}–{eta + 1} days", "runStarted": runs.rerun_after_action()}


@router.post("/transfers")
def create_transfer(body: TransferIn) -> dict:
    """Move stock between two sites. More units than the source holds are clamped to what is there."""
    run_id = _run_id()
    if body.fromId == body.toId:
        raise HTTPException(status_code=400, detail="source and destination are the same site")
    with session() as s:
        if s.get(Sku, body.skuId) is None:
            raise HTTPException(status_code=404, detail=f"SKU {body.skuId} not found")
        for site in (body.fromId, body.toId):
            if s.get(Location, site) is None:
                raise HTTPException(status_code=404, detail=f"location {site} not found")
    result = execute_payload("transfer", {"from": body.fromId, "to": body.toId, "units": body.units},
                             sku_id=body.skuId, run_id=run_id, source="user")
    with session() as s:
        moved = s.get(TransferOrder, result["txid"]).qty
    presenters.bump()
    return {"transferId": result["txid"], "units": moved, "runStarted": runs.rerun_after_action()}


@router.post("/campaigns")
def launch_campaign(body: CampaignIn) -> dict:
    """Launch the campaign of this type that the Overstock Resolver costed in the latest run."""
    run_id = _run_id()
    stored = Blackboard(run_id).metrics(key="campaigns")
    candidate = next((c for c in (stored[-1].get("items", []) if stored else []) if c["type"] == body.type), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"no '{body.type}' campaign is proposed")
    sku_ids = list(candidate.get("skuIds", []))
    execute_payload("campaign", {"type": candidate["type"], "target_zone": candidate["target"], "sku_ids": sku_ids,
                                 "discount_pct": candidate["discount"], "est_impact_value": candidate["estImpactValue"]},
                    sku_id=sku_ids[0] if sku_ids else None, run_id=run_id, source="user")
    presenters.bump()
    return {"status": "live"}
