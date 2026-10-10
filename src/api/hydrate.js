// Fills the objects exported by src/data/appData.js, in place, with the backend's latest results.
// Pages keep importing the same arrays; when the backend cannot be reached nothing is touched and
// they show their built-in data.
import * as D from "../data/appData.js";
import { rawI18n } from "../data/i18n.js";
import { api } from "./client.js";

const replaceArray = (target, src) => { if (Array.isArray(src)) target.splice(0, target.length, ...src); };
const replaceObject = (target, src) => {
  if (!src || typeof src !== "object") return;
  Object.keys(target).forEach((k) => delete target[k]);
  Object.assign(target, src);
};

let lastPayload = null;

// Resolves to false when the backend is unreachable, "same" when nothing changed since the last
// call, and "updated" when the data was replaced.
export async function hydrate() {
  let lang = "EN";
  try { lang = localStorage.getItem("sarthi_lang") || "EN"; } catch { /* storage blocked: English */ }
  const b = await api.get(`/bootstrap?lang=${lang}`, 4000);
  if (!b || !Array.isArray(b.skuData) || !b.skuData.length || !b.live) { D.live.online = false; return false; }

  const payload = JSON.stringify(b);
  if (payload === lastPayload && D.live.online) return "same";
  lastPayload = payload;

  replaceArray(D.skuData, b.skuData);
  replaceArray(D.mbaRules, b.mbaRules);
  replaceArray(D.cannibalization, b.cannibalization);
  replaceArray(D.distributors, b.distributors);
  replaceArray(D.monthLabels, b.monthLabels);
  replaceArray(D.forecastMonths, b.forecastMonths);
  replaceObject(D.skuMonteCarlo, b.skuMonteCarlo);
  replaceObject(D.bullwhipData, b.bullwhipData);
  replaceArray(D.aisles, b.aisles);

  replaceObject(D.live, { ...b.live, online: true });

  // names for SKUs / regions the static translations do not contain
  for (const [key, label] of Object.entries(b.labels || {})) {
    if (!(key in rawI18n.EN)) rawI18n.EN[key] = label;
  }
  return "updated";
}
