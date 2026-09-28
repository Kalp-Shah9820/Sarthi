import { createContext, useContext, useState } from "react";

const SarthiContext = createContext();

export function SarthiProvider({ children }) {
  const [lang, setLangState] = useState(localStorage.getItem("sarthi_lang") || "EN");
  const [strategy, setStrategy] = useState({
    mode: "Balanced",
    savingsPriority: 0.5,
    safetyStockMultiplier: 1.0,
    leadTimeBuffer: 1.2,
    lastUpdate: "SYSTEM_INITIALIZED"
  });

  const setLang = (newLang) => {
    setLangState(newLang);
    localStorage.setItem("sarthi_lang", newLang);
  };

  const updateStrategy = (newStrategy) => {
    setStrategy(prev => ({ ...prev, ...newStrategy, lastUpdate: new Date().toLocaleTimeString() }));
  };

  return (
    <SarthiContext.Provider value={{ strategy, updateStrategy, lang, setLang }}>
      {children}
    </SarthiContext.Provider>
  );
}

export function useSarthi() {
  return useContext(SarthiContext);
}
