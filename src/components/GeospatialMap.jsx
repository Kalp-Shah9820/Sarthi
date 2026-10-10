import { C } from "../theme.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import { SarthiIcon } from "./ui.jsx";
import { live } from "../data/appData.js";

export default function GeospatialMap() {
  const { lang } = useSarthi();
  const risks = live.mapRisks ?? [
    { id: "bayOfBengal", type: "cyclone", x: 75, y: 70, severity: "high", icon: "CloudLightning" },
    { id: "jnptMumbai",   type: "port",    x: 18, y: 65, severity: "medium", icon: "Anchor" },
    { id: "delhiNcr",     type: "heatwave",x: 35, y: 25, severity: "low", icon: "Thermometer" },
    { id: "keralaCoast",  type: "strike",  x: 32, y: 92, severity: "high", icon: "Truck" },
  ];

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}>
      {/* Stylized India SVG mask/shape */}
      <svg viewBox="0 0 100 120" style={{ width: "100%", height: "100%", filter: "drop-shadow(0 0 10px rgba(0,0,0,0.5))" }}>
        <path
          d="M 35 15 L 45 12 L 55 18 L 60 30 L 75 40 L 70 55 L 75 70 L 60 85 L 50 110 L 40 115 L 30 100 L 25 85 L 15 75 L 12 55 L 20 40 L 35 25 Z"
          fill={C.surface}
          stroke={C.border}
          strokeWidth="0.5"
        />
        
        {/* Risk Markers */}
        {risks.map((risk, i) => {
          const color = risk.severity === "high" ? C.chaos : risk.severity === "medium" ? C.money : C.sweet;
          return (
            <g key={i}>
              <circle cx={risk.x} cy={risk.y} r="3" fill={color}>
                <animate attributeName="r" values="2.5;4.5;2.5" dur="2s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="1;0.4;1" dur="2s" repeatCount="indefinite" />
              </circle>
              <circle cx={risk.x} cy={risk.y} r="8" fill="transparent" stroke={color} strokeWidth="0.3" opacity="0.3">
                <animate attributeName="r" values="3;10;3" dur="2s" repeatCount="indefinite" />
              </circle>
              <text x={risk.x + 5} y={risk.y + 1} fill={C.text} fontSize="4" fontFamily="'DM Mono'" fontWeight="600">
                {i18n[lang][risk.id] || risk.id}
              </text>
            </g>
          );
        })}
      </svg>

      <div style={{ position: "absolute", bottom: 10, right: 10, display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.chaos }} />
          <span style={{ fontFamily: "'DM Mono'", fontSize: 9, color: C.muted }}>{i18n[lang].highRisk}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.money }} />
          <span style={{ fontFamily: "'DM Mono'", fontSize: 9, color: C.muted }}>{i18n[lang].mediumRisk}</span>
        </div>
      </div>
    </div>
  );
}
