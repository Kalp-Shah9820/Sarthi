import { useCallback, useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { aisles, live, mbaRules, zoneInfo } from "../data/appData.js";
import { i18n } from "../data/i18n.js";
import { C } from "../theme.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { CustomTooltip, SectionLabel, Tag, SarthiIcon } from "../components/ui.jsx";

function StoreViz() {
  const { lang } = useSarthi();
  const [hovered, setHovered] = useState(null);
  const [camX, setCamX] = useState(0);
  const [camZ, setCamZ] = useState(0);
  const [animating, setAnimating] = useState(false);
  const frameRef = useRef(null);
  const camXRef = useRef(0);
  const camZRef = useRef(0);
  const targetX = useRef(0);
  const targetZ = useRef(0);

  // Smooth camera
  const animate = useCallback(() => {
    camXRef.current += (targetX.current - camXRef.current) * 0.08;
    camZRef.current += (targetZ.current - camZRef.current) * 0.08;
    setCamX(camXRef.current);
    setCamZ(camZRef.current);
    frameRef.current = requestAnimationFrame(animate);
  }, []);

  useEffect(() => {
    frameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameRef.current);
  }, [animate]);

  const walkTo = (aisle) => {
    targetX.current = (aisle.x - 1.5) * 60;
    targetZ.current = (aisle.y - 1) * 40;
    setHovered(aisle.id);
  };

  const hoveredAisle = aisles.find(a => a.id === hovered);

  // 3D perspective transform
  const perspective = 600;
  const tiltX = 25; // degrees
  const viewW = 520, viewH = 320;

  const project = (wx, wy) => {
    const cx = wx - camX * 0.3;
    const cy = wy - camZ * 0.3;
    return { px: viewW/2 + cx, py: viewH/2 - cy };
  };

  const CELL = 100, GAP = 18;
  const cols = 4, rows = 3;

  return (
    <div>
      <div style={{ marginBottom:32 }}>
        <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:3, color:C.muted, textTransform:"uppercase", marginBottom:10 }}>{i18n[lang].marketBasketTitle}</div>
        <h1 style={{ fontFamily:"'Sora'", fontSize:36, fontWeight:700, color:C.text, margin:0 }}>{i18n[lang].storeWalkthrough}</h1>
        <p style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted, marginTop:8, fontWeight:300 }}>{i18n[lang].storeWalkthroughDesc}</p>
      </div>

      {/* 3D Store view */}
      <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:16, padding:"28px", marginBottom:20, overflow:"hidden" }}>
        <SectionLabel>{i18n[lang].isometricView}</SectionLabel>

        <div style={{ position:"relative", height:380, overflow:"hidden", borderRadius:8, background:"#0a0a0a" }}>
          {/* Floor grid lines */}
          <svg style={{ position:"absolute", inset:0, width:"100%", height:"100%", pointerEvents:"none" }}>
            {[0,1,2,3,4,5].map(i => (
              <line key={`h${i}`} x1="0" y1={i*60+20} x2="100%" y2={i*60+20} stroke={C.border} strokeWidth={0.5} strokeDasharray="4 8" />
            ))}
            {[0,1,2,3,4,5,6,7].map(i => (
              <line key={`v${i}`} x1={i*80} y1="0" x2={i*80} y2="100%" stroke={C.border} strokeWidth={0.5} strokeDasharray="4 8" />
            ))}
            {/* Entrance */}
            <text x="50%" y="360" textAnchor="middle" fill={C.muted} fontSize="10" fontFamily="'DM Mono'" letterSpacing="4">{i18n[lang].entrance}</text>
            <rect x="42%" y="355" width="16%" height="1" fill={C.border} />
          </svg>

          {/* Connection lines */}
          <svg style={{ position:"absolute", inset:0, width:"100%", height:"100%", pointerEvents:"none" }}>
            {hoveredAisle && hoveredAisle.connections.map(cid => {
              const target = aisles.find(a => a.id === cid);
              if (!target) return null;
              const srcX = (hoveredAisle.x * 120) + 50 + 30;
              const srcY = (hoveredAisle.y * 110) + 40 + 30;
              const dstX = (target.x * 120) + 50 + 30;
              const dstY = (target.y * 110) + 40 + 30;
              return (
                <line key={cid} x1={srcX} y1={srcY} x2={dstX} y2={dstY}
                  stroke={zoneInfo[hoveredAisle.zone]?.color} strokeWidth={2} strokeDasharray="6 4" opacity={0.7}>
                  <animate attributeName="stroke-dashoffset" values="0;-20" dur="0.8s" repeatCount="indefinite" />
                </line>
              );
            })}
          </svg>

          {/* Aisle shelves */}
          {aisles.map(aisle => {
            const z = zoneInfo[aisle.zone];
            const isHovered = hovered === aisle.id;
            const isConnected = hoveredAisle?.connections.includes(aisle.id);
            const ax = aisle.x * 120 + 50;
            const ay = aisle.y * 110 + 40;

            return (
              <div key={aisle.id} onClick={() => walkTo(aisle)} style={{
                position:"absolute", left:ax, top:ay, width:88, height:68,
                cursor:"pointer", transition:"all 0.3s",
                transform: isHovered ? "scale(1.08) translateY(-4px)" : "scale(1)",
                opacity: hovered ? (isHovered || isConnected ? 1 : 0.4) : 1,
                zIndex: isHovered ? 10 : 1,
              }}>
                {/* Shelf top face */}
                <div style={{
                  width:"100%", height:"100%", borderRadius:8,
                  background: isHovered ? z.color+"33" : isConnected ? z.color+"18" : C.surface,
                  border:`2px solid ${isHovered ? z.color : isConnected ? z.color+"77" : C.borderLight}`,
                  display:"flex", flexDirection:"column", justifyContent:"space-between",
                  padding:"8px 10px", boxShadow: isHovered ? `0 12px 32px ${z.color}33` : "0 4px 12px #00000066",
                  transition:"all 0.3s"
                }}>
                  <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start" }}>
                    <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:z.color, fontWeight:600 }}>{aisle.id}</div>
                    {/* Heat dot */}
                    <div style={{ width:6, height:6, borderRadius:"50%", background:z.color, opacity:aisle.heat }} />
                  </div>
                  <div style={{ fontFamily:"'Inter'", fontSize:15, color: isHovered ? C.text : C.muted, lineHeight:1.3, fontWeight: isHovered ? 500 : 300 }}>
                    {i18n[lang][aisle.id.toLowerCase()] || aisle.label}
                  </div>
                  {/* Heat bar */}
                  <div style={{ height:2, background:C.border, borderRadius:1 }}>
                    <div style={{ height:"100%", width:(aisle.heat*100)+"%", background:z.color, borderRadius:1, transition:"width 0.3s" }} />
                  </div>
                </div>
                {/* 3D depth shadow */}
                <div style={{
                  position:"absolute", bottom:-6, left:4, right:4, height:6,
                  background: isHovered ? z.color+"44" : "#00000066",
                  borderRadius:"0 0 6px 6px", filter:"blur(4px)"
                }} />
              </div>
            );
          })}
        </div>

        {/* Zone legend */}
        <div style={{ display:"flex", gap:16, marginTop:16, flexWrap:"wrap" }}>
          {Object.entries(zoneInfo).map(([k,v]) => (
            <div key={k} style={{ display:"flex", alignItems:"center", gap:6 }}>
              <div style={{ width:10, height:10, borderRadius:3, background:v.color }} />
              <span style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>
                {i18n[lang][k + "Spot"] || i18n[lang][k + "Zone"] || i18n[lang][k + "Pit"] || v.label}
              </span>
            </div>
          ))}
          <SarthiIcon name="Info" size={14} style={{ color: C.muted, marginLeft: "auto", marginRight: 6 }} />
          <span style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{i18n[lang].heatBarDesc}</span>
        </div>
      </div>

      {/* Aisle info panel */}
      {hoveredAisle && (
        <div style={{ background:C.surface, border:`1px solid ${zoneInfo[hoveredAisle.zone]?.color}44`, borderRadius:12, padding:"24px" }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:16 }}>
            <div>
              <Tag color={zoneInfo[hoveredAisle.zone]?.color}>
                {hoveredAisle.id} · {i18n[lang][hoveredAisle.zone + "Spot"] || i18n[lang][hoveredAisle.zone + "Zone"] || i18n[lang][hoveredAisle.zone + "Pit"] || zoneInfo[hoveredAisle.zone]?.label}
              </Tag>
              <h3 style={{ fontFamily:"'Sora'", fontSize:22, color:C.text, margin:"10px 0 4px" }}>{i18n[lang][hoveredAisle.id.toLowerCase()] || hoveredAisle.label}</h3>
            </div>
            <div style={{ textAlign:"right" }}>
              <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{i18n[lang].purchaseFrequency}</div>
              <div style={{ fontFamily:"'Sora'", fontSize:28, color:zoneInfo[hoveredAisle.zone]?.color, fontWeight:600 }}>{Math.round(hoveredAisle.heat*100)}%</div>
            </div>
          </div>
          <div style={{ display:"flex", gap:12, flexWrap:"wrap", marginBottom:16 }}>
            {hoveredAisle.items.map(item => {
              const itemKey = item.toLowerCase().replace(/\s/g, "").replace(/-/g, "");
              return (
                <div key={item} style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:6, padding:"6px 12px", fontFamily:"'DM Mono'", fontSize:15, color:C.text }}>
                  {i18n[lang][itemKey] || item}
                </div>
              );
            })}
          </div>
          {hoveredAisle.connections.length > 0 && (
            <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted, fontWeight:300 }}>
              <span style={{ color:zoneInfo[hoveredAisle.zone]?.color }}>{i18n[lang].coPurchaseSignal}</span> {i18n[lang].customersVisiting}{" "}
              <strong style={{ color:C.text }}>{i18n[lang][hoveredAisle.id.toLowerCase()] || hoveredAisle.label}</strong> {i18n[lang].frequentlyAlsoVisit}{" "}
              <strong style={{ color:zoneInfo[hoveredAisle.zone]?.color }}>
                {hoveredAisle.connections.map(c => i18n[lang][c.toLowerCase()] || aisles.find(a=>a.id===c)?.label).join(` ${lang === "HI" ? "और" : "and"} `)}
              </strong>.{" "}
              {i18n[lang].considerCrossPromotion}
            </div>
          )}
        </div>
      )}

      {/* Co-purchase bar chart */}
      <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:12, padding:"28px", marginTop:16 }}>
        <SectionLabel>{i18n[lang].topCoPurchasePairs}</SectionLabel>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={(live.coPurchasePairs ?? null)?.map(p => ({ pair: `${i18n[lang][p.from]} → ${i18n[lang][p.to]}`, strength: p.strength })) ?? [
            { pair: `${i18n[lang].a} → ${i18n[lang].b}`,      strength:84 },
            { pair: `${i18n[lang].c} → ${i18n[lang].d}`,  strength:78 },
            { pair: `${i18n[lang].e} → ${i18n[lang].b}`,    strength:72 },
            { pair: `${i18n[lang].f} → ${i18n[lang].e}`,   strength:67 },
            { pair: `${i18n[lang].a} → ${i18n[lang].d}`,   strength:61 },
            { pair: `${i18n[lang].c} → ${i18n[lang].g}`,   strength:55 },
            { pair: `${i18n[lang].i} → ${i18n[lang].f}`,      strength:48 },
          ]} layout="vertical" margin={{ left:10, right:20, top:0, bottom:0 }}>
            <CartesianGrid stroke={C.border} strokeDasharray="4 4" horizontal={false} />
            <XAxis type="number" domain={[0,100]} tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:13 }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="pair" tick={{ fill:C.text, fontFamily:"'DM Mono'", fontSize:13 }} axisLine={false} tickLine={false} width={240} />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="strength" fill={C.ghost} radius={[0,4,4,0]} name={i18n[lang].coPurchasePct} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}


export default StoreViz;
