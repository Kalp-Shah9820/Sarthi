import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Bar, BarChart } from "recharts";
import { skuData, monthLabels, zoneInfo } from "../data/appData.js";
import { C } from "../theme.js";
import { CustomTooltip, SectionLabel, Tag, SarthiIcon } from "../components/ui.jsx";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import GeospatialMap from "../components/GeospatialMap.jsx";

const riskSignals = [
  { id: 1, type: "WEATHER", msgKey: "riskMsgWeather", severity: "HIGH", skus: 23, actionKey: "viewImpact", icon: "CloudLightning" },
  { id: 2, type: "LOGISTICS", msgKey: "riskMsgLogistics", severity: "HIGH", skus: 67, actionKey: "viewImpact", icon: "Anchor" },
  { id: 3, type: "COMMODITY", msgKey: "riskMsgCommodity", severity: "MEDIUM", skus: 34, actionKey: "viewImpact", icon: "TrendingUp" },
  { id: 4, type: "TRANSPORT", msgKey: "riskMsgTransport", severity: "HIGH", skus: 89, actionKey: "reroute", icon: "Truck" },
];

function RiskSignals() {
  const { lang } = useSarthi();
  return (
    <div className="glass" style={{ borderRadius: 20, padding: 24, height: "100%" }}>
      <SectionLabel>{i18n[lang].globalRisk}</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 20 }}>
        {riskSignals.map(s => (
          <div key={s.id} style={{ display: "flex", gap: 16, padding: "12px 16px", background: C.surface, borderRadius: 12, border: `1px solid ${s.severity === "HIGH" ? C.chaos : s.severity === "MEDIUM" ? C.money : C.border}` }}>
            <div style={{ color: s.severity === "HIGH" ? C.chaos : C.muted, display: "flex", alignItems: "center" }}>
              <SarthiIcon name={s.icon} size={24} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontFamily: "'DM Mono'", fontSize: 10, fontWeight: 700, color: s.severity === "HIGH" ? C.chaos : C.muted }}>{i18n[lang]["riskType" + s.type.charAt(0) + s.type.slice(1).toLowerCase()] || s.type}</span>
                <div style={{ width: 4, height: 4, borderRadius: "50%", background: C.border }} />
                <span style={{ fontFamily: "'DM Mono'", fontSize: 10, color: C.muted }}>{s.skus} {i18n[lang].skusAtRisk}</span>
              </div>
              <div style={{ fontSize: 13, fontWeight: 500, color: C.text, lineHeight: 1.4 }}>{i18n[lang][s.msgKey]}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { lang, strategy } = useSarthi();
  const navigate = useNavigate();

  const trendData = monthLabels.map((m, i) => ({
    month: m,
    sales: skuData.reduce((acc, d) => acc + (d.sales[i] || 0), 0),
    forecast: skuData.reduce((acc, d) => acc + (d.forecast[i] || 0), 0),
  }));

  const zoneCards = Object.entries(zoneInfo).map(([key, info]) => {
    const skus = skuData.filter(s => s.zone === key);
    const par = skus.reduce((acc, s) => acc + s.par, 0);
    return { key, ...info, count: skus.length, par };
  });

  return (
    <div style={{ animation: "fadeIn 0.5s ease" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 48 }}>
        <div>
          <div style={{ fontFamily: "'DM Mono'", fontSize: 15, letterSpacing: 3, color: C.muted, textTransform: "uppercase", marginBottom: 12 }}>{i18n[lang].commandCenter}</div>
          <h1 style={{ fontFamily: "'Sora'", fontSize: 48, fontWeight: 700, color: C.text, margin: 0, letterSpacing: "-0.03em" }}>Sarthi · {i18n[lang].liveIntel}</h1>
        </div>
        <Link to="/chat" style={{ background: C.accent, color: C.bg, padding: "14px 28px", borderRadius: 12, textDecoration: "none", fontFamily: "'Sora'", fontWeight: 700, fontSize: 15, boxShadow: `0 8px 24px ${C.accent}44` }}>{i18n[lang].askIntel}</Link>
      </div>

      {/* 1. AGGREGATE SALES VS FORECAST */}
      <div className="glass" style={{ borderRadius: 24, padding: 32, marginBottom: 48 }}>
        <SectionLabel>{i18n[lang].aggregateSalesTitle}</SectionLabel>
        <div style={{ height: 400, marginTop: 32 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trendData}>
              <defs>
                <linearGradient id="colorSales" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={C.sweet} stopOpacity={0.2}/>
                  <stop offset="95%" stopColor={C.sweet} stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
               <XAxis dataKey="month" tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Area type="monotone" dataKey="sales" stroke={C.sweet} strokeWidth={3} fillOpacity={1} fill="url(#colorSales)" name={i18n[lang].actualSales} />
              <Area type="monotone" dataKey="forecast" stroke={C.accent} strokeWidth={2} strokeDasharray="5 5" fill="transparent" name={i18n[lang].aiForecast} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <SectionLabel>{i18n[lang].zoneDistributionTitle}</SectionLabel>
      
      {/* 2. ZONE CARDS (REPRODUCED FROM SCREENSHOT) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 48 }}>
        {zoneCards.map(z => (
          <div key={z.key} className="glass" style={{ borderRadius: 24, padding: 32, position: "relative" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <Tag color={z.color}>{z.icon} {(z.key === "sweet" ? i18n[lang].sweetSpot : z.key === "chaos" ? i18n[lang].chaosZone : z.key === "money" ? i18n[lang].moneyPit : i18n[lang].ghostZone).toUpperCase()}</Tag>
              {z.par > 0 && (
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 10, fontFamily: "'DM Mono'", color: C.muted }}>{i18n[lang].profitAtRisk}</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: z.color }}>₹{(z.par / 1000).toFixed(0)}K</div>
                </div>
              )}
            </div>
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: 64, fontWeight: 700, color: C.text, lineHeight: 1 }}>{z.count}</div>
              <div style={{ fontSize: 13, color: C.muted, marginTop: 8, fontFamily: "'DM Mono'" }}>{i18n[lang].skusInZone}</div>
            </div>
            <div style={{ marginTop: 32, fontSize: 15, color: C.muted, lineHeight: 1.6, maxWidth: "80%" }}>
              {i18n[lang][z.key + 'Desc']}
            </div>
          </div>
        ))}
      </div>

      {/* 3. RISK INTELLIGENCE & MAP (Enterprise Features) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 400px", gap: 24, marginBottom: 48 }}>
        <div className="glass" style={{ borderRadius: 20, padding: 32, position: "relative", overflow: "hidden" }}>
           <SectionLabel>{i18n[lang].globalRisk} · {i18n[lang].indiaImpactMap}</SectionLabel>
           <div style={{ height: 400, marginTop: 24, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <GeospatialMap />
           </div>
        </div>
        <RiskSignals />
      </div>
    </div>
  );
}
