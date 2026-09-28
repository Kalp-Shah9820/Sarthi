import { useState, useRef, useEffect } from "react";
import { cannibalization, distributors, mbaRules, skuData } from "../data/appData.js";
import { C } from "../theme.js";
import { SectionLabel, Tag, SarthiIcon } from "../components/ui.jsx";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";

// ─── SHARED CONTEXT STORE (visible inter-agent state) ────────────────────────
const sharedContext = [
  { key:"lead_time_risk", value:"HIGH", agentKey:"macroSentinel", color:C.chaos, updated:"14:32:01" },
  { key:"demand_signal", value:"STABLE",  agentKey:"forecaster", color:C.sweet, updated:"14:31:45" },
  { key:"snacks_cannibalization", value:"-0.72", agentKey:"demandIntel", color:C.money, updated:"14:30:12" },
  { key:"par_total", value:"₹113.7K", agentKey:"cfoAgent", color:C.chaos, updated:"14:28:30" },
  { key:"esg_preference", value:"RAIL_BALANCED", agentKey:"esgGuardian", color:C.sweet, updated:"14:27:15" },
  { key:"supplier_primary", value:"Reliance Metro", agentKey:"negotiator", color:C.ghost, updated:"14:25:00" },
  { key:"ghost_transfer_ready", value:"TRUE", agentKey:"overstockResolver", color:C.sweet, updated:"14:24:10" },
  { key:"rlhf_weight_update", value:"+0.04", agentKey:"rlhfArbiter", color:C.money, updated:"14:22:30" },
];

function Intelligence() {
  const { strategy, updateStrategy, lang } = useSarthi();
  const [query, setQuery] = useState("");
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);
  const [chat, setChat] = useState([
    { role:"bot", text: i18n[lang].intelIntro }
  ]);

  const langMap = { 
    EN: "en-IN", HI: "hi-IN", BN: "bn-IN", 
    TA: "ta-IN", TE: "te-IN", MR: "mr-IN" 
  };

  useEffect(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      const rec = new SpeechRecognition();
      rec.lang = langMap[lang] || "en-IN";
      rec.continuous = false;
      rec.interimResults = true;

      rec.onresult = (e) => {
        const transcript = Array.from(e.results)
          .map((r) => r[0].transcript)
          .join("");
        setQuery(transcript);
      };

      rec.onend = () => setListening(false);
      rec.onerror = () => setListening(false);
      recognitionRef.current = rec;
    }
  }, [lang]);

  const toggleListen = () => {
    if (listening) {
      recognitionRef.current?.stop();
    } else {
      recognitionRef.current?.start();
      setListening(true);
    }
  };

  // XAI Audit trail
  const auditTrailData = [
    { ts:"14:32:01", agentKey:"forecaster", key: 1, params: { sku: i18n[lang].sku003 } },
    { ts:"14:31:45", agentKey:"negotiator", key: 2, params: { sku: i18n[lang].sku010, dist: i18n[lang].reliance } },
    { ts:"14:30:12", agentKey:"monteCarloRiskEngine", key: 3, params: {} },
    { ts:"14:28:30", agentKey:"cfoAgent", key: 4, params: {} },
    { ts:"14:27:15", agentKey:"esgGuardian", key: 5, params: { sku: i18n[lang].sku005 } },
    { ts:"14:25:00", agentKey:"rlhfArbiter", key: 6, params: { sku: i18n[lang].sku002 } },
  ];

  const ask = () => {
    if(!query.trim()) return;
    const userMsg = { role:"user", text:query };
    setChat(prev => [...prev, userMsg]);

    setTimeout(() => {
      let response = "";
      const q = query.toLowerCase();
      const lq = query.toLowerCase(); // Added for the instruction's use of lq

      if (q.includes("hi") || q.includes("hello") || q.includes("namaste") || q.includes("नमस्ते")) {
        response = i18n[lang].intelGreetings;
      } else if (q.includes("prioritize") || q.includes("strategy") || q.includes("mode") || q.includes("प्राथमिकता") || q.includes("रणनीति")) {
        if (q.includes("cash") || q.includes("savings") || q.includes("कैश") || q.includes("बचत")) {
          updateStrategy({ mode: "Cash Flow", savingsPriority: 0.9, safetyStockMultiplier: 0.8 });
          response = i18n[lang].strategyUpdatedCash;
        } else if (lq.includes("growth") || lq.includes("aggressive") || lq.includes("विकास")) {
          response = i18n[lang].strategyUpdatedGrowth;
          updateStrategy({ mode: "Growth", savingsPriority: 0.2, safetyStockMultiplier: 1.5, leadTimeBuffer: 1.5 });
        } else {
          response = i18n[lang].currentStrategyWeights
            .replace("{mode}", strategy.mode)
            .replace("{savings}", strategy.savingsPriority)
            .replace("{safety}", strategy.safetyStockMultiplier);
        }
      } else if (q.includes("supplier") || q.includes("distributor") || q.includes("आपूर्तिकर्ता") || q.includes("वितरक")) {
        const list = distributors.map(d => `${d.name} (${d.tier}, ${(d.fulfillment*100).toFixed(0)}% reliability, ${d.avgTAT}d avg TAT)`).join("; ");
        response = i18n[lang].supplierTrack.replace("{count}", distributors.length).replace("{list}", list);
      } else if (q.includes("basket") || q.includes("mba") || q.includes("co-purchase") || q.includes("बास्केट") || q.includes("सह-खरीদ")) {
        response = i18n[lang].basketRules
          .replace("{count}", mbaRules.length)
          .replace("{rule}", "{Amul Butter, Maggi} leads to Parle-G")
          .replace("{conf}", 81)
          .replace("{lift}", 3.1)
          .replace("{cannibalCount}", cannibalization.length);
      } else if (q.includes("bullwhip") || q.includes("smooth") || q.includes("बुलव्हिप")) {
        response = i18n[lang].bullwhipDampening;
      } else if (q.includes("monte carlo") || q.includes("simulation") || q.includes("सिमुलेशन")) {
        const highRisk = skuData.filter(s => s.risk > 60);
        const list = highRisk.map(s => `${s.name} (${s.risk}%)`).join(", ");
        response = i18n[lang].mcSimResults.replace("{count}", highRisk.length).replace("{list}", list);
      } else if (q.includes("sku") || q.includes("product") || q.includes("item") || q.includes("उत्पाद")) {
        if (q.includes("risk") || q.includes("high") || q.includes("critical") || q.includes("जोखिम")) {
          const highRisk = skuData.filter(s => s.risk > 60).map(s => `${s.name} (${s.risk}%)`).join(", ");
          const parSum = (skuData.filter(s=>s.risk>60).reduce((a,s)=>a+s.par,0)/1000).toFixed(0);
          response = i18n[lang].criticalSkuRiskRes.replace("{list}", highRisk).replace("{par}", parSum);
        } else if (q.includes("best") || q.includes("sweet") || q.includes("अच्छा")) {
          const sweet = skuData.filter(s => s.zone === "sweet").map(s => s.name).join(", ");
          response = i18n[lang].sweetSpotRes.replace("{list}", sweet).replace("{count}", skuData.filter(s=>s.zone==="sweet").length);
        } else {
          response = i18n[lang].skuZoneSummary
            .replace("{count}", skuData.length)
            .replace("{sweet}", skuData.filter(s=>s.zone==="sweet").length)
            .replace("{chaos}", skuData.filter(s=>s.zone==="chaos").length)
            .replace("{ghost}", skuData.filter(s=>s.zone==="ghost").length)
            .replace("{money}", skuData.filter(s=>s.zone==="money").length);
        }
      } else if (q.includes("stock") || q.includes("inventory") || q.includes("स्टॉक") || q.includes("इन्वेंट्री")) {
        const totalStock = skuData.reduce((acc, s) => acc + s.stock, 0);
        const ghostStock = skuData.filter(s=>s.zone==="ghost").reduce((a,s)=>a+s.stock,0);
        const parSum = (skuData.reduce((a,s)=>a+s.par,0)/1000).toFixed(0);
        response = i18n[lang].inventoryStockSummary
          .replace("{total}", totalStock)
          .replace("{count}", skuData.length)
          .replace("{ghostStock}", ghostStock)
          .replace("{ghostPct}", Math.round(ghostStock/totalStock*100))
          .replace("{par}", parSum);
      } else if (q.includes("zone") || q.includes("spot") || q.includes("pit") || q.includes("chaos") || q.includes("ghost") || q.includes("जोन")) {
        response = i18n[lang].zoneIkigaiDesc;
      } else {
        response = i18n[lang].intelDefaultPrompt;
      }

      setChat(prev => [...prev, { role:"bot", text:response }]);
    }, 600);
    setQuery("");
  };

  return (
    <div>
      <div style={{ marginBottom:48 }}>
        <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:3, color:C.muted, textTransform:"uppercase", marginBottom:12 }}>
          {i18n[lang].naturalLanguageInterface}
        </div>
        <h1 style={{ fontFamily:"'Sora'", fontSize:48, fontWeight:700, color:C.text, margin:0, letterSpacing:"-0.03em" }}>
          {i18n[lang].intelligence}
        </h1>
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 340px", gap:20 }}>
        {/* Chat */}
        <div className="glass" style={{ borderRadius:24, height:500, display:"flex", flexDirection:"column", overflow:"hidden" }}>
          <div style={{ flex:1, padding:32, overflowY:"auto", display:"flex", flexDirection:"column", gap:20 }}>
            {chat.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role==='user' ? 'flex-end' : 'flex-start',
                maxWidth:'80%',
                padding:'14px 20px',
                borderRadius:18,
                background: m.role==='user' ? C.text : C.faint,
                color: m.role==='user' ? C.bg : C.text,
                fontFamily:"'Inter'",
                fontSize:14,
                lineHeight:1.5,
                border: m.role==='bot' ? `1px solid ${C.border}` : 'none'
              }}>
                {m.text}
              </div>
            ))}
          </div>

          <div style={{ padding:24, borderTop:`1px solid ${C.border}`, display:"flex", gap:12 }}>
            <div style={{ flex:1, position:"relative", display:"flex", alignItems:"center" }}>
              <input
                value={query}
                onChange={e=>setQuery(e.target.value)}
                onKeyDown={e=>e.key==='Enter' && ask()}
                placeholder={i18n[lang].queryIntelligencePlaceholder}
                style={{ width:"100%", background:C.surface, border:`1px solid ${C.border}`, borderRadius:12, padding:"14px 50px 14px 20px", color:C.text, fontFamily:"'Inter'", fontSize:14, outline:"none" }}
              />
              <button 
                onClick={toggleListen}
                style={{
                  position:"absolute", right:12, background:"transparent", border:"none", 
                  cursor:"pointer", color: listening ? C.chaos : C.muted,
                  transition:"all 0.3s", display:"flex", alignItems:"center", justifyContent:"center",
                  padding:8, borderRadius:8
                }}
              >
                <SarthiIcon name="Mic" size={20} />
              </button>
            </div>
            <button onClick={ask} style={{ background:C.text, color:C.bg, border:"none", borderRadius:12, padding:"0 24px", fontFamily:"'Inter'", fontWeight:700, cursor:"pointer" }}>{i18n[lang].askBtn}</button>
          </div>
        </div>

        {/* Right sidebar: XAI + Shared Context */}
        <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
          {/* XAI Audit Trail */}
          <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:16, padding:"20px", height:260, overflowY:"auto" }}>
            <SectionLabel>{i18n[lang].xaiAuditTrail}</SectionLabel>
            <div style={{ fontFamily:"'Inter'", fontSize:12, color:C.muted, marginBottom:12, fontWeight:300 }}>
              {i18n[lang].xaiAuditDesc}
            </div>
            {auditTrailData.map((entry, i) => {
              const action = i18n[lang][`auditTrail_${entry.key}_action`] || "";
              const result = i18n[lang][`auditTrail_${entry.key}_result`] || "";
              const finalAction = Object.entries(entry.params).reduce((str, [k, v]) => str.replace(`{${k}}`, v), action);
              return (
                <div key={i} style={{ borderLeft:`2px solid ${C.accent}44`, paddingLeft:12, marginBottom:12 }}>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:11, color:C.accent, marginBottom:3 }}>{entry.ts} · {i18n[lang][entry.agentKey] || entry.agentKey}</div>
                  <div style={{ fontFamily:"'Inter'", fontSize:12, color:C.text, lineHeight:1.4, marginBottom:2 }}>{finalAction}</div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:11, color:C.muted }}>{result}</div>
                </div>
              );
            })}
          </div>

          {/* Shared Context Store */}
          <div style={{ background:C.surface, border:`1px solid ${C.border}`, borderRadius:16, padding:"20px", height:228, overflowY:"auto" }}>
            <SectionLabel>{i18n[lang].sharedContextStore}</SectionLabel>
            <div style={{ fontFamily:"'Inter'", fontSize:11, color:C.muted, marginBottom:10, fontWeight:300 }}>
              {i18n[lang].sharedContextDesc}
            </div>
            {sharedContext.map((ctx, i) => (
              <div key={i} style={{
                display:"flex", justifyContent:"space-between", alignItems:"center",
                padding:"6px 0", borderBottom:`1px solid ${C.border}22`
              }}>
                <div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:11, color:C.text }}>{i18n[lang][ctx.key] || ctx.key}</div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:10, color:C.muted }}>{i18n[lang][ctx.agentKey] || ctx.agentKey} · {ctx.updated}</div>
                </div>
                <span style={{
                  fontFamily:"'DM Mono'", fontSize:11, fontWeight:600, color:ctx.color,
                  background:ctx.color+"18", borderRadius:4, padding:"2px 8px"
                }}>{i18n[lang][ctx.value.toLowerCase()] || ctx.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}


export default Intelligence;
