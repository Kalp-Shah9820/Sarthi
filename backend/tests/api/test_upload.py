"""Data Hub uploads."""



from sarthi.api.routers import datahub
from sarthi.config import BACKEND_ROOT
from tests.api.support import boot

# ── data hub ─────────────────────────────────────────────────────────────────

def test_upload_sample_and_reject_bad_files(client, monkeypatch):
    sample = (BACKEND_ROOT / "samples" / "sku.csv").read_bytes()
    r = client.post("/api/datahub/upload/sku", files={"file": ("sku.csv", sample, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["records"] == 10 and r.json()["dropped"] == 0 and set(r.json()) >= {"records", "dropped", "errors", "time"}
    shown = boot(client)["live"]["dataHub"]["uploads"]["sku"]
    assert shown["records"] == 10 and shown["status"] == "done" and shown["progress"] == 100

    bad = client.post("/api/datahub/upload/sku", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad.status_code == 400 and bad.json()["detail"]
    assert client.post("/api/datahub/upload/payroll", files={"file": ("sku.csv", sample, "text/csv")}).status_code == 404
    assert client.post("/api/datahub/upload/sku").status_code == 422           # no file
    monkeypatch.setattr(datahub, "MAX_BYTES", 100)
    assert client.post("/api/datahub/upload/sku", files={"file": ("sku.csv", sample, "text/csv")}).status_code == 413
