import os
from datetime import date

import pytest

from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine

TEST_TODAY = date(2026, 10, 1)  # fixed "today" so seeded data is identical on every run
TEST_SEED = 1


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory):
    """Point every test at a throwaway database and switch off outside calls (model, weather)."""
    root = tmp_path_factory.mktemp("sarthi")
    env = {
        "SARTHI_DB_PATH": str(root / "test.db"),
        "SARTHI_LLM_ENABLED": "false",
        "SARTHI_WEATHER_ENABLED": "false",
        "SARTHI_MC_PATHS": "800",
    }
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    get_settings.cache_clear()
    reset_engine()
    yield root
    reset_engine()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


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
def forecasts(store_data):
    """Forecasts for every seeded SKU, computed once (the slowest step in the suite) with its run time."""
    import time

    from sarthi.analytics.forecast import forecast_all

    started = time.perf_counter()
    results = forecast_all(*store_data)
    return results, time.perf_counter() - started
