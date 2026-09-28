import { useLocation } from "react-router-dom";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { C } from "../theme.js";
import { SarthiIcon } from "./ui.jsx";

const phases = [
  { id: "sense", label: "pipelineSense", agents: ["MS", "DI"], path: "/chat" },
  { id: "decide", label: "pipelineDecide", agents: ["IO", "CG"], path: "/inventory" },
  { id: "resolve", label: "pipelineResolve", agents: ["DS", "OR"], path: "/replenish" },
  { id: "execute", label: "pipelineExecute", agents: ["EE"], path: "/replenish" }
];

const agentNames = {
  MS: "macroSentinel", DI: "demandIntel", IO: "inventoryOptimizer",
  CG: "complianceGuardian", DS: "distributorSelector",
  OR: "overstockResolver", EE: "executionEngine"
};

export default function PipelineVisualizer() {
  const { lang } = useSarthi();
  const { pathname } = useLocation();

  const getActivePhase = () => {
    if (pathname.includes("/chat")) return 0;
    if (pathname.includes("/inventory") || pathname.includes("/sku")) return 1;
    if (pathname.includes("/replenish") && !pathname.includes("order")) return 2;
    if (pathname.includes("/replenish") || pathname.includes("/sandbox")) return 3;
    return -1;
  };

  const activeIndex = getActivePhase();

  return (
    <div className="glass" style={{
      margin: "0 0 32px 0", padding: "20px 40px", borderRadius: 16,
      display: "flex", alignItems: "center", justifyContent: "space-between",
      border: `1px solid ${C.borderLight}`
    }}>
      {phases.map((p, i) => {
        const isActive = i === activeIndex;
        const isPast = i < activeIndex;
        const color = isActive ? C.accent : isPast ? C.sweet : C.muted;

        return (
          <div key={p.id} style={{ flex: 1, display: "flex", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, position: "relative" }}>
              <div style={{
                width: 32, height: 32, borderRadius: "50%",
                background: isActive ? C.accent + "22" : isPast ? C.sweet + "22" : C.bg,
                border: `2px solid ${color}`, display: "flex", alignItems: "center",
                justifyContent: "center", fontSize: 12, fontWeight: 700, color,
                transition: "all 0.5s", animation: isActive ? "pulse-blue 2s infinite" : "none"
              }}>
                {isPast ? <SarthiIcon name="Check" size={14} /> : i + 1}
                <div style={{ 
                    position: "absolute", top: -10, left: 32, 
                    background: C.accent, color: C.bg, fontSize: 8, 
                    padding: "1px 4px", borderRadius: 3, fontWeight: 800 
                }}>
                    23 {i18n[lang].signs}
                </div>
              </div>
              <div style={{ 
                fontFamily: "'Sora'", fontSize: 11, fontWeight: 600, 
                color: isActive ? C.text : C.muted, textTransform: "uppercase", letterSpacing: 1 
              }}>
                {i18n[lang][p.label]}
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {p.agents.map(a => (
                  <div key={a} title={i18n[lang][agentNames[a]]} style={{
                    width: 14, height: 14, borderRadius: "50%",
                    background: color, fontSize: 7, color: C.bg,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontWeight: 800
                  }}>{a}</div>
                ))}
              </div>
            </div>
            {i < phases.length - 1 && (
              <div style={{ 
                flex: 1, height: 2, background: isPast ? C.sweet : C.border, 
                margin: "0 20px", opacity: 0.5 
              }} />
            )}
          </div>
        );
      })}
      <style>{`
        @keyframes pulse-blue {
          0% { box-shadow: 0 0 0 0 rgba(77, 181, 255, 0.4); }
          70% { box-shadow: 0 0 0 10px rgba(77, 181, 255, 0); }
          100% { box-shadow: 0 0 0 0 rgba(77, 181, 255, 0); }
        }
      `}</style>
    </div>
  );
}
