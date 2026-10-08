"""The demo catalogue, copied from the frontend's built-in data so the UI looks the same on first load.

Sources: src/data/appData.js (skuData, distributors, aisles, mbaRules, cannibalization) and
src/pages/Replenish.jsx (warehouses).
"""

STORE_ID = "STORE-01"

# sales = the mock's 12-month profile; only its shape is used (see generator.daily_mean).
SKUS = [
    {"id": "SKU001", "name": "Amul Butter 500g", "cat": "Dairy", "stock": 340, "vel": 42, "margin": 18, "lead": 2,
     "age": 3, "zone": "sweet", "cogs": 200, "shelfLife": 15, "holdingCostPct": 0.02,
     "sales": [30, 35, 32, 40, 38, 42, 45, 41, 48, 44, 50, 52]},
    {"id": "SKU002", "name": "Surf Excel 1kg", "cat": "Detergent", "stock": 820, "vel": 8, "margin": 22, "lead": 7,
     "age": 28, "zone": "ghost", "cogs": 180, "shelfLife": 365, "holdingCostPct": 0.06,
     "sales": [50, 45, 40, 38, 35, 30, 28, 25, 22, 20, 18, 15]},
    {"id": "SKU003", "name": "Lays Classic 26g", "cat": "Snacks", "stock": 150, "vel": 95, "margin": 31, "lead": 12,
     "age": 1, "zone": "chaos", "cogs": 12, "shelfLife": 45, "holdingCostPct": 0.04,
     "sales": [40, 55, 45, 70, 60, 85, 65, 90, 75, 95, 80, 100]},
    {"id": "SKU004", "name": "Tata Salt 1kg", "cat": "Staples", "stock": 1200, "vel": 12, "margin": 4, "lead": 3,
     "age": 45, "zone": "money", "cogs": 18, "shelfLife": 730, "holdingCostPct": 0.03,
     "sales": [80, 78, 75, 82, 70, 65, 68, 60, 55, 58, 50, 45]},
    {"id": "SKU005", "name": "Maggi 70g", "cat": "Instant Food", "stock": 280, "vel": 67, "margin": 28, "lead": 4,
     "age": 5, "zone": "sweet", "cogs": 8, "shelfLife": 180, "holdingCostPct": 0.02,
     "sales": [55, 60, 58, 65, 63, 68, 72, 70, 75, 73, 78, 80]},
    {"id": "SKU006", "name": "Colgate Strong 200g", "cat": "Personal Care", "stock": 95, "vel": 38, "margin": 35,
     "lead": 9, "age": 2, "zone": "chaos", "cogs": 90, "shelfLife": 730, "holdingCostPct": 0.03,
     "sales": [40, 35, 42, 38, 50, 45, 55, 60, 52, 65, 58, 70]},
    {"id": "SKU007", "name": "Fortune Oil 5L", "cat": "Edible Oil", "stock": 640, "vel": 6, "margin": 9, "lead": 5,
     "age": 62, "zone": "money", "cogs": 750, "shelfLife": 180, "holdingCostPct": 0.07,
     "sales": [80, 75, 70, 78, 65, 60, 62, 55, 50, 52, 45, 40]},
    {"id": "SKU008", "name": "Parle-G 800g", "cat": "Biscuits", "stock": 180, "vel": 55, "margin": 19, "lead": 3,
     "age": 4, "zone": "sweet", "cogs": 55, "shelfLife": 120, "holdingCostPct": 0.02,
     "sales": [45, 48, 50, 52, 55, 58, 60, 62, 65, 68, 70, 72]},
    {"id": "SKU009", "name": "Dettol Liquid 250ml", "cat": "Personal Care", "stock": 220, "vel": 30, "margin": 26,
     "lead": 6, "age": 8, "zone": "sweet", "cogs": 95, "shelfLife": 730, "holdingCostPct": 0.02,
     "sales": [25, 28, 27, 30, 29, 32, 34, 33, 36, 35, 38, 40]},
    {"id": "SKU010", "name": "Haldirams Namkeen 400g", "cat": "Snacks", "stock": 75, "vel": 48, "margin": 24,
     "lead": 8, "age": 2, "zone": "chaos", "cogs": 60, "shelfLife": 60, "holdingCostPct": 0.04,
     "sales": [35, 40, 38, 45, 42, 50, 48, 55, 52, 58, 55, 60]},
]

ZONE_TARGET = {s["id"]: s["zone"] for s in SKUS}

# lead_sigma: spread of actual vs. promised lead time (lognormal sigma) by tier.
SUPPLIERS = [
    {"id": "SUP-REL", "name": "Reliance Metro WH", "tier": "Gold", "price": 18.50, "incentive": "2% on 500+ units",
     "incentive_min_qty": 500, "incentive_pct": 2.0, "fulfillment": 0.96, "defectRate": 0.012,
     "capacityLimit": 1800, "avgTAT": 1.4, "city": "Mumbai", "lat": 19.08, "lon": 72.88, "esg_score": 82,
     "max_discount_pct": 4.0, "lead_sigma": 0.10, "email": "orders@reliance-metro.example"},
    {"id": "SUP-HUL", "name": "HUL Regional Dist", "tier": "Silver", "price": 17.80, "incentive": "1.5% on 300+",
     "incentive_min_qty": 300, "incentive_pct": 1.5, "fulfillment": 0.89, "defectRate": 0.034,
     "capacityLimit": 1200, "avgTAT": 2.6, "city": "Pune", "lat": 18.52, "lon": 73.86, "esg_score": 74,
     "max_discount_pct": 5.0, "lead_sigma": 0.22, "email": "supply@hul-regional.example"},
    {"id": "SUP-MCC", "name": "Metro Cash & Carry", "tier": "Bronze", "price": 16.90, "incentive": "3% on 1000+",
     "incentive_min_qty": 1000, "incentive_pct": 3.0, "fulfillment": 0.82, "defectRate": 0.058,
     "capacityLimit": 2000, "avgTAT": 4.1, "city": "Delhi", "lat": 28.61, "lon": 77.21, "esg_score": 61,
     "max_discount_pct": 7.0, "lead_sigma": 0.38, "email": "b2b@metro-cc.example"},
]
REFERENCE_SUPPLIER_PRICE = 18.50  # SkuSupplier.unit_price = cogs x supplier.price / this


def primary_supplier(zone: str) -> str:
    """Chaos SKUs depend on the volatile Bronze supplier; everything else on the Gold one."""
    return "SUP-MCC" if zone == "chaos" else "SUP-REL"


LOCATIONS = [
    {"id": STORE_ID, "name": "Sarthi Store Mumbai", "city": "Mumbai", "kind": "store",
     "lat": 19.08, "lon": 72.88, "capacity": 5000, "stock": {}},
    {"id": "WH-MUM", "name": "Mumbai Central", "city": "Mumbai", "kind": "warehouse",
     "lat": 19.08, "lon": 72.88, "capacity": 2000, "stock": {"SKU002": 180, "SKU004": 200, "SKU007": 90}},
    {"id": "WH-DEL", "name": "Delhi NCR Hub", "city": "Delhi", "kind": "warehouse",
     "lat": 28.61, "lon": 77.21, "capacity": 3000, "stock": {"SKU002": 640, "SKU004": 500, "SKU007": 350}},
    {"id": "WH-BLR", "name": "Bangalore South", "city": "Bangalore", "kind": "warehouse",
     "lat": 12.97, "lon": 77.59, "capacity": 1800, "stock": {"SKU002": 120, "SKU004": 300, "SKU007": 200}},
    {"id": "WH-CHN", "name": "Chennai Port", "city": "Chennai", "kind": "warehouse",
     "lat": 13.08, "lon": 80.27, "capacity": 1500, "stock": {"SKU002": 90, "SKU004": 180, "SKU007": 100}},
]

AISLES = [
    {"id": "A", "label": "Dairy & Eggs", "x": 1, "y": 0, "items": ["Amul Butter", "Mother Dairy", "Nestle Curd"],
     "heat": 0.92, "connections": ["B", "D"], "zone": "sweet"},
    {"id": "B", "label": "Bakery", "x": 2, "y": 0, "items": ["Britannia", "Parle-G", "Monaco"],
     "heat": 0.76, "connections": ["A", "C", "E"], "zone": "sweet"},
    {"id": "C", "label": "Snacks", "x": 3, "y": 0, "items": ["Lays", "Kurkure", "Haldirams"],
     "heat": 0.88, "connections": ["B", "D", "F"], "zone": "chaos"},
    {"id": "D", "label": "Beverages", "x": 0, "y": 1, "items": ["Pepsi", "Frooti", "Real Juice"],
     "heat": 0.72, "connections": ["A", "E"], "zone": "sweet"},
    {"id": "E", "label": "Instant Food", "x": 1, "y": 1, "items": ["Maggi", "Yippee", "Top Ramen"],
     "heat": 0.81, "connections": ["B", "D", "F"], "zone": "sweet"},
    {"id": "F", "label": "Staples", "x": 2, "y": 1, "items": ["Tata Salt", "Sugar", "Atta"],
     "heat": 0.62, "connections": ["C", "E", "G"], "zone": "money"},
    {"id": "G", "label": "Personal Care", "x": 3, "y": 1, "items": ["Colgate", "Pantene", "Dettol"],
     "heat": 0.54, "connections": ["F", "H"], "zone": "chaos"},
    {"id": "H", "label": "Home Care", "x": 0, "y": 2, "items": ["Surf Excel", "Vim", "HIT"],
     "heat": 0.44, "connections": ["G"], "zone": "ghost"},
    {"id": "I", "label": "Edible Oils", "x": 1, "y": 2, "items": ["Fortune", "Saffola", "Sundrop"],
     "heat": 0.50, "connections": ["F", "H"], "zone": "money"},
    {"id": "J", "label": "Frozen Foods", "x": 2, "y": 2, "items": ["McCain", "Amul Ice Cream", "Haldirams"],
     "heat": 0.68, "connections": ["A", "B"], "zone": "sweet"},
    {"id": "K", "label": "Health & OTC", "x": 3, "y": 2, "items": ["Glucose-D", "Ensure", "Complan"],
     "heat": 0.38, "connections": ["G"], "zone": "ghost"},
]

AISLE_OF_CATEGORY = {
    "Dairy": "A", "Biscuits": "B", "Snacks": "C", "Instant Food": "E", "Staples": "F",
    "Personal Care": "G", "Detergent": "H", "Edible Oil": "I",
}
DEFAULT_AISLE = "F"

# Map positions (x, y in the dashboard's 100x120 SVG) and real coordinates for risk regions.
# The first four are the ones the UI already draws and has translated labels for.
REGIONS = {
    "bayOfBengal": {"x": 75, "y": 70, "lat": 15.00, "lon": 88.00, "label": "Bay of Bengal"},
    "jnptMumbai": {"x": 18, "y": 65, "lat": 18.95, "lon": 72.95, "label": "JNPT Mumbai"},
    "delhiNcr": {"x": 35, "y": 25, "lat": 28.61, "lon": 77.21, "label": "Delhi NCR"},
    "keralaCoast": {"x": 32, "y": 92, "lat": 9.97, "lon": 76.28, "label": "Kerala Coast"},
    "puneHub": {"x": 23, "y": 69, "lat": 18.52, "lon": 73.86, "label": "Pune Hub"},
    "bengaluru": {"x": 38, "y": 88, "lat": 12.97, "lon": 77.59, "label": "Bengaluru"},
    "chennaiPort": {"x": 50, "y": 88, "lat": 13.08, "lon": 80.27, "label": "Chennai Port"},
    "kolkataPort": {"x": 70, "y": 50, "lat": 22.57, "lon": 88.36, "label": "Kolkata Port"},
}

# (antecedent SKU ids, consequent SKU id, confidence) — the mock's mbaRules, used to generate baskets.
BASKET_AFFINITY = [
    (("SKU001",), "SKU008", 0.72),
    (("SKU005",), "SKU003", 0.65),
    (("SKU003",), "SKU010", 0.58),
    (("SKU006",), "SKU009", 0.61),
    (("SKU004",), "SKU007", 0.54),
    (("SKU001", "SKU005"), "SKU008", 0.81),
    (("SKU002",), "SKU009", 0.47),
    (("SKU010",), "SKU005", 0.55),
]

# (rising SKU, falling SKU): when the falling one is out of stock, part of its demand moves to the rising one.
CANNIBALIZATION = [("SKU010", "SKU003"), ("SKU009", "SKU006")]
CANNIBAL_SHARE = 0.6

DEFAULT_STRATEGY = {"savingsPriority": 0.5, "safetyStockMultiplier": 1.0, "leadTimeBuffer": 1.2}

# Simulated non-weather feeds; they match the three non-weather signals on the dashboard.
FEEDS = {
    "logistics": [
        {"type": "LOGISTICS", "region_key": "jnptMumbai", "severity": "HIGH", "icon": "Anchor",
         "msg": "Port congestion at JNPT Mumbai is adding about 2 days to inbound shipments.",
         "msg_key": "riskMsgLogistics", "lead_modifier": 1.25, "demand_multiplier": 1.0,
         "categories": [], "active": True},
    ],
    "commodity": [
        {"type": "COMMODITY", "region_key": "", "severity": "MEDIUM", "icon": "TrendingUp",
         "msg": "Palm oil prices are rising; edible oil costs are under pressure.",
         "msg_key": "riskMsgCommodity", "lead_modifier": 1.0, "demand_multiplier": 0.97,
         "categories": ["Edible Oil"], "active": True},
    ],
    "transport": [
        {"type": "TRANSPORT", "region_key": "keralaCoast", "severity": "HIGH", "icon": "Truck",
         "msg": "Transport strike on the Kerala coast is disrupting road freight.",
         "msg_key": "riskMsgTransport", "lead_modifier": 1.3, "demand_multiplier": 1.0,
         "categories": [], "active": True},
    ],
}
