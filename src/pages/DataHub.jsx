import { useState, useEffect, useRef } from "react";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { live } from "../data/appData.js";
import { api } from "../api/client.js";
import { C } from "../theme.js";
import { SarthiIcon, SectionLabel, Tag } from "../components/ui.jsx";
import { Box, BarChart3, Warehouse, Users, TrendingUp, AlertTriangle, MapPin, Leaf } from "lucide-react";

const uploadTypes = [
  { id: "sku", label: "uploadSKU", desc: "SKU_ID, Name, Category, Unit_Cost, Lead_Time...", types: "CSV/XLS", icon: Box },
  { id: "sales", label: "uploadSales", desc: "Transaction_ID, Date, SKU_ID, Qty, Price...", types: "CSV/XLS", icon: BarChart3 },
  { id: "stock", label: "uploadStock", desc: "SKU_ID, Warehouse_ID, Current_Stock, Transit...", types: "CSV/JSON", icon: Warehouse },
  { id: "vendor", label: "uploadVendor", desc: "Supplier_ID, Tier, Lead_Time, Quality_Score...", types: "XLS/CSV", icon: Users },
  { id: "forecast", label: "uploadForecast", desc: "SKU_ID, Forecast_Date, Predicted_Demand...", types: "CSV/JSON", icon: TrendingUp },
  { id: "risk", label: "uploadRisk", desc: "Signal_ID, Type, Severity, SKUs_at_Risk[]...", types: "JSON/XML", icon: AlertTriangle },
  { id: "locations", label: "uploadLocations", desc: "Location_ID, City, Lat, Long, Capacity...", types: "CSV/GEO", icon: MapPin },
  { id: "esg", label: "uploadESG", desc: "Supplier_ID, Carbon_kg, Water_L, Waste_kg...", types: "XLS/CSV", icon: Leaf }
];

export default function DataHub() {
  const { lang, refreshData } = useSarthi();
  const [uploads, setUploads] = useState(live.dataHub?.uploads ?? {}); // id -> { progress, status, records }
  const [syncing, setSyncing] = useState(false);
  const fileInputRef = useRef(null);
  const [activeUploadId, setActiveUploadId] = useState(null);

  const handleCardClick = (id) => {
    if (uploads[id]?.status === "done" || uploads[id]?.status === "uploading") return;
    setActiveUploadId(id);
    if (fileInputRef.current) fileInputRef.current.click();
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      startUpload(activeUploadId, e.target.files[0]);
    }
    e.target.value = null;
  };

  // send the file to the backend; offline, the simulated upload below is used
  const startUpload = async (id, file) => {
    if (!live.online) { simulateUpload(id); return; }
    if (uploads[id]?.status === "done") return;
    setUploads(prev => ({ ...prev, [id]: { progress: 0, status: "uploading" } }));
    const r = await api.upload(`/datahub/upload/${id}`, file, (p) => setUploads(prev => ({ ...prev, [id]: { ...prev[id], progress: Math.min(p, 95) } })));
    setUploads(prev => r
      ? ({ ...prev, [id]: { progress: 100, status: "done", records: r.records, time: r.time } })
      : (({ [id]: _failed, ...rest }) => rest)(prev));      // failed upload: the card returns to its empty state
  };

  const simulateUpload = (id) => {
    if (uploads[id]?.status === "done") return;
    
    setUploads(prev => ({ ...prev, [id]: { progress: 0, status: "uploading" } }));
    
    let p = 0;
    const interval = setInterval(() => {
      p += Math.random() * 30;
      if (p >= 100) {
        p = 100;
        clearInterval(interval);
        setUploads(prev => ({ 
          ...prev, 
          [id]: { progress: 100, status: "done", records: Math.floor(Math.random() * 5000) + 500, time: new Date().toLocaleTimeString() } 
        }));
      } else {
        setUploads(prev => ({ ...prev, [id]: { ...prev[id], progress: p } }));
      }
    }, 400);
  };

  const runSync = async () => {
    setSyncing(true);
    const run = live.online ? await api.post("/runs") : null;
    if (run) {                                          // watch the pipeline run, then show its results
      api.stream(`/runs/${run.runId}/stream`, () => {}, async () => { await refreshData(); setSyncing(false); });
      return;
    }
    setTimeout(() => {
      setSyncing(false);
      // Toast would go here
    }, 3000);
  };

  const allDone = live.online || Object.values(uploads).filter(u => u.status === "done").length === uploadTypes.length;

  return (
    <div style={{ animation: "fadeIn 0.5s ease" }}>
      <input type="file" ref={fileInputRef} style={{ display: "none" }} onChange={handleFileChange} accept=".csv,.xlsx,.xls,.json,.xml" />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 32 }}>
        <div>
          <div style={{ fontFamily: "'DM Mono'", fontSize: 14, color: C.muted, letterSpacing: 2, textTransform: "uppercase", marginBottom: 8 }}>{i18n[lang].dataHealth}</div>
          <h1 style={{ fontSize: 40, fontWeight: 700 }}>{i18n[lang].dataInfrastructure}</h1>
        </div>
        <button 
          onClick={runSync}
          disabled={!allDone || syncing}
          style={{
            background: syncing ? C.border : allDone ? C.accent : C.border,
            color: C.bg, padding: "12px 24px", borderRadius: 12, border: "none",
            fontFamily: "'Sora'", fontWeight: 700, cursor: allDone ? "pointer" : "default",
            opacity: syncing || !allDone ? 0.6 : 1, transition: "all 0.3s"
          }}
        >
          {syncing ? i18n[lang].syncing : i18n[lang].syncIntelligence}
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 20, marginBottom: 40 }}>
        {[
          { label: i18n[lang].totalIngested, value: live.dataHub?.metrics?.totalIngested ?? "42,847", color: C.sweet },
          { label: i18n[lang].freshness, value: live.dataHub?.metrics?.freshness ?? "98.2%", color: C.sweet },
          { label: i18n[lang].joinQuality, value: live.dataHub?.metrics?.joinQuality ?? "99.1%", color: C.sweet },
          { label: i18n[lang].alertsGenerated, value: live.dataHub?.metrics?.alertsGenerated ?? "23", color: C.chaos }
        ].map(m => (
          <div key={m.label} className="glass" style={{ padding: 20, borderRadius: 16 }}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, fontFamily: "'DM Mono'" }}>{m.label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: m.color }}>{m.value}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 24 }}>
        {uploadTypes.map(t => {
          const up = uploads[t.id];
          return (
            <div key={t.id} className="glass card-hover" style={{ padding: 24, borderRadius: 20, cursor: "pointer" }} onClick={() => handleCardClick(t.id)}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                <div style={{ color: C.accent }}>
                  <t.icon size={28} />
                </div>
                <Tag color={up?.status === "done" ? C.sweet : C.accent} small>{t.types}</Tag>
              </div>
              <h3 style={{ fontSize: 18, marginBottom: 4 }}>{i18n[lang][t.label]}</h3>
              <p style={{ fontSize: 13, color: C.muted, marginBottom: 20, height: 40, overflow: "hidden" }}>{t.desc}</p>
              
              {up ? (
                <div>
                  <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: "hidden", marginBottom: 12 }}>
                    <div style={{ height: "100%", width: up.progress + "%", background: up.status === "done" ? C.sweet : C.accent, transition: "width 0.4s ease" }} />
                  </div>
                  {up.status === "done" ? (
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontFamily: "'DM Mono'", color: C.sweet, alignItems: "center" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <SarthiIcon name="Check" size={12} />
                        {up.records} {i18n[lang].recordsParsed}
                      </span>
                      <span>{up.time}</span>
                    </div>
                  ) : (
                    <div style={{ fontSize: 11, fontFamily: "'DM Mono'", color: C.accent }}>{i18n[lang].uploading} {Math.round(up.progress)}%</div>
                  )}
                </div>
              ) : (
                <div style={{ border: `1px dashed ${C.border}`, borderRadius: 12, padding: 20, textAlign: "center", fontSize: 12, color: C.muted }}>
                  {i18n[lang].dragDropBrowse}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
