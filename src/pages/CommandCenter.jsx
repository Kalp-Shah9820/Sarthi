import { useState, useMemo } from "react";
import { skuData, distributors, live } from "../data/appData.js";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { C } from "../theme.js";
import { SectionLabel, Tag, CustomTooltip, SarthiIcon } from "../components/ui.jsx";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function CommandCenter() {
  const { lang, strategy, dataVersion } = useSarthi();

  // Extend SKU data with more enterprise metrics for the Command Center.
  // The backend supplies them; the placeholders are used only when it is not reachable.
  const richSkuData = useMemo(() => skuData.map((s, i) => ({
    ...s,
    daysStock: s.daysStock ?? Math.floor(Math.random() * 45) + 2,
    esg: s.esg ?? Math.floor(Math.random() * 40) + 55,
    co2: s.co2 ?? (Math.random() * 1.5 + 0.2).toFixed(2),
    stockoutProb: s.stockoutProb ?? Math.floor(Math.random() * 60) + 5,
    lastReorder: s.lastReorder ?? "2026-03-" + (Math.floor(Math.random() * 10) + 1).toString().padStart(2, '0'),
    decisionStatus: s.decisionStatus ?? (i % 5 === 0 ? "critical" : i % 3 === 0 ? "pending" : "auto"),
    supplier: s.supplier ?? (distributors[i % distributors.length]?.name || "Local Vendor"),
    tier: s.tier ?? (distributors[i % distributors.length]?.tier || "Bronze")
  })), [dataVersion]);

  const [search, setSearch] = useState("");
  const [zoneFilter, setZoneFilter] = useState("all");
  const [sortConfig, setSortConfig] = useState({ key: "par", direction: "desc" });
  const [selectedRows, setSelectedRows] = useState([]);

  const [selectedZoneForExplanation, setSelectedZoneForExplanation] = useState(null);

  const filteredData = useMemo(() => {
    return richSkuData
      .filter(s => {
        const matchesSearch = s.name.toLowerCase().includes(search.toLowerCase()) || s.id.toLowerCase().includes(search.toLowerCase());
        const matchesZone = zoneFilter === "all" || s.zone === zoneFilter;
        return matchesSearch && matchesZone;
      })
      .sort((a, b) => {
        const valA = a[sortConfig.key];
        const valB = b[sortConfig.key];
        if (sortConfig.direction === "asc") return valA > valB ? 1 : -1;
        return valA < valB ? 1 : -1;
      });
  }, [search, zoneFilter, sortConfig, richSkuData]);

  const exportCsv = () => {
    const cols = ["id", "name", "cat", "zone", "stock", "daysStock", "vel", "risk", "par", "esg", "co2", "lastReorder", "decisionStatus", "supplier", "tier"];
    const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.join(","), ...filteredData.map(s => cols.map(c => cell(s[c])).join(","))].join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "sarthi-skus.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  const toggleSort = (key) => {
    setSortConfig(prev => ({
      key,
      direction: prev.key === key && prev.direction === "desc" ? "asc" : "desc"
    }));
  };

  const toggleSelect = (id) => {
    setSelectedRows(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const zones = [
    { id: "sweet", label: i18n[lang].sweetSpot, color: C.sweet, icon: "Zap", tagline: i18n[lang].sweetTagline, desc: i18n[lang].sweetDesc, logic: i18n[lang].sweetLogic, action: i18n[lang].sweetAgentAction, agentName: "Auto-pilot Agent" },
    { id: "chaos", label: i18n[lang].chaosZone, color: C.chaos, icon: "AlertTriangle", tagline: i18n[lang].chaosTagline, desc: i18n[lang].chaosDesc, logic: i18n[lang].chaosLogic, action: i18n[lang].chaosAgentAction, agentName: "Negotiator Agent" },
    { id: "ghost", label: i18n[lang].ghostZone, color: C.ghost, icon: "Wind", tagline: i18n[lang].ghostTagline, desc: i18n[lang].ghostDesc, logic: i18n[lang].ghostLogic, action: i18n[lang].ghostAgentAction, agentName: "Logistics Agent" },
    { id: "money", label: i18n[lang].moneyPit, color: C.money, icon: "TrendingDown", tagline: i18n[lang].moneyTagline, desc: i18n[lang].moneyDesc, logic: i18n[lang].moneyLogic, action: i18n[lang].moneyAgentAction, agentName: "CFO Agent" },
  ];

  return (
    <div style={{ animation: "fadeIn 0.5s ease", position: "relative" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 32 }}>
        <div>
          <div style={{ fontFamily: "'DM Mono'", fontSize: 14, color: C.muted, letterSpacing: 2, textTransform: "uppercase", marginBottom: 8 }}>{i18n[lang].skuCommand}</div>
          <h1 style={{ fontSize: 40, fontWeight: 700 }}>{i18n[lang].enterpriseHub}</h1>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
            <button onClick={exportCsv} className="glass" style={{ padding: "10px 20px", borderRadius: 10, color: C.text, fontSize: 13, cursor: "pointer" }}>{i18n[lang].exportCSV}</button>
            <button style={{ padding: "10px 20px", borderRadius: 10, background: C.accent, color: C.bg, border: "none", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>{i18n[lang].bulkActions} ({selectedRows.length})</button>
        </div>
      </div>

      {/* ZONE SUMMARIES */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 32 }}>
        {zones.map(z => {
          const zoneSkus = richSkuData.filter(s => s.zone === z.id);
          const count = zoneSkus.length;
          const parTotal = zoneSkus.reduce((sum, s) => sum + s.par, 0);
          const isSelected = zoneFilter === z.id;
          return (
            <div 
              key={z.id} 
              onClick={() => setZoneFilter(isSelected ? "all" : z.id)}
              className="glass card-hover" 
              style={{ 
                borderRadius: 16, padding: "20px 24px", cursor: "pointer", 
                border: `2px solid ${isSelected ? z.color : "transparent"}`,
                boxShadow: isSelected ? `0 0 20px ${z.color}33` : "none",
                position: "relative",
                minHeight: 180,
                display: "flex",
                flexDirection: "column"
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <SarthiIcon name={z.icon} size={20} color={z.color} />
                    <span style={{ fontFamily: "'DM Mono'", fontSize: 11, color: z.color, fontWeight: 700, letterSpacing: 1 }}>{z.label.toUpperCase()}</span>
                </div>
                {parTotal > 0 && (
                    <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono'" }}>{i18n[lang].profitAtRisk}</div>
                        <div style={{ fontSize: 16, fontWeight: 700, color: C.text }}>₹{(parTotal/1000).toFixed(0)}K</div>
                    </div>
                )}
              </div>
              
              <div style={{ fontSize: 48, fontWeight: 700, color: C.text, lineHeight: 1 }}>{count}</div>
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 16 }}>{i18n[lang].skusInZone}</div>
              
              <div style={{ marginTop: "auto" }}>
                <div style={{ fontSize: 13, color: C.text, fontWeight: 600, marginBottom: 4 }}>{z.tagline}</div>
                <div style={{ fontSize: 11, color: C.muted, lineHeight: 1.5 }}>{z.desc}</div>
              </div>
              
              {/* Info Indicator */}
              <div 
                onClick={(e) => { e.stopPropagation(); setSelectedZoneForExplanation(z); }}
                style={{ position: "absolute", top: 12, right: 12, fontSize: 12, color: C.muted, background: "rgba(255,255,255,0.05)", width: 20, height: 20, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", transition: "0.2s", cursor: "pointer" }}
                className="hover-bright"
              ><SarthiIcon name="Info" size={12} /></div>
            </div>
          )
        })}
      </div>

      <div className="glass" style={{ borderRadius: 20, padding: 24, marginBottom: 24, display: "flex", gap: 20, alignItems: "center" }}>
        <input 
          placeholder={i18n[lang].searchPlaceholder}
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ flex: 1, background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: "12px 20px", color: C.text, fontFamily: "'Inter'" }}
        />
        <select 
          value={zoneFilter}
          onChange={e => setZoneFilter(e.target.value)}
          style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: "12px 20px", color: C.text, width: 220 }}
        >
          <option value="all">{i18n[lang].allZones}</option>
          {zones.map(z => (
            <option key={z.id} value={z.id}>{z.label}</option>
          ))}
        </select>
      </div>

      <div className="glass" style={{ borderRadius: 20, overflow: "hidden", border: `1px solid ${C.borderLight}` }}>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
          <thead style={{ background: C.surface, borderBottom: `1px solid ${C.border}` }}>
            <tr>
              <th style={{ padding: 16 }}><input type="checkbox" /></th>
              {[
                { label: i18n[lang].sku, key: "name" },
                { label: i18n[lang].zone, key: "zone" },
                { label: i18n[lang].stock, key: "stock" },
                { label: i18n[lang].daysStock, key: "daysStock" },
                { label: i18n[lang].status, key: "decisionStatus" },
                { label: i18n[lang].profitAtRisk, key: "par" },
                { label: i18n[lang].esgScore, key: "esg" }
              ].map(h => (
                <th 
                  key={h.key} 
                  onClick={() => toggleSort(h.key)}
                  style={{ 
                    padding: "16px 12px", fontSize: 12, fontFamily: "'DM Mono'", 
                    color: C.muted, cursor: "pointer", borderBottom: sortConfig.key === h.key ? `2px solid ${C.accent}` : "none" 
                  }}
                >
                  {h.label.toUpperCase()} {sortConfig.key === h.key ? (sortConfig.direction === "asc" ? "↑" : "↓") : ""}
                </th>
              ))}
              <th style={{ padding: 16 }}></th>
            </tr>
          </thead>
          <tbody>
            {filteredData.slice(0, 15).map(s => (
              <tr key={s.id} style={{ borderBottom: `1px solid ${C.border}44`, background: selectedRows.includes(s.id) ? C.accent + "11" : "transparent" }}>
                <td style={{ padding: 16 }}><input type="checkbox" checked={selectedRows.includes(s.id)} onChange={() => toggleSelect(s.id)} /></td>
                <td style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</div>
                  <div style={{ fontSize: 10, color: C.muted }}>{s.id} · {i18n[lang]["category" + s.cat.replace(/\s/g, "")] || s.cat}</div>
                </td>
                <td style={{ padding: 12 }}>
                    <Tag color={s.zone === "sweet" ? C.sweet : s.zone === "chaos" ? C.chaos : s.zone === "money" ? C.money : C.ghost} small>
                        {s.zone === "sweet" ? i18n[lang].sweetSpot : s.zone === "chaos" ? i18n[lang].chaosZone : s.zone === "money" ? i18n[lang].moneyPit : i18n[lang].ghostZone}
                    </Tag>
                </td>
                <td style={{ padding: 12 }}>
                    <div style={{ fontSize: 13 }}>{s.stock}</div>
                    <div style={{ height: 3, width: 40, background: C.border, borderRadius: 2, marginTop: 4 }}>
                        <div style={{ height: "100%", width: (s.stock/1000)*100 + "%", background: C.text, borderRadius: 2 }} />
                    </div>
                </td>
                <td style={{ padding: 12, color: s.daysStock < 7 ? C.chaos : C.text, fontWeight: 700 }}>{s.daysStock}d</td>
                <td style={{ padding: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <div style={{ width: 8, height: 8, borderRadius: "50%", background: s.decisionStatus === "critical" ? C.chaos : s.decisionStatus === "pending" ? C.money : C.sweet }} />
                        <span style={{ fontSize: 11, fontWeight: 600 }}>{i18n[lang]["status" + s.decisionStatus.charAt(0).toUpperCase() + s.decisionStatus.slice(1)] || s.decisionStatus}</span>
                    </div>
                </td>
                <td style={{ padding: 12, color: C.chaos, fontWeight: 700 }}>₹{(s.par/1000).toFixed(1)}K</td>
                <td style={{ padding: 12 }}>
                    <div style={{ fontSize: 12 }}>{s.esg}%</div>
                    <div style={{ height: 4, width: 60, background: C.border, borderRadius: 2 }}>
                        <div style={{ height: "100%", width: s.esg + "%", background: s.esg > 80 ? C.sweet : s.esg > 60 ? C.money : C.chaos, borderRadius: 2 }} />
                    </div>
                </td>
                <td style={{ padding: 16, textAlign: "right" }}>
                    <button style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontSize: 18, display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <SarthiIcon name="Eye" size={18} />
                    </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: 16, background: C.surface, display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12, color: C.muted, fontFamily: "'DM Mono'" }}>
            <div>{i18n[lang].showingSkus}</div>
            <div style={{ display: "flex", gap: 12 }}>
                <span style={{ cursor: "pointer" }}>{i18n[lang].prev}</span>
                <span style={{ color: C.accent }}>1</span>
                <span style={{ cursor: "pointer" }}>2</span>
                <span style={{ cursor: "pointer" }}>3</span>
                <span style={{ cursor: "pointer" }}>{i18n[lang].next}</span>
            </div>
        </div>
      </div>

      {/* EXPLAINABILITY MODAL */}
      {selectedZoneForExplanation && (
        <div 
          onClick={() => setSelectedZoneForExplanation(null)}
          style={{ 
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0, 
            background: "rgba(0,0,0,0.8)", backdropFilter: "blur(10px)", 
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
            animation: "fadeIn 0.3s ease"
          }}
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            style={{ 
              width: 500, background: C.surface, borderRadius: 24, padding: 32, border: `1px solid ${selectedZoneForExplanation.color}44`,
              boxShadow: `0 20px 50px ${selectedZoneForExplanation.color}22`
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
              <SarthiIcon name={selectedZoneForExplanation.icon} size={32} color={selectedZoneForExplanation.color} />
              <div>
                <div style={{ fontSize: 12, fontFamily: "'DM Mono'", color: selectedZoneForExplanation.color, textTransform: "uppercase", letterSpacing: 1 }}>{i18n[lang].explainDecision}</div>
                <h2 style={{ fontSize: 24, fontWeight: 700 }}>{selectedZoneForExplanation.label}</h2>
              </div>
            </div>

            <div style={{ marginBottom: 32 }}>
              <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>{i18n[lang].zoneLogic}</div>
              <div className="glass" style={{ padding: 16, borderRadius: 12, border: `1px solid ${C.border}`, fontFamily: "'DM Mono'", fontSize: 13, background: "rgba(255,255,255,0.02)" }}>
                {selectedZoneForExplanation.logic}
              </div>
            </div>

            <div style={{ marginBottom: 32 }}>
              <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>{i18n[lang].agentStrategy} • <span style={{ color: selectedZoneForExplanation.color }}>{selectedZoneForExplanation.agentName}</span></div>
              <div style={{ fontSize: 14, lineHeight: 1.6, color: C.text }}>
                {selectedZoneForExplanation.action}
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 32 }}>
              <div className="glass" style={{ padding: 16, borderRadius: 12 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>{i18n[lang].avgStockoutRisk}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: selectedZoneForExplanation.color }}>{live.zoneStats?.[selectedZoneForExplanation.id]?.avgRisk ?? (selectedZoneForExplanation.id === "sweet" ? "2.4%" : selectedZoneForExplanation.id === "chaos" ? "42.1%" : "12.8%")}</div>
              </div>
              <div className="glass" style={{ padding: 16, borderRadius: 12 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>{i18n[lang].decisionConfidence}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: C.sweet }}>{live.zoneStats?.[selectedZoneForExplanation.id]?.confidence ?? (selectedZoneForExplanation.id === "sweet" ? "99.8%" : "92.4%")}</div>
              </div>
            </div>

            <button 
              onClick={() => setSelectedZoneForExplanation(null)}
              style={{ width: "100%", padding: 16, borderRadius: 12, background: C.text, color: C.bg, border: "none", fontWeight: 700, cursor: "pointer" }}
            >
              {i18n[lang].done}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
