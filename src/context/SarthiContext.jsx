import { createContext, useContext, useState, useCallback, useEffect } from "react";
import { live } from "../data/appData.js";
import { api } from "../api/client.js";
import { hydrate } from "../api/hydrate.js";

const SarthiContext = createContext();

const DEFAULT_STRATEGY = {
  mode: "Balanced",
  savingsPriority: 0.5,
  safetyStockMultiplier: 1.0,
  leadTimeBuffer: 1.2,
  lastUpdate: "SYSTEM_INITIALIZED"
};

const sameStrategy = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function SarthiProvider({ children }) {
  const [lang, setLangState] = useState(localStorage.getItem("sarthi_lang") || "EN");
  const [strategy, setStrategy] = useState(live.strategy ?? DEFAULT_STRATEGY);
  const [dataVersion, setDataVersion] = useState(0);

  // re-fill appData from the backend; pages re-render only when something actually changed
  const refreshData = useCallback(async () => {
    const result = await hydrate();
    if (result && live.strategy) setStrategy(prev => (sameStrategy(prev, live.strategy) ? prev : live.strategy));
    if (result === "updated") setDataVersion(v => v + 1);
    return Boolean(result);
  }, []);

  // after something that starts a pipeline run (a strategy change, a voice adjustment): refresh until its results are in
  const refreshAfterRun = useCallback(() => {
    const before = live.runId;
    let tries = 0;
    const tick = async () => {
      await refreshData();
      if (live.online && live.runId === before && ++tries < 20) setTimeout(tick, 3000);
    };
    setTimeout(tick, 2500);
  }, [refreshData]);

  // backend not reachable (or still on its first run) when the app opened: keep the built-in data and look again now and then
  useEffect(() => {
    if (live.online) return;
    const t = setInterval(async () => { if (await refreshData()) clearInterval(t); }, 15000);
    return () => clearInterval(t);
  }, [refreshData]);

  const setLang = (newLang) => {
    setLangState(newLang);
    localStorage.setItem("sarthi_lang", newLang);
    refreshData();                                   // alert and debate text is language-specific
  };

  // serverStrategy: an object the backend has already applied (e.g. from the chat) -> just adopt it
  const updateStrategy = (newStrategy, serverStrategy = null) => {
    if (serverStrategy) { setStrategy(serverStrategy); refreshAfterRun(); return; }
    setStrategy(prev => ({ ...prev, ...newStrategy, lastUpdate: new Date().toLocaleTimeString() }));
    if (newStrategy?.mode) api.put("/strategy", newStrategy).then(r => { if (r) { setStrategy(r); refreshAfterRun(); } });
  };

  return (
    <SarthiContext.Provider value={{ strategy, updateStrategy, lang, setLang, dataVersion, refreshData, refreshAfterRun }}>
      {children}
    </SarthiContext.Provider>
  );
}

export function useSarthi() {
  return useContext(SarthiContext);
}
