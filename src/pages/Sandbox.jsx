import { useState, useEffect } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { C } from "../theme.js";
import { CustomTooltip, SectionLabel } from "../components/ui.jsx";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";

const debateMessages = (lang) => [
  { agent: i18n[lang].forecaster, color: C.ghost, msg: i18n[lang].debate1 },
  { agent: i18n[lang].riskAgent, color: C.chaos, msg: i18n[lang].debate2 },
  { agent: i18n[lang].cfoAgent, color: C.money, msg: i18n[lang].debate3 },
  { agent: i18n[lang].negotiator, color: C.sweet, msg: i18n[lang].debate4 },
  { agent: i18n[lang].esgGuardian, color: C.ghost, msg: i18n[lang].debate5 },
  { agent: i18n[lang].forecaster, color: C.ghost, msg: i18n[lang].debate6 },
  { agent: i18n[lang].riskAgent, color: C.chaos, msg: i18n[lang].debate7 },
  { agent: i18n[lang].executionEngine, color: C.sweet, msg: i18n[lang].debate8 },
];

export default function Sandbox() {
  const { lang } = useSarthi();
  const [lead, setLead] = useState(5);
  const [demand, setDemand] = useState(50);
  const [stock, setStock] = useState(300);
  const [margin, setMargin] = useState(20);
  const [debateIdx, setDebateIdx] = useState(0);

  const daysLeft = Math.round(stock / demand);
  const risk = Math.max(0, Math.min(100, Math.round(((lead - daysLeft) / (lead || 1)) * 100 + 20)));
  const par = risk > 50 ? Math.round(risk * demand * margin * 0.8) : 0;
  const safetyStock = Math.round(demand * lead * 1.3);

  const messages = debateMessages(lang);

  useEffect(() => {
    const t = setInterval(() => {
      setDebateIdx(i => (i < messages.length - 1 ? i + 1 : 0));
    }, 3000);
    return () => clearInterval(t);
  }, [messages.length]);

  const sensitivityData = Array.from({ length: 15 }, (_, i) => {
    const lt = i + 1;
    const r = Math.max(0, Math.min(100, Math.round(((lt - daysLeft) / (lt || 1)) * 100 + 20)));
    return { lt: `${lt}d`, risk: r };
  });

  return (
    <div style={{ animation: "fadeIn 0.5s ease" }}>
      <div style={{ marginBottom: 32 }}>
        <div style={{ fontFamily: "'DM Mono'", fontSize: 15, letterSpacing: 3, color: C.muted, textTransform: "uppercase", marginBottom: 10 }}>{i18n[lang].scenarioPlanningEngine}</div>
        <h1 style={{ fontFamily: "'Sora'", fontSize: 36, fontWeight: 700, color: C.text, margin: 0 }}>{i18n[lang].whatIfSandbox}</h1>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 24 }}>
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: "28px" }}>
          <SectionLabel>{i18n[lang].adjustParameters}</SectionLabel>
          {[
            { label: i18n[lang].leadTime, unit: i18n[lang].days, value: lead, min: 1, max: 30, set: setLead, color: C.chaos },
            { label: i18n[lang].dailyDemand, unit: i18n[lang].unitsPerDay, value: demand, min: 10, max: 200, set: setDemand, color: C.sweet },
            { label: i18n[lang].currentStock, unit: i18n[lang].units, value: stock, min: 50, max: 1500, set: setStock, color: C.ghost },
            { label: i18n[lang].grossMargin, unit: "%", value: margin, min: 2, max: 60, set: setMargin, color: C.money },
          ].map(s => (
            <div key={s.label} style={{ marginBottom: 24 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontFamily: "'Inter'", fontSize: 15, color: C.text, fontWeight: 400 }}>{s.label}</span>
                <span style={{ fontFamily: "'Sora'", fontSize: 18, color: s.color, fontWeight: 600 }}>{s.value} <span style={{ fontSize: 15, color: C.muted }}>{s.unit}</span></span>
              </div>
              <input type="range" min={s.min} max={s.max} value={s.value} onChange={e => s.set(Number(e.target.value))}
                style={{ width: "100%", accentColor: s.color, cursor: "pointer", height: 4 }} />
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="glass" style={{ borderRadius: 12, padding: 20, height: "100%", display: "flex", flexDirection: "column" }}>
            <SectionLabel>{i18n[lang].agentDebate}</SectionLabel>
            <div style={{ flex: 1, marginTop: 16, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
              {messages.slice(0, debateIdx + 1).map((m, i) => (
                <div key={i} style={{ 
                    padding: "10px 14px", background: m.color + "11", 
                    borderLeft: `3px solid ${m.color}`, borderRadius: "0 8px 8px 0",
                    animation: "fadeIn 0.4s ease" 
                }}>
                  <div style={{ fontFamily: "'DM Mono'", fontSize: 10, color: m.color, fontWeight: 800, marginBottom: 4 }}>{m.agent.toUpperCase()}</div>
                  <div style={{ fontSize: 12, color: C.text, lineHeight: 1.4 }}>{m.msg}</div>
                </div>
              ))}
              <div style={{ fontSize: 10, color: C.muted, fontStyle: "italic", padding: "4px 0" }}>{i18n[lang].agentsDeliberating}</div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 20 }}>
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: "28px" }}>
            <SectionLabel>{i18n[lang].riskSensitivityTitle}</SectionLabel>
            <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={sensitivityData}>
                <defs>
                <linearGradient id="riskGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.chaos} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={C.chaos} stopOpacity={0} />
                </linearGradient>
                </defs>
                <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
                <XAxis dataKey="lt" tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={60} stroke={C.chaos} strokeDasharray="4 4" label={{ value: i18n[lang].critical, fill: C.chaos, fontSize: 12, fontFamily: "'DM Mono'" }} />
                <Area type="monotone" dataKey="risk" stroke={C.chaos} strokeWidth={2} fill="url(#riskGrad)" name={i18n[lang].stockoutRiskPct} />
            </AreaChart>
            </ResponsiveContainer>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {[
                { l: i18n[lang].stockoutProb, v: `${risk}%`, c: risk > 60 ? C.chaos : risk > 30 ? C.money : C.sweet },
                { l: i18n[lang].parLabel, v: par > 0 ? `₹${(par / 1000).toFixed(1)}K` : "—", c: par > 0 ? C.chaos : C.sweet }
            ].map(s => (
                <div key={s.l} className="glass" style={{ padding: 20, borderRadius: 12, flex: 1 }}>
                    <div style={{ fontSize: 11, color: C.muted, fontFamily: "'DM Mono'", marginBottom: 4 }}>{s.l.toUpperCase()}</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: s.c }}>{s.v}</div>
                </div>
            ))}
        </div>
      </div>
    </div>
  );
}
