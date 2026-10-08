"""Header normalisation and per-type validation for Data Hub uploads.

Each validator takes a frame with normalised headers and returns `(clean_frame, problems)`.
A missing required column is a hard failure (`IngestError`); a bad row is dropped and reported.
"""

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sarthi.models import local_today

MAX_MESSAGES = 50
UPLOAD_TYPES = ("sku", "sales", "stock", "vendor", "forecast", "risk", "locations", "esg")
RISK_TYPES = {"WEATHER", "LOGISTICS", "COMMODITY", "TRANSPORT"}
SEVERITIES = {"HIGH", "MEDIUM", "LOW"}
LOCATION_KINDS = {"store", "warehouse"}


class IngestError(ValueError):
    """The file cannot be used at all (wrong type, unreadable, missing a required column)."""


@dataclass
class Context:
    """What already exists in the database, for referential checks."""

    sku_ids: set[str] = field(default_factory=set)
    supplier_ids: set[str] = field(default_factory=set)
    location_ids: set[str] = field(default_factory=set)


REQUIRED = {
    "sku": ["sku_id", "name", "category", "unit_cost"],
    "sales": ["date", "sku_id", "qty"],
    "stock": ["sku_id", "warehouse_id", "current_stock"],
    "vendor": ["supplier_id", "name", "tier"],
    "forecast": ["sku_id", "forecast_date", "predicted_demand"],
    "risk": ["type", "severity"],
    "locations": ["location_id", "city"],
    "esg": ["supplier_id", "carbon_kg"],
}

# alias -> canonical column. Type-specific entries win over the common ones.
COMMON_ALIASES = {
    "sku": "sku_id", "skuid": "sku_id", "item_id": "sku_id", "product_id": "sku_id",
    "quantity": "qty", "quantity_sold": "qty", "units": "qty", "units_sold": "qty",
    "lead_time_days": "lead_time", "leadtime": "lead_time",
    "lng": "long", "lon": "long", "longitude": "long", "latitude": "lat",
    "supplier": "supplier_id", "vendor_id": "supplier_id", "vendor": "supplier_id",
}
TYPE_ALIASES = {
    "sku": {"cogs": "unit_cost", "cost": "unit_cost", "sku_name": "name", "product": "name", "product_name": "name",
            "cat": "category", "shelf_life": "shelf_life_days", "unit_price": "price", "mrp": "price",
            "selling_price": "price", "holding_cost": "holding_cost_pct", "min_order_qty": "moq"},
    "sales": {"day": "date", "sale_date": "date", "transaction": "transaction_id", "txn_id": "transaction_id",
              "basket_id": "transaction_id", "order_id": "transaction_id", "unit_price": "price",
              "warehouse_id": "location_id", "store_id": "location_id"},
    "stock": {"location_id": "warehouse_id", "warehouse": "warehouse_id", "location": "warehouse_id",
              "stock": "current_stock", "on_hand": "current_stock", "in_transit": "transit", "day": "date"},
    "vendor": {"supplier_name": "name", "vendor_name": "name", "quality": "quality_score", "capacity": "capacity_limit",
               "price": "unit_price"},
    "forecast": {"date": "forecast_date", "day": "forecast_date", "forecast": "predicted_demand",
                 "demand": "predicted_demand", "predicted": "predicted_demand"},
    "risk": {"id": "signal_id", "signal_type": "type", "region_key": "region", "msg": "message",
             "description": "message", "skus": "skus_at_risk"},
    "locations": {"warehouse_id": "location_id", "id": "location_id", "location_name": "name", "type": "kind"},
    "esg": {"carbon": "carbon_kg", "co2_kg": "carbon_kg", "water": "water_l", "waste": "waste_kg"},
}


def normalise_headers(df: pd.DataFrame, upload_type: str) -> pd.DataFrame:
    """`SKU_ID`, `Unit Cost`, `SKUs_at_Risk[]` -> `sku_id`, `unit_cost`, `skus_at_risk`; then apply aliases."""
    aliases = {**COMMON_ALIASES, **TYPE_ALIASES.get(upload_type, {})}
    renamed = {}
    for col in df.columns:
        key = re.sub(r"[^a-z0-9_]", "", re.sub(r"[\s\-]+", "_", str(col).strip().lower()))
        renamed[col] = aliases.get(key, key)
    out = df.rename(columns=renamed)
    return out.loc[:, ~out.columns.duplicated()]


def _text(series: pd.Series) -> pd.Series:
    return series.astype(object).where(series.notna(), "").astype(str).str.strip()


class _Clean:
    """Small helper that tracks which rows were dropped and why."""

    def __init__(self, df: pd.DataFrame, upload_type: str):
        missing = [c for c in REQUIRED[upload_type] if c not in df.columns]
        if missing:
            found = ", ".join(map(str, df.columns)) or "none"
            raise IngestError(
                f"The {upload_type} file is missing required column(s): {', '.join(missing)}. Columns found: {found}."
            )
        self.problems: list[str] = []
        df = df.reset_index(drop=True)
        duplicate = df.astype(str).duplicated()
        if duplicate.any():
            self._note(f"{int(duplicate.sum())} exact duplicate row(s) removed")
            df = df[~duplicate]
        self.df = df.copy()
        self.df["_row"] = self.df.index + 1  # 1-based data row, as a person counts them under the header

    def _note(self, message: str) -> None:
        if len(self.problems) < MAX_MESSAGES:
            self.problems.append(message)

    def drop(self, mask: pd.Series, why: str, show: str | None = None) -> None:
        for _, row in self.df[mask].head(MAX_MESSAGES - len(self.problems)).iterrows():
            detail = f" ({show}={row[show]!r})" if show else ""
            self._note(f"row {row['_row']}: {why}{detail}")
        self.df = self.df[~mask]

    def text(self, *cols: str) -> None:
        for col in cols:
            if col in self.df.columns:
                self.df[col] = _text(self.df[col])

    def required_text(self, col: str) -> None:
        self.text(col)
        self.drop(self.df[col] == "", f"{col} is empty")

    def number(self, col: str, *, minimum: float | None = None, exclusive: bool = False) -> None:
        """Required numeric column: unparseable or out-of-range rows are dropped."""
        raw = self.df[col]
        self.df[col] = pd.to_numeric(raw, errors="coerce")
        self.drop(self.df[col].isna(), f"{col} is not a number")
        if minimum is not None:
            bad = self.df[col] <= minimum if exclusive else self.df[col] < minimum
            self.drop(bad, f"{col} must be {'greater than' if exclusive else 'at least'} {minimum:g}", show=col)

    def optional_number(self, col: str, default: float = np.nan, lo: float | None = None, hi: float | None = None) -> None:
        """Optional numeric column: missing or unparseable values become `default`; created if absent."""
        values = pd.to_numeric(self.df[col], errors="coerce") if col in self.df.columns else pd.Series(np.nan, index=self.df.index)
        values = values.fillna(default)
        if lo is not None or hi is not None:
            values = values.clip(lower=lo, upper=hi)
        self.df[col] = values

    def date(self, col: str, default=None) -> None:
        if col not in self.df.columns:
            self.df[col] = default
            return
        parsed = pd.to_datetime(self.df[col], errors="coerce")
        if default is not None:
            blank = _text(self.df[col]) == ""
            self.df[col] = parsed.dt.date.astype(object).where(~blank, default)
            self.drop(parsed.isna() & ~blank, f"{col} is not a valid date")
        else:
            self.df[col] = parsed.dt.date
            self.drop(parsed.isna(), f"{col} is not a valid date")

    def known(self, col: str, known: set[str], hint: str) -> None:
        self.drop(~self.df[col].isin(known), f"unknown {col}; {hint}", show=col)

    def keep_last(self, keys: list[str]) -> None:
        duplicate = self.df.duplicated(subset=keys, keep="last")
        if duplicate.any():
            self._note(f"{int(duplicate.sum())} earlier row(s) superseded by a later row with the same {' + '.join(keys)}")
            self.df = self.df[~duplicate]

    def finish(self) -> tuple[pd.DataFrame, list[str]]:
        return self.df.drop(columns="_row").reset_index(drop=True), self.problems


def validate_sku(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "sku")
    c.required_text("sku_id")
    c.text("name", "category")
    c.number("unit_cost", minimum=0, exclusive=True)
    for col in ("price", "lead_time", "shelf_life_days", "holding_cost_pct", "moq"):
        c.optional_number(col, lo=0)
    c.keep_last(["sku_id"])
    return c.finish()


def validate_sales(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "sales")
    c.required_text("sku_id")
    c.date("date")
    c.number("qty", minimum=0)
    c.known("sku_id", ctx.sku_ids, "upload the SKU file first")
    c.optional_number("price", lo=0)
    c.text("transaction_id", "location_id")
    if "transaction_id" not in c.df.columns:
        c.df["transaction_id"] = ""
    if "location_id" not in c.df.columns:
        c.df["location_id"] = ""
    c.df["qty"] = c.df["qty"].round().astype(int)
    return c.finish()


def validate_stock(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "stock")
    c.required_text("sku_id")
    c.required_text("warehouse_id")
    c.number("current_stock", minimum=0)
    c.known("sku_id", ctx.sku_ids, "upload the SKU file first")
    c.known("warehouse_id", ctx.location_ids, "upload the Locations file first")
    c.optional_number("transit", default=0, lo=0)
    c.optional_number("lead_time", default=3, lo=0)
    c.date("date", default=local_today())
    c.df["current_stock"] = c.df["current_stock"].round().astype(int)
    c.df["transit"] = c.df["transit"].round().astype(int)
    c.keep_last(["sku_id", "warehouse_id", "date"])
    return c.finish()


def validate_vendor(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "vendor")
    c.required_text("supplier_id")
    c.text("name", "tier", "city", "email", "sku_id")
    c.df["tier"] = c.df["tier"].str.title()
    for col in ("lead_time", "quality_score", "capacity_limit", "unit_price"):
        c.optional_number(col, lo=0)
    c.df["quality_score"] = c.df["quality_score"].clip(upper=100)
    if "sku_id" not in c.df.columns:
        c.df["sku_id"] = ""
    c.drop((c.df["sku_id"] != "") & ~c.df["sku_id"].isin(ctx.sku_ids), "unknown sku_id; upload the SKU file first", show="sku_id")
    c.keep_last(["supplier_id", "sku_id"])
    return c.finish()


def validate_forecast(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "forecast")
    c.required_text("sku_id")
    c.date("forecast_date")
    c.number("predicted_demand", minimum=0)
    c.known("sku_id", ctx.sku_ids, "upload the SKU file first")
    c.keep_last(["sku_id", "forecast_date"])
    return c.finish()


def _count(value) -> int:
    """`SKUs_at_Risk` may be a list of ids, a comma-separated string, or already a number."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return len([part for part in re.split(r"[,;|\s]+", str(value).strip("[] ")) if part])


def validate_risk(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "risk")
    c.text("type", "severity", "region", "message", "signal_id")
    c.df["type"] = c.df["type"].str.upper()
    c.df["severity"] = c.df["severity"].str.upper()
    c.drop(~c.df["type"].isin(RISK_TYPES), f"type must be one of {', '.join(sorted(RISK_TYPES))}", show="type")
    c.drop(~c.df["severity"].isin(SEVERITIES), f"severity must be one of {', '.join(sorted(SEVERITIES))}", show="severity")
    c.optional_number("lead_modifier", default=1.0, lo=0.5, hi=2.0)
    c.optional_number("demand_multiplier", default=1.0, lo=0.5, hi=2.0)
    c.df["skus_at_risk"] = c.df["skus_at_risk"].map(_count) if "skus_at_risk" in c.df.columns else 0
    for col in ("region", "message"):
        if col not in c.df.columns:
            c.df[col] = ""
    return c.finish()


def validate_locations(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "locations")
    c.required_text("location_id")
    c.text("city", "name", "kind")
    c.optional_number("lat")
    c.optional_number("long")
    c.drop(c.df["lat"].notna() & ~c.df["lat"].between(-90, 90), "lat must be between -90 and 90", show="lat")
    c.drop(c.df["long"].notna() & ~c.df["long"].between(-180, 180), "long must be between -180 and 180", show="long")
    c.optional_number("capacity", default=0, lo=0)
    kind = c.df["kind"].str.lower() if "kind" in c.df.columns else pd.Series("", index=c.df.index)
    c.df["kind"] = kind.where(kind.isin(LOCATION_KINDS), "warehouse")
    c.keep_last(["location_id"])
    return c.finish()


def validate_esg(df: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, list[str]]:
    c = _Clean(df, "esg")
    c.required_text("supplier_id")
    c.number("carbon_kg", minimum=0)
    c.known("supplier_id", ctx.supplier_ids, "upload the Vendor file first")
    c.keep_last(["supplier_id"])
    return c.finish()


VALIDATORS = {
    "sku": validate_sku, "sales": validate_sales, "stock": validate_stock, "vendor": validate_vendor,
    "forecast": validate_forecast, "risk": validate_risk, "locations": validate_locations, "esg": validate_esg,
}
