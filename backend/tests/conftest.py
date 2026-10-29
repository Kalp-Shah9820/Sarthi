import hashlib
import os
import pickle
import shutil
from contextlib import contextmanager
from datetime import date
from importlib import metadata
from pathlib import Path

import pytest

from sarthi.config import BACKEND_ROOT, Settings, get_settings
from sarthi.db import init_db, reset_engine
from sarthi.llm import reset_llm

TEST_TODAY = date(2026, 10, 1)  # fixed "today" so seeded data is identical on every run
TEST_SEED = 1
ANALYSIS_CACHE = BACKEND_ROOT / ".pytest_cache" / "sarthi"
# Source the seeded analysis depends on: a change to any of it invalidates the cached analysis.
ANALYSIS_SOURCES = ("analytics", "seed", "agents/demand_intel.py", "ingest", "models.py")


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory):
    """Point every test at a throwaway database and switch off outside calls (model, weather)."""
    root = tmp_path_factory.mktemp("sarthi")
    env = {
        "SARTHI_DB_PATH": str(root / "test.db"),
        "SARTHI_OUTBOX_PATH": str(root / "outbox"),
        "SARTHI_LLM_ENABLED": "false",
        "SARTHI_WEATHER_ENABLED": "false",
        "SARTHI_MC_PATHS": "800",
        "SARTHI_SKIP_STARTUP_RUN": "true",
        "SARTHI_RERUN_AFTER_ACTION": "false",     # tests that want the follow-up run switch it on themselves
    }
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    yield root
    reset_engine()
    reset_llm()
    _restore(previous)
    get_settings.cache_clear()


def settings_without_env_file(**overrides) -> Settings:
    """Settings built from defaults, the environment and `overrides` only, ignoring backend/.env.

    pydantic-settings accepts `_env_file` at run time, but its type stubs do not declare it.
    """
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def must[T](value: T | None) -> T:
    """`value`, which a test expects to exist: fails the test with a clear message if it is None."""
    assert value is not None, "expected a value, got None"
    return value


def _restore(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session")
def template_db(isolated_settings, tmp_path_factory) -> tuple[Path, dict]:
    """The demo history, seeded once into a file that no test opens. Every seeded database is a copy of it.

    Returns (path, rows per table).
    """
    from sarthi.seed.generator import seed_database

    path = tmp_path_factory.mktemp("template") / "template.db"
    previous: dict[str, str | None] = {"SARTHI_DB_PATH": os.environ["SARTHI_DB_PATH"]}
    os.environ["SARTHI_DB_PATH"] = str(path)
    get_settings.cache_clear()
    reset_engine()
    try:
        init_db()
        counts = seed_database(TEST_SEED, today=TEST_TODAY)
    finally:
        reset_engine()                  # closing the last connection folds the write-ahead log into the file
        _restore(previous)
        get_settings.cache_clear()
    return path, counts


def _copy_database(template: Path, target: Path) -> None:
    """Copy the seeded database file, and the simulated outside feeds the seed writes next to it."""
    for suffix in ("-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    shutil.copyfile(template, target)
    feeds = template.parent / "feeds"
    if feeds.is_dir():
        shutil.copytree(feeds, target.parent / "feeds", dirs_exist_ok=True)


@contextmanager
def private_database(tmp_path_factory, template_db, name: str, *, analysis: dict | None = None,
                     env: dict[str, str] | None = None, outbox: bool = True):
    """A copy of the seeded demo database for tests that change data. Yields the folder it lives in.

    `analysis`: the session's forecast analysis, reused (same data, so it holds). `env`: extra settings.
    Everything is put back as it was afterwards.
    """
    from sarthi.agents import demand_intel
    from sarthi.api import presenters

    root = tmp_path_factory.mktemp(name)
    changes = {"SARTHI_DB_PATH": str(root / f"{name}.db"), **({"SARTHI_OUTBOX_PATH": str(root / "outbox")} if outbox else {}),
               **(env or {})}
    previous = {k: os.environ.get(k) for k in changes}
    reset_engine()
    _copy_database(template_db[0], root / f"{name}.db")
    os.environ.update(changes)
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()
    init_db()
    saved_cache = dict(demand_intel._cache)
    if analysis is not None:
        demand_intel._cache[demand_intel._fingerprint(get_settings())] = analysis
    try:
        yield root
    finally:
        demand_intel._cache.clear()
        demand_intel._cache.update(saved_cache)
        reset_engine()
        _restore(previous)
        get_settings.cache_clear()
        reset_engine()
        reset_llm()
        presenters.reset()


@pytest.fixture(scope="session")
def db(isolated_settings):
    """The test database with tables created (it may or may not be seeded)."""
    init_db()


@pytest.fixture(scope="session")
def seeded_db(isolated_settings, template_db):
    """The session database, filled with the demo history (a copy of the template). Returns rows per table."""
    reset_engine()
    _copy_database(template_db[0], Path(os.environ["SARTHI_DB_PATH"]))
    init_db()
    return template_db[1]


@pytest.fixture(scope="session")
def store_data(seeded_db):
    """(sales_daily, stock_daily) for the store, loaded once."""
    from sarthi.analytics import data

    return data.store_frames()


def _analysis_key() -> str:
    """Changes whenever anything the seeded analysis depends on changes: its code, the libraries, the seed."""
    digest = hashlib.sha256(f"{TEST_SEED}:{TEST_TODAY}".encode())
    src = BACKEND_ROOT / "src" / "sarthi"
    for entry in ANALYSIS_SOURCES:
        path = src / entry
        for file in sorted(path.rglob("*.py")) if path.is_dir() else [path]:
            digest.update(file.relative_to(src).as_posix().encode())
            digest.update(file.read_bytes())
    for package in ("statsforecast", "numpy", "pandas", "scipy"):
        digest.update(f"{package}={metadata.version(package)}".encode())
    return digest.hexdigest()[:20]


@pytest.fixture(scope="session")
def analysis(seeded_db):
    """Demand Intelligence's full analysis of the seeded history, left in the agent's cache so agent tests
    do not forecast again.

    Fitting the forecasts is by far the slowest step of the suite, and its result depends only on the code
    and the fixed seed. So it is also kept on disk, under a key made from that code and the library
    versions, and reused by the next test session. `SARTHI_TEST_FRESH_FORECAST=1` forces a new fit.
    """
    import time

    from sarthi.agents import demand_intel

    settings = get_settings()
    cached = ANALYSIS_CACHE / f"analysis-{_analysis_key()}.pkl"
    result = None
    if cached.exists() and os.environ.get("SARTHI_TEST_FRESH_FORECAST") != "1":
        try:
            result = pickle.loads(cached.read_bytes())      # written by this suite, keyed by its own source
        except Exception:  # noqa: BLE001  a damaged or outdated file just means fitting again
            result = None
    if result is None:
        started = time.perf_counter()
        result = demand_intel.analyse(settings)
        result["seconds"] = time.perf_counter() - started
        ANALYSIS_CACHE.mkdir(parents=True, exist_ok=True)
        for old in ANALYSIS_CACHE.glob("analysis-*.pkl"):
            old.unlink(missing_ok=True)
        cached.write_bytes(pickle.dumps(result))
    demand_intel._cache[demand_intel._fingerprint(settings)] = result
    return result


@pytest.fixture(scope="session")
def forecasts(analysis):
    """(forecasts per SKU, seconds the analysis took)."""
    return analysis["forecasts"], analysis["seconds"]
