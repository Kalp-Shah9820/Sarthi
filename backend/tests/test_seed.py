import json
import math
from collections import Counter
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import func, select

from sarthi import validate
from sarthi.api.main import create_app
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.models import (
    Aisle,
    Delivery,
    Inbound,
    Location,
    PriceHistory,
    Sale,
    Sku,
    SkuSupplier,
    StockDaily,
    StrategyPolicy,
    Supplier,
)
from sarthi.seed import catalog as cat
from sarthi.seed.generator import HISTORY_DAYS, build_dataset, dispersion
from tests.conftest import TEST_SEED, TEST_TODAY


def count(model) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model)).one()


def test_master_data_counts(seeded_db):
    assert count(Sku) == 10
    assert count(Supplier) == 3
    assert count(Location) == 5
    assert count(Aisle) == 11
    assert count(SkuSupplier) == 30
    assert count(PriceHistory) == 60
    assert count(Sale) > 100_000


def test_final_store_stock_matches_the_ui(seeded_db):
    with session() as s:
        rows = s.exec(select(StockDaily).where(StockDaily.day == TEST_TODAY, StockDaily.location_id == cat.STORE_ID)).all()
    final = {r.sku_id: r.on_hand for r in rows}
    assert final == {sku["id"]: sku["stock"] for sku in cat.SKUS}


def test_ledger_is_complete_and_never_negative(seeded_db):
    with session() as s:
        per_sku = s.exec(
            select(StockDaily.sku_id, func.count(), func.min(StockDaily.on_hand))
            .where(StockDaily.location_id == cat.STORE_ID).group_by(StockDaily.sku_id)
        ).all()
    assert len(per_sku) == 10
    for sku_id, days, lowest in per_sku:
        assert days == HISTORY_DAYS, sku_id
        assert lowest >= 0, sku_id


def test_recent_velocity_is_consistent_with_the_catalogue(seeded_db):
    """Last-14-day sales average sits near `vel`: within 25 %, or within 3 standard errors for noisy slow movers."""
    start = TEST_TODAY - timedelta(days=13)
    with session() as s:
        sold = dict(s.exec(select(Sale.sku_id, func.sum(Sale.qty)).where(Sale.day >= start).group_by(Sale.sku_id)).all())
    for sku in cat.SKUS:
        vel, k = sku["vel"], dispersion(sku["zone"])
        standard_error = math.sqrt((vel + vel * vel / k) / 14)
        assert abs(sold[sku["id"]] / 14 - vel) <= max(0.25 * vel, 3 * standard_error), sku["id"]


def test_sales_reference_known_skus_and_form_baskets(seeded_db):
    with session() as s:
        sku_ids = set(s.exec(select(Sku.id)).all())
        sold_ids = set(s.exec(select(Sale.sku_id).distinct()).all())
        rows, baskets = s.exec(select(func.count(), func.count(func.distinct(Sale.basket_id))).select_from(Sale)).one()
        duplicates = s.exec(
            select(func.count()).select_from(
                select(Sale.basket_id, Sale.sku_id).group_by(Sale.basket_id, Sale.sku_id).having(func.count() > 1).subquery()
            )
        ).one()
    assert sold_ids == sku_ids
    assert duplicates == 0
    assert rows / baskets > 1.2  # co-purchase rules produce multi-item baskets


def test_deliveries_per_supplier_and_bronze_only_serves_chaos(seeded_db):
    with session() as s:
        per_supplier = dict(s.exec(select(Delivery.supplier_id, func.count()).group_by(Delivery.supplier_id)).all())
        bronze_skus = set(s.exec(select(Delivery.sku_id).where(Delivery.supplier_id == "SUP-MCC").distinct()).all())
    assert set(per_supplier) == {"SUP-REL", "SUP-HUL", "SUP-MCC"}
    assert min(per_supplier.values()) >= 40
    assert bronze_skus <= {sku["id"] for sku in cat.SKUS if sku["zone"] == "chaos"}


def test_open_inbound_and_stock_age_follow_the_zone(seeded_db):
    with session() as s:
        inbounds = s.exec(select(Inbound)).all()
    open_by_sku = Counter(i.sku_id for i in inbounds if i.received_on is None)
    last_received: dict = {}
    for i in inbounds:
        if i.received_on is not None:
            last_received[i.sku_id] = max(last_received.get(i.sku_id, i.received_on), i.received_on)
    for sku in cat.SKUS:
        assert open_by_sku[sku["id"]] == (1 if sku["zone"] == "sweet" else 0), sku["id"]
        assert (TEST_TODAY - last_received[sku["id"]]).days == sku["age"], sku["id"]
    for i in inbounds:
        if i.received_on is None:
            assert i.expected_on == TEST_TODAY + timedelta(days=1)


def test_warehouse_stock_and_strategy(seeded_db):
    with session() as s:
        wh = s.exec(select(StockDaily).where(StockDaily.location_id == "WH-DEL", StockDaily.day == TEST_TODAY)).all()
        policies = s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).all()
    assert {r.sku_id: r.on_hand for r in wh} == {"SKU002": 640, "SKU004": 500, "SKU007": 350}
    assert len(policies) == 1
    assert policies[0].mode == "Balanced"
    assert policies[0].params == cat.DEFAULT_STRATEGY


def test_feed_files_are_written(seeded_db):
    feeds_dir = get_settings().feeds_dir
    for name in ("logistics", "commodity", "transport"):
        entries = json.loads((feeds_dir / f"{name}.json").read_text(encoding="utf-8"))
        assert entries and all(e["region_key"] in cat.REGIONS or e["region_key"] == "" for e in entries)


def test_generation_is_deterministic():
    a = build_dataset(TEST_SEED, TEST_TODAY)
    b = build_dataset(TEST_SEED, TEST_TODAY)
    assert a["sale"] == b["sale"]
    assert a["stock_daily"] == b["stock_daily"]
    assert a["delivery"] == b["delivery"]
    different = build_dataset(TEST_SEED + 1, TEST_TODAY)
    assert len(different["sale"]) != len(a["sale"])


def test_server_startup_keeps_seeded_data(seeded_db):
    before = count(Sale)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    assert count(Sale) == before


def test_validator_database_check(seeded_db):
    result = validate.check_database()
    assert result.status == validate.PASS, result.detail
