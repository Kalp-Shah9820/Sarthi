import { useEffect, useState } from "react";
import { CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { skuData, zoneInfo, live } from "../data/appData.js";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { C } from "../theme.js";
import { SectionLabel, Tag, CustomTooltip, SarthiIcon } from "../components/ui.jsx";
import { Play, Pause } from "lucide-react";

// ─── IKIGAI DRIFT DATA (zone migration over 7 days) ─────────────────────────
const driftDays = ["1","2","3","4","5","6","7"];
const driftData = [
  { id:"SKU001", zones:["sweet","sweet","sweet","sweet","sweet","sweet","sweet"] },
  { id:"SKU002", zones:["ghost","ghost","ghost","ghost","money","money","money"] },
  { id:"SKU003", zones:["sweet","sweet","chaos","chaos","chaos","chaos","chaos"] },
  { id:"SKU004", zones:["money","money","money","money","money","ghost","ghost"] },
  { id:"SKU005", zones:["sweet","sweet","sweet","sweet","sweet","sweet","sweet"] },
  { id:"SKU006", zones:["sweet","chaos","chaos","chaos","chaos","chaos","chaos"] },
  { id:"SKU007", zones:["money","money","money","money","money","money","ghost"] },
  { id:"SKU008", zones:["sweet","sweet","sweet","sweet","sweet","sweet","sweet"] },
  { id:"SKU009", zones:["sweet","sweet","sweet","sweet","sweet","sweet","sweet"] },
  { id:"SKU010", zones:["chaos","chaos","chaos","chaos","chaos","chaos","chaos"] },
];

function Inventory({ setPage, setSku }) {
  const { lang } = useSarthi();
  const [filter, setFilter] = useState("all");
  const [driftDay, setDriftDay] = useState(6);
  const [driftPlaying, setDriftPlaying] = useState(false);
  const filtered = filter === "all" ? skuData : skuData.filter(d => d.zone === filter);

  // Auto-play drift time-lapse
  useEffect(() => {
    if (!driftPlaying) return;
    if (driftDay >= 6) { setDriftDay(0); }
    const t = setInterval(() => {
      setDriftDay(d => {
        if (d >= 6) { setDriftPlaying(false); return 6; }
        return d + 1;
      });
    }, 800);
    return () => clearInterval(t);
  }, [driftPlaying]);

  // Zone scatter data: velocity vs margin
  const scatterData = skuData.map(d => ({ x:d.vel, y:d.margin, name: i18n[lang][d.id.toLowerCase()] || d.name.split(" ")[0], zone:d.zone, risk:d.risk }));

  return (
    <div>
      <div style={{ marginBottom:48 }}>
        <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:2, color:C.muted, textTransform:"uppercase", marginBottom:12 }}>{i18n[lang].intelligence}</div>
        <h1 style={{ fontFamily:"'Sora'", fontSize:48, fontWeight:700, color:C.text, margin:0, letterSpacing:"-0.03em" }}>{i18n[lang].inventoryHealth}</h1>
      </div>

      {/* Velocity vs Margin scatter */}
      <div className="glass" style={{ borderRadius:20, padding:"32px", marginBottom:32 }}>
        <SectionLabel>{i18n[lang].velocityVsMargin}</SectionLabel>
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart margin={{ top:10, right:20, bottom:10, left:0 }}>
            <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
            <XAxis dataKey="x" name={i18n[lang].velocity} tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} label={{ value: i18n[lang].velocity + " →", position:"insideBottomRight", fill:C.muted, fontSize:15, fontFamily:"'DM Mono'" }} />
            <YAxis dataKey="y" name={i18n[lang].margin} tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} label={{ value: i18n[lang].margin + " ↑", angle:-90, position:"insideLeft", fill:C.muted, fontSize:15, fontFamily:"'DM Mono'" }} />
            <ReferenceLine x={40} stroke={C.borderLight} strokeDasharray="3 3" />
            <ReferenceLine y={20} stroke={C.borderLight} strokeDasharray="3 3" />
            <Tooltip cursor={{ stroke:C.border }} content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0]?.payload;
              return (
                <div className="glass" style={{ borderRadius:12, padding:"12px 16px", border:`1px solid ${C.borderLight}` }}>
                  <div style={{ color:zoneInfo[d?.zone]?.color, fontFamily:"'Sora'", fontSize:16, fontWeight:600 }}>{d?.name}</div>
                  <div style={{ color:C.text, fontFamily:"'Inter'", fontSize:15, marginTop:6 }}>{i18n[lang].velocity}: <span style={{color:C.sweet}}>{d?.x}/day</span></div>
                  <div style={{ color:C.text, fontFamily:"'Inter'", fontSize:15 }}>{i18n[lang].margin}: <span style={{color:C.ghost}}>{d?.y}%</span></div>
                  <div style={{ color:C.muted, fontFamily:"'DM Mono'", fontSize:15, marginTop:8, textTransform:"uppercase" }}>{i18n[lang].risk}: {d?.risk}%</div>
                </div>
              );
            }} />
            <Scatter data={scatterData} fill={C.muted}>
              {scatterData.map((entry, i) => (
                <Cell key={i} fill={zoneInfo[entry.zone]?.color} fillOpacity={0.8} strokeWidth={2} stroke={C.surface} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
        <div style={{ display:"flex", gap:16, justifyContent:"flex-end", marginTop:8 }}>
          {Object.entries(zoneInfo).map(([k,v]) => (
            <div key={k} style={{ display:"flex", alignItems:"center", gap:5 }}>
              <div style={{ width:8, height:8, borderRadius:"50%", background:v.color }} />
              <span style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{v.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── IKIGAI DRIFT TIME-LAPSE ── */}
      <div className="glass" style={{ borderRadius:20, padding:"32px", marginBottom:32 }}>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:20 }}>
          <SectionLabel>{i18n[lang].ikigaiDriftTitle}</SectionLabel>
          <div style={{ display:"flex", alignItems:"center", gap:12 }}>
            <button onClick={() => { setDriftDay(0); setDriftPlaying(true); }} style={{
              background:C.sweet+"22", color:C.sweet, border:`1px solid ${C.sweet}44`,
              borderRadius:8, padding:"8px 18px", fontFamily:"'DM Mono'", fontSize:12,
              cursor:"pointer", transition:"all 0.2s"
            }}>
              {driftPlaying ? i18n[lang].playing : i18n[lang].play}
              <SarthiIcon name={driftPlaying ? "Pause" : "Play"} size={12} style={{ marginLeft: 6 }} />
            </button>
            <span style={{ fontFamily:"'DM Mono'", fontSize:13, color:C.accent, fontWeight:600 }}>
              {i18n[lang].daysStock} {driftDays[driftDay]}
            </span>
          </div>
        </div>
        {/* Day slider */}
        <input type="range" min={0} max={6} value={driftDay} onChange={e => { setDriftPlaying(false); setDriftDay(Number(e.target.value)); }}
          style={{ width:"100%", accentColor:C.accent, marginBottom:16 }} />
        <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:11, color:C.muted, marginBottom:16 }}>
          {driftDays.map(d => <span key={d}>{d}</span>)}
        </div>
        {/* SKU zone strips */}
        <div style={{ display:"flex", flexDirection:"column", gap:6 }}>
          {(live.drift?.data ?? driftData).map(sku => {
            const currentZone = sku.zones[driftDay];
            const prevZone = driftDay > 0 ? sku.zones[driftDay - 1] : currentZone;
            const migrated = currentZone !== prevZone;
            const z = zoneInfo[currentZone];
            return (
              <div key={sku.id} style={{
                display:"flex", alignItems:"center", gap:12,
                background: migrated ? z.color+"12" : "transparent",
                border:`1px solid ${migrated ? z.color+"44" : C.border}`,
                borderRadius:8, padding:"8px 14px", transition:"all 0.4s"
              }}>
                <div style={{ fontFamily:"'Inter'", fontSize:13, color:C.text, fontWeight:500, width:120 }}>{i18n[lang][sku.id.toLowerCase()]}</div>
                {/* Zone history dots */}
                <div style={{ display:"flex", gap:4, flex:1 }}>
                  {sku.zones.map((zone, di) => (
                    <div key={di} style={{
                      width:20, height:20, borderRadius:4,
                      background: di <= driftDay ? zoneInfo[zone].color+"44" : C.border,
                      border: di === driftDay ? `2px solid ${zoneInfo[zone].color}` : "1px solid transparent",
                      transition:"all 0.3s",
                      display:"flex", alignItems:"center", justifyContent:"center"
                    }}>
                      {di === driftDay && <div style={{ width:6, height:6, borderRadius:"50%", background:zoneInfo[zone].color }} />}
                    </div>
                  ))}
                </div>
                <Tag color={z.color} small>
                  <SarthiIcon name={z.icon} size={12} style={{ marginRight: 6 }} />
                  {z.label}
                </Tag>
                {migrated && <span style={{ fontFamily:"'DM Mono'", fontSize:10, color:C.chaos, fontWeight:600 }}>{i18n[lang].migrated}</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Filter */}
      <div style={{ display:"flex", gap:8, marginBottom:20, flexWrap:"wrap" }}>
        {[
            ["all", i18n[lang].allZones],
            ["sweet", i18n[lang].sweetSpot],
            ["chaos", i18n[lang].chaosZone],
            ["ghost", i18n[lang].ghostZone],
            ["money", i18n[lang].moneyPit]
        ].map(([key, label]) => {
          const col = key === "all" ? C.text : zoneInfo[key]?.color;
          const zoneKey = key === "all" ? null : key;
          const tagline = zoneKey ? i18n[lang][zoneKey + "Tagline"] : "";
          const desc = zoneKey ? i18n[lang][zoneKey + "Desc"] : "";

          return (
            <button key={key} onClick={() => setFilter(key)} 
              title={tagline ? `${tagline}: ${desc}` : ""}
              style={{
                background: filter===key ? col+"18" : "transparent",
                border:`1px solid ${filter===key ? col : C.border}`,
                color: filter===key ? col : C.muted,
                borderRadius:6, padding:"6px 14px", fontFamily:"'DM Mono'", fontSize:15, cursor:"pointer", transition:"all 0.2s"
              }}>{label}</button>
          );
        })}
      </div>

      {/* SKU table */}
      <div className="glass" style={{ borderRadius:20, overflow:"hidden" }}>
        <div style={{ display:"grid", gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 0.8fr 0.8fr 1fr", padding:"20px 24px", background:C.surface, borderBottom:`1px solid ${C.border}` }}>
          {[i18n[lang].sku, i18n[lang].stock, i18n[lang].velocity, i18n[lang].margin, i18n[lang].risk, i18n[lang].lead, i18n[lang].hold, i18n[lang].zone].map(h => (
            <div key={h} style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, textTransform:"uppercase", letterSpacing:2, fontWeight:500 }}>{h}</div>
          ))}
        </div>
        {filtered.map((sku, i) => {
          const z = zoneInfo[sku.zone];
          const trendColor = sku.velocityTrend > 0 ? C.sweet : sku.velocityTrend < -0.05 ? C.chaos : C.muted;
          return (
            <div key={sku.id} onClick={() => { setSku(sku); }} style={{
              display:"grid", gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 0.8fr 0.8fr 1fr",
              padding:"24px 24px", borderBottom: i<filtered.length-1 ? `1px solid ${C.border}` : "none",
              cursor:"pointer", transition:"all 0.2s", alignItems:"center"
            }}
              onMouseEnter={e=>e.currentTarget.style.background=C.surface}
              onMouseLeave={e=>e.currentTarget.style.background="transparent"}
            >
              <div>
                <div style={{ fontFamily:"'Sora'", fontSize:15, color:C.text, fontWeight:600 }}>{i18n[lang][sku.id.toLowerCase()] || sku.name}</div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginTop:4 }}>{i18n[lang]["category" + sku.cat.replace(/\s/g, "")] || sku.cat}</div>
              </div>
              <div>
                <div style={{ fontFamily:"'Inter'", fontSize:16, color:C.text, fontWeight:500 }}>{sku.stock}</div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{i18n[lang].safeStockLabel} {sku.safetyStock}</div>
              </div>
              <div>
                <div style={{ fontFamily:"'Inter'", fontSize:16, color:C.sweet, fontWeight:500 }}>{sku.vel}/d</div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:trendColor }}>{sku.velocityTrend > 0 ? "+" : ""}{(sku.velocityTrend*100).toFixed(0)}%</div>
              </div>
              <div style={{ fontFamily:"'Inter'", fontSize:16, color:C.ghost, fontWeight:500 }}>{sku.margin}%</div>
              <div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:16, color: sku.risk>60 ? C.chaos : sku.risk>30 ? C.money : C.sweet, fontWeight:600 }}>{sku.risk}%</div>
                <div style={{ height:3, background:C.border, borderRadius:2, marginTop:6, width:60 }}>
                  <div style={{ height:"100%", width:sku.risk+"%", background: sku.risk>60 ? C.chaos : sku.risk>30 ? C.money : C.sweet, borderRadius:2 }} />
                </div>
              </div>
              <div style={{ fontFamily:"'DM Mono'", fontSize:15, color: sku.lead > 7 ? C.chaos : C.muted }}>{sku.lead}d</div>
              <div style={{ fontFamily:"'DM Mono'", fontSize:15, color: sku.holdingCostPct > 0.04 ? C.money : C.muted }}>{(sku.holdingCostPct*100).toFixed(1)}%</div>
              <Tag color={z.color} small>
                <SarthiIcon name={z.icon} size={12} style={{ marginRight: 6 }} />
                {sku.zone === "sweet" ? i18n[lang].sweetSpot : sku.zone === "chaos" ? i18n[lang].chaosZone : sku.zone === "money" ? i18n[lang].moneyPit : i18n[lang].ghostZone}
              </Tag>
            </div>
          );
        })}
      </div>
    </div>
  );
}


export default Inventory;
