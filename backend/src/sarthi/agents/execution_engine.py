"""Execution Engine (EXECUTE): turns approved proposals into alerts, and approved alerts into records.

Nothing leaves the machine. A purchase order becomes database rows plus an `.eml` and a `.json` file in
the outbox folder; a transfer moves stock between two locations' records; a campaign is marked live.
Executing the same alert twice returns the first result and changes nothing.
"""

import json
import time
from datetime import date, timedelta
from email.message import EmailMessage

from sqlmodel import func, select

from sarthi.agents.base import Agent
from sarthi.blackboard.store import write_event
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.llm import prompts, templates
from sarthi.models import (
    Alert,
    Campaign,
    Inbound,
    Location,
    Negotiation,
    OutboxEmail,
    PurchaseOrder,
    Sku,
    StockDaily,
    Supplier,
    TransferOrder,
    local_today,
    utcnow,
)
from sarthi.seed.catalog import STORE_ID

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SENDER = "sarthi@localhost"


def base36(number: int) -> str:
    out = ""
    while number:
        number, rem = divmod(number, 36)
        out = DIGITS[rem] + out
    return out or "0"


def data_today() -> date:
    """The business day the data is on: the last day with a store stock record."""
    with session() as s:
        return s.exec(select(func.max(StockDaily.day)).where(StockDaily.location_id == STORE_ID)).one() or local_today()


def _new_id(prefix: str, model, length: int | None = None) -> str:
    """A short unique id such as PO-MG1K2J3 or TRF-K2J3A."""
    with session() as s:
        while True:
            token = base36(time.time_ns() // 1_000)
            candidate = f"{prefix}-{token[-length:] if length else token}"
            if s.get(model, candidate) is None:
                return candidate


def _purchase(payload: dict, sku_id: str, source: str) -> tuple[str, str]:
    settings = get_settings()
    today = data_today()
    po_id = _new_id("PO", PurchaseOrder)
    with session() as s:
        sku, supplier = s.get(Sku, sku_id), s.get(Supplier, payload["supplier_id"])
        qty, unit_price = int(payload["qty"]), float(payload["unit_price"])
        eta = max(1, int(payload.get("eta_days", 3)))
        mode = payload.get("mode", "multimodal")
        s.add(PurchaseOrder(id=po_id, sku_id=sku_id, supplier_id=payload["supplier_id"], qty=qty, unit_price=unit_price,
                            mode=mode, status="confirmed", source=source))
        s.add(Inbound(sku_id=sku_id, location_id=STORE_ID, supplier_id=payload["supplier_id"], qty=qty,
                      ordered_on=today, expected_on=today + timedelta(days=eta)))
        deal = payload.get("negotiation")
        if deal:
            s.add(Negotiation(po_id=po_id, supplier_id=payload["supplier_id"], rounds=deal.get("rounds", []),
                              agreed_price=deal.get("agreed_price"), status="agreed" if deal.get("agreed_price") else "list_price"))

        sku_name = sku.name if sku else sku_id
        supplier_name = supplier.name if supplier else payload["supplier_id"]
        draft = s.exec(select(OutboxEmail).where(OutboxEmail.ref == payload.get("proposal_id", ""), OutboxEmail.status == "draft")).first()
        if draft is None:       # an order placed by hand has no negotiated draft: write a plain one
            text = templates.render("email", "opening", "EN", supplier=supplier_name, sku=sku_name, qty=qty,
                                    offer=unit_price, eta_days=eta, list_price=unit_price)
            draft = OutboxEmail(to_addr=(supplier.email if supplier else "") or "unknown@localhost",
                                subject=text["subject"], body=text["body"])
        draft.status, draft.ref = "queued", po_id
        s.add(draft)
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = SENDER, draft.to_addr, f"{draft.subject} [{po_id}]"
        message.set_content(draft.body)
        summary = {"po_id": po_id, "sku_id": sku_id, "sku": sku_name, "supplier_id": payload["supplier_id"],
                   "supplier": supplier_name, "qty": qty, "unit_price": unit_price, "total": round(qty * unit_price, 2),
                   "mode": mode, "ordered_on": today.isoformat(), "expected_on": (today + timedelta(days=eta)).isoformat(),
                   "source": source, "negotiation": deal}
        s.commit()

    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    (settings.outbox_dir / f"{po_id}.eml").write_bytes(bytes(message))
    (settings.outbox_dir / f"{po_id}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return po_id, f"PO {po_id}: {qty} units of {sku_name} from {supplier_name} at ₹{unit_price}"


def _latest_stock(s, sku_id: str, location_id: str) -> int:
    row = s.exec(select(StockDaily).where(StockDaily.sku_id == sku_id, StockDaily.location_id == location_id)
                 .order_by(StockDaily.day.desc())).first()
    return row.on_hand if row else 0


def _set_stock(s, day: date, sku_id: str, location_id: str, on_hand: int) -> None:
    row = s.get(StockDaily, (day, sku_id, location_id))
    if row is None:
        row = StockDaily(day=day, sku_id=sku_id, location_id=location_id, on_hand=on_hand)
    row.on_hand = on_hand
    s.add(row)


def _transfer(payload: dict, sku_id: str) -> tuple[str, str]:
    today = data_today()
    transfer_id = _new_id("TRF", TransferOrder, length=5)
    with session() as s:
        source_stock = _latest_stock(s, sku_id, payload["from"])
        units = max(0, min(int(payload["units"]), source_stock))     # never move more than the source holds
        _set_stock(s, today, sku_id, payload["from"], source_stock - units)
        _set_stock(s, today, sku_id, payload["to"], _latest_stock(s, sku_id, payload["to"]) + units)
        s.add(TransferOrder(id=transfer_id, sku_id=sku_id, from_location=payload["from"], to_location=payload["to"],
                            qty=units, status="confirmed"))
        names = {x.id: x.name for x in s.exec(select(Location)).all()}
        s.commit()
    return transfer_id, f"Transfer {transfer_id}: {units} units of {sku_id} from {names.get(payload['from'], payload['from'])} to {names.get(payload['to'], payload['to'])}"


def _campaign(payload: dict) -> tuple[str, str]:
    with session() as s:
        campaign = s.exec(select(Campaign).where(Campaign.type == payload["type"])).first() or Campaign(
            type=payload["type"], target_zone=payload.get("target_zone", ""))
        campaign.target_zone = payload.get("target_zone", campaign.target_zone)
        campaign.sku_ids = list(payload.get("sku_ids", []))
        campaign.discount_pct = float(payload.get("discount_pct", 0))
        campaign.est_impact_value = float(payload.get("est_impact_value", 0))
        campaign.status = "live"
        s.add(campaign)
        s.commit()
        s.refresh(campaign)
        return f"CMP-{campaign.id}", f"{payload['type'].title()} campaign live at {campaign.discount_pct:.0f}% off"


def execute_payload(kind: str, payload: dict, *, sku_id: str, run_id: int, source: str = "user") -> dict:
    """Carry out one action and log it. Returns {"txid", "kind"}."""
    if kind == "purchase":
        txid, line = _purchase(payload, sku_id, source)
    elif kind == "transfer":
        txid, line = _transfer(payload, sku_id)
    elif kind == "campaign":
        txid, line = _campaign(payload)
    elif kind == "audit":
        txid = f"TASK-{base36(time.time_ns() // 1_000)[-5:]}"
        task = {"cap_order": "Cap the next order at normal demand", "cycle_count": f"Count stock in aisle {payload.get('aisle', '?')}"}
        line = f"{task.get(payload.get('task'), 'Review')} for {sku_id}"
    else:
        raise ValueError(f"cannot execute kind '{kind}'")
    write_event(run_id, kind="action", agent="executionEngine", phase="execute", text=line, sku_id=sku_id,
                value={"result": txid, "facts": {"source": source}})
    return {"txid": txid, "kind": kind}


def execute_alert(alert_id: int, source: str = "alert") -> dict:
    """Carry out an alert's action once. A second call returns the first result and does nothing."""
    with session() as s:
        alert = s.get(Alert, alert_id)
        if alert is None:
            raise LookupError(f"alert {alert_id} not found")
        if alert.txid:
            return {"txid": alert.txid, "kind": alert.payload.get("kind"), "repeat": True}
        payload, sku_id, run_id = dict(alert.payload), alert.sku_id, alert.run_id
    result = execute_payload(payload["kind"], payload, sku_id=sku_id, run_id=run_id, source=source)
    with session() as s:
        alert = s.get(Alert, alert_id)
        alert.txid, alert.status, alert.decided_at = result["txid"], "approved", utcnow()
        s.add(alert)
        s.commit()
    return {**result, "repeat": False}


class ExecutionEngine(Agent):
    key, phase = "executionEngine", "execute"

    async def work(self, state: dict) -> dict:
        if state.get("dry_run"):        # a what-if creates no alerts and executes nothing
            return {"alerts": [], "executed": []}
        lang = state.get("lang", "EN")
        decisions = state.get("decisions", {})
        alert_ids, executed = [], []
        for ruling in state.get("rulings", []):
            if ruling.get("status") != "approved":
                continue
            p = ruling["proposal"]
            facts = p["facts"]
            wording = templates.render("alert", p["alert_type"], lang, **facts)
            msg = wording["msg"]
            if lang == "EN":            # the model may reword the explanation; buttons and totals stay templated
                needed = {k: facts[k] for k in templates.fields("alert", p["alert_type"], "EN") if k in facts}
                names = [v for v in needed.values() if isinstance(v, str) and v and v in wording["msg"]]
                msg, _ = await self.llm.text(prompts.REPHRASE, wording["msg"], facts=needed, fallback=wording["msg"],
                                             run_id=self.bb.run_id, task="alert", max_tokens=120, must_contain=names,
                                             max_chars=round(len(wording["msg"]) * 1.6) + 20)
            decision = decisions.get(p["sku_id"], {})
            with session() as s:
                alert = Alert(
                    run_id=self.bb.run_id, sku_id=p["sku_id"], sku_label=p.get("label") or facts.get("sku") or p["sku_id"],
                    zone=decision.get("zone", p.get("zone", "sweet")), type=p["alert_type"], risk=int(decision.get("risk", 0)),
                    confidence=int(ruling.get("confidence", 0)), impact_value=float(p["par_rescued"]),
                    msg=msg, action=wording["action"], impact=wording["impact"], routed=ruling.get("routed", "review"),
                    payload={**p["payload"], "kind": p["kind"], "alert_type": p["alert_type"], "proposal_id": p["id"],
                             "facts": facts, "cost": p["cost"], "par_rescued": p["par_rescued"],
                             "counterfactual": ruling.get("counterfactual")},
                )
                s.add(alert)
                s.commit()
                s.refresh(alert)
                alert_id = alert.id
            alert_ids.append(alert_id)
            if ruling.get("routed") == "auto":
                result = execute_alert(alert_id, source="auto")
                executed.append(result["txid"])
                self.bb.say(self.key, wording["action"], stance="execute", phase=self.phase, sku_id=p["sku_id"], txid=result["txid"])
        self.bb.act(self.key, self.phase, f"Raised {len(alert_ids)} alerts", result=f"{len(executed)} executed automatically",
                    alerts=len(alert_ids), executed=len(executed))
        return {"alerts": alert_ids, "executed": executed}
