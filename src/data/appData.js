import { C } from "../theme.js";

const skuData = [
  { id:"SKU001", name:"Amul Butter 500g",    cat:"Dairy",         stock:340,  vel:42, margin:18, lead:2,  age:3,  zone:"sweet", risk:12, par:0,     safetyStock:109, reorderPoint:193, cogs:200, shelfLife:15,  velocityTrend:0.08,  historicalStockouts:0, holdingCostPct:0.02, forecast:[38,40,42,45,48,50,52,55,58,60,62,65], sales:[30,35,32,40,38,42,45,41,48,44,50,52] },
  { id:"SKU002", name:"Surf Excel 1kg",      cat:"Detergent",     stock:820,  vel:8,  margin:22, lead:7,  age:28, zone:"ghost", risk:68, par:24000, safetyStock:73,  reorderPoint:129, cogs:180, shelfLife:365, velocityTrend:-0.12, historicalStockouts:3, holdingCostPct:0.06, forecast:[10,9,8,7,8,6,7,5,6,4,5,4],           sales:[50,45,40,38,35,30,28,25,22,20,18,15] },
  { id:"SKU003", name:"Lays Classic 26g",    cat:"Snacks",        stock:150,  vel:95, margin:31, lead:12, age:1,  zone:"chaos", risk:84, par:18500, safetyStock:1482,reorderPoint:2622,cogs:12,  shelfLife:45,  velocityTrend:0.15,  historicalStockouts:5, holdingCostPct:0.04, forecast:[80,90,95,100,105,98,110,115,108,120,115,125], sales:[40,55,45,70,60,85,65,90,75,95,80,100] },
  { id:"SKU004", name:"Tata Salt 1kg",       cat:"Staples",       stock:1200, vel:12, margin:4,  lead:3,  age:45, zone:"money", risk:45, par:9200,  safetyStock:47,  reorderPoint:83,  cogs:18,  shelfLife:730, velocityTrend:-0.08, historicalStockouts:1, holdingCostPct:0.03, forecast:[14,13,12,11,12,10,11,9,10,8,9,8],     sales:[80,78,75,82,70,65,68,60,55,58,50,45] },
  { id:"SKU005", name:"Maggi 70g",           cat:"Instant Food",  stock:280,  vel:67, margin:28, lead:4,  age:5,  zone:"sweet", risk:8,  par:0,     safetyStock:348, reorderPoint:616, cogs:8,   shelfLife:180, velocityTrend:0.10,  historicalStockouts:0, holdingCostPct:0.02, forecast:[60,63,67,70,72,68,75,78,74,80,82,85], sales:[55,60,58,65,63,68,72,70,75,73,78,80] },
  { id:"SKU006", name:"Colgate Strong 200g", cat:"Personal Care", stock:95,   vel:38, margin:35, lead:9,  age:2,  zone:"chaos", risk:71, par:14800, safetyStock:444, reorderPoint:786, cogs:90,  shelfLife:730, velocityTrend:0.05,  historicalStockouts:4, holdingCostPct:0.03, forecast:[35,38,40,42,45,43,48,50,47,52,50,55],  sales:[40,35,42,38,50,45,55,60,52,65,58,70] },
  { id:"SKU007", name:"Fortune Oil 5L",      cat:"Edible Oil",    stock:640,  vel:6,  margin:9,  lead:5,  age:62, zone:"money", risk:52, par:31000, safetyStock:39,  reorderPoint:69,  cogs:750, shelfLife:180, velocityTrend:-0.15, historicalStockouts:2, holdingCostPct:0.07, forecast:[7,6,6,5,6,5,5,4,5,4,4,3],             sales:[80,75,70,78,65,60,62,55,50,52,45,40] },
  { id:"SKU008", name:"Parle-G 800g",        cat:"Biscuits",      stock:180,  vel:55, margin:19, lead:3,  age:4,  zone:"sweet", risk:15, par:0,     safetyStock:215, reorderPoint:380, cogs:55,  shelfLife:120, velocityTrend:0.06,  historicalStockouts:0, holdingCostPct:0.02, forecast:[50,52,55,58,60,62,65,68,70,72,75,78], sales:[45,48,50,52,55,58,60,62,65,68,70,72] },
  { id:"SKU009", name:"Dettol Liquid 250ml", cat:"Personal Care", stock:220,  vel:30, margin:26, lead:6,  age:8,  zone:"sweet", risk:18, par:0,     safetyStock:234, reorderPoint:414, cogs:95,  shelfLife:730, velocityTrend:0.04,  historicalStockouts:1, holdingCostPct:0.02, forecast:[28,30,32,33,35,34,37,38,36,40,39,42], sales:[25,28,27,30,29,32,34,33,36,35,38,40] },
  { id:"SKU010", name:"Haldirams Namkeen 400g",cat:"Snacks",      stock:75,   vel:48, margin:24, lead:8,  age:2,  zone:"chaos", risk:78, par:16200, safetyStock:499, reorderPoint:883, cogs:60,  shelfLife:60,  velocityTrend:0.12,  historicalStockouts:4, holdingCostPct:0.04, forecast:[42,45,48,50,52,49,55,58,54,60,57,63], sales:[35,40,38,45,42,50,48,55,52,58,55,60] },
];

// ─── MBA RULES (Market Basket Analysis) ─────────────────────────────────────
const mbaRules = [
  { antecedent:["Amul Butter 500g"],          consequent:"Parle-G 800g",        confidence:0.72, lift:2.4, support:0.18 },
  { antecedent:["Maggi 70g"],                 consequent:"Lays Classic 26g",    confidence:0.65, lift:1.9, support:0.15 },
  { antecedent:["Lays Classic 26g"],          consequent:"Haldirams Namkeen 400g", confidence:0.58, lift:2.1, support:0.12 },
  { antecedent:["Colgate Strong 200g"],       consequent:"Dettol Liquid 250ml", confidence:0.61, lift:2.8, support:0.10 },
  { antecedent:["Tata Salt 1kg"],             consequent:"Fortune Oil 5L",      confidence:0.54, lift:1.6, support:0.22 },
  { antecedent:["Amul Butter 500g","Maggi 70g"], consequent:"Parle-G 800g",     confidence:0.81, lift:3.1, support:0.08 },
  { antecedent:["Surf Excel 1kg"],            consequent:"Dettol Liquid 250ml", confidence:0.47, lift:1.4, support:0.09 },
  { antecedent:["Haldirams Namkeen 400g"],    consequent:"Maggi 70g",           confidence:0.55, lift:1.7, support:0.11 },
];

// ─── PER-SKU MONTE CARLO RESULTS ────────────────────────────────────────────
const skuMonteCarlo = {};
skuData.forEach(s => {
  const sigma = Math.round(s.vel * 0.15);
  const bins = Array.from({length:20}, (_, i) => {
    const center = i * 5 + 2.5;
    const dist = Math.abs(center - s.risk);
    const count = Math.max(1, Math.round(60 * Math.exp(-(dist*dist)/(2*18*18))));
    return { range:`${i*5}–${(i+1)*5}%`, count, highlight: i*5 >= 60 };
  });
  const daysOfCover = s.vel > 0 ? (s.stock / s.vel).toFixed(1) : "N/A";
  skuMonteCarlo[s.id] = { stockoutProb: s.risk, p95Stock: Math.max(0, Math.round(s.stock - s.vel * s.lead * 1.2)), sigma, bins, daysOfCover };
});

// ─── BULLWHIP DATA ──────────────────────────────────────────────────────────
const bullwhipData = {
  labels: ["W1","W2","W3","W4","W5","W6","W7","W8","W9","W10","W11","W12"],
  raw:      [42,58,35,72,48,65,38,80,44,62,50,70],
  smoothed: [45,50,46,55,50,56,48,60,52,58,54,60],
  reorder:  [null,null,null,55,null,null,null,60,null,null,null,60],
};

// ─── CANNIBALIZATION ────────────────────────────────────────────────────────
const cannibalization = [
  { rising:"SKU010", falling:"SKU003", rName:"Haldirams Namkeen 400g", fName:"Lays Classic 26g", category:"Snacks", correlation:-0.72 },
  { rising:"SKU009", falling:"SKU006", rName:"Dettol Liquid 250ml",   fName:"Colgate Strong 200g", category:"Personal Care", correlation:-0.45 },
];

const zoneInfo = {
  sweet: { label: "Sweet Spot",  color: C.sweet, icon: "Zap" },
  chaos: { label: "Chaos Zone",  color: C.chaos, icon: "AlertTriangle" },
  ghost: { label: "Ghost Zone",  color: C.ghost, icon: "Wind" },
  money: { label: "Money Pit",   color: C.money, icon: "TrendingDown" },
};

const monthLabels = ["Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul"];
const forecastMonths = ["Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul"];

const distributors = [
  { name:"Reliance Metro WH",  tat:"1–2 days", reliability:96, price:18.50, incentive:"2% on 500+ units", score:94, tier:"Gold",   fulfillment:0.96, defectRate:0.012, capacityLimit:1800, avgTAT:1.4, tatHistory:[1.2,1.4,1.3,1.5,1.1,1.4,1.6,1.3,1.2,1.4,1.5,1.3] },
  { name:"HUL Regional Dist",  tat:"2–3 days", reliability:89, price:17.80, incentive:"1.5% on 300+",    score:88, tier:"Silver", fulfillment:0.89, defectRate:0.034, capacityLimit:1200, avgTAT:2.6, tatHistory:[2.5,2.8,2.4,3.0,2.6,2.7,2.9,2.5,2.3,2.6,2.8,2.5] },
  { name:"Metro Cash & Carry", tat:"3–5 days", reliability:82, price:16.90, incentive:"3% on 1000+",     score:79, tier:"Bronze", fulfillment:0.82, defectRate:0.058, capacityLimit:2000, avgTAT:4.1, tatHistory:[3.8,4.2,4.5,3.9,4.0,4.3,4.6,4.1,3.7,4.2,4.4,4.0] },
];

// ─── STORE LAYOUT ──────────────────────────────────────────────────────────────
const aisles = [
  { id:"A", label:"Dairy & Eggs",  x:1, y:0, items:["Amul Butter","Mother Dairy","Nestle Curd"],  heat:0.92, connections:["B","D"], zone:"sweet" },
  { id:"B", label:"Bakery",        x:2, y:0, items:["Britannia","Parle-G","Monaco"],              heat:0.76, connections:["A","C","E"], zone:"sweet" },
  { id:"C", label:"Snacks",        x:3, y:0, items:["Lays","Kurkure","Haldirams"],                heat:0.88, connections:["B","D","F"], zone:"chaos" },
  { id:"D", label:"Beverages",     x:0, y:1, items:["Pepsi","Frooti","Real Juice"],               heat:0.72, connections:["A","E"], zone:"sweet" },
  { id:"E", label:"Instant Food",  x:1, y:1, items:["Maggi","Yippee","Top Ramen"],               heat:0.81, connections:["B","D","F"], zone:"sweet" },
  { id:"F", label:"Staples",       x:2, y:1, items:["Tata Salt","Sugar","Atta"],                  heat:0.62, connections:["C","E","G"], zone:"money" },
  { id:"G", label:"Personal Care", x:3, y:1, items:["Colgate","Pantene","Dettol"],               heat:0.54, connections:["F","H"], zone:"chaos" },
  { id:"H", label:"Home Care",     x:0, y:2, items:["Surf Excel","Vim","HIT"],                   heat:0.44, connections:["G"], zone:"ghost" },
  { id:"I", label:"Edible Oils",   x:1, y:2, items:["Fortune","Saffola","Sundrop"],              heat:0.50, connections:["F","H"], zone:"money" },
  { id:"J", label:"Frozen Foods",  x:2, y:2, items:["McCain","Amul Ice Cream","Haldirams"],      heat:0.68, connections:["A","B"], zone:"sweet" },
  { id:"K", label:"Health & OTC",  x:3, y:2, items:["Glucose-D","Ensure","Complan"],             heat:0.38, connections:["G"], zone:"ghost" },
];

// nav
const navItems = [
  { id:"landing",   icon:"Home",  label:"Home" },
  { id:"dashboard", icon:"LayoutDashboard",  label:"Monitor" },
  { id:"inventory", icon:"Package",  label:"Inventory" },
  { id:"replenish", icon:"RefreshCw",  label:"Replenish" },
  { id:"store",     icon:"Store",  label:"Store" },
  { id:"chat",      icon:"MessageSquare",  label:"Intelligence" },
  { id:"alerts",    icon:"Bell",  label:"Alerts" },
  { id:"sandbox",   icon:"Settings",  label:"What-If" },
];

// ─── SMALL COMPONENTS ──────────────────────────────────────────────────────────

export {
  skuData,
  mbaRules,
  skuMonteCarlo,
  bullwhipData,
  cannibalization,
  zoneInfo,
  monthLabels,
  forecastMonths,
  distributors,
  aisles,
  navItems,
};
