import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { cannibalization, distributors, live, mbaRules, monthLabels, skuMonteCarlo, zoneInfo } from "../data/appData.js";
import { C } from "../theme.js";
import { CustomTooltip, SectionLabel, Tag, SarthiIcon } from "../components/ui.jsx";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import { ArrowRight } from "lucide-react";

const esgOptions = [
  { modeKey: "esgAir", icon: "Plane", tat: "1 day", co2Key: "esgHigh", co2Pct: 100, color: C.chaos },
  { modeKey: "esgSea", icon: "Ship", tat: "7 days", co2Key: "esgLow", co2Pct: 25, color: C.ghost },
  { modeKey: "esgMultimodal", icon: "Train", tat: "3 days", co2Key: "esgMedium", co2Pct: 55, color: C.sweet },
];

export default function SKUDetail({ sku, setPage }) {
  const { lang } = useSarthi();
  const z = zoneInfo[sku.zone];
  const daysLeft = Math.round(sku.stock / sku.vel);
  const mc = skuMonteCarlo[sku.id];

  const chartData = monthLabels.map((m, i) => ({
    month: m, actual: sku.sales[i], forecast: sku.forecast[i],
    upper: Math.round(sku.forecast[i] * 1.2),
    lower: Math.round(sku.forecast[i] * 0.8),
  }));

  const mcData = mc.bins;
  const relatedRules = mbaRules.filter(r => r.antecedent.includes(sku.name) || r.consequent === sku.name);
  const relatedCannibs = cannibalization.filter(c => c.rising === sku.id || c.falling === sku.id);

  const stockPct = Math.min(100, (sku.stock / (sku.safetyStock * 2)) * 100);
  const gaugeColor = sku.stock < sku.safetyStock ? C.chaos : sku.stock < sku.reorderPoint ? C.money : C.sweet;

  // shipping numbers for this SKU from the backend, when it is reachable
  const liveEsg = live.esg?.bySku?.[sku.id]?.options ?? live.esg?.options;
  const options = esgOptions.map((o, i) => ({ ...o, ...(liveEsg?.[i] ? { tat: liveEsg[i].tat, co2Key: liveEsg[i].co2Key, co2Pct: liveEsg[i].co2Pct } : {}) }));

  return (
    <div style={{ animation: "fadeIn 0.5s ease" }}>
      <button onClick={() => setPage("inventory")} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 8, padding: "8px 16px", fontFamily: "'Inter'", fontSize: 13, cursor: "pointer", marginBottom: 32, transition: "all 0.2s", display:"flex", alignItems:"center", gap:6 }} onMouseEnter={e => e.currentTarget.style.color = C.text}>
        <SarthiIcon name="ChevronLeft" size={14} /> {i18n[lang].back}
      </button>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 40 }}>
        <div>
          <Tag color={z.color}>
            <SarthiIcon name={z.icon} size={14} style={{ marginRight: 8 }} />
            {z.label.toUpperCase()}
          </Tag>
          <h1 style={{ fontFamily: "'Sora'", fontSize: 48, fontWeight: 700, color: C.text, margin: "16px 0 8px", letterSpacing: "-0.02em" }}>{i18n[lang][sku.id.toLowerCase()] || sku.name}</h1>
          <div style={{ fontFamily: "'DM Mono'", fontSize: 13, color: C.muted }}>{sku.id} · {i18n[lang]["category" + sku.cat.replace(/\s/g, "")] || sku.cat}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontFamily: "'DM Mono'", fontSize: 11, color: C.muted, letterSpacing: 2, textTransform: "uppercase" }}>{i18n[lang].stockoutProb}</div>
          <div style={{ fontFamily: "'Sora'", fontSize: 56, fontWeight: 700, color: sku.risk > 60 ? C.chaos : sku.risk > 30 ? C.money : C.sweet, lineHeight: 1 }}>{sku.risk}%</div>
          {sku.par > 0 && <div style={{ fontFamily: "'DM Mono'", fontSize: 13, color: C.money, marginTop: 8 }}>₹{(sku.par / 1000).toFixed(0)}K {i18n[lang].parLabel}</div>}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
        {[
          { l: i18n[lang].currentStock, v: `${sku.stock}`, c: C.text },
          { l: i18n[lang].velocityLabel, v: `${sku.vel}/d`, c: C.sweet },
          { l: i18n[lang].daysCover, v: `${daysLeft}d`, c: daysLeft < sku.lead ? C.chaos : C.sweet },
          { l: i18n[lang].esgScoreDetail, v: `${sku.esg ?? Math.round(85 + Math.random() * 10)}%`, c: C.ghost },
        ].map(s => (
          <div key={s.l} className="glass" style={{ borderRadius: 16, padding: "20px" }}>
            <div style={{ fontFamily: "'DM Mono'", fontSize: 11, color: C.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>{s.l}</div>
            <div style={{ fontFamily: "'Sora'", fontSize: 24, color: s.c, fontWeight: 700 }}>{s.v}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 24, marginBottom: 24 }}>
        <div className="glass" style={{ borderRadius: 20, padding: 32 }}>
          <SectionLabel>{i18n[lang].salesVsForecastTitle}</SectionLabel>
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="confBand" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={C.ghost} stopOpacity={0.15}/>
                  <stop offset="95%" stopColor={C.ghost} stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="month" tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Area type="monotone" dataKey="upper" stroke="transparent" fill="url(#confBand)" />
              <Line type="monotone" dataKey="actual" stroke={z.color} strokeWidth={3} dot={{ fill: z.color, r: 4 }} name={i18n[lang].actual} />
              <Line type="monotone" dataKey="forecast" stroke={C.accent} strokeWidth={2} strokeDasharray="6 3" dot={false} name={i18n[lang].aiForecast} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            <div className="glass" style={{ borderRadius: 20, padding: 24 }}>
                <SectionLabel>{i18n[lang].esgComplianceFeed}</SectionLabel>
                <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                    {options.map(opt => (
                        <div key={opt.modeKey} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: `1px solid ${C.border}33` }}>
                            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                                <SarthiIcon name={opt.icon} size={20} color={C.text} />
                                <div>
                                    <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{i18n[lang][opt.modeKey]}</div>
                                    <div style={{ fontSize: 11, color: C.muted }}>{opt.tat} {i18n[lang].deliverySuffix}</div>
                                </div>
                            </div>
                            <div style={{ textAlign: "right" }}>
                                <div style={{ fontSize: 11, color: opt.color, fontWeight: 700 }}>{i18n[lang][opt.co2Key]} {i18n[lang].co2LabelDetail}</div>
                                <div style={{ width: 40, height: 3, background: C.border, borderRadius: 2, marginTop: 4 }}>
                                    <div style={{ height: "100%", width: opt.co2Pct + "%", background: opt.color }} />
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            <div className="glass" style={{ borderRadius: 20, padding: 24, flex: 1 }}>
                <SectionLabel>{i18n[lang].stockCoverageGauge}</SectionLabel>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 120, position: "relative" }}>
                    <svg viewBox="0 0 100 60" style={{ width: 140 }}>
                        <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke={C.border} strokeWidth="8" strokeLinecap="round" />
                        <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke={gaugeColor} strokeWidth="8" strokeLinecap="round" strokeDasharray={`${stockPct * 1.25} 125`} />
                    </svg>
                    <div style={{ position: "absolute", bottom: 10, textAlign: "center" }}>
                        <div style={{ fontSize: 24, fontWeight: 700, color: gaugeColor }}>{sku.stock}</div>
                        <div style={{ fontSize: 9, color: C.muted, fontFamily: "'DM Mono'" }}>{i18n[lang].unitsCaps}</div>
                    </div>
                </div>
            </div>
        </div>
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 20, padding: 32, marginBottom: 40 }}>
        <SectionLabel>{i18n[lang].monteCarloTitle}</SectionLabel>
        <div style={{ height: 140, marginTop: 20 }}>
            <ResponsiveContainer width="100%" height="100%">
                <BarChart data={mcData}>
                    <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                        {mcData.map((e, i) => <Cell key={i} fill={e.highlight ? C.chaos : z.color} fillOpacity={e.highlight ? 0.9 : 0.3} />)}
                    </Bar>
                </BarChart>
            </ResponsiveContainer>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, fontFamily: "'DM Mono'", fontSize: 11, color: C.muted }}>
            <span>{i18n[lang].p95ProbableStockout}</span>
            <span style={{ color: C.chaos, fontWeight: 700 }}>{sku.risk}% {i18n[lang].confidenceFactor}</span>
        </div>
      </div>

      <button onClick={() => setPage("replenish")} style={{ width: "100%", background: C.text, color: C.bg, border: "none", borderRadius: 16, padding: "20px", fontFamily: "'Sora'", fontWeight: 700, fontSize: 16, cursor: "pointer", boxShadow: `0 12px 32px rgba(0,0,0,0.4)`, display:"flex", alignItems:"center", justifyContent:"center", gap:10 }}>
        {i18n[lang].initiateReplenishmentAction} <ArrowRight size={20} />
      </button>
    </div>
  );
}
