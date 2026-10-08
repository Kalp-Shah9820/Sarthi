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


# Later milestones append their checks here (database seeded, pipeline run completes, ...).
CHECKS: list[Callable[[], Result]] = [check_dependencies, check_imports, check_config, check_api, check_llm]


def _run_tool(name: str, cmd: list[str], cwd) -> Result:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
    except FileNotFoundError:
        return Result(name, FAIL, f"command not found: {cmd[0]}")
    lines = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if ln.strip()]
    tail = lines[-1] if lines else ""
    return Result(name, PASS if proc.returncode == 0 else FAIL, tail[:160])


def run(full: bool = False, frontend: bool = False) -> list[Result]:
    from sarthi.config import BACKEND_ROOT

    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a crashing check is itself a failure, not a crash of the validator
            results.append(Result(check.__name__.removeprefix("check_"), FAIL, f"{type(exc).__name__}: {exc}"))
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
