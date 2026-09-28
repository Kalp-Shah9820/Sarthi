import { SarthiIcon } from "./components/ui.jsx";
import { Link, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { navItems, skuData } from "./data/appData.js";
import { i18n } from "./data/i18n.js";
import { C, G } from "./theme.js";
import { useSarthi } from "./context/SarthiContext.jsx";
import VoiceCommand from "./components/VoiceCommand.jsx";
import LanguageSelector from "./components/LanguageSelector.jsx";
import PipelineVisualizer from "./components/PipelineVisualizer.jsx";
import Alerts from "./pages/Alerts.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Intelligence from "./pages/Intelligence.jsx";
import Inventory from "./pages/Inventory.jsx";
import Landing from "./pages/Landing.jsx";
import Replenish from "./pages/Replenish.jsx";
import Sandbox from "./pages/Sandbox.jsx";
import SKUDetail from "./pages/SKUDetail.jsx";
import StoreViz from "./pages/StoreViz.jsx";
import DataHub from "./pages/DataHub.jsx";
import CommandCenter from "./pages/CommandCenter.jsx";


function PageFrame({ children, maxWidth = 1000 }) {
  return (
    <div style={{ maxWidth, margin: "0 auto", padding: "20px 40px" }}>
      <PipelineVisualizer />
      {children}
    </div>
  );
}

function SKURouteWrapper() {
  const { lang } = useSarthi();
  const { id } = useParams();
  const navigate = useNavigate();
  const sku = skuData.find((item) => item.id === id);

  if (!sku) {
    return <div style={{ padding: 40, color: C.muted, fontFamily: "'DM Mono'" }}>{i18n[lang].skuNotFound}</div>;
  }

  return <SKUDetail sku={sku} setPage={(page) => navigate("/" + page)} />;
}

export default function App() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { strategy, lang, setLang } = useSarthi();
  const isLanding = pathname === "/";

  const extendedNav = [
    ...navItems.filter(n => n.id !== "landing"),
    { id: "command", label: "Command", icon: "Hexagon" },
    { id: "datahub", label: "Data Hub", icon: "Database" },
  ];

  const handleVoiceCommand = (cmd) => {
    const { type, skuId, path, lang: targetLang } = cmd;
    if (type === "PROCURE" && skuId) navigate("/replenish");
    else if (type === "NAVIGATE_SKU" && skuId) navigate(`/sku/${skuId}`);
    else if (type === "SKU_DETAIL" && skuId) navigate(`/sku/${skuId}`);
    else if (type === "SET_LANG") setLang(targetLang);
    else if (type === "NAVIGATE") navigate("/" + path);
  };

  return (
    <>
      <style>{`
        ${G}
        *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
        body { background:${C.bg}; color:${C.text}; font-family: 'Inter', sans-serif; -webkit-font-smoothing: antialiased; }
        h1, h2, h3, h4, h5 { font-family: 'Sora', sans-serif; }
        input[type=range] { height:4px; cursor:pointer; }
        input[type=range]::-webkit-slider-thumb { width:14px; height:14px; border-radius:50%; cursor:pointer; }
        ::-webkit-scrollbar { width:4px; }
        ::-webkit-scrollbar-track { background:${C.bg}; }
        ::-webkit-scrollbar-thumb { background:${C.border}; border-radius:2px; }
        @keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        .glass {
          background: ${C.glass};
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border: 1px solid ${C.border};
        }
        .card-hover {
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .card-hover:hover {
          transform: translateY(-4px);
          border-color: ${C.accent}44;
          box-shadow: 0 12px 30px rgba(0,0,0,0.4);
        }
      `}</style>

      <div style={{ display: "flex", minHeight: "100vh", background: C.bg }}>
        {!isLanding && (
          <div style={{ width: 100, background: C.surface, borderRight: `1px solid ${C.border}`, display: "flex", flexDirection: "column", alignItems: "center", padding: "24px 0", gap: 6, position: "fixed", top: 0, left: 0, bottom: 0, zIndex: 100 }}>
            <div style={{ width: 52, height: 52, border: `1px solid ${C.border}`, borderRadius: 14, display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 32, color: C.accent, background: `rgba(0,0,0,0.2)`, overflow: "hidden" }}>
              <img src="/sarthi-logo.png" alt="Sarthi" style={{ width: "80%", height: "auto", display: "block" }} />
            </div>
            
            {/* Strategy Indicator */}
            <div style={{ position:"absolute", top:74, width:80, textAlign:"center" }}>
              <div style={{ fontFamily:"'DM Mono'", fontSize:8, color:C.muted, textTransform:"uppercase", letterSpacing:1, marginBottom:4 }}>{i18n[lang].strategyLabel}</div>
              <div style={{ fontFamily:"'DM Mono'", fontSize:9, color: strategy.mode === "Balanced" ? C.sweet : C.chaos, fontWeight:600 }}>{(i18n[lang][strategy.mode.toLowerCase()] || strategy.mode).toUpperCase()}</div>
            </div>

            {extendedNav
              .map((nav) => {
                const isActive = pathname.startsWith("/" + nav.id);
                const label = i18n[lang][nav.id] || nav.label;

                return (
                  <Link
                    key={nav.id}
                    to={"/" + nav.id}
                    title={label}
                    style={{
                      width: 80, height: 60,
                      background: isActive ? C.faint : "transparent",
                      border: `1px solid ${isActive ? C.borderLight : "transparent"}`,
                      borderRadius: 14, cursor: "pointer",
                      display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "center",
                      gap: 6, transition: "all 0.3s",
                      color: isActive ? C.text : C.muted,
                      textDecoration: "none",
                    }}
                  >
                    <SarthiIcon name={nav.icon} size={20} strokeWidth={isActive ? 2.5 : 2} />
                    <span style={{ fontSize: 10, fontFamily: "'DM Mono'", letterSpacing: 0.5, fontWeight: isActive ? 600 : 400, textAlign:"center" }}>{label}</span>
                  </Link>
                );
              })}
            
            <div style={{ marginTop: "auto", display:"flex", flexDirection:"column", gap:16, alignItems:"center" }}>
              <Link to="/" title={i18n[lang].landing} style={{ width: 50, height: 50, background: "transparent", border: `1px solid ${C.border}`, borderRadius: 12, cursor: "pointer", color: C.muted, fontSize: 18, display: "flex", alignItems: "center", justifyContent: "center", textDecoration: "none", transition: "all 0.2s" }} onMouseEnter={(event) => { event.currentTarget.style.color = C.text; }} onMouseLeave={(event) => { event.currentTarget.style.color = C.muted; }}>
                <SarthiIcon name="Home" size={20} />
              </Link>
            </div>
          </div>
        )}
      {!isLanding && <VoiceCommand onCommand={handleVoiceCommand} />}
      {!isLanding && <LanguageSelector />}

      {/* Main Content Area */}
      <div style={{ flex: 1, marginLeft: isLanding ? 0 : 100, padding: isLanding ? 0 : "40px 60px", height: "100vh", overflowY: "auto", overflowX: "hidden" }}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/dashboard" element={<PageFrame><Dashboard /></PageFrame>} />
            <Route path="/inventory" element={<PageFrame><Inventory setSku={(sku) => navigate(`/sku/${sku.id}`)} /></PageFrame>} />
            <Route path="/replenish" element={<PageFrame><Replenish /></PageFrame>} />
            <Route path="/store" element={<PageFrame><StoreViz /></PageFrame>} />
            <Route path="/chat" element={<PageFrame maxWidth={1100}><Intelligence /></PageFrame>} />
            <Route path="/alerts" element={<PageFrame><Alerts /></PageFrame>} />
            <Route path="/sandbox" element={<PageFrame><Sandbox /></PageFrame>} />
            <Route path="/datahub" element={<PageFrame><DataHub /></PageFrame>} />
            <Route path="/command" element={<PageFrame maxWidth={1300}><CommandCenter /></PageFrame>} />
            <Route path="/sku/:id" element={<PageFrame><SKURouteWrapper /></PageFrame>} />
            <Route path="*" element={<Link to="/" style={{ color: C.text, padding: 40, display: "block", fontFamily: "'DM Mono'" }}>404 · {i18n[lang].returnToSarthi}</Link>} />
          </Routes>
        </div>
      </div>
    </>
  );
}
