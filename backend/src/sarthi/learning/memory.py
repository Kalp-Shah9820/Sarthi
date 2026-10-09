"""Learning from the manager's decisions on alerts.

Two things happen when an alert is approved or dismissed:
1. the approval rate for that kind of action is updated (see `bandit`), which governs future automation;
2. any feedback text is turned into a standing rule ("avoid supplier X", "max 300 units") that the
   Compliance Guardian enforces from the next run on.
"""

import re
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz, process
from sqlmodel import select

from sarthi.blackboard.store import Blackboard
from sarthi.db import session
from sarthi.learning import bandit
from sarthi.learning.confidence import arm_key
from sarthi.llm import prompts
from sarthi.models import Alert, Preference, Run, Sku, Supplier, utcnow

MATCH_CUTOFF = 70
TARGETED = {"avoid_supplier": "supplier", "prefer_supplier": "supplier", "cap_qty": "sku", "min_cover_days": "sku",
            "safety_stock_pct": "sku"}


class PreferenceOut(BaseModel):
    scope: Literal["sku", "category", "zone", "supplier", "global"] = "global"
    target: str = ""                # an id or a name; "" for global
    directive: Literal["avoid_supplier", "prefer_supplier", "cap_qty", "min_cover_days", "safety_stock_pct",
                       "no_auto", "other"] = "other"
    value: float | None = None
    note: str = ""


def rule_based(text: str) -> PreferenceOut:
    """Common phrasings, without the model. Anything else is kept as a note."""
    t = text.strip()
    low = t.lower()
    number = re.search(r"(\d+(?:\.\d+)?)", low)
    if m := re.search(r"(?:avoid|don'?t use|do not use|never use|stop using|no more)\s+(.+)", low):
        return PreferenceOut(scope="supplier", target=m.group(1).strip(" ."), directive="avoid_supplier", note=t)
    if m := re.search(r"(?:prefer|always use|use only|stick with)\s+(.+)", low):
        return PreferenceOut(scope="supplier", target=m.group(1).strip(" ."), directive="prefer_supplier", note=t)
    if number and re.search(r"\b(max|maximum|at most|no more than|cap)\b", low) and "unit" in low:
        return PreferenceOut(scope="sku", directive="cap_qty", value=float(number.group(1)), note=t)
    if number and re.search(r"\b(keep|hold|at least|minimum)\b", low) and "day" in low:
        return PreferenceOut(scope="sku", directive="min_cover_days", value=float(number.group(1)), note=t)
    if re.search(r"ask me|check with me|always ask|consult me|my approval|don'?t auto|no auto", low):
        return PreferenceOut(scope="sku", directive="no_auto", note=t)
    return PreferenceOut(directive="other", note=t)


def _resolve(name: str, choices: dict[str, str]) -> str | None:
    """Map free text to an id. `choices` is id -> name; an exact id also matches."""
    if not name:
        return None
    for ident in choices:
        if ident.lower() == name.lower():
            return ident
    found = process.extractOne(name, {i: n.lower() for i, n in choices.items()}, scorer=fuzz.partial_ratio,
                               score_cutoff=MATCH_CUTOFF, processor=str.lower)
    return found[2] if found else None


def to_preference(parsed: PreferenceOut, alert_sku_id: str) -> Preference:
    """Attach the rule to a real supplier or SKU; a rule whose target cannot be found becomes a plain note."""
    with session() as s:
        suppliers = dict(s.exec(select(Supplier.id, Supplier.name)).all())
        skus = dict(s.exec(select(Sku.id, Sku.name)).all())
    directive, scope, target = parsed.directive, parsed.scope, ""
    kind = TARGETED.get(directive)
    if kind == "supplier":
        target = _resolve(parsed.target, suppliers) or ""
        scope = "supplier"
    elif kind == "sku" or directive == "no_auto":
        target = _resolve(parsed.target, skus) or alert_sku_id      # "max 300 units" means the alert's product
        scope = "sku"
    if kind and not target:
        directive = "other"
    if directive in ("cap_qty", "min_cover_days", "safety_stock_pct") and parsed.value is None:
        directive = "other"
    return Preference(scope=scope, target=target, directive=directive, value=parsed.value, note=parsed.note[:300])


async def remember_feedback(alert: Alert, decision: str, text: str, llm) -> Preference | None:
    """Turn feedback text into a stored rule. Returns None for empty text."""
    if not text or not text.strip():
        return None
    fallback = rule_based(text)
    parsed = fallback
    if fallback.directive == "other":        # the simple patterns did not recognise it: ask the model
        parsed = await llm.json(PreferenceOut, prompts.EXTRACT_PREFERENCE,
                                f"Alert: {alert.msg}\nDecision: {decision}\nFeedback: {text}",
                                fallback=lambda: fallback, task="preference")
        stated = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}
        if parsed.value is not None and parsed.value not in stated:
            parsed = fallback                # a number the manager never wrote: keep the feedback as a plain note
        parsed.note = text.strip()           # the stored note is always the manager's own words
    preference = to_preference(parsed, alert.sku_id)
    with session() as s:
        s.add(preference)
        s.commit()
        s.refresh(preference)
    return preference


def record_decision(alert: Alert, approved: bool) -> float:
    """Update the approval rate for this kind of action and note it on the latest run. Returns the change."""
    key = arm_key(alert.zone, alert.type)
    delta = bandit.record(key, approved)
    remedy = alert.payload.get("remedy") if alert.payload else None
    if remedy:                               # overstock remedies also learn which remedy is preferred
        bandit.record(f"{alert.zone}:{remedy}", approved)
    with session() as s:
        latest = s.exec(select(Run.id).where(Run.dry_run.is_(False)).order_by(Run.id.desc())).first()
    bb = Blackboard(latest if latest is not None else alert.run_id)
    bb.put("rlhfArbiter", "execute", "rlhf_weight_update", f"{delta:+.2f}", tone="money")
    verb = "approved" if approved else "dismissed"
    bb.act("rlhfArbiter", "execute", f"Manager {verb} a {alert.type.replace('_', ' ')} for {alert.sku_label}",
           result=f"approval rate for {key} now {bandit.mean(key):.0%}", sku_id=alert.sku_id)
    return delta


def note_decision(alert_id: int, approved: bool, feedback: str | None) -> tuple[Alert, float]:
    """Record the manager's click and update the approval rate. Returns the alert and the rate's change."""
    with session() as s:
        alert = s.get(Alert, alert_id)
        if alert is None:
            raise LookupError(f"alert {alert_id} not found")
        if not approved:
            alert.status, alert.decided_at = "dismissed", utcnow()
        if feedback:
            alert.feedback = feedback
        s.add(alert)
        s.commit()
        s.refresh(alert)
    return alert, record_decision(alert, approved)


async def learn_from_decision(alert_id: int, approved: bool, feedback: str | None, llm) -> dict:
    """Everything that follows a manager's click, except executing the action itself."""
    alert, delta = note_decision(alert_id, approved, feedback)
    preference = await remember_feedback(alert, "approved" if approved else "dismissed", feedback or "", llm)
    return {"delta": delta, "preference": preference.directive if preference else None,
            "target": preference.target if preference else None}
