# MK11 — Frontend wiring

**Goal:** every screen reads from and writes to the backend, with no change to layout, styling, text, or interactions.

## Rules for this milestone

1. Only data sources change. No JSX structure, style object, class name, i18n string or route is edited.
2. Every replaced value keeps today's hardcoded value as its fallback (`live.x ?? existingConstant`). With the backend stopped, the app must render exactly as it does now.
3. No new npm dependency.

## How it works

`src/data/appData.js` exports arrays and objects that pages import directly. Instead of rewriting every page to fetch, the app **fills those same exported objects in place** before React renders, then re-fills them after any action and bumps a counter in context so pages re-render. Pages that only read `skuData`, `distributors`, `mbaRules`, `aisles`, `skuMonteCarlo`, `monthLabels` or `cannibalization` need no edit at all.

Data that currently lives as constants inside page files (alerts, risk signals, drift, warehouses, …) is read from a new exported object `live`, falling back to the page's own constant.

## Step 1 — Dev proxy: `vite.config.js`

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
```

## Step 2 — `src/data/appData.js`

Add one export (and include it in the export list at the bottom):

```js
// Filled by src/api/hydrate.js when the backend is reachable; pages fall back to their own constants.
const live = { online: false };
```

## Step 3 — New file `src/api/client.js`

```js
const BASE = "/api";

async function request(method, path, body, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;                       // backend down or slow: callers fall back to local behaviour
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: (path, timeoutMs) => request("GET", path, undefined, timeoutMs),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),

  // multipart upload with real progress (fetch cannot report upload progress)
  upload(path, file, onProgress) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.open("POST", BASE + path);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress((e.loaded / e.total) * 100); };
      xhr.onload = () => {
        try { resolve(xhr.status < 300 ? JSON.parse(xhr.responseText) : null); } catch { resolve(null); }
      };
      xhr.onerror = () => resolve(null);
      xhr.send(form);
    });
  },

  // server-sent events; returns a function that closes the stream
  stream(path, onMessage, onDone) {
    const es = new EventSource(BASE + path);
    const close = () => { es.close(); if (onDone) onDone(); };
    es.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* ignore malformed line */ } };
    es.addEventListener("done", close);
    es.onerror = close;
    return () => es.close();
  },
};

export const fill = (template, params = {}) =>
  Object.entries(params).reduce((s, [k, v]) => s.replace(`{${k}}`, v), template);
```

## Step 4 — New file `src/api/hydrate.js`

```js
import * as D from "../data/appData.js";
import { rawI18n } from "../data/i18n.js";
import { api } from "./client.js";

const replaceArray = (target, src) => { if (Array.isArray(src)) target.splice(0, target.length, ...src); };
const replaceObject = (target, src) => {
  if (!src || typeof src !== "object") return;
  Object.keys(target).forEach((k) => delete target[k]);
  Object.assign(target, src);
};

export async function hydrate() {
  const lang = localStorage.getItem("sarthi_lang") || "EN";
  const b = await api.get(`/bootstrap?lang=${lang}`, 4000);
  if (!b || !b.skuData) { D.live.online = false; return false; }

  replaceArray(D.skuData, b.skuData);
  replaceArray(D.mbaRules, b.mbaRules);
  replaceArray(D.cannibalization, b.cannibalization);
  replaceArray(D.distributors, b.distributors);
  replaceArray(D.monthLabels, b.monthLabels);
  replaceArray(D.forecastMonths, b.forecastMonths);
  replaceObject(D.skuMonteCarlo, b.skuMonteCarlo);
  replaceObject(D.bullwhipData, b.bullwhipData);
  // aisles: keep x/y/label from the static layout if the backend omits them
  if (Array.isArray(b.aisles)) replaceArray(D.aisles, b.aisles);

  replaceObject(D.live, { ...b.live, online: true });

  // names for SKUs / regions the static translations do not contain
  for (const [key, label] of Object.entries(b.labels || {})) {
    if (!(key in rawI18n.EN)) rawI18n.EN[key] = label;
  }
  return true;
}
```

## Step 5 — `main.jsx`

Data must be in place before any page module is evaluated, so the app is imported after hydration:

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { hydrate } from './src/api/hydrate.js'

async function start() {
  await hydrate()                                   // resolves false (and changes nothing) if the backend is down
  const [{ default: App }, { SarthiProvider }] = await Promise.all([
    import('./SarthiApp.jsx'),
    import('./src/context/SarthiContext.jsx'),
  ])
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <SarthiProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </SarthiProvider>
    </React.StrictMode>,
  )
}

start()
```

## Step 6 — `src/context/SarthiContext.jsx`

```jsx
import { createContext, useContext, useState, useCallback } from "react";
import { live } from "../data/appData.js";
import { api } from "../api/client.js";
import { hydrate } from "../api/hydrate.js";

const SarthiContext = createContext();

const DEFAULT_STRATEGY = {
  mode: "Balanced", savingsPriority: 0.5, safetyStockMultiplier: 1.0,
  leadTimeBuffer: 1.2, lastUpdate: "SYSTEM_INITIALIZED",
};

export function SarthiProvider({ children }) {
  const [lang, setLangState] = useState(localStorage.getItem("sarthi_lang") || "EN");
  const [strategy, setStrategy] = useState(live.strategy ?? DEFAULT_STRATEGY);
  const [dataVersion, setDataVersion] = useState(0);

  // re-fill appData from the backend and re-render every page
  const refreshData = useCallback(async () => {
    const ok = await hydrate();
    if (ok && live.strategy) setStrategy(live.strategy);
    setDataVersion((v) => v + 1);
    return ok;
  }, []);

  const setLang = (newLang) => {
    setLangState(newLang);
    localStorage.setItem("sarthi_lang", newLang);
    refreshData();                                   // alert/debate text is language-specific
  };

  // serverStrategy: object already applied by the backend (e.g. from /chat) -> just adopt it
  const updateStrategy = (newStrategy, serverStrategy = null) => {
    if (serverStrategy) { setStrategy(serverStrategy); return; }
    setStrategy((prev) => ({ ...prev, ...newStrategy, lastUpdate: new Date().toLocaleTimeString() }));
    api.put("/strategy", newStrategy).then((r) => { if (r) { setStrategy(r); setTimeout(refreshData, 4000); } });
  };

  return (
    <SarthiContext.Provider value={{ strategy, updateStrategy, lang, setLang, dataVersion, refreshData }}>
      {children}
    </SarthiContext.Provider>
  );
}

export function useSarthi() {
  return useContext(SarthiContext);
}
```

Every page already calls `useSarthi()`, so a `dataVersion` change re-renders all of them with the refreshed arrays.

## Step 7 — Page edits

Each item names the existing expression and its replacement. Add `live` to the page's `appData.js` import and `api` (and `fill` where noted) from `../api/client.js` as needed.

### `src/components/PipelineVisualizer.jsx`
- Badge text `23 {i18n[lang].signs}` → `{live.pipeline?.signals ?? 23} {i18n[lang].signs}`.

### `src/pages/Dashboard.jsx`
- In `RiskSignals`: iterate `(live.riskSignals ?? riskSignals)`.
- Message line `{i18n[lang][s.msgKey]}` → `{s.msg ?? i18n[lang][s.msgKey]}`.
- Charts and zone cards already read `skuData`/`monthLabels`; no edit.

### `src/components/GeospatialMap.jsx`
- `const risks = [ … ]` → `const risks = live.mapRisks ?? [ …existing array… ];`

### `src/pages/Inventory.jsx`
- `driftData.map(...)` → `(live.drift?.data ?? driftData).map(...)`.
- Row label `{i18n[lang][sku.id.toLowerCase()]}` stays (new SKUs get labels from hydration).

### `src/pages/SKUDetail.jsx`
- ESG tile value `` `${Math.round(85 + Math.random() * 10)}%` `` → `` `${sku.esg ?? Math.round(85 + Math.random() * 10)}%` ``.
- Before `return`, merge live numbers into the local options and render `options` instead of `esgOptions`:
  ```js
  const liveEsg = live.esg?.bySku?.[sku.id]?.options ?? live.esg?.options;
  const options = esgOptions.map((o, i) => ({ ...o, ...(liveEsg?.[i] ? { tat: liveEsg[i].tat, co2Key: liveEsg[i].co2Key, co2Pct: liveEsg[i].co2Pct } : {}) }));
  ```

### `src/pages/Replenish.jsx`
- **Distributor chart** (`distChart`): replace the body with
  ```js
  const distChart = (selectedSku && live.distributorScores?.[selectedSku.id]) || distributors.map(d => { …existing code… });
  ```
- **Recommended units** (three places: `handleProceed`, confirm modal, draft popup): `Math.round(sku.vel * sku.lead * 1.3)` → `(sku.recommendedQty ?? Math.round(sku.vel * sku.lead * 1.3))`.
- **Default price** (same three places): `sku.cogs` as the default → `(live.replenishment?.[sku.id]?.price ?? sku.cogs)`. The `COGS: ₹{sku.cogs}` label is unchanged.
- **Price history**: `const priceHistory = live.replenishment?.[sku.id]?.priceHistory ?? getPriceHistory(sku);`
- **ESG cards**: build `const esgCards = esgOptions.map((o, i) => ({ ...o, ...(live.esg?.options?.[i] ?? {}) }));` and map over `esgCards`. Change the two literals tied to the recommended card: initial state `useState(2)` → `useState(live.esg?.recommendedIndex ?? 2)` and badge condition `i===2` → `i === (live.esg?.recommendedIndex ?? 2)`.
- **Warehouses**: `warehouses.map(...)` → `(live.warehouses ?? warehouses).map(...)`; inside it use the same list for lookups (`const whList = live.warehouses ?? warehouses;` at the top of the component).
- **Transfer button**: replace the `wh.id === "WH-DEL"` block's condition and click handler:
  ```js
  const suggestion = live.transfers ? live.transfers.find(t => t.fromId === wh.id) : (wh.id === "WH-DEL" ? { toId: "WH-MUM", toCity: "Mumbai", skuId: "SKU002", units: 200 } : null);
  // render the button only when `suggestion` exists
  onClick={() => setTransferModal({ from: wh, to: whList.find(w => w.id === suggestion.toId), skuId: suggestion.skuId, units: suggestion.units })}
  // label: i18n[lang].transferOverstock.replace("{city}", suggestion.toCity)
  ```
- **Place order** (`handleProceed`): make it `async`; before `downloadReceipt` call
  ```js
  const r = await api.post("/orders", { skuId: sku.id, distributor: dist.name, units, price, esgIndex: selectedEsg });
  ```
  and pass `r?.orderId` as a fifth argument to `downloadReceipt`, whose first lines become `const orderId = serverOrderId ?? \`PO-${Date.now().toString(36).toUpperCase()}\`;`. After `setOrderSuccess(...)` call `refreshData()` (take it from `useSarthi()`).
- **Confirm transfer** button: `async () => { const t = transferModal; const r = await api.post("/transfers", { skuId: t.skuId, fromId: t.from.id, toId: t.to.id, units: t.units }); setTransferSuccess({ ...t, id: r?.transferId }); setTransferModal(null); refreshData(); }` and in the success text use `transferSuccess.id ?? ("TRF-" + …existing expression…)`.
- **Campaigns**: merge live numbers `const camps = campaignTemplates.map(c => ({ ...c, ...(live.campaigns?.find(x => x.type === c.type) ?? {}) }));` then map over `camps`; `estImpact` cell → `{camp.estImpact ?? i18n[lang][camp.estKey]}`; initial `campaignLaunched` → indexes of camps whose `status === "live"`; launch click → `() => { setCampaignLaunched(prev => [...prev, i]); api.post("/campaigns", { type: camp.type }); }`.

### `src/pages/Alerts.jsx`
- `const all = [ …7 hardcoded… ]` → `const all = (live.alerts ?? [ …7 hardcoded… ])` (keep the `.filter` on `dismissed`).
- Initial approved state: `useState(() => (live.alerts ?? []).filter(a => a.status === "approved").map(a => a.id))`.
- Approve click: `() => { setApproved(prev => [...prev, alert.id]); api.post(\`/alerts/${alert.id}/approve\`, { feedback: feedback[alert.id] }).then(r => { if (r) refreshData(); }); }`.
- Dismiss click: `() => { setDismissed(prev => [...prev, alert.id]); api.post(\`/alerts/${alert.id}/dismiss\`, { feedback: feedback[alert.id] }); }`.
- TXID text `S-{alert.id}B47X` → `{alert.txid ?? \`S-${alert.id}B47X\`}`.

### `src/pages/Sandbox.jsx`
- Add state `const [sim, setSim] = useState(null);` and `const [liveDebate, setLiveDebate] = useState(null);`.
- Debounced simulation and debate stream:
  ```js
  useEffect(() => {
    const t = setTimeout(async () => {
      setSim(await api.post("/sandbox/simulate", { lead, demand, stock, margin }));
    }, 250);
    return () => clearTimeout(t);
  }, [lead, demand, stock, margin]);

  useEffect(() => {
    if (!live.online) return;
    let close = null;
    const t = setTimeout(() => {
      const lines = [];
      close = api.stream(`/sandbox/debate/stream?lead=${lead}&demand=${demand}&stock=${stock}&margin=${margin}&lang=${lang}`,
        (m) => { lines.push(m); setLiveDebate([...lines]); setDebateIdx(lines.length - 1); });
    }, 1200);
    return () => { clearTimeout(t); if (close) close(); };
  }, [lead, demand, stock, margin, lang]);
  ```
- Rename the existing local results to fallbacks and prefer the server's: `const risk = sim?.risk ?? localRisk; const par = sim?.par ?? localPar; const sensitivityData = sim?.sensitivity ?? localSensitivity;`.
- Debate list: `const messages = liveDebate ? liveDebate.map(m => ({ agent: i18n[lang][m.agentKey], color: C[m.tone] ?? C.ghost, msg: m.msg })) : debateMessages(lang);`
- The existing 3-second cycling `useEffect` gets a guard as its first line: `if (liveDebate) return;` (live lines appear as they arrive; the canned loop runs only offline). Add `liveDebate` to its dependency list.

### `src/pages/Intelligence.jsx`
- Move the body of the existing `setTimeout` callback in `ask()` into a function `localAnswer(query)` that returns the response string (unchanged logic).
- `ask` becomes:
  ```js
  const ask = async () => {
    if (!query.trim()) return;
    const text = query;
    setChat(prev => [...prev, { role: "user", text }]);
    setQuery("");
    const r = await api.post("/chat", { text, lang });
    let response;
    if (r) {
      response = r.key ? fill(i18n[lang][r.key], r.params) : r.text;
      if (r.strategy) updateStrategy(null, r.strategy);
      if (r.refresh) setTimeout(refreshData, 4000);
    } else {
      response = localAnswer(text);
    }
    setChat(prev => [...prev, { role: "bot", text: response }]);
  };
  ```
- Audit trail: iterate `(live.auditTrail ?? auditTrailData)`; inside, `const finalAction = entry.text ?? …existing reduce…;` and `const result = entry.result ?? …existing lookup…;`.
- Shared context: iterate `(live.sharedContext ?? sharedContext)`; colour `ctx.color` → `(ctx.color ?? C[ctx.tone] ?? C.ghost)` in the three places it is used.

### `src/pages/CommandCenter.jsx`
- Delete the module-level `richSkuData` and compute it inside the component so it refreshes:
  ```js
  const { lang, strategy, dataVersion } = useSarthi();
  const richSkuData = useMemo(() => skuData.map((s, i) => ({
    ...s,
    daysStock: s.daysStock ?? Math.floor(Math.random() * 45) + 2,
    esg: s.esg ?? Math.floor(Math.random() * 40) + 55,
    co2: s.co2 ?? (Math.random() * 1.5 + 0.2).toFixed(2),
    stockoutProb: s.stockoutProb ?? Math.floor(Math.random() * 60) + 5,
    lastReorder: s.lastReorder ?? "2026-03-01",
    decisionStatus: s.decisionStatus ?? (i % 5 === 0 ? "critical" : i % 3 === 0 ? "pending" : "auto"),
    supplier: s.supplier ?? (distributors[i % distributors.length]?.name || "Local Vendor"),
    tier: s.tier ?? (distributors[i % distributors.length]?.tier || "Bronze"),
  })), [dataVersion]);
  ```
  (The old random `margin` override is dropped; the real `margin` from `skuData` is used. It is not displayed in this table.)
- Add `richSkuData` to the dependency list of the `filteredData` `useMemo`.
- Zone modal figures: `avgStockoutRisk` value → `live.zoneStats?.[selectedZoneForExplanation.id]?.avgRisk ?? (…existing ternary…)`; `decisionConfidence` value → `live.zoneStats?.[selectedZoneForExplanation.id]?.confidence ?? (…existing ternary…)`.

### `src/pages/DataHub.jsx`
- Initial uploads: `useState(live.dataHub?.uploads ?? {})`.
- Keep the selected file: in `handleFileChange` call `startUpload(activeUploadId, e.target.files[0])`.
- `startUpload(id, file)`: if `!live.online` call the existing `simulateUpload(id)`; otherwise
  ```js
  setUploads(prev => ({ ...prev, [id]: { progress: 0, status: "uploading" } }));
  const r = await api.upload(`/datahub/upload/${id}`, file, (p) => setUploads(prev => ({ ...prev, [id]: { ...prev[id], progress: Math.min(p, 95) } })));
  setUploads(prev => r
    ? ({ ...prev, [id]: { progress: 100, status: "done", records: r.records, time: r.time } })
    : (({ [id]: _failed, ...rest }) => rest)(prev));      // failed upload: card returns to its empty state
  ```
- `runSync`: `setSyncing(true); await api.post("/runs"); setTimeout(async () => { await refreshData(); setSyncing(false); }, 8000);` (offline: keep the existing 3-second timeout).
- Metric tiles: each `value` → `live.dataHub?.metrics?.<name> ?? "<existing literal>"` for `totalIngested`, `freshness`, `joinQuality`, `alertsGenerated`.

### `src/pages/StoreViz.jsx`
- Co-purchase chart data: `data={(live.coPurchasePairs ?? null)?.map(p => ({ pair: \`${i18n[lang][p.from]} → ${i18n[lang][p.to]}\`, strength: p.strength })) ?? [ …existing 7 rows… ]}`.
- Aisles already come from `appData.js`; no edit.

### `src/components/VoiceCommand.jsx`
- Rename the body of `handleFinalTranscript` (keyword parsing) to `localParse(text)`; new function:
  ```js
  const handleFinalTranscript = async (text) => {
    const cmd = await api.post("/voice/intent", { text, lang });
    if (cmd) onCommand(cmd); else localParse(text);
    setTimeout(() => setTranscript(""), 3000);
  };
  ```
  (Remove the duplicate `setTimeout` from `localParse`.)

### `SarthiApp.jsx`, `src/App.jsx`, `Landing.jsx`, `LanguageSelector.jsx`, `ui.jsx`, `theme.js`, `i18n.js`
No changes.

## Verify

Two terminals:

```powershell
# terminal 1
cd "c:\Users\Kalp Shah\Desktop\Sarthi\backend"; uv run sarthi serve
# terminal 2
cd "c:\Users\Kalp Shah\Desktop\Sarthi"; npm run dev
```

Open `http://localhost:5173` and walk every screen:

| Screen | Check |
|---|---|
| Monitor | Chart and zone cards show computed values; risk signals list matches `live.riskSignals`; map markers pulse |
| Inventory | Scatter, drift time-lapse (Play works), table; clicking a row opens the SKU page |
| SKU detail | Histogram shape differs per SKU; ESG tile is stable across re-renders (no longer random) |
| Replenish | Expand a critical SKU → draft → confirm → Proceed: receipt opens with a `PO-…` id that also exists in the `PurchaseOrder` table and `backend/outbox/` |
| Replenish | Transfer button → confirm: warehouse quantities change after refresh; Launch campaign shows "live" and stays live after reload |
| Alerts | Approve shows the green state with a real TXID; reload keeps it approved; Dismiss removes the card permanently |
| What-If | Moving sliders updates risk/PaR/sensitivity; the debate panel fills with new lines about real SKUs about a second after you stop |
| Intelligence | "critical skus" answers from live data; "prioritize cash flow" flips the sidebar strategy label and, after a few seconds, safety stocks on the Inventory page; audit trail and shared context show recent entries |
| Command | Days of stock, status, ESG are stable across reloads; zone filter and sort work |
| Data Hub | Upload the 8 files from `backend/samples/`; real record counts appear; Sync enables, runs, and the Monitor numbers refresh |
| Voice | "show risk for Colgate" opens SKU006; "switch to Hindi" changes language |
| Language | Switch to Hindi: alert messages arrive in Hindi; switch to Tamil: static UI is Tamil, alert text English |

**Before starting this milestone:** the folder is not a git repository yet. Run `git init; git add -A; git commit -m "frontend baseline"` in the repo root and take a screenshot of each screen, so there is a baseline to compare against and to roll back to.

**Offline check:** stop the backend, reload the page. Every screen must look and behave exactly as in the baseline screenshots. `npm run build` completes without errors.

## Done when

Both checklists pass and `git diff --stat` against the baseline commit shows changes only in: `vite.config.js`, `main.jsx`, `src/data/appData.js`, `src/context/SarthiContext.jsx`, `src/api/*` (new), the two components and eight pages listed above.

## Implementation notes (as built, 2026-10-09)

The git baseline already existed (commits "plan 1" to "plan 10"), so no `git init` was needed. Verified with headless Chrome driven over its debugging port: every screen captured before the change, after it with no backend, and after it against a private backend on a copy of the database; then every action clicked and checked on the backend. Deviations from the text above:

- **`vite.config.js`** reads the backend address from `SARTHI_API_TARGET` (default `http://127.0.0.1:8000`) and applies the same proxy to `vite preview`.
- **`hydrate()`** returns `false` (backend unreachable), `"same"` or `"updated"`. The context bumps `dataVersion` only on `"updated"`, so polling does not re-render (and re-animate) every chart when nothing changed.
- **`refreshAfterRun()`** (new, in the context) replaces the fixed `setTimeout(refreshData, 4000)`: after a strategy change or a voice adjustment it refreshes every 3 s until the run those start has finished (up to a minute). A first run after a server start takes longer than 4 s.
- **Opening the app before the backend is ready**: the context looks again every 15 s while offline, so the screens switch to live data once the first run finishes, without a reload.
- **`fill()`** replaces every occurrence of a placeholder and does not interpret `$` in values.
- **Intelligence**: offline, the built-in answer still appears after the original 600 ms. A chat reply that changed the strategy goes through `updateStrategy(null, r.strategy)`; one that only changed data (`refresh`) calls `refreshData()`.
- **Data Hub Sync** waits for the run it started by listening to `/api/runs/{id}/stream`, instead of a fixed 8 s.
- **Voice**: after a "PROCURE" command (safety stock raised) the app calls `refreshAfterRun()`.
- **Replenish "critical" list** filters on `stockoutProb ?? risk`. The backend's `risk` also covers overstock, and with it the list offered to *order more* of overstocked products. For the same reason `skuData.stockoutProb` and `skuMonteCarlo.stockoutProb` are now the chance of running out (they equal `risk` except for ghost and money-pit products).
- **Replenish transfer button**: shown for the first live suggestion leaving each warehouse, and only if its destination is in the list.
- **Command Center**: `lastReorder` keeps its original random placeholder offline (the plan had a constant).
- **Dashboard risk messages** show the backend's sentence (`s.msg`), as planned; the static translations for those keys contain invented figures ("+2.3 day avg delay") that would contradict it.
- **`sharedContext[*].value`** is always a string on the backend, because the page calls `.toLowerCase()` on it.
- **Known, left as it was**: the receipt window is opened with `noopener`, for which browsers return no window handle, so the printable receipt was never written in the original app either. The order itself is placed and shown.
- **Validator**: `check_frontend_wiring` reads the frontend source and fails if a page calls an endpoint the backend does not serve, or reads a `live` field `/api/bootstrap` does not send.

Offline result: 9 of 12 captured screens have text identical to the baseline; the other 3 (two SKU pages, Command) differ only in numbers the original code draws with `Math.random()`.

## Follow-up: closing the loop (2026-10-10)

After the wiring, every click reached the database but the screens did not change, because they show the latest pipeline run and nothing the manager clicked started a new one. Verified by `tests/api/test_live_loop.py` (8 tests) and by clicking through the real pages in headless Chrome.

- **Actions re-run the agents.** An order, a transfer, or an approval that moves stock schedules a pipeline run one second later (several clicks in a row share one run). The response carries `runStarted`, and the page then refreshes until that run's results are in. Approving a count or a cap, dismissing, and launching a campaign change nothing the agents would see, so they start no run. Switch off with `SARTHI_RERUN_AFTER_ACTION=false`.
- **Decisions carry over between runs** (`execution_engine.already_decided`). A dismissed recommendation is not raised again for `SARTHI_DECISION_MEMORY_DAYS` (7) while the product stays in the same zone. An approved check or campaign is not repeated. An approved order or transfer changed the stock, so anything the agents still propose for that product afterwards is a new recommendation.
- **The Alerts list keeps approvals** from earlier runs for 24 hours, and reorder requests made in chat move to each new run until they are decided.
- **The audit trail spans runs**: the 30 most recent actions, not only the latest run's.
- **Data Hub Sync** is available whenever the backend is connected (it re-runs the agents on the data already stored). Offline it still waits for all eight simulated uploads.
- **Receipt.** The receipt tab is opened when Proceed is clicked and filled once the order is placed. It was opened with `noopener` before, for which browsers return no window to write into, so it stayed blank.
- **Command Center Export CSV** downloads the table as shown (same filter and sort).
- **Still not wired**, because they never did anything in the original design and need a decision on what they should do: Bulk Actions, the per-row action button, Prev/Next and the "Showing 15 of 847" text on the Command Center, and the four headline numbers on the landing page.
