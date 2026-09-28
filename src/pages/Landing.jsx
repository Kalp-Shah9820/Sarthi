import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { C } from "../theme.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import { EyeOff, Dices, Handshake, TrendingDown, Map, Brain, MousePointer2, ArrowRight } from "lucide-react";

function Landing({ setPage }) {
  const { lang } = useSarthi();
  const [visible, setVisible] = useState(false);
  useEffect(() => { setTimeout(() => setVisible(true), 100); }, []);

  const features = [
    { icon:EyeOff, title:i18n[lang].featurePhantom,  desc:i18n[lang].descPhantom },
    { icon:Dices, title:i18n[lang].featureMonteCarlo,   desc:i18n[lang].descMonteCarlo },
    { icon:Handshake, title:i18n[lang].featureNegotiator,     desc:i18n[lang].descNegotiator },
    { icon:TrendingDown, title:i18n[lang].featureProfitRisk,    desc:i18n[lang].descProfitRisk },
    { icon:Map, title:i18n[lang].featureLayout, desc:i18n[lang].descLayout },
    { icon:Brain, title:i18n[lang].featureRLHF,        desc:i18n[lang].descRLHF },
  ];

  const stats = [
    { value:"8", unit:i18n[lang].statsParallelAgents, desc:i18n[lang].statsDescAgents },
    { value:"1K+", unit:i18n[lang].statsSimulations, desc:i18n[lang].statsDescSims },
    { value:"99.8%", unit:i18n[lang].statsUptime, desc:i18n[lang].statsDescUptime },
    { value:"0", unit:i18n[lang].statsHumanTouch, desc:i18n[lang].statsDescHuman },
  ];

  return (
    <div style={{ minHeight:"100vh", background:C.bg }}>
      {/* Hero */}
      <div style={{
        minHeight:"100vh", display:"flex", alignItems:"center",
        padding:"0 80px", position:"relative", overflow:"hidden",
        opacity: visible ? 1 : 0, transition:"opacity 1s ease"
      }}>
        {/* Background grid */}
        <div style={{
          position:"absolute", inset:0, pointerEvents:"none",
          backgroundImage:`linear-gradient(${C.border} 1px, transparent 1px), linear-gradient(90deg, ${C.border} 1px, transparent 1px)`,
          backgroundSize:"60px 60px", opacity:0.3
        }} />
        {/* Gradient fade */}
        <div style={{ position:"absolute", inset:0, background:`radial-gradient(ellipse at 30% 50%, #1a1a0e22 0%, transparent 70%)`, pointerEvents:"none" }} />

        {/* Two-column layout */}
        <div style={{ position:"relative", width:"100%", display:"flex", alignItems:"center", justifyContent:"space-between", gap:40 }}>

          {/* LEFT: Text content */}
          <div style={{ flex:"0 0 auto", maxWidth:560 }}>
            <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:4, color:C.muted, textTransform:"uppercase", marginBottom:28 }}>
              {i18n[lang].sarthiVersionTag}
            </div>
            <h1 style={{
              fontFamily:"'Sora'", fontSize:"clamp(44px, 5.5vw, 76px)", fontWeight:700,
              color:C.text, lineHeight:1.05, margin:"0 0 12px", letterSpacing:"-0.03em"
            }}>
              {i18n[lang].heroTitlePart1}<br /><em style={{ color:C.accent, fontStyle:"italic" }}>{i18n[lang].heroTitlePart2}</em>
            </h1>
            <p style={{ fontFamily:"'Inter'", fontSize:18, color:C.text, lineHeight:1.7, maxWidth:480, margin:"24px 0 48px", fontWeight:400 }}>
              {i18n[lang].heroSubtitle}
            </p>

            <div style={{ display:"flex", gap:14 }}>
              <Link to="/dashboard" style={{
                background:C.text, color:C.bg, border:"none", borderRadius:8,
                padding:"14px 32px", fontFamily:"'Inter'", fontWeight:600, fontSize:15,
                cursor:"pointer", transition:"all 0.3s", textDecoration:"none", boxShadow:`0 10px 20px ${C.bg}`,
                display:"flex", alignItems:"center", gap:8
              }} onMouseEnter={e=>{e.target.style.transform="translateY(-2px)"; e.target.style.opacity=0.9}} onMouseLeave={e=>{e.target.style.transform="translateY(0)"; e.target.style.opacity=1}}>
                {i18n[lang].enterCommand}
                <ArrowRight size={18} />
              </Link>
              <Link to="/inventory" style={{
                background:"transparent", color:C.muted, border:`1px solid ${C.border}`,
                borderRadius:8, padding:"14px 32px", fontFamily:"'Inter'", fontWeight:400, fontSize:15, cursor:"pointer", textDecoration:"none", transition:"all 0.3s"
              }} onMouseEnter={e=>e.target.style.borderColor=C.muted} onMouseLeave={e=>e.target.style.borderColor=C.border}>
                {i18n[lang].viewInventory}
              </Link>
            </div>
          </div>

          {/* RIGHT: Sarthi Logo */}
          <div style={{ flex:1, display:"flex", alignItems:"center", justifyContent:"center", position:"relative", minHeight:600 }}>
            {/* Outer glow ring */}
            <div style={{
              position:"absolute", width:600, height:600, borderRadius:"50%",
              background:"radial-gradient(circle, rgba(80,140,255,0.1) 0%, transparent 70%)",
              animation:"pulse 3s ease-in-out infinite",
            }} />
            {/* Mid glow ring */}
            <div style={{
              position:"absolute", width:520, height:520, borderRadius:"50%",
              border:"1px solid rgba(80,140,255,0.15)",
              animation:"spin 20s linear infinite",
            }} />
            {/* Inner glow ring */}
            <div style={{
              position:"absolute", width:440, height:440, borderRadius:"50%",
              border:"1px dashed rgba(80,140,255,0.2)",
              animation:"spin 12s linear infinite reverse",
            }} />
            {/* Logo image */}
            <div style={{
              position:"relative", zIndex:2,
              filter:"drop-shadow(0 0 60px rgba(80,140,255,0.4)) drop-shadow(0 0 100px rgba(80,100,255,0.2))",
              animation:"float 4s ease-in-out infinite",
            }}>
              <img
                src="/sarthi-logo.png"
                alt="Sarthi Logo"
                style={{ width:400, height:"auto", display:"block", borderRadius:20 }}
              />
            </div>
          </div>

        </div>

        {/* Keyframe styles */}
        <style>{`
          @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-16px); }
          }
          @keyframes pulse {
            0%, 100% { opacity: 0.6; transform: scale(1); }
            50% { opacity: 1; transform: scale(1.06); }
          }
          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}</style>

        {/* Scroll hint */}
        <div style={{ position:"absolute", bottom:40, left:"50%", transform:"translateX(-50%)", color:C.muted, fontFamily:"'DM Mono'", fontSize:15, letterSpacing:2, display:"flex", flexDirection:"column", alignItems:"center", gap:8 }}>
          {i18n[lang].scroll}
          <div style={{ width:1, height:40, background:`linear-gradient(${C.muted}, transparent)` }} />
        </div>
      </div>


      {/* Stats strip */}
      <div style={{ borderTop:`1px solid ${C.border}`, borderBottom:`1px solid ${C.border}`, display:"grid", gridTemplateColumns:"repeat(4,1fr)", background:C.surface }}>
        {stats.map((s,i) => (
          <div key={i} style={{ padding:"40px 48px", borderRight: i<3 ? `1px solid ${C.border}` : "none" }}>
            <div style={{ fontFamily:"'Sora'", fontSize:40, fontWeight:600, color:C.text }}>{s.value}</div>
            <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted, marginTop:4, fontWeight:500 }}>{s.unit}</div>
            <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginTop:2 }}>{s.desc}</div>
          </div>
        ))}
      </div>

      {/* Features grid */}
      <div style={{ padding:"80px", borderBottom:`1px solid ${C.border}` }}>
        <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:4, color:C.muted, textTransform:"uppercase", marginBottom:48 }}>
          {i18n[lang].whatSarthiDoes}
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:1, border:`1px solid ${C.border}`, background:C.border }}>
          {features.map((f,i) => (
            <div key={i} style={{
              padding:"36px 32px", background:C.bg,
              transition:"background 0.3s", cursor:"default"
            }}
              onMouseEnter={e=>e.currentTarget.style.background=C.surface}
              onMouseLeave={e=>e.currentTarget.style.background=C.bg}
            >
              <div style={{ color:C.accent, marginBottom:16 }}>
                <f.icon size={28} strokeWidth={1.5} />
              </div>
              <div style={{ fontFamily:"'Sora'", fontSize:18, color:C.text, marginBottom:10 }}>{f.title}</div>
              <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted, lineHeight:1.7, fontWeight:300 }}>{f.desc}</div>
            </div>
          ))}
        </div>
      </div>


      <div style={{ padding:"100px 80px", textAlign:"center", background:`radial-gradient(circle at center, ${C.surface} 0%, ${C.bg} 100%)` }}>
        <div style={{ fontFamily:"'Sora'", fontSize:40, color:C.text, marginBottom:16, fontWeight:600 }}>
          {i18n[lang].readyToRun}
        </div>
        <div style={{ fontFamily:"'Inter'", fontSize:16, color:C.muted, marginBottom:40, fontWeight:300, maxWidth:500, margin:"0 auto 40px" }}>
          {i18n[lang].readyDesc}
        </div>
        <Link to="/dashboard" style={{
          background:C.text, color:C.bg, border:"none", borderRadius:8,
          padding:"16px 48px", fontFamily:"'Inter'", fontWeight:600, fontSize:16, cursor:"pointer",
          textDecoration:"none", display:"inline-flex", alignItems:"center", gap:10, transition:"all 0.3s", boxShadow:`0 10px 30px rgba(0,0,0,0.5)`
        }} onMouseEnter={e=>{e.target.style.transform="translateY(-2px)"; e.target.style.boxShadow="0 15px 40px rgba(0,0,0,0.6)"}} onMouseLeave={e=>{e.target.style.transform="translateY(0)"; e.target.style.boxShadow="0 10px 30px rgba(0,0,0,0.5)"}}>
          {i18n[lang].launchCommand}
          <ArrowRight size={20} />
        </Link>
      </div>
    </div>
  );
}


export default Landing;
