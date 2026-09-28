import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { skuData, zoneInfo } from "../data/appData.js";
import { C } from "../theme.js";
import { CustomTooltip, SectionLabel, Tag, SarthiIcon } from "../components/ui.jsx";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";

function Alerts() {
  const { lang } = useSarthi();
  const [dismissed, setDismissed] = useState([]);
  const [approved, setApproved] = useState([]);
  const [feedback, setFeedback] = useState({});

  const all = [
    { id: 1, sku: "Lays Classic 26g", zone: "chaos", risk: 84, confidence: 94, msg: i18n[lang].alert1msg, action: i18n[lang].alert1action, impact: i18n[lang].alert1impact },
    { id: 2, sku: "Haldirams Namkeen 400g", zone: "chaos", risk: 78, confidence: 89, msg: i18n[lang].alert2msg, action: i18n[lang].alert2action, impact: i18n[lang].alert2impact },
    { id: 3, sku: "Colgate Strong 200g", zone: "chaos", risk: 71, confidence: 67, msg: i18n[lang].alert3msg, action: i18n[lang].alert3action, impact: i18n[lang].alert3impact },
    { id: 4, sku: "Surf Excel 1kg", zone: "ghost", risk: 68, confidence: 82, msg: i18n[lang].alert4msg, action: i18n[lang].alert4action, impact: i18n[lang].alert4impact },
    { id: 5, sku: "Fortune Oil 5L", zone: "money", risk: 52, confidence: 74, msg: i18n[lang].alert5msg, action: i18n[lang].alert5action, impact: i18n[lang].alert5impact },
    { id: 6, sku: "Tata Salt 1kg", zone: "money", risk: 45, confidence: 58, msg: i18n[lang].alert6msg, action: i18n[lang].alert6action, impact: i18n[lang].alert6impact },
    { id: 7, sku: "Haldirams → Lays", zone: "chaos", risk: 72, confidence: 91, msg: i18n[lang].alert7msg, action: i18n[lang].alert7action, impact: i18n[lang].alert7impact },
  ].filter(a => !dismissed.includes(a.id));

  return (
    <div style={{ animation: "fadeIn 0.5s ease" }}>
      <div style={{ marginBottom: 48 }}>
        <div style={{ fontFamily: "'DM Mono'", fontSize: 15, letterSpacing: 3, color: C.muted, textTransform: "uppercase", marginBottom: 12 }}>{i18n[lang].alerts}</div>
        <h1 style={{ fontFamily: "'Sora'", fontSize: 48, fontWeight: 700, color: C.text, margin: 0, letterSpacing: "-0.03em" }}>{i18n[lang].intelApprovals}</h1>
      </div>

      <div className="glass" style={{ borderRadius: 16, padding: "28px", marginBottom: 24 }}>
        <SectionLabel>{i18n[lang].riskVolumeDist}</SectionLabel>
        <ResponsiveContainer width="100%" height={160}>
            <BarChart 
                data={Object.entries(zoneInfo).map(([k,v]) => ({ 
                    zone: v.label, 
                    count: skuData.filter(d=>d.zone===k).length, 
                    par: Math.round(skuData.filter(d=>d.zone===k).reduce((s,d)=>s+d.par,0)/1000) 
                }))}
                barGap={8}
            >
                <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
                <XAxis dataKey="zone" tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: C.muted, fontFamily: "'DM Mono'", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="count" radius={[4,4,0,0]} name={i18n[lang].skuCount} barSize={32} fill={C.accent} fillOpacity={0.8} />
                <Bar dataKey="par" radius={[4,4,0,0]} name={i18n[lang].parK} barSize={32} fill={C.chaos} fillOpacity={0.2} stroke={C.chaos} />
            </BarChart>
        </ResponsiveContainer>
      </div>

      {all.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontFamily: "'Sora'", fontSize: 28, color: C.sweet, marginBottom: 8, fontStyle: "italic" }}>{i18n[lang].allClear}</div>
          <div style={{ fontFamily: "'Inter'", fontSize: 16, color: C.muted }}>{i18n[lang].syncStatusMsg}</div>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {all.map(alert => {
          const z = zoneInfo[alert.zone];
          const isApproved = approved.includes(alert.id);
          const confColor = alert.confidence >= 85 ? C.sweet : alert.confidence >= 70 ? C.money : C.chaos;
          
          return (
            <div key={alert.id} className="glass card-hover" style={{ 
                borderRadius: 20, padding: "24px", 
                borderLeft: `6px solid ${isApproved ? C.sweet : z.color}`,
                background: isApproved ? C.sweet + "08" : "transparent"
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <Tag color={z.color} small>
                      <SarthiIcon name={z.icon} size={12} style={{ marginRight: 6 }} />
                      {z.label.toUpperCase()}
                    </Tag>
                    {/* ENHANCED CONFIDENCE SCORE BAR */}
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div style={{ width: 80, height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
                            <div style={{ height: "100%", width: alert.confidence + "%", background: confColor }} />
                        </div>
                        <span style={{ fontFamily: "'DM Mono'", fontSize: 11, fontWeight: 700, color: confColor }}>
                            {alert.confidence}% {i18n[lang].confidence}
                        </span>
                    </div>
                  </div>
                  <h3 style={{ fontSize: 18, fontWeight: 600, color: C.text, marginTop: 12, marginBottom: 4 }}>{alert.sku}</h3>
                  <p style={{ fontSize: 14, color: C.muted, fontWeight: 400, lineHeight: 1.5 }}>{alert.msg}</p>
                </div>
                
                <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono'", letterSpacing: 1 }}>{i18n[lang].riskImpact}</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: z.color }}>{alert.risk}%</div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: C.text, marginTop: 4 }}>{alert.impact}</div>
                </div>
              </div>

              {!isApproved ? (
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                   <button 
                    onClick={() => setApproved(prev => [...prev, alert.id])}
                    style={{ 
                        flex: 1, background: z.color, color: C.bg, border: "none", 
                        borderRadius: 12, padding: "12px 24px", fontFamily: "'Sora'", 
                        fontWeight: 700, fontSize: 14, cursor: "pointer", transition: "all 0.2s" 
                    }}
                    onMouseEnter={e => e.currentTarget.style.opacity = 0.9}
                    onMouseLeave={e => e.currentTarget.style.opacity = 1}
                   >
                     {alert.action}
                   </button>
                   <button 
                    onClick={() => setDismissed(prev => [...prev, alert.id])}
                    style={{ 
                        background: "transparent", border: `1px solid ${C.border}`, color: C.muted, 
                        borderRadius: 12, padding: "12px 20px", fontSize: 14, cursor: "pointer" 
                    }}
                   >
                     {i18n[lang].dismiss}
                   </button>
                   <input 
                    placeholder={i18n[lang].refinementFeedbackPlaceholder}
                    onChange={e => setFeedback(prev => ({ ...prev, [alert.id]: e.target.value }))}
                    style={{ flex: 1, background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: "12px 20px", fontSize: 12, color: C.text, outline: "none" }}
                   />
                </div>
              ) : (
                <div style={{ 
                    background: C.sweet + "22", border: `1px solid ${C.sweet}44`, 
                    borderRadius: 12, padding: "14px 20px", display: "flex", 
                    justifyContent: "space-between", alignItems: "center" 
                }}>
                   <div style={{ color: C.sweet, fontWeight: 600, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
                     <SarthiIcon name="CheckCircle" size={16} /> {i18n[lang].autoApproved} · {i18n[lang].rlhfUpdated}
                   </div>
                   <div style={{ fontSize: 11, color: C.muted, fontFamily: "'DM Mono'" }}>
                     TXID: S-{alert.id}B47X
                   </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default Alerts;
