import os
from datetime import date

import pytest

from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine
from sarthi.llm import reset_llm

TEST_TODAY = date(2026, 10, 1)  # fixed "today" so seeded data is identical on every run
TEST_SEED = 1


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
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def db(isolated_settings):
    """The test database with tables created (it may or may not be seeded)."""
    init_db()


@pytest.fixture(scope="session")
def seeded_db(isolated_settings):
    """A database filled once per test session with the demo history."""
    from sarthi.seed.generator import seed_database

    init_db()
    return seed_database(TEST_SEED, today=TEST_TODAY)


@pytest.fixture(scope="session")
def store_data(seeded_db):
    """(sales_daily, stock_daily) for the store, loaded once."""
    from sarthi.analytics import data

    return data.store_frames()


@pytest.fixture(scope="session")
def analysis(seeded_db):
    """Demand Intelligence's full analysis of the seeded history, computed once (the slowest step in the
    suite) and left in the agent's cache so agent tests do not forecast again."""
    import time

    from sarthi.agents import demand_intel

    settings = get_settings()
    started = time.perf_counter()
    result = demand_intel.analyse(settings)
    result["seconds"] = time.perf_counter() - started
    demand_intel._cache[demand_intel._fingerprint(settings)] = result
    return result


@pytest.fixture(scope="session")
def forecasts(analysis):
    """(forecasts per SKU, seconds the analysis took)."""
    return analysis["forecasts"], analysis["seconds"]
