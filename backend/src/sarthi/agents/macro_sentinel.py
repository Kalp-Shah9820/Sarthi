"""Macro Sentinel (SENSE): outside risk signals -> lead-time modifier per supplier, demand multiplier per category.

Sources: real weather (Open-Meteo, free, no key), simulated logistics / commodity / transport feeds,
signals uploaded through the Data Hub, optional news headlines classified by the local model, and the
what-if scenario of the current run.
"""

import json
import time
from typing import Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import delete
from sqlmodel import select

from sarthi.agents.base import Agent
from sarthi.analytics.transfer import haversine_km
from sarthi.db import session
from sarthi.llm import prompts
from sarthi.models import Location, RiskSignal, Sku, SkuSupplier, Supplier
from sarthi.seed.catalog import REGIONS, STORE_ID

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL_S = 3 * 3600
REACH_KM = 300            # a signal affects suppliers (and the store) within this distance
REGION_SNAP_KM = 150      # a city is labelled with a known map region when this close
SIGNAL_LEAD_CAP = 2.0     # overlapping alerts must not compound into absurd lead times
DEMAND_RANGE = (0.7, 1.6)
SCENARIO_RANGE = (0.5, 3.0)
HIGH_LEAD, MEDIUM_LEAD = 1.3, 1.1
ICONS = {"WEATHER": "CloudLightning", "LOGISTICS": "Anchor", "COMMODITY": "TrendingUp", "TRANSPORT": "Truck"}
REPLACED_SOURCES = ("weather", "feed", "news", "scenario")   # rebuilt every run; uploads are kept

_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}


class NewsSignal(BaseModel):
    relevant: bool
    type: Literal["WEATHER", "LOGISTICS", "COMMODITY", "TRANSPORT"] = "LOGISTICS"
    severity: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    region_key: str = ""
    lead_modifier: float = 1.0


async def fetch_weather(lat: float, lon: float) -> dict:
    """Seven-day maxima for a place: {'rain_mm', 'wind_kmh', 'temp_c'}. Raises on network or format errors."""
    key = (round(lat, 2), round(lon, 2))
    cached = _weather_cache.get(key)
    if cached and time.monotonic() - cached[0] < WEATHER_TTL_S:
        return cached[1]
    params = {"latitude": lat, "longitude": lon, "forecast_days": 7, "timezone": "auto",
              "daily": "precipitation_sum,wind_speed_10m_max,temperature_2m_max"}
    async with httpx.AsyncClient(timeout=6) as client:
        response = await client.get(WEATHER_URL, params=params)
    response.raise_for_status()
    daily = response.json()["daily"]

    def peak(name: str) -> float:
        values = [v for v in daily[name] if v is not None]
        return float(max(values)) if values else 0.0

    maxima = {"rain_mm": peak("precipitation_sum"), "wind_kmh": peak("wind_speed_10m_max"), "temp_c": peak("temperature_2m_max")}
    _weather_cache[key] = (time.monotonic(), maxima)
    return maxima


def nearest_region(lat: float, lon: float) -> str:
    """Key of the map region closest to a point, or '' when none is near."""
    key, distance = min(((k, haversine_km(lat, lon, r["lat"], r["lon"])) for k, r in REGIONS.items()), key=lambda x: x[1])
    return key if distance <= REGION_SNAP_KM else ""


def weather_signal(city: str, lat: float, lon: float, maxima: dict) -> dict | None:
    """Turn a forecast into a signal, or None when the weather is unremarkable."""
    rain, wind, temp = maxima["rain_mm"], maxima["wind_kmh"], maxima["temp_c"]
    base = {"type": "WEATHER", "region_key": nearest_region(lat, lon), "icon": ICONS["WEATHER"], "source": "weather",
            "msg_key": None, "lat": lat, "lon": lon}
    if wind >= 62 or rain >= 115:
        return {**base, "severity": "HIGH", "lead_modifier": 1.5, "demand_multiplier": 1.25,
                "categories": ["Instant Food", "Staples"],
                "msg": f"Storm forecast near {city}: wind up to {wind:.0f} km/h, rain up to {rain:.0f} mm in a day."}
    if rain >= 65:
        return {**base, "severity": "MEDIUM", "lead_modifier": 1.2, "demand_multiplier": 1.15,
                "categories": ["Instant Food"], "msg": f"Heavy rain forecast near {city}: up to {rain:.0f} mm in a day."}
    if temp >= 42:
        return {**base, "severity": "MEDIUM", "lead_modifier": 1.0, "demand_multiplier": 1.1,
                "categories": ["Snacks"], "msg": f"Heatwave forecast near {city}: up to {temp:.0f} °C."}
    return None


def load_feeds(feeds_dir) -> list[dict]:
    """Active entries of the simulated feeds (logistics, commodity, transport)."""
    signals = []
    for name in ("logistics", "commodity", "transport"):
        path = feeds_dir / f"{name}.json"
        if not path.exists():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")):
            if entry.get("active", True):
                signals.append({
                    "type": entry.get("type", name.upper()), "severity": entry.get("severity", "LOW"),
                    "region_key": entry.get("region_key", ""), "msg": entry.get("msg", ""), "msg_key": entry.get("msg_key"),
                    "icon": entry.get("icon") or ICONS.get(entry.get("type", name.upper()), ""),
                    "lead_modifier": float(entry.get("lead_modifier", 1.0)),
                    "demand_multiplier": float(entry.get("demand_multiplier", 1.0)),
                    "categories": list(entry.get("categories", [])), "source": "feed",
                })
    return signals


def _position(signal: dict) -> tuple[float, float] | None:
    if "lat" in signal and "lon" in signal:
        return signal["lat"], signal["lon"]
    region = REGIONS.get(signal.get("region_key", ""))
    return (region["lat"], region["lon"]) if region else None


def fuse(signals: list[dict], suppliers: list[dict], store: dict, skus: list[dict]) -> tuple[dict, dict]:
    """Combine signals into (lead_modifier per supplier, demand_multiplier per category).

    A located signal slows a supplier when it is within reach of that supplier or of the store (the last leg
    of every delivery). Each signal dict also gets `skus_at_risk`: SKUs whose primary supplier it slows or
    whose category it names.
    """
    lead = {s["id"]: 1.0 for s in suppliers}
    categories = sorted({k["category"] for k in skus})
    demand = dict.fromkeys(categories, 1.0)
    for signal in signals:
        hit_suppliers: set[str] = set()
        position = _position(signal)
        if position and signal["lead_modifier"] != 1.0:
            near_store = haversine_km(*position, store["lat"], store["lon"]) <= REACH_KM
            for s in suppliers:
                if near_store or haversine_km(*position, s["lat"], s["lon"]) <= REACH_KM:
                    lead[s["id"]] *= signal["lead_modifier"]
                    hit_suppliers.add(s["id"])
        named = set(signal["categories"]) if signal["categories"] else (set(categories) if signal["demand_multiplier"] != 1.0 else set())
        for category in named & set(categories):
            demand[category] *= signal["demand_multiplier"]
        signal["skus_at_risk"] = sum(1 for k in skus if k["primary_supplier"] in hit_suppliers or k["category"] in named)
    lead = {k: round(min(SIGNAL_LEAD_CAP, v), 3) for k, v in lead.items()}
    demand = {k: round(min(DEMAND_RANGE[1], max(DEMAND_RANGE[0], v)), 3) for k, v in demand.items()}
    return lead, demand


class MacroSentinel(Agent):
    key, phase = "macroSentinel", "sense"

    def _master_data(self) -> tuple[list[dict], dict, list[dict], list[dict], list[dict]]:
        with session() as s:
            suppliers = [{"id": x.id, "name": x.name, "city": x.city, "lat": x.lat, "lon": x.lon} for x in s.exec(select(Supplier)).all()]
            locations = [{"id": x.id, "city": x.city, "lat": x.lat, "lon": x.lon} for x in s.exec(select(Location)).all()]
            primary = dict(s.exec(select(SkuSupplier.sku_id, SkuSupplier.supplier_id).where(SkuSupplier.is_primary)).all())
            skus = [{"id": k.id, "category": k.category, "primary_supplier": primary.get(k.id)} for k in s.exec(select(Sku)).all()]
            uploaded = [
                {"type": r.type, "severity": r.severity, "region_key": r.region_key, "msg": r.msg, "msg_key": r.msg_key,
                 "icon": r.icon, "lead_modifier": r.lead_modifier, "demand_multiplier": r.demand_multiplier,
                 "categories": list(r.categories or []), "source": "upload", "id": r.id}
                for r in s.exec(select(RiskSignal).where(RiskSignal.source == "upload", RiskSignal.active)).all()
            ]
        store = next((x for x in locations if x["id"] == STORE_ID), locations[0] if locations else {"lat": 0.0, "lon": 0.0})
        return suppliers, store, locations, skus, uploaded

    async def _weather(self, suppliers: list[dict], locations: list[dict]) -> list[dict]:
        places = {}
        for place in suppliers + locations:
            if place.get("city") and (place["lat"], place["lon"]) != (0.0, 0.0):
                places.setdefault(place["city"], (place["lat"], place["lon"]))
        signals = []
        for city, (lat, lon) in places.items():
            try:
                signal = weather_signal(city, lat, lon, await fetch_weather(lat, lon))
            except Exception as exc:  # noqa: BLE001  no internet, timeout, changed API: carry on without weather
                self.bb.error(self.key, exc, phase=self.phase)
                break            # one failure means the service is unreachable; do not wait on every city
            if signal:
                signals.append(signal)
        return signals

    async def _news(self, run_id: int) -> list[dict]:
        path = self.settings.feeds_dir / "news.json"
        if not path.exists():
            return []
        signals = []
        for headline in json.loads(path.read_text(encoding="utf-8"))[:5]:
            parsed = await self.llm.json(NewsSignal, prompts.CLASSIFY_NEWS + f" Known regions: {', '.join(REGIONS)}.",
                                         str(headline), fallback=lambda: NewsSignal(relevant=False), run_id=run_id, task="news")
            if parsed.relevant:
                signals.append({
                    "type": parsed.type, "severity": parsed.severity, "msg": str(headline), "msg_key": None,
                    "region_key": parsed.region_key if parsed.region_key in REGIONS else "", "icon": ICONS[parsed.type],
                    "lead_modifier": min(1.5, max(1.0, parsed.lead_modifier)), "demand_multiplier": 1.0,
                    "categories": [], "source": "news",
                })
        return signals

    def _persist(self, fresh: list[dict], uploaded: list[dict]) -> None:
        with session() as s:
            s.connection().execute(delete(RiskSignal).where(RiskSignal.source.in_(REPLACED_SOURCES)))
            for signal in fresh:
                s.add(RiskSignal(
                    type=signal["type"], severity=signal["severity"], region_key=signal["region_key"], msg=signal["msg"],
                    msg_key=signal["msg_key"], icon=signal["icon"], lead_modifier=signal["lead_modifier"],
                    demand_multiplier=signal["demand_multiplier"], categories=signal["categories"],
                    skus_at_risk=signal["skus_at_risk"], source=signal["source"], active=True,
                ))
            for signal in uploaded:
                row = s.get(RiskSignal, signal["id"])
                if row:
                    row.skus_at_risk = signal["skus_at_risk"]
                    s.add(row)
            s.commit()

    async def work(self, state: dict) -> dict:
        suppliers, store, locations, skus, uploaded = self._master_data()
        fresh = load_feeds(self.settings.feeds_dir)
        if self.settings.weather_enabled:
            fresh += await self._weather(suppliers, locations)
        fresh += await self._news(self.bb.run_id)

        lead, demand = fuse(fresh + uploaded, suppliers, store, skus)
        if not state.get("dry_run"):
            self._persist(fresh, uploaded)

        # The what-if scenario is applied on top of real signals and is never stored.
        scenario = state.get("scenario") or {}
        lead_mult = min(SCENARIO_RANGE[1], max(SCENARIO_RANGE[0], float(scenario.get("lead_mult", 1.0))))
        demand_mult = min(SCENARIO_RANGE[1], max(SCENARIO_RANGE[0], float(scenario.get("demand_mult", 1.0))))
        lead = {k: round(v * lead_mult, 3) for k, v in lead.items()}
        demand = {k: round(v * demand_mult, 3) for k, v in demand.items()}

        worst = max(lead.values(), default=1.0)
        level, tone = ("HIGH", "chaos") if worst >= HIGH_LEAD else ("MEDIUM", "money") if worst >= MEDIUM_LEAD else ("LOW", "sweet")
        self.bb.put(self.key, self.phase, "lead_time_risk", level, tone=tone)
        for signal in fresh + uploaded:
            if signal["severity"] == "HIGH":
                self.bb.act(self.key, self.phase, signal["msg"] or signal["type"].title(),
                            result=f"{signal['skus_at_risk']} SKUs exposed", type=signal["type"], lead_modifier=signal["lead_modifier"])
        self.bb.metric(self.key, key="macro", phase=self.phase, lead_modifier=lead, demand_multiplier=demand,
                       signals=len(fresh) + len(uploaded))
        return {"lead_modifier": lead, "demand_multiplier": demand, "signal_count": len(fresh) + len(uploaded)}
