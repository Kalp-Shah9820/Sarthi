"""Cross-milestone validation: one command that tells you whether anything is broken.

`uv run sarthi validate` runs the quick checks; `--full` adds ruff and pytest; `--frontend` adds the
Vite build. Each plan milestone appends its own check function to CHECKS, so breakage introduced by a
later milestone in an earlier one shows up here.
"""

import importlib
import pkgutil
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

THIRD_PARTY = [
    "fastapi", "uvicorn", "sqlmodel", "pydantic_settings", "typer", "numpy", "pandas", "scipy",
    "statsforecast", "openai", "httpx", "langgraph", "sse_starlette", "rapidfuzz", "python_multipart",
    "openpyxl", "xlrd", "lxml",
]


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


def check_dependencies() -> Result:
    missing = []
    for mod in THIRD_PARTY:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # a broken install can raise more than ImportError
            missing.append(f"{mod} ({type(exc).__name__})")
    if missing:
        return Result("dependencies", FAIL, "cannot import: " + ", ".join(missing))
    return Result("dependencies", PASS, f"{len(THIRD_PARTY)} packages import")


def check_imports() -> Result:
    """Import every module under `sarthi` so a broken import in any milestone is caught."""
    import sarthi

    broken, count = [], 0
    for info in pkgutil.walk_packages(sarthi.__path__, prefix="sarthi."):
        count += 1
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            broken.append(f"{info.name}: {type(exc).__name__}: {exc}")
    if broken:
        return Result("imports", FAIL, "; ".join(broken))
    return Result("imports", PASS, f"{count} sarthi modules import")


def check_config() -> Result:
    from sarthi.config import BACKEND_ROOT, get_settings

    s = get_settings()
    problems = []
    if not s.llm_base_url.startswith("http"):
        problems.append("SARTHI_LLM_BASE_URL is not a URL")
    if s.mc_paths < 100:
        problems.append("SARTHI_MC_PATHS is below 100")
    if not s.db_file.parent.exists():
        problems.append(f"database folder missing: {s.db_file.parent}")
    if problems:
        return Result("config", FAIL, "; ".join(problems))
    if not (BACKEND_ROOT / ".env").exists():
        return Result("config", WARN, "no .env file; using built-in defaults")
    return Result("config", PASS, f"model={s.llm_model}, db={s.db_file.name}")


def check_api() -> Result:
    from fastapi.testclient import TestClient

    from sarthi.api.main import create_app

    with TestClient(create_app()) as client:
        r = client.get("/api/health")
    if r.status_code != 200 or r.json().get("status") != "ok":
        return Result("api", FAIL, f"/api/health returned {r.status_code}: {r.text[:120]}")
    return Result("api", PASS, f"/api/health -> {r.json()}")


def check_llm() -> Result:
    """LM Studio is optional: offline mode is a supported state, so problems here are warnings."""
    import httpx

    from sarthi.config import get_settings

    s = get_settings()
    if not s.llm_enabled:
        return Result("llm", WARN, "disabled by SARTHI_LLM_ENABLED=false (offline mode)")
    try:
        r = httpx.get(s.llm_base_url.rstrip("/") + "/models", timeout=3)
        ids = [m["id"] for m in r.json().get("data", [])]
    except Exception as exc:
        return Result("llm", WARN, f"LM Studio not reachable ({type(exc).__name__}); offline mode will be used")
    if s.llm_model not in ids:
        return Result("llm", WARN, f"'{s.llm_model}' not listed by LM Studio; available: {ids}")
    return Result("llm", PASS, f"LM Studio lists {s.llm_model}")


def _schema_drift() -> str:
    """Columns the code expects but the database file lacks (a model changed after the file was created)."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel

    from sarthi.db import get_engine

    inspector = inspect(get_engine())
    missing = []
    for table in SQLModel.metadata.sorted_tables:
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    return ", ".join(missing)


def samples_dir():
    from sarthi.config import BACKEND_ROOT

    return BACKEND_ROOT / "samples"


SAMPLE_FILES = {
    "sku": "sku.csv", "sales": "sales.csv", "stock": "stock.json", "vendor": "vendor.xlsx",
    "forecast": "forecast.csv", "risk": "risk.json", "locations": "locations.csv", "esg": "esg.xlsx",
}


def check_ingest() -> Result:
    """mk3: every sample upload file still parses and validates (dry run, nothing is written)."""
    from sarthi.ingest.loader import check_file

    folder = samples_dir()
    present = {t: folder / name for t, name in SAMPLE_FILES.items() if (folder / name).exists()}
    if len(present) < len(SAMPLE_FILES):
        return Result("ingest", WARN, f"{len(present)} of 8 sample files found; run `uv run sarthi export-samples`")
    problems, rows = [], 0
    for upload_type, path in present.items():
        clean, _, dropped = check_file(upload_type, path.name, path.read_bytes())
        rows += len(clean)
        if dropped or clean.empty:
            problems.append(f"{path.name}: {dropped} rows rejected")
    if problems:
        return Result("ingest", FAIL, "; ".join(problems) + " (reseed and re-export if the data changed)")
    return Result("ingest", PASS, f"8 sample files validate, {rows:,} rows")


def check_database() -> Result:
    """mk2: tables exist, data is seeded, and the basic integrity rules hold."""
    from sqlmodel import func, select

    from sarthi.db import init_db, session
    from sarthi.models import Delivery, Sale, Sku, StockDaily, Supplier

    init_db()
    drift = _schema_drift()
    if drift:
        return Result("database", FAIL, f"schema is out of date ({drift}); run `uv run sarthi seed` to rebuild it")
    with session() as s:
        skus = s.exec(select(func.count()).select_from(Sku)).one()
        if skus == 0:
            return Result("database", WARN, "tables exist but are empty; run `uv run sarthi seed`")
        sales = s.exec(select(func.count()).select_from(Sale)).one()
        suppliers = s.exec(select(func.count()).select_from(Supplier)).one()
        deliveries = s.exec(select(func.count()).select_from(Delivery)).one()
        negative = s.exec(select(func.count()).select_from(StockDaily).where(StockDaily.on_hand < 0)).one()
        orphans = s.exec(
            select(func.count()).select_from(Sale).where(Sale.sku_id.not_in(select(Sku.id)))
        ).one()
        latest = s.exec(select(func.max(StockDaily.day))).one()
    problems = []
    if negative:
        problems.append(f"{negative} stock rows are negative")
    if orphans:
        problems.append(f"{orphans} sales reference unknown SKUs")
    if sales == 0:
        problems.append("no sales history")
    if suppliers == 0:
        problems.append("no suppliers")
    if problems:
        return Result("database", FAIL, "; ".join(problems))
    return Result("database", PASS, f"{skus} SKUs, {sales:,} sales, {deliveries} deliveries, stock as of {latest}")


def check_analytics() -> Result:
    """mk4: the calculation core runs end to end on the current data (quick: no forecast models are fitted)."""
    import numpy as np
    from sqlmodel import select

    from sarthi.analytics import basket, budget, data, montecarlo, policy, topsis, transfer
    from sarthi.analytics.forecast import uncensor, weekday_mean_forecast
    from sarthi.db import session
    from sarthi.models import Sku

    sales, stock = data.store_frames()
    if sales.empty:
        return Result("analytics", WARN, "no sales history to analyse; run `uv run sarthi seed`")
    sku_id = sales.groupby("sku_id")["qty"].sum().idxmax()
    sold = sales[sales["sku_id"] == sku_id].sort_values("day")["qty"].to_numpy()
    held = stock[stock["sku_id"] == sku_id].sort_values("day")["on_hand"].to_numpy()
    mu = weekday_mean_forecast(uncensor(sold, held), 30)
    mc = montecarlo.simulate(float(held[-1]), [], mu, 8.0, np.array([5.0]), 500, montecarlo.sku_seed(1, sku_id))
    safety, rop = policy.reorder_point(mc.ltd, 0.95)
    with session() as s:
        names = dict(s.exec(select(Sku.id, Sku.name)).all())
    rules = basket.mine_rules(data.basket_frame(), names)

    ideal = {name: best for name, (best, _) in topsis.CRITERIA.items()}
    worst = {name: anti for name, (_, anti) in topsis.CRITERIA.items()}
    problems = []
    if not (0.0 <= mc.stockout_prob <= 1.0 and rop >= safety >= 0 and len(montecarlo.histogram(mc.lost_frac)) == 20):
        problems.append("stockout simulation returned out-of-range values")
    if topsis.score([ideal, worst], "Balanced") != [100.0, 0.0]:
        problems.append("supplier scoring anchors moved")
    if budget.fund_orders(np.array([5.0, 9.0]), np.array([10.0, 10.0]), 10).tolist() != [0, 1]:
        problems.append("budget optimiser gave a wrong answer")
    moves = transfer.plan_transfers({"A": 100}, {"B": 60}, {("A", "B"): 5.0}, {"B": 20.0})
    if [m["units"] for m in moves] != [60]:
        problems.append("transfer optimiser gave a wrong answer")
    if problems:
        return Result("analytics", FAIL, "; ".join(problems))
    return Result(
        "analytics", PASS,
        f"{sku_id}: {mc.stockout_prob:.0%} stockout risk, reorder point {rop}; {len(rules)} basket rules; optimisers ok",
    )


VALIDATION_RUN_ID = -1  # events written by the validator use this id and are removed afterwards


def check_agent_core() -> Result:
    """mk5: blackboard, wording templates, grounding check and the model gateway (offline and, if up, live)."""
    import asyncio
    import time

    from pydantic import BaseModel

    from sarthi.blackboard.store import Blackboard, delete_run_events
    from sarthi.config import get_settings
    from sarthi.llm import prompts, templates
    from sarthi.llm.gateway import LlmGateway
    from sarthi.llm.grounding import grounded

    problems = []

    facts = {"prob": 0.84, "qty": 1482, "par": 18500, "name": "Lays Classic 26g"}
    if not grounded("84% stockout risk on Lays Classic 26g; order 1,482 units to protect \u20b918.5K", facts):
        problems.append("grounding rejected a correct sentence")
    if grounded("Order 1,500 units of Lays Classic 26g", facts):
        problems.append("grounding accepted an invented number")

    for kind in templates.ALERT:
        for lang in ("EN", "HI"):
            dummy = dict.fromkeys(templates.fields("alert", kind, lang), 7)
            if set(templates.render("alert", kind, lang, **dummy)) != {"msg", "action", "impact"}:
                problems.append(f"alert template {kind}/{lang} is incomplete")

    bb = Blackboard(VALIDATION_RUN_ID)
    try:
        bb.put("validator", "sense", "lead_time_risk", "LOW")
        bb.put("validator", "sense", "lead_time_risk", "HIGH")
        bb.act("validator", "sense", "checked the blackboard", result="ok")
        if bb.get("lead_time_risk") != "HIGH" or len(bb.context()) != 1 or len(bb.events(kind="action")) != 1:
            problems.append("blackboard did not return what was written")
    finally:
        delete_run_events(VALIDATION_RUN_ID)

    class Probe(BaseModel):
        ok: bool

    async def exercise() -> tuple[str, float]:
        offline = LlmGateway(get_settings())            # mode stays "offline": must fall back without any call
        if (await offline.json(Probe, "s", "u", fallback=lambda: Probe(ok=False))).ok:
            problems.append("offline gateway did not use the fallback")
        live = LlmGateway(get_settings())
        if await live.probe() != "llm":
            return "offline", 0.0
        said = {"name": "Lays Classic 26g", "days_of_cover": 1.6, "lead_time_days": 12, "stockout_probability_pct": 97}
        started = time.perf_counter()
        raw = await live.raw_reply(prompts.NARRATE_ALERT, prompts.user_message(said), max_tokens=120)
        seconds = time.perf_counter() - started
        if raw is None:
            return "no-reply", seconds
        return ("ok" if grounded(raw, said) else "ungrounded"), seconds

    outcome, seconds = asyncio.run(exercise())
    if problems:
        return Result("agent core", FAIL, "; ".join(problems))
    base = "blackboard, templates and grounding ok"
    if outcome == "offline":
        return Result("agent core", WARN, f"{base}; model offline, so template wording will be used")
    if outcome == "no-reply":
        return Result("agent core", WARN,
                      f"{base}; model listed but gave no usable reply in {seconds:.0f}s (wrong or thinking model?)")
    if outcome == "ungrounded":
        return Result("agent core", PASS,
                      f"{base}; live reply in {seconds:.1f}s was rejected by the grounding check (template used)")
    return Result("agent core", PASS, f"{base}; live grounded reply in {seconds:.1f}s")


def check_agents() -> Result:
    """mk6: the SENSE and DECIDE agents produce a decision for every SKU (quick: uses the simple forecast)."""
    from collections import Counter

    from sarthi.agents.compliance_guardian import build_envelope, review
    from sarthi.agents.inventory_optimizer import Context, decide, finalise
    from sarthi.agents.macro_sentinel import MacroSentinel, fuse, load_feeds
    from sarthi.agents.stages import BALANCED
    from sarthi.analytics import data
    from sarthi.analytics.forecast import _fallback, uncensor
    from sarthi.blackboard.store import Blackboard
    from sarthi.config import get_settings

    settings = get_settings()
    sales, stock = data.store_frames()
    if sales.empty:
        return Result("agents", WARN, "no sales history; run `uv run sarthi seed`")

    sentinel = MacroSentinel(Blackboard(VALIDATION_RUN_ID), None, settings, dict(BALANCED))
    suppliers, store, _, skus, uploaded = sentinel._master_data()
    lead_modifier, demand_multiplier = fuse(load_feeds(settings.feeds_dir) + uploaded, suppliers, store, skus)

    forecasts = {}
    for sku_id, g in sales.sort_values("day").groupby("sku_id"):
        held = stock[stock["sku_id"] == sku_id].set_index("day")["on_hand"].reindex(g["day"]).to_numpy(dtype=float)
        sold = g["qty"].to_numpy(dtype=float)
        forecasts[sku_id] = _fallback(sku_id, uncensor(sold, held), sold, 30, g["day"].iloc[-1])
    ctx = Context(forecasts, {"sales": sales, "stock": stock, "as_of": sales["day"].max()})
    zones_found: Counter = Counter()
    missing = []
    for sku in ctx.skus:
        inp = ctx.inputs(sku, ctx.as_of, lead_modifier, demand_multiplier)
        if inp is None:
            missing.append(sku["id"])
            continue
        record = decide(inp, settings, BALANCED, 300, settings.seed)
        zones_found[finalise(record, record["zone_raw"])["zone"]] += 1

    envelope = build_envelope(settings, BALANCED)
    over = {"kind": "purchase", "sku_id": "X", "moq": 6,
            "payload": {"supplier_id": "S", "qty": 6, "unit_price": envelope["budget_remaining"] + 1.0, "mode": "multimodal"}}
    problems = []
    if missing:
        problems.append(f"no decision for {', '.join(missing)}")
    if not any(v.rule == "BUDGET_CAP" for v in review(over, envelope)):
        problems.append("the budget rule did not fire on an order above the budget")
    if not lead_modifier or min(lead_modifier.values()) < 1.0:
        problems.append("lead-time modifiers are missing or below 1")
    if problems:
        return Result("agents", FAIL, "; ".join(problems))
    summary = ", ".join(f"{n} {zone}" for zone, n in sorted(zones_found.items()))
    return Result("agents", PASS, f"{sum(zones_found.values())} SKUs decided ({summary}); "
                                  f"budget \u20b9{envelope['budget_remaining'] / 1000:.0f}K; "
                                  f"worst lead-time modifier {max(lead_modifier.values())}")


STAGE_BUDGET_S = 90


def check_performance() -> Result:
    """Time a cold SENSE + DECIDE pass (forecasting dominates). A warning, not a failure: it depends on machine load."""
    import asyncio
    import time

    from sarthi.agents import demand_intel
    from sarthi.agents.stages import sense_and_decide
    from sarthi.analytics import data
    from sarthi.blackboard.store import delete_run_events

    if data.store_frames()[0].empty:
        return Result("performance", WARN, "no sales history; run `uv run sarthi seed`")
    demand_intel.clear_cache()
    try:
        started = time.perf_counter()
        state = asyncio.run(sense_and_decide(VALIDATION_RUN_ID, dry_run=True))
        cold = time.perf_counter() - started
        started = time.perf_counter()
        asyncio.run(sense_and_decide(VALIDATION_RUN_ID, dry_run=True))
        warm = time.perf_counter() - started
    finally:
        delete_run_events(VALIDATION_RUN_ID)
    if state["errors"] or "decisions" not in state:
        return Result("performance", FAIL, "SENSE + DECIDE did not complete: " + "; ".join(state["errors"]))
    detail = f"SENSE + DECIDE for {len(state['decisions'])} SKUs: {cold:.0f}s cold (budget {STAGE_BUDGET_S}s), {warm:.1f}s warm"
    if cold > STAGE_BUDGET_S:
        return Result("performance", WARN, detail + "; check power mode (battery throttles the CPU) and other load")
    return Result("performance", PASS, detail)


# Each milestone appends its check here, so a later change that breaks an earlier milestone is caught.
CHECKS: list[Callable[[], Result]] = [
    check_dependencies, check_imports, check_config, check_api, check_llm, check_database, check_ingest,
    check_analytics, check_agent_core, check_agents,
]


def _run_tool(name: str, cmd: list[str], cwd) -> Result:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
    except FileNotFoundError:
        return Result(name, FAIL, f"command not found: {cmd[0]}")
    lines = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if ln.strip()]
    tail = lines[-1] if lines else ""
    return Result(name, PASS if proc.returncode == 0 else FAIL, tail[:160])


def run(full: bool = False, frontend: bool = False, perf: bool = False) -> list[Result]:
    from sarthi.config import BACKEND_ROOT

    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a crashing check is itself a failure, not a crash of the validator
            results.append(Result(check.__name__.removeprefix("check_"), FAIL, f"{type(exc).__name__}: {exc}"))
    if perf:
        results.append(check_performance())
    if full:
        results.append(_run_tool("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests"], BACKEND_ROOT))
        results.append(_run_tool("pytest", [sys.executable, "-m", "pytest", "-q"], BACKEND_ROOT))
    if frontend:
        npm = shutil.which("npm")
        if npm is None:
            results.append(Result("frontend build", FAIL, "npm not found on PATH"))
        else:
            results.append(_run_tool("frontend build", [npm, "run", "build"], BACKEND_ROOT.parent))
    return results


def report(results: list[Result]) -> bool:
    """Print a table; return True when nothing failed."""
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"[{r.status}] {r.name.ljust(width)}  {r.detail}")
    failed = [r for r in results if r.status == FAIL]
    warned = [r for r in results if r.status == WARN]
    print(f"\n{len(results) - len(failed) - len(warned)} passed, {len(warned)} warnings, {len(failed)} failed")
    return not failed
