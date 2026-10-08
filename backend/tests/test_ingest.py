import io
import json
import os
from datetime import timedelta

import pandas as pd
import pytest
from sqlmodel import func, select

from sarthi import validate
from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.ingest.loader import check_file, data_health, esg_score_from_carbon, ingest, read_table
from sarthi.ingest.schemas import IngestError
from sarthi.models import Inbound, Location, RiskSignal, Sale, Sku, SkuSupplier, StockDaily, Supplier, Upload
from sarthi.seed.generator import seed_database
from sarthi.seed.samples import export_samples
from tests.conftest import TEST_SEED, TEST_TODAY

SAMPLE_TYPES = {
    "sku": "sku.csv", "locations": "locations.csv", "vendor": "vendor.xlsx", "sales": "sales.csv",
    "stock": "stock.json", "forecast": "forecast.csv", "risk": "risk.json", "esg": "esg.xlsx",
}


@pytest.fixture(scope="module")
def ingest_db(tmp_path_factory):
    """These tests change data, so they get their own freshly seeded database and sample files."""
    root = tmp_path_factory.mktemp("ingest")
    previous = os.environ["SARTHI_DB_PATH"]
    os.environ["SARTHI_DB_PATH"] = str(root / "ingest.db")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    samples = root / "samples"
    export_samples(samples)
    yield samples
    reset_engine()
    os.environ["SARTHI_DB_PATH"] = previous
    get_settings.cache_clear()


def count(model) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model)).one()


def csv_bytes(text: str) -> bytes:
    return text.strip().encode()


def test_export_writes_one_file_per_card(ingest_db):
    assert sorted(p.name for p in ingest_db.iterdir()) == sorted(SAMPLE_TYPES.values())


@pytest.mark.parametrize("upload_type", list(SAMPLE_TYPES))
def test_every_sample_file_ingests_cleanly(ingest_db, upload_type):
    name = SAMPLE_TYPES[upload_type]
    result = ingest(upload_type, name, (ingest_db / name).read_bytes())
    assert result["records"] > 0
    assert result["dropped"] == 0
    assert result["errors"] == []
    assert len(result["time"]) == 8


def test_samples_round_trip_without_changing_the_demo_state(ingest_db):
    """Uploading the exported samples must leave the seeded picture intact."""
    before_sales, before_stock = count(Sale), count(StockDaily)
    for upload_type in ("sku", "locations", "vendor", "sales", "stock", "esg"):
        name = SAMPLE_TYPES[upload_type]
        ingest(upload_type, name, (ingest_db / name).read_bytes())
    with session() as s:
        esg = dict(s.exec(select(Supplier.id, Supplier.esg_score)).all())
        defect = dict(s.exec(select(Supplier.id, Supplier.defect_rate)).all())
        open_inbound = s.exec(select(Inbound).where(Inbound.received_on.is_(None))).all()
        primaries = s.exec(select(func.count()).select_from(SkuSupplier).where(SkuSupplier.is_primary)).one()
        butter = s.get(Sku, "SKU001")
    assert (count(Sale), count(StockDaily)) == (before_sales, before_stock)
    assert esg == {"SUP-REL": 82.0, "SUP-HUL": 74.0, "SUP-MCC": 61.0}
    assert defect == pytest.approx({"SUP-REL": 0.012, "SUP-HUL": 0.034, "SUP-MCC": 0.058})
    assert len(open_inbound) == 4
    assert all(i.expected_on == TEST_TODAY + timedelta(days=1) for i in open_inbound)
    assert primaries == 10
    assert (butter.cogs, butter.price, butter.lead_time_days, butter.aisle_id) == (200.0, 243.9, 2.0, "A")


def test_reingesting_sales_adds_nothing(ingest_db):
    data = (ingest_db / "sales.csv").read_bytes()
    before = count(Sale)
    result = ingest("sales", "sales.csv", data)
    assert result["inserted"] == 0
    assert count(Sale) == before


def test_new_sales_are_appended_once(ingest_db):
    data = csv_bytes("""
Date,SKU_ID,Qty,Transaction_ID
2026-10-01,SKU001,3,NEW-1
2026-10-01,SKU005,2,NEW-1
""")
    before = count(Sale)
    assert ingest("sales", "new.csv", data)["inserted"] == 2
    assert ingest("sales", "new.csv", data)["inserted"] == 0
    assert count(Sale) == before + 2
    with session() as s:
        row = s.exec(select(Sale).where(Sale.basket_id == "NEW-1", Sale.sku_id == "SKU001")).one()
    assert (row.qty, row.location_id, row.price) == (3, "STORE-01", 243.9)  # price defaults to the SKU's price


def test_missing_required_column_is_a_hard_failure(ingest_db):
    with pytest.raises(IngestError, match="sku_id"):
        ingest("sales", "bad.csv", csv_bytes("Date,Qty\n2026-10-01,3"))
    with session() as s:
        last = s.exec(select(Upload).order_by(Upload.id.desc())).first()
    assert (last.type, last.status) == ("sales", "failed")


def test_bad_rows_are_dropped_and_reported(ingest_db):
    data = csv_bytes("""
Date,SKU_ID,Qty
2026-10-01,SKU001,4
2026-10-01,SKU999,2
2026-10-01,SKU005,-1
""")
    before = count(Sale)
    result = ingest("sales", "mixed.csv", data)
    assert (result["records"], result["dropped"]) == (1, 2)
    assert len(result["errors"]) == 2
    assert any("SKU999" in e and "SKU file first" in e for e in result["errors"])
    assert any("qty" in e for e in result["errors"])
    assert count(Sale) == before + 1


def test_unsupported_and_unusable_files_are_rejected(ingest_db):
    with pytest.raises(IngestError, match="Unsupported file type"):
        ingest("sku", "notes.txt", b"hello")
    with pytest.raises(IngestError, match="empty"):
        ingest("sku", "empty.csv", b"  ")
    with pytest.raises(IngestError, match="Unknown upload type"):
        ingest("orders", "x.csv", b"a,b\n1,2")
    with pytest.raises(IngestError, match="Could not read"):
        ingest("stock", "broken.json", b"{not json")
    with pytest.raises(IngestError, match="limit"):
        read_table("big.csv", b"x" * (20 * 1024 * 1024 + 1))


def test_card_style_headers_and_defaults_for_a_new_sku(ingest_db):
    data = csv_bytes("""
SKU_ID,Name,Category,Unit_Cost,Lead_Time
SKU011,Britannia Bread 400g,Biscuits,32,2
SKU012,Mystery Item,Unknown Category,10,
""")
    result = ingest("sku", "new_skus.csv", data)
    assert (result["records"], result["dropped"]) == (2, 0)
    with session() as s:
        bread, mystery = s.get(Sku, "SKU011"), s.get(Sku, "SKU012")
    assert (bread.cogs, bread.price, bread.lead_time_days, bread.aisle_id) == (32.0, 40.0, 2.0, "B")
    assert (bread.shelf_life_days, bread.holding_cost_pct, bread.moq) == (365, 0.03, 6)
    assert (mystery.aisle_id, mystery.lead_time_days) == ("F", 3.0)


def test_sku_ids_with_leading_zeros_survive_csv(ingest_db):
    ingest("sku", "zeros.csv", csv_bytes("sku,name,category,cost\n00123,Zero Padded,Snacks,5"))
    with session() as s:
        assert s.get(Sku, "00123") is not None


def test_invalid_sku_rows_are_reported(ingest_db):
    data = csv_bytes("""
sku_id,name,category,unit_cost
SKU020,Free Item,Snacks,0
SKU021,No Cost,Snacks,abc
,No Id,Snacks,5
SKU022,First,Snacks,5
SKU022,Second,Snacks,6
""")
    result = ingest("sku", "bad_skus.csv", data)
    assert result["records"] == 1
    assert result["dropped"] == 4
    with session() as s:
        assert s.get(Sku, "SKU022").name == "Second"  # the later row wins
        assert s.get(Sku, "SKU020") is None


def test_stock_upsert_and_transit(ingest_db):
    payload = [
        {"sku_id": "SKU003", "warehouse": "STORE-01", "stock": 500, "in_transit": 240, "lead_time": 5, "date": "2026-10-01"},
        {"sku_id": "SKU003", "warehouse": "NOWHERE", "stock": 10},
    ]
    result = ingest("stock", "stock.json", json.dumps(payload).encode())
    assert (result["records"], result["dropped"]) == (1, 1)
    assert "Locations file first" in result["errors"][0]
    with session() as s:
        row = s.get(StockDaily, (TEST_TODAY, "SKU003", "STORE-01"))
        inbound = s.exec(select(Inbound).where(Inbound.sku_id == "SKU003", Inbound.received_on.is_(None))).all()
    assert row.on_hand == 500
    assert [(i.qty, i.supplier_id, i.expected_on) for i in inbound] == [(240, "SUP-MCC", TEST_TODAY + timedelta(days=5))]

    payload[0]["in_transit"] = 0  # the shipment arrived: transit back to zero removes the open inbound
    ingest("stock", "stock.json", json.dumps(payload[:1]).encode())
    with session() as s:
        assert s.exec(select(Inbound).where(Inbound.sku_id == "SKU003", Inbound.received_on.is_(None))).all() == []


def test_vendor_upload_creates_supplier_and_price_link(ingest_db):
    frame = pd.DataFrame([{"Supplier_ID": "SUP-NEW", "Name": "New Distributor", "Tier": "silver", "Lead_Time": 2.5,
                           "Quality_Score": 97, "SKU_ID": "SKU001", "Unit_Price": 191.5}])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    assert ingest("vendor", "vendor.xlsx", buffer.getvalue())["records"] == 1
    with session() as s:
        sup = s.get(Supplier, "SUP-NEW")
        link = s.get(SkuSupplier, ("SKU001", "SUP-NEW"))
    assert (sup.tier, sup.avg_tat_days, sup.defect_rate) == ("Silver", 2.5, 0.003)
    assert (link.unit_price, link.is_primary) == (191.5, False)  # SKU001 already has a primary supplier


def test_risk_upload_replaces_earlier_uploads_only(ingest_db):
    first = [{"Type": "weather", "Severity": "high", "Region": "BAYOFBENGAL", "Message": "Cyclone", "SKUs_at_Risk[]": ["A", "B"]},
             {"Type": "RUMOUR", "Severity": "HIGH"}]
    result = ingest("risk", "risk.json", json.dumps(first).encode())
    assert (result["records"], result["dropped"]) == (1, 1)
    ingest("risk", "risk.json", json.dumps([{"Type": "TRANSPORT", "Severity": "LOW", "Lead_Modifier": 9}]).encode())
    with session() as s:
        rows = s.exec(select(RiskSignal).where(RiskSignal.source == "upload")).all()
    assert [(r.type, r.severity, r.lead_modifier, r.icon) for r in rows] == [("TRANSPORT", "LOW", 2.0, "Truck")]

    ingest("risk", "risk.json", json.dumps(first[:1]).encode())
    with session() as s:
        row = s.exec(select(RiskSignal).where(RiskSignal.source == "upload")).one()
    assert (row.region_key, row.skus_at_risk, row.msg) == ("bayOfBengal", 2, "Cyclone")


def test_locations_accept_geojson_and_xml(ingest_db):
    geo = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"Location_ID": "WH-HYD", "City": "Hyderabad", "Capacity": 1200},
         "geometry": {"type": "Point", "coordinates": [78.49, 17.39]}}]}
    ingest("locations", "sites.json", json.dumps(geo).encode())
    xml = b"<rows><row><location_id>WH-PUN</location_id><city>Pune</city><lat>18.52</lat><long>999</long></row></rows>"
    result = ingest("locations", "sites.xml", xml)
    with session() as s:
        hyd = s.get(Location, "WH-HYD")
        assert s.get(Location, "WH-PUN") is None
    assert (hyd.city, hyd.lat, hyd.lon, hyd.capacity, hyd.kind) == ("Hyderabad", 17.39, 78.49, 1200, "warehouse")
    assert result["dropped"] == 1
    assert "long must be between" in result["errors"][0]


def test_forecast_upload_writes_side_file(ingest_db):
    ingest("forecast", "forecast.csv", (ingest_db / "forecast.csv").read_bytes())
    saved = pd.read_csv(get_settings().external_forecast_file)
    assert list(saved.columns) == ["sku_id", "forecast_date", "predicted_demand"]
    assert len(saved) == 140


def test_esg_score_formula():
    assert esg_score_from_carbon(0) == 100.0
    assert esg_score_from_carbon(450) == 82.0
    assert esg_score_from_carbon(99999) == 0.0


def test_check_file_does_not_write(ingest_db):
    before = count(Sku)
    clean, problems, dropped = check_file("sku", "x.csv", csv_bytes("sku_id,name,category,unit_cost\nSKU777,Dry Run,Snacks,9"))
    assert (len(clean), problems, dropped) == (1, [], 0)
    assert count(Sku) == before


def test_data_health_tiles(ingest_db):
    health = data_health()
    assert set(health) == {"totalIngested", "freshness", "joinQuality", "alertsGenerated"}
    assert int(health["totalIngested"].replace(",", "")) > 100_000
    assert health["joinQuality"] == "100.0%"
    assert health["freshness"].endswith("%")
    assert health["alertsGenerated"] == "0"


def test_validator_ingest_check(ingest_db, monkeypatch):
    monkeypatch.setattr(validate, "samples_dir", lambda: ingest_db)
    assert validate.check_ingest().status == validate.PASS
    monkeypatch.setattr(validate, "samples_dir", lambda: ingest_db / "missing")
    assert validate.check_ingest().status == validate.WARN
