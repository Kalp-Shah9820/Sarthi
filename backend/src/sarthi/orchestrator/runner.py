"""Runs the agent graph once and records the run."""

import asyncio

from sqlmodel import select

from sarthi.blackboard.store import Blackboard, end_run
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.llm import get_llm
from sarthi.models import Run, Sku, StrategyPolicy, local_today, utcnow
from sarthi.orchestrator.graph import build_agents, build_graph
from sarthi.orchestrator.state import PHASE_OF
from sarthi.seed.catalog import DEFAULT_STRATEGY

# One real run at a time; what-if runs queue separately so they never block a real run.
_locks: dict[bool, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_summaries: dict[int, dict] = {}       # run_id -> what the run produced, for callers in the same process


def _lock(dry_run: bool) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    held = _locks.get(dry_run)
    if held is None or held[0] is not loop:         # a lock belongs to one event loop (the CLI and tests use several)
        held = _locks[dry_run] = (loop, asyncio.Lock())
    return held[1]


def active_strategy() -> dict:
    """The strategy in force: {mode, savingsPriority, safetyStockMultiplier, leadTimeBuffer}. Expired policies revert to Balanced."""
    with session() as s:
        policy = s.exec(select(StrategyPolicy).where(StrategyPolicy.active).order_by(StrategyPolicy.id.desc())).first()
        if policy is not None and policy.expires_on is not None and policy.expires_on < local_today():
            policy.active = False
            s.add(policy)
            policy = StrategyPolicy(mode="Balanced", params=dict(DEFAULT_STRATEGY), source_text="previous policy expired", active=True)
            s.add(policy)
            s.commit()
            s.refresh(policy)
        if policy is None:
            return {"mode": "Balanced", **DEFAULT_STRATEGY}
        return {"mode": policy.mode, **DEFAULT_STRATEGY, **(policy.params or {})}


def latest_run_id(dry_run: bool = False, status: str | None = "done") -> int | None:
    with session() as s:
        query = select(Run.id).where(Run.dry_run == dry_run)
        if status:
            query = query.where(Run.status == status)
        return s.exec(query.order_by(Run.id.desc())).first()


def run_summary(run_id: int) -> dict | None:
    """What a run in this process produced: status, errors, counts of proposals, rulings, alerts."""
    return _summaries.get(run_id)


def has_data() -> bool:
    with session() as s:
        return s.exec(select(Sku.id)).first() is not None


def start_run(trigger: str, *, scenario: dict | None = None, dry_run: bool = False) -> int:
    """Create the run record. Split from `execute_run` so a caller can subscribe to its events before it starts."""
    with session() as s:
        run = Run(trigger=trigger, dry_run=dry_run, scenario=scenario or {}, strategy=active_strategy(), status="running")
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


async def execute_run(run_id: int, *, lang: str = "EN") -> dict:
    """Run the graph for an existing run record. Returns the run's summary."""
    settings, llm = get_settings(), get_llm()
    with session() as s:
        run = s.get(Run, run_id)
        scenario, dry_run, strategy = dict(run.scenario or {}), run.dry_run, dict(run.strategy or {})
    bb = Blackboard(run_id)
    summary = {"run_id": run_id, "status": "running", "errors": [], "proposals": 0, "rulings": 0, "approved": 0,
               "alerts": [], "executed": [], "decisions": {}}
    try:
        async with _lock(dry_run):
            await llm.probe()                   # opening or closing LM Studio takes effect on the next run
            graph = build_graph(build_agents(bb, llm, settings, strategy))
            state = {"run_id": run_id, "dry_run": dry_run, "lang": lang, "scenario": scenario, "strategy": strategy}
            phase = None
            async for update in graph.astream(state, stream_mode="updates"):
                for node, out in update.items():
                    out = out or {}
                    if PHASE_OF.get(node) and PHASE_OF[node] != phase:
                        phase = PHASE_OF[node]
                        bb.put("orchestrator", phase, "phase", phase)
                    summary["errors"] += out.get("errors", [])
                    summary["proposals"] += len(out.get("proposals", []))
                    if "decisions" in out:
                        summary["decisions"] = {k: {"zone": d["zone"], "risk": d["risk"], "par": d["par"], "qty": d["qty"],
                                                    "stockout_prob": d["stockout_prob"]} for k, d in out["decisions"].items()}
                    if "rulings" in out:
                        summary["rulings"] = len(out["rulings"])
                        summary["approved"] = sum(r["status"] == "approved" for r in out["rulings"])
                        summary["rejected"] = [(r["proposal"]["id"], r["reasons"]) for r in out["rulings"] if r["status"] == "rejected"]
                    summary["alerts"] += out.get("alerts", [])
                    summary["executed"] += out.get("executed", [])
        summary["status"] = "failed" if summary["errors"] else "done"
    except Exception as exc:
        summary["status"] = "failed"
        summary["errors"].append(f"orchestrator: {type(exc).__name__}: {exc}")
        bb.error("orchestrator", exc)
        raise
    finally:
        with session() as s:
            run = s.get(Run, run_id)
            run.status, run.finished_at = summary["status"], utcnow()
            s.add(run)
            s.commit()
        _summaries[run_id] = summary
        if len(_summaries) > 50:
            _summaries.pop(next(iter(_summaries)))
        end_run(run_id)                         # tell live listeners the stream is over
    return summary


async def run_pipeline(trigger: str, *, scenario: dict | None = None, dry_run: bool = False, lang: str = "EN") -> int:
    """Run every agent once. Returns the run id; details are in the database and in `run_summary(run_id)`."""
    run_id = start_run(trigger, scenario=scenario, dry_run=dry_run)
    await execute_run(run_id, lang=lang)
    return run_id
