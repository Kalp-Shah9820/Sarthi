import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { distributors, live, skuData, zoneInfo } from "../data/appData.js";
import { api } from "../api/client.js";
import { C } from "../theme.js";
import { CustomTooltip, SectionLabel, Tag } from "../components/ui.jsx";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import { 
  Plane, Ship, Train, Tag as TagIcon, Gift, Zap, 
  AlertTriangle, CheckCircle, RefreshCcw, X 
} from "lucide-react";

// ─── ESG SHIPPING OPTIONS ────────────────────────────────────────────────────
const esgOptions = [
  { modeKey:"esgAir", icon:Plane, tat:"1 day", cost:"₹₹₹", co2Key:"esgHigh", co2Pct:100, costVal:3, color:C.chaos, labelKey:"esgFastest", descKey:"esgAirDesc" },
  { modeKey:"esgSea", icon:Ship, tat:"7 days", cost:"₹", co2Key:"esgLow", co2Pct:25, costVal:1, color:C.ghost, labelKey:"esgCheapest", descKey:"esgSeaDesc" },
  { modeKey:"esgMultimodal", icon:Train, tat:"3 days", cost:"₹₹", co2Key:"esgMedium", co2Pct:55, costVal:2, color:C.sweet, labelKey:"esgIkigai", descKey:"esgMultimodalDesc" },
];

// ─── WAREHOUSE DATA (for transfers) ─────────────────────────────────────────
const warehouses = [
  { id:"WH-MUM", name:"Mumbai Central", city:"Mumbai", stock:{ SKU002:180, SKU004:200, SKU007:90 }, capacity:2000, utilization:0.72 },
  { id:"WH-DEL", name:"Delhi NCR Hub", city:"Delhi",  stock:{ SKU002:640, SKU004:500, SKU007:350 }, capacity:3000, utilization:0.88 },
  { id:"WH-BLR", name:"Bangalore South", city:"Bangalore", stock:{ SKU002:120, SKU004:300, SKU007:200 }, capacity:1800, utilization:0.65 },
  { id:"WH-CHN", name:"Chennai Port", city:"Chennai", stock:{ SKU002:90, SKU004:180, SKU007:100 }, capacity:1500, utilization:0.58 },
];

// ─── CAMPAIGN TEMPLATES ──────────────────────────────────────────────────────
const campaignTemplates = [
  { type:"markdown", icon:TagIcon, labelKey:"markdownSale", descKey:"markdownSaleDesc", target:"ghost", discount:"15%", estKey:"estImpact" },
  { type:"bundle", icon:Gift, labelKey:"iplCombo", descKey:"iplComboDesc", target:"chaos", discount:"10% bundle", estKey:"estImpact" },
  { type:"flash", icon:Zap, labelKey:"flashCampaign", descKey:"flashCampaignDesc", target:"money", discount:"20% off", estKey:"estImpact" },
];

function Replenish() {
  const { strategy, lang, refreshData, refreshAfterRun } = useSarthi();
  const [selected, setSelected] = useState(null);
  const [selectedDist, setSelectedDist] = useState({});  // skuId → distIndex
  const [draftOrder, setDraftOrder] = useState(null);     // { skuId, distIdx } — opens recommendations panel
  const [orderUnits, setOrderUnits] = useState({});       // skuId → units
  const [orderPrice, setOrderPrice] = useState({});       // skuId → edited price
  const [confirmModal, setConfirmModal] = useState(null);  // { skuId, distIdx } — "Are you sure?" popup
  const [orderSuccess, setOrderSuccess] = useState(null);  // { sku, dist, units, price } — after Proceed
  const [selectedEsg, setSelectedEsg] = useState(live.esg?.recommendedIndex ?? 2);       // default to the recommended option (Ikigai-Balanced offline)
  const [transferModal, setTransferModal] = useState(null); // { fromWH, toWH, skuId, units }
  const [transferSuccess, setTransferSuccess] = useState(null);
  const [campaignModal, setCampaignModal] = useState(null);
  const [campaignLaunched, setCampaignLaunched] = useState(() =>
    campaignTemplates.map((c, i) => (live.campaigns?.find(x => x.type === c.type)?.status === "live" ? i : -1)).filter(i => i >= 0));
  const critical = skuData.filter(d => (d.stockoutProb ?? d.risk)>60);

  // live data from the backend, each falling back to this page's own constants
  const whList = live.warehouses ?? warehouses;
  const esgCards = esgOptions.map((o, i) => ({ ...o, ...(live.esg?.options?.[i] ?? {}) }));
  const camps = campaignTemplates.map(c => ({ ...c, ...(live.campaigns?.find(x => x.type === c.type) ?? {}) }));
  const transferFrom = (wh) => {
    const s = live.transfers
      ? live.transfers.find(t => t.fromId === wh.id)
      : (wh.id === "WH-DEL" ? { toId: "WH-MUM", toCity: "Mumbai", skuId: "SKU002", units: 200 } : null);
    return s && whList.some(w => w.id === s.toId) ? s : null;
  };

  // Dynamic chart based on selected SKU
  const selectedSku = skuData.find(s => s.id === selected);
  const distChart = (selectedSku && live.distributorScores?.[selectedSku.id]) || distributors.map(d => {
    let contextualScore = d.score;
    if (selectedSku) {
      if (selectedSku.cat === "Dairy" && d.name.includes("Reliance")) contextualScore += 4;
      if (selectedSku.cat === "Snacks" && d.name.includes("HUL")) contextualScore += 5;
      if (selectedSku.cat === "Staples" && d.name.includes("Metro")) contextualScore += 3;
    }
    return { 
      name: d.name.split(" ")[0], 
      tat: parseInt(d.tat), 
      reliability: d.reliability, 
      score: Math.min(98, contextualScore) 
    };
  });

  // Price history data per SKU (simulated)
  const getPriceHistory = (sku) => [
    { month:"Aug", price: sku.cogs * 0.92 },
    { month:"Sep", price: sku.cogs * 0.95 },
    { month:"Oct", price: sku.cogs * 0.97 },
    { month:"Nov", price: sku.cogs * 1.02 },
    { month:"Dec", price: sku.cogs * 1.00 },
    { month:"Jan", price: sku.cogs * 0.98 },
  ];

  // Generate and download a dummy receipt as printable HTML
  const downloadReceipt = (sku, dist, units, price, serverOrderId, receiptWindow) => {
    const total = (units * price).toFixed(2);
    const now = new Date();
    const orderId = serverOrderId ?? `PO-${Date.now().toString(36).toUpperCase()}`;

    const htmlContent = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>${i18n[lang].replenish} - ${orderId}</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Segoe UI', Arial, sans-serif; padding:40px; color:#222; background:#fff; max-width:700px; margin:0 auto; }
  .header { text-align:center; border-bottom:3px solid #222; padding-bottom:20px; margin-bottom:24px; }
  .header h1 { font-size:22px; letter-spacing:2px; margin-bottom:4px; }
  .header p { font-size:11px; color:#666; letter-spacing:1px; }
  .order-info { display:flex; justify-content:space-between; margin-bottom:20px; font-size:13px; }
  .section { margin-bottom:20px; }
  .section-title { font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:2px; color:#555; border-bottom:1px solid #ddd; padding-bottom:6px; margin-bottom:10px; }
  .row { display:flex; justify-content:space-between; padding:6px 0; font-size:13px; border-bottom:1px solid #f0f0f0; }
  .row .label { color:#666; }
  .row .value { font-weight:600; color:#222; }
  .total-row { display:flex; justify-content:space-between; padding:12px 0; font-size:16px; font-weight:700; border-top:2px solid #222; margin-top:8px; }
  .status { text-align:center; margin-top:24px; padding:16px; background:#f0f8f0; border:1px solid #c0e0c0; border-radius:8px; }
  .status h3 { color:#2a7a2a; font-size:15px; margin-bottom:4px; }
  .status p { color:#555; font-size:12px; }
  .footer { text-align:center; margin-top:30px; font-size:10px; color:#999; border-top:1px solid #eee; padding-top:16px; }
  @media print { body { padding:20px; } }
</style></head><body>
  <div class="header">
    <h1>SARTHI</h1>
    <p>${i18n[lang].replenish} &mdash; DELOITTE HACKATHON 2026</p>
  </div>
  <div class="order-info">
    <div><strong>${i18n[lang].orderId || "Order ID"}:</strong> ${orderId}</div>
    <div><strong>${i18n[lang].date || "Date"}:</strong> ${now.toLocaleDateString(lang === "EN" ? "en-IN" : "hi-IN")} &nbsp; ${now.toLocaleTimeString(lang === "EN" ? "en-IN" : "hi-IN")}</div>
  </div>
  <div class="section">
    <div class="section-title">${i18n[lang].productDetails || "Product Details"}</div>
    <div class="row"><span class="label">SKU ID</span><span class="value">${sku.id}</span></div>
    <div class="row"><span class="label">${i18n[lang].sku}</span><span class="value">${i18n[lang][sku.id.toLowerCase()] || sku.name}</span></div>
    <div class="row"><span class="label">${i18n[lang].category || "Category"}</span><span class="value">${i18n[lang]["category" + sku.cat.replace(/\s/g, "")] || sku.cat}</span></div>
    <div class="row"><span class="label">${i18n[lang].quantity}</span><span class="value">${units} ${i18n[lang].units.toLowerCase()}</span></div>
    <div class="row"><span class="label">${i18n[lang].unitPrice}</span><span class="value">&#8377;${price}</span></div>
    <div class="total-row"><span>${i18n[lang].total}</span><span>&#8377;${total}</span></div>
  </div>
  <div class="section">
    <div class="section-title">${i18n[lang].distributorDetails || "Distributor Details"}</div>
    <div class="row"><span class="label">${i18n[lang].sku || "Name"}</span><span class="value">${i18n[lang][dist.name.split(" ")[0].toLowerCase()] || dist.name}</span></div>
    <div class="row"><span class="label">${i18n[lang].tierLabel}</span><span class="value">${dist.tier}</span></div>
    <div class="row"><span class="label">${i18n[lang].tatLabel}</span><span class="value">${dist.tat}</span></div>
    <div class="row"><span class="label">${i18n[lang].reliabilityLabel}</span><span class="value">${dist.reliability}%</span></div>
    <div class="row"><span class="label">${i18n[lang].incentiveLabel}</span><span class="value">${dist.incentive}</span></div>
  </div>
  <div class="section">
    <div class="section-title">${i18n[lang].inventoryContext || "Inventory Context"}</div>
    <div class="row"><span class="label">${i18n[lang].currentStock}</span><span class="value">${sku.stock} ${i18n[lang].units.toLowerCase()}</span></div>
    <div class="row"><span class="label">${i18n[lang].safetyStock}</span><span class="value">${sku.safetyStock} ${i18n[lang].units.toLowerCase()}</span></div>
    <div class="row"><span class="label">${i18n[lang].reorderPoint}</span><span class="value">${sku.reorderPoint} ${i18n[lang].units.toLowerCase()}</span></div>
    <div class="row"><span class="label">${i18n[lang].leadTime}</span><span class="value">${sku.lead} ${i18n[lang].days || "days"}</span></div>
    <div class="row"><span class="label">${i18n[lang].stockoutProb || "Stockout Risk"}</span><span class="value">${sku.risk}%</span></div>
  </div>
  <div class="status">
    <h3>&#10003; ${i18n[lang].orderPlacedSuccess}</h3>
    <p>${i18n[lang].orderSuccessMsg}</p>
  </div>
  <div class="footer">
    <p>Generated by Sarthi ARS/v2 &bull; Deloitte Hackathon 2026</p>
  </div>
  <script>window.onload = function() { window.print(); }</script>
</body></html>`;

    if (!receiptWindow) return;
    receiptWindow.document.write(htmlContent);
    receiptWindow.document.close();
  };

  // Handle proceed from confirmation
  const handleProceed = async () => {
    if (!confirmModal) return;
    const sku = skuData.find(s => s.id === confirmModal.skuId);
    const dist = distributors[confirmModal.distIdx];
    const units = orderUnits[confirmModal.skuId] || (sku.recommendedQty ?? Math.round(sku.vel * sku.lead * 1.3));
    const price = orderPrice[confirmModal.skuId] || (live.replenishment?.[sku.id]?.price ?? sku.cogs);
    // Open the receipt tab now, while the click still counts as the user's, and fill it once the order is placed.
    // (It used to be opened with "noopener", for which the browser returns no window to write the receipt into.)
    const receiptWindow = window.open("", "_blank");
    if (receiptWindow) receiptWindow.opener = null;
    const r = await api.post("/orders", { skuId: sku.id, distributor: dist.name, units, price, esgIndex: selectedEsg });
    downloadReceipt(sku, dist, units, price, r?.orderId, receiptWindow);
    setOrderSuccess({ sku, dist, units, price });
    setConfirmModal(null);
    setDraftOrder(null);
    refreshData();
    if (r?.runStarted) refreshAfterRun();
  };

  return (
    <div>
      <div style={{ marginBottom:48 }}>
        <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:3, color:C.muted, textTransform:"uppercase", marginBottom:12 }}>
           {i18n[lang].supplyChainOps}
        </div>
        <h1 style={{ fontFamily:"'Sora'", fontSize:48, fontWeight:700, color:C.text, margin:0, letterSpacing:"-0.03em" }}>
          {i18n[lang].replenishHub}
        </h1>
      </div>


      {/* ── ORDER SUCCESS SCREEN ── */}
      {orderSuccess && (
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.7)", zIndex:1000,
          display:"flex", alignItems:"center", justifyContent:"center",
          backdropFilter:"blur(8px)"
        }}>
          <div style={{
            background:C.surface, border:`2px solid ${C.sweet}44`, borderRadius:20,
            padding:"48px", maxWidth:500, width:"90%", textAlign:"center",
            animation:"fadeIn 0.4s ease"
          }}>
            <div style={{ width:80, height:80, borderRadius:"50%", background:C.sweet+"22",
              border:`3px solid ${C.sweet}`, display:"flex", alignItems:"center", justifyContent:"center",
              margin:"0 auto 24px", color:C.sweet
            }}>
              <CheckCircle size={48} />
            </div>
            <h2 style={{ fontFamily:"'Sora'", fontSize:28, fontWeight:700, color:C.sweet, margin:"0 0 8px" }}>
              {i18n[lang].orderPlacedSuccess}
            </h2>
            <div style={{ fontFamily:"'Inter'", fontSize:16, color:C.muted, lineHeight:1.7, marginBottom:24 }}>
              {i18n[lang].orderPlacedMsg.replace("{sku}", orderSuccess.sku.name).replace("{dist}", orderSuccess.dist.name)}
            </div>
            <div style={{
              background:C.bg, border:`1px solid ${C.border}`, borderRadius:12,
              padding:"16px", marginBottom:24, textAlign:"left"
            }}>
              {[
                { l:i18n[lang].quantity, v:`${orderSuccess.units} units` },
                { l:i18n[lang].unitPrice, v:`₹${orderSuccess.price}` },
                { l:i18n[lang].total, v:`₹${(orderSuccess.units * orderSuccess.price).toFixed(2)}` },
                { l:i18n[lang].distributor, v:orderSuccess.dist.name },
                { l:i18n[lang].expectedDelivery, v:orderSuccess.dist.tat },
              ].map(r => (
                <div key={r.l} style={{ display:"flex", justifyContent:"space-between", padding:"6px 0",
                  fontFamily:"'DM Mono'", fontSize:15, borderBottom:`1px solid ${C.border}22`
                }}>
                  <span style={{color:C.muted}}>{r.l}</span>
                  <span style={{color:C.text, fontWeight:600}}>{r.v}</span>
                </div>
              ))}
            </div>
            <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.sweet, marginBottom:20 }}>
              ✓ {i18n[lang].receiptDownloaded}
            </div>
            <button onClick={() => setOrderSuccess(null)} style={{
              background:C.text, color:C.bg, border:"none", borderRadius:10,
              padding:"14px 40px", fontFamily:"'Inter'", fontWeight:700, fontSize:16,
              cursor:"pointer", transition:"all 0.2s"
            }}
              onMouseEnter={e => e.currentTarget.style.opacity="0.9"}
              onMouseLeave={e => e.currentTarget.style.opacity="1"}
            >
              {i18n[lang].done}
            </button>
          </div>
        </div>
      )}

      {/* ── CONFIRMATION MODAL — "Are you sure?" ── */}
      {confirmModal && (() => {
        const sku = skuData.find(s => s.id === confirmModal.skuId);
        const dist = distributors[confirmModal.distIdx];
        const units = orderUnits[confirmModal.skuId] || (sku.recommendedQty ?? Math.round(sku.vel * sku.lead * 1.3));
        const price = orderPrice[confirmModal.skuId] || (live.replenishment?.[sku.id]?.price ?? sku.cogs);
        return (
          <div style={{
            position:"fixed", inset:0, background:"rgba(0,0,0,0.6)", zIndex:1000,
            display:"flex", alignItems:"flex-start", justifyContent:"center",
            backdropFilter:"blur(6px)", paddingTop:"60px"
          }}>
            <div style={{
              background:C.surface, border:`1px solid ${C.border}`, borderRadius:16,
              padding:"36px", maxWidth:440, width:"90%", textAlign:"center",
              animation:"fadeIn 0.3s ease"
            }}>
            <div style={{ color:C.chaos, marginBottom:16 }}>
                <AlertTriangle size={48} style={{ margin:"0 auto" }} />
              </div>
              <h3 style={{ fontFamily:"'Sora'", fontSize:22, fontWeight:700, color:C.text, margin:"0 0 12px" }}>
                {i18n[lang].areYouSure}
              </h3>
              <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted, lineHeight:1.7, marginBottom:24 }}>
                {i18n[lang].confirmOrderBody
                  .replace("{units}", units)
                  .replace("{sku}", sku.name)
                  .replace("{dist}", dist.name)
                  .replace("{price}", price)}
              </div>
              <div style={{ display:"flex", gap:12, justifyContent:"center" }}>
                <button onClick={() => setConfirmModal(null)} style={{
                  background:"transparent", color:C.muted, border:`1px solid ${C.border}`,
                  borderRadius:10, padding:"12px 32px", fontFamily:"'Inter'", fontWeight:600,
                  fontSize:16, cursor:"pointer", transition:"all 0.2s"
                }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor=C.muted; e.currentTarget.style.color=C.text; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor=C.border; e.currentTarget.style.color=C.muted; }}
                >
                  {i18n[lang].declineLabel}
                </button>
                <button onClick={handleProceed} style={{
                  background:C.sweet, color:C.bg, border:"none", borderRadius:10,
                  padding:"12px 32px", fontFamily:"'Inter'", fontWeight:700, fontSize:16,
                  cursor:"pointer", transition:"all 0.2s",
                  boxShadow:`0 4px 16px ${C.sweet}44`
                }}
                  onMouseEnter={e => e.currentTarget.style.opacity="0.9"}
                  onMouseLeave={e => e.currentTarget.style.opacity="1"}
                >
                  {i18n[lang].proceedLabel}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* ── AGENT RECOMMENDATIONS POPUP (Draft Order view) ── */}
      {draftOrder && (() => {
        const sku = skuData.find(s => s.id === draftOrder.skuId);
        const z = zoneInfo[sku.zone];
        const agentUnits = (sku.recommendedQty ?? Math.round(sku.vel * sku.lead * 1.3));
        const units = orderUnits[sku.id] ?? agentUnits;
        const price = orderPrice[sku.id] ?? (live.replenishment?.[sku.id]?.price ?? sku.cogs);
        const priceHistory = live.replenishment?.[sku.id]?.priceHistory ?? getPriceHistory(sku);
        const popupDistIdx = selectedDist[sku.id] ?? draftOrder.distIdx;
        const dist = distributors[popupDistIdx];

        return (
          <div style={{
            position:"fixed", inset:0, background:"rgba(0,0,0,0.7)", zIndex:1000,
            display:"flex", alignItems:"center", justifyContent:"center",
            backdropFilter:"blur(8px)"
          }} onClick={(e) => { if (e.target === e.currentTarget) setDraftOrder(null); }}>
            <div style={{
              background:C.surface, border:`1px solid ${C.border}`, borderRadius:20,
              padding:"36px", maxWidth:620, width:"95%", maxHeight:"90vh", overflowY:"auto",
              animation:"fadeIn 0.3s ease",
              boxShadow:`0 24px 80px rgba(0,0,0,0.6), 0 0 60px ${z.color}15`
            }}>
              {/* Header */}
              <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:24 }}>
                <div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:3, textTransform:"uppercase", color:C.muted, marginBottom:6 }}>
                    {i18n[lang].agentRecommendsTitle}
                  </div>
                  <div style={{ fontFamily:"'Sora'", fontSize:24, fontWeight:700, color:C.text }}>
                    {sku.name}
                  </div>
                </div>
                <button onClick={() => setDraftOrder(null)} style={{
                  background:"transparent", border:`1px solid ${C.border}`, color:C.muted,
                  borderRadius:8, padding:"8px 16px", fontFamily:"'Inter'", fontSize:15,
                  cursor:"pointer", transition:"all 0.2s", display:"flex", alignItems:"center", gap:6
                }}
                  onMouseEnter={e => e.currentTarget.style.color=C.text}
                  onMouseLeave={e => e.currentTarget.style.color=C.muted}
                ><X size={16} /> {i18n[lang].close}</button>
              </div>

              <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:20 }}>
                {/* Units — Editable */}
                <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:12, padding:"18px" }}>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, textTransform:"uppercase", letterSpacing:1, marginBottom:10 }}>
                    ● {i18n[lang].units}
                  </div>
                  <div style={{ display:"flex", alignItems:"center", gap:10 }}>
                    <input
                      type="number"
                      value={units}
                      onChange={e => setOrderUnits(prev => ({ ...prev, [sku.id]: parseInt(e.target.value) || 0 }))}
                      style={{
                        background:C.surface, border:`1px solid ${C.borderLight}`, borderRadius:8,
                        padding:"10px 14px", color:C.text, fontFamily:"'Sora'", fontSize:22,
                        fontWeight:700, width:120, outline:"none"
                      }}
                    />
                    <span style={{ fontFamily:"'Inter'", fontSize:15, color:C.muted }}>{i18n[lang].units.toLowerCase()}</span>
                  </div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.sweet, marginTop:6 }}>
                    {i18n[lang].aiRecommended} {agentUnits} {i18n[lang].units.toLowerCase()}
                  </div>
                </div>

                {/* Price — Editable */}
                <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:12, padding:"18px" }}>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, textTransform:"uppercase", letterSpacing:1, marginBottom:10 }}>
                    ● {i18n[lang].priceEdit}
                  </div>
                  <div style={{ display:"flex", alignItems:"center", gap:8 }}>
                    <span style={{ fontFamily:"'Sora'", fontSize:22, color:C.text, fontWeight:700 }}>₹</span>
                    <input
                      type="number"
                      step="0.1"
                      value={price}
                      onChange={e => setOrderPrice(prev => ({ ...prev, [sku.id]: parseFloat(e.target.value) || 0 }))}
                      style={{
                        background:C.surface, border:`1px solid ${C.borderLight}`, borderRadius:8,
                        padding:"10px 14px", color:C.text, fontFamily:"'Sora'", fontSize:22,
                        fontWeight:700, width:120, outline:"none"
                      }}
                    />
                  </div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginTop:6 }}>
                    {i18n[lang].cogs}: ₹{sku.cogs} · {i18n[lang].total}: <span style={{color:C.money, fontWeight:600}}>₹{(units * price).toFixed(2)}</span>
                  </div>
                </div>
              </div>

              {/* Price History Chart */}
              <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:12, padding:"18px", marginBottom:20 }}>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, textTransform:"uppercase", letterSpacing:1, marginBottom:10 }}>
                  ● {i18n[lang].priceHistoryTitle}
                </div>
                <ResponsiveContainer width="100%" height={120}>
                  <LineChart data={priceHistory}>
                    <CartesianGrid stroke={C.border} strokeDasharray="4 4" vertical={false} />
                    <XAxis dataKey="month" tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} domain={['dataMin - 5', 'dataMax + 5']} />
                    <Tooltip content={<CustomTooltip />} />
                    <Line type="monotone" dataKey="price" stroke={C.ghost} strokeWidth={2.5} dot={{ r:3, fill:C.ghost, stroke:C.bg, strokeWidth:2 }} name="Price (₹)" />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Distributor Selection */}
              <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:12, padding:"18px", marginBottom:20 }}>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, textTransform:"uppercase", letterSpacing:1, marginBottom:12 }}>
                  ● {i18n[lang].distributionSelection}
                </div>
                {distributors.map((d, i) => {
                  const isChosen = i === popupDistIdx;
                  return (
                    <div key={d.name}
                      onClick={() => setSelectedDist(prev => ({ ...prev, [sku.id]: i }))}
                      style={{
                        background: isChosen ? z.color+"18" : "transparent",
                        border:`1.5px solid ${isChosen ? z.color : C.border}`,
                        borderRadius:10, padding:"12px 16px", marginBottom:6,
                        display:"flex", justifyContent:"space-between", alignItems:"center",
                        cursor:"pointer", transition:"all 0.2s"
                      }}
                      onMouseEnter={e => { if(!isChosen) e.currentTarget.style.borderColor = z.color+"66"; }}
                      onMouseLeave={e => { if(!isChosen) e.currentTarget.style.borderColor = C.border; }}
                    >
                      <div style={{ display:"flex", alignItems:"center", gap:10 }}>
                        <div style={{
                          width:16, height:16, borderRadius:"50%",
                          border:`2px solid ${isChosen ? z.color : C.muted}`,
                          background: isChosen ? z.color : "transparent",
                          display:"flex", alignItems:"center", justifyContent:"center",
                          flexShrink:0, transition:"all 0.2s"
                        }}>
                          {isChosen && <div style={{ width:5, height:5, borderRadius:"50%", background:C.bg }} />}
                        </div>
                        <div>
                          <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.text, fontWeight:500 }}>
                            {i===0 && <span style={{ color:C.money, fontSize:15, marginRight:6 }}>★ {i18n[lang].aiPick}</span>}
                            {i18n[lang][d.name.split(" ")[0].toLowerCase()] || d.name}
                          </div>
                          <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginTop:2 }}>
                            {d.tier} · TAT: {d.tat} · Reliability: {d.reliability}%
                          </div>
                        </div>
                      </div>
                      <div style={{ fontFamily:"'Sora'", fontSize:18, color: isChosen ? z.color : C.text, fontWeight:700 }}>{d.score}</div>
                    </div>
                  );
                })}
              </div>

              {/* Send Quotation Button */}
              <button
                onClick={() => { setConfirmModal({ skuId: sku.id, distIdx: popupDistIdx }); setDraftOrder(null); }}
                style={{
                  width:"100%", background:`linear-gradient(135deg, ${z.color}, ${C.ghost})`,
                  color:C.bg, border:"none", borderRadius:10, padding:"16px",
                  fontFamily:"'Inter'", fontWeight:700, fontSize:15, cursor:"pointer",
                  transition:"all 0.3s", boxShadow:`0 6px 24px ${z.color}44`,
                  display:"flex", alignItems:"center", justifyContent:"center", gap:10
                }}
                onMouseEnter={e => { e.currentTarget.style.transform="translateY(-2px)"; e.currentTarget.style.boxShadow=`0 10px 30px ${z.color}66`; }}
                onMouseLeave={e => { e.currentTarget.style.transform="translateY(0)"; e.currentTarget.style.boxShadow=`0 6px 24px ${z.color}44`; }}
              >
                {i18n[lang].sendQuotationDist}
              </button>
            </div>
          </div>
        );
      })()}

      {selectedSku && !draftOrder && (
        <div className="glass" style={{ borderRadius:16, padding:"28px", marginBottom:24 }}>
          <SectionLabel>{i18n[lang].distScoreFor} {selectedSku.name}</SectionLabel>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={distChart} layout="vertical">
              <CartesianGrid stroke={C.border} strokeDasharray="4 4" horizontal={false} />
              <XAxis type="number" domain={[0,100]} tick={{ fill:C.muted, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={{ fill:C.text, fontFamily:"'DM Mono'", fontSize:15 }} axisLine={false} tickLine={false} width={100} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="score" fill={C.ghost} radius={[0,4,4,0]} name={i18n[lang].sarthiScore} />
              <Bar dataKey="reliability" fill={C.sweet} radius={[0,4,4,0]} name={i18n[lang].reliability} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <SectionLabel>{i18n[lang].criticalSkusAction}</SectionLabel>
      <div style={{ display:"flex", flexDirection:"column", gap:10, marginBottom:32 }}>
        {critical.map(sku => {
          const z = zoneInfo[sku.zone];
          const open = selected === sku.id;
          const chosenIdx = selectedDist[sku.id] ?? 0;
          const chosenDist = distributors[chosenIdx];
          return (
            <div key={sku.id} style={{
              background:C.surface, border:`1px solid ${open ? z.color+"66" : C.border}`,
              borderRadius:12, padding:"20px", transition:"border-color 0.2s"
            }}>
              <div
                onClick={() => setSelected(open ? null : sku.id)}
                style={{ display:"flex", justifyContent:"space-between", alignItems:"center", cursor:"pointer" }}
              >
                <div>
                  <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.text, fontWeight:500 }}>{sku.name}</div>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:z.color, marginTop:4 }}>
                    {i18n[lang].riskStockRemaining
                      .replace("{risk}", sku.risk)
                      .replace("{stock}", sku.stock)} · {i18n[lang].daysLeft.replace("{days}", Math.round(sku.stock/sku.vel))}
                  </div>
                </div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{open ? "▲" : "▼"}</div>
              </div>

              {open && (
                <div style={{ marginTop:20, borderTop:`1px solid ${C.border}`, paddingTop:20 }}>
                  <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginBottom:12, textTransform:"uppercase", letterSpacing:2 }}>
                    {i18n[lang].selectDistributor}
                  </div>
                  {distributors.map((d,i) => {
                    const isChosen = i === chosenIdx;
                    return (
                      <div
                        key={d.name}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedDist(prev => ({ ...prev, [sku.id]: i }));
                        }}
                        style={{
                          background: isChosen ? z.color+"18" : C.bg,
                          border:`2px solid ${isChosen ? z.color : C.border}`,
                          borderRadius:10, padding:"14px 18px", marginBottom:8,
                          display:"flex", justifyContent:"space-between", alignItems:"center",
                          cursor:"pointer", transition:"all 0.2s",
                        }}
                        onMouseEnter={e => { if(!isChosen) e.currentTarget.style.borderColor = z.color+"66"; }}
                        onMouseLeave={e => { if(!isChosen) e.currentTarget.style.borderColor = C.border; }}
                      >
                        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
                          <div style={{
                            width:18, height:18, borderRadius:"50%",
                            border:`2px solid ${isChosen ? z.color : C.muted}`,
                            background: isChosen ? z.color : "transparent",
                            display:"flex", alignItems:"center", justifyContent:"center",
                            flexShrink:0, transition:"all 0.2s"
                          }}>
                            {isChosen && <div style={{ width:6, height:6, borderRadius:"50%", background:C.bg }} />}
                          </div>
                          <div>
                            <div style={{ fontFamily:"'Inter'", fontSize:15, color:C.text, fontWeight:500, display:"flex", alignItems:"center", gap:6 }}>
                              {i===0 && <span style={{ color:C.money, fontSize:15 }}>★ {i18n[lang].aiPick}</span>}
                              {i18n[lang][d.name.split(" ")[0].toLowerCase()] || d.name}
                            </div>
                            <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted, marginTop:3 }}>
                              TAT: {d.tat} · ₹{d.price}/unit · {d.incentive}
                            </div>
                          </div>
                        </div>
                        <div style={{ textAlign:"right" }}>
                          <div style={{ fontFamily:"'Sora'", fontSize:22, color: isChosen ? z.color : C.text, fontWeight:700 }}>{d.score}</div>
                          <div style={{ fontFamily:"'DM Mono'", fontSize:15, color:C.muted }}>{i18n[lang].scoreLabel}</div>
                        </div>
                      </div>
                    );
                  })}
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      setDraftOrder({ skuId: sku.id, distIdx: chosenIdx });
                    }}
                    style={{
                      width:"100%", background:z.color, color:C.bg, border:"none",
                      borderRadius:8, padding:"14px", fontFamily:"'Inter'", fontWeight:700,
                      fontSize:16, cursor:"pointer", marginTop:8, transition:"all 0.2s",
                      boxShadow:`0 4px 16px ${z.color}44`
                    }}
                    onMouseEnter={e => { e.currentTarget.style.opacity="0.88"; e.currentTarget.style.transform="translateY(-1px)"; }}
                    onMouseLeave={e => { e.currentTarget.style.opacity="1"; e.currentTarget.style.transform="translateY(0)"; }}
                  >
                    {i18n[lang].draftOrderWith.replace("{name}", i18n[lang][chosenDist.name.split(" ")[0].toLowerCase()] || chosenDist.name)}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── ESG SHIPPING COMPARISON — Agent 6 Compliance Guardian ── */}
      <div className="glass" style={{ borderRadius:20, padding:"32px", marginBottom:32 }}>
        <SectionLabel>{i18n[lang].esgComparisonTitle}</SectionLabel>
        <div style={{ fontFamily:"'Inter'", fontSize:13, color:C.muted, marginBottom:20, fontWeight:300 }}>
          {i18n[lang].esgComparisonDesc}
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:14 }}>
          {esgCards.map((opt, i) => {
            const isSelected = i === selectedEsg;
            return (
              <div key={opt.modeKey} onClick={() => setSelectedEsg(i)} style={{
                background: isSelected ? opt.color+"12" : C.bg,
                border:`2px solid ${isSelected ? opt.color : C.border}`,
                borderRadius:14, padding:"22px", cursor:"pointer",
                transition:"all 0.25s", position:"relative"
              }}>
                {i === (live.esg?.recommendedIndex ?? 2) && (
                  <div style={{
                    position:"absolute", top:-10, right:12,
                    fontFamily:"'DM Mono'", fontSize:10, letterSpacing:1, textTransform:"uppercase",
                    color:C.bg, background:C.sweet, borderRadius:4, padding:"3px 10px", fontWeight:700
                  }}>{i18n[lang].recommended}</div>
                )}
                <div style={{ color:opt.color, marginBottom:12 }}>
                  <opt.icon size={28} />
                </div>
                <div style={{ fontFamily:"'Sora'", fontSize:16, color:C.text, fontWeight:600, marginBottom:2 }}>{i18n[lang][opt.labelKey]}</div>
                <div style={{ fontFamily:"'DM Mono'", fontSize:12, color:opt.color, marginBottom:10 }}>{i18n[lang][opt.modeKey]}</div>
                <div style={{ fontFamily:"'Inter'", fontSize:12, color:C.muted, lineHeight:1.5, marginBottom:14 }}>{i18n[lang][opt.descKey]}</div>
                <div style={{ display:"flex", flexDirection:"column", gap:6 }}>
                  <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:12 }}>
                    <span style={{ color:C.muted }}>TAT</span>
                    <span style={{ color:C.text, fontWeight:500 }}>{opt.tat}</span>
                  </div>
                  <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:12 }}>
                    <span style={{ color:C.muted }}>{i18n[lang].total}</span>
                    <span style={{ color:C.text, fontWeight:500 }}>{opt.cost}</span>
                  </div>
                  <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:12, alignItems:"center" }}>
                    <span style={{ color:C.muted }}>CO₂</span>
                    <div style={{ display:"flex", alignItems:"center", gap:6 }}>
                      <div style={{ width:50, height:4, background:C.border, borderRadius:2 }}>
                        <div style={{ width:opt.co2Pct+"%", height:"100%", background:opt.co2Pct>70?C.chaos:opt.co2Pct>40?C.money:C.sweet, borderRadius:2 }} />
                      </div>
                      <span style={{ color: opt.co2Pct>70?C.chaos:opt.co2Pct>40?C.money:C.sweet, fontWeight:500 }}>{i18n[lang][opt.co2Key]}</span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── MULTI-WAREHOUSE TRANSFER — Agent 5 Overstock Resolver ── */}
      <div className="glass" style={{ borderRadius:20, padding:"32px", marginBottom:32 }}>
        <SectionLabel>{i18n[lang].multiWhTitle}</SectionLabel>
        <div style={{ fontFamily:"'Inter'", fontSize:13, color:C.muted, marginBottom:20, fontWeight:300 }}>
          {i18n[lang].multiWhDesc}
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"repeat(4, 1fr)", gap:12 }}>
          {whList.map(wh => (
            <div key={wh.id} style={{
              background:C.bg, border:`1px solid ${C.border}`, borderRadius:12, padding:"18px"
            }}>
              <div style={{ fontFamily:"'Sora'", fontSize:14, color:C.text, fontWeight:600, marginBottom:2 }}>{wh.name}</div>
              <div style={{ fontFamily:"'DM Mono'", fontSize:11, color:C.muted, marginBottom:12 }}>{wh.id} · {wh.city}</div>
              <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:11, marginBottom:4 }}>
                <span style={{ color:C.muted }}>{i18n[lang].utilization}</span>
                <span style={{ color: wh.utilization>0.8 ? C.chaos : wh.utilization>0.6 ? C.money : C.sweet, fontWeight:600 }}>{Math.round(wh.utilization*100)}%</span>
              </div>
              <div style={{ height:4, background:C.border, borderRadius:2, marginBottom:12 }}>
                <div style={{ height:"100%", width:Math.round(wh.utilization*100)+"%", background:wh.utilization>0.8?C.chaos:wh.utilization>0.6?C.money:C.sweet, borderRadius:2 }} />
              </div>
              {Object.entries(wh.stock).map(([skuId, qty]) => {
                const sku = skuData.find(s => s.id === skuId);
                if (!sku) return null;
                const isOver = qty > sku.safetyStock * 1.5;
                const isUnder = qty < sku.safetyStock * 0.5;
                return (
                  <div key={skuId} style={{
                    display:"flex", justifyContent:"space-between", alignItems:"center",
                    fontFamily:"'DM Mono'", fontSize:11, padding:"4px 0",
                    borderBottom:`1px solid ${C.border}22`
                  }}>
                    <span style={{ color:C.muted }}>{sku.name.split(" ")[0]}</span>
                    <span style={{ color: isOver ? C.money : isUnder ? C.chaos : C.text, fontWeight:500 }}>
                      {qty} {isOver ? "↑" : isUnder ? "↓" : ""}
                    </span>
                  </div>
                );
              })}
              {transferFrom(wh) && (
                <button onClick={() => { const s = transferFrom(wh); setTransferModal({ from:wh, to:whList.find(w => w.id === s.toId), skuId:s.skuId, units:s.units }); }} style={{
                  width:"100%", marginTop:10, background:C.sweet+"22", color:C.sweet,
                  border:`1px solid ${C.sweet}44`, borderRadius:6, padding:"8px",
                  fontFamily:"'DM Mono'", fontSize:11, cursor:"pointer", transition:"all 0.2s"
                }}>
                  {i18n[lang].transferOverstock.replace("{city}", transferFrom(wh).toCity)}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Transfer confirmation modal */}
      {transferModal && (
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.6)", zIndex:1000,
          display:"flex", alignItems:"center", justifyContent:"center",
          backdropFilter:"blur(6px)"
        }}>
          <div style={{
            background:C.surface, border:`1px solid ${C.sweet}33`, borderRadius:16,
            padding:"36px", maxWidth:440, width:"90%", textAlign:"center",
            animation:"fadeIn 0.3s ease"
          }}>
            <div style={{ color:C.sweet, marginBottom:16 }}>
                <RefreshCcw size={48} style={{ margin:"0 auto" }} />
              </div>
            <h3 style={{ fontFamily:"'Sora'", fontSize:20, fontWeight:700, color:C.text, margin:"0 0 12px" }}>
              {i18n[lang].createStockTransfer}
            </h3>
            <div style={{ fontFamily:"'Inter'", fontSize:14, color:C.muted, lineHeight:1.7, marginBottom:24 }}>
              {i18n[lang].transferConfirmBody
                .replace("{units}", transferModal.units)
                .replace("{sku}", skuData.find(s=>s.id===transferModal.skuId)?.name)
                .replace("{from}", transferModal.from.name)
                .replace("{to}", transferModal.to.name)}
            </div>
            <div style={{ display:"flex", gap:12, justifyContent:"center" }}>
                <button onClick={() => setTransferModal(null)} style={{
                background:"transparent", color:C.muted, border:`1px solid ${C.border}`,
                borderRadius:10, padding:"12px 28px", fontFamily:"'Inter'", fontWeight:600,
                fontSize:14, cursor:"pointer"
              }}>{i18n[lang].back}</button>
              <button onClick={async () => { const t = transferModal; const r = await api.post("/transfers", { skuId: t.skuId, fromId: t.from.id, toId: t.to.id, units: t.units }); setTransferSuccess({ ...t, id: r?.transferId }); setTransferModal(null); refreshData(); if (r?.runStarted) refreshAfterRun(); }} style={{
                background:C.sweet, color:C.bg, border:"none", borderRadius:10,
                padding:"12px 28px", fontFamily:"'Inter'", fontWeight:700, fontSize:14,
                cursor:"pointer", boxShadow:`0 4px 16px ${C.sweet}44`
              }}>{i18n[lang].done} ✓</button>
            </div>
          </div>
        </div>
      )}

      {/* Transfer Success */}
      {transferSuccess && (
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.6)", zIndex:1000,
          display:"flex", alignItems:"center", justifyContent:"center",
          backdropFilter:"blur(6px)"
        }}>
          <div style={{
            background:C.surface, border:`2px solid ${C.sweet}44`, borderRadius:20,
            padding:"48px", maxWidth:440, width:"90%", textAlign:"center",
            animation:"fadeIn 0.4s ease"
          }}>
            <div style={{ width:70, height:70, borderRadius:"50%", background:C.sweet+"22",
              border:`3px solid ${C.sweet}`, display:"flex", alignItems:"center", justifyContent:"center",
              margin:"0 auto 20px", color:C.sweet
            }}>
              <CheckCircle size={32} />
            </div>
            <h2 style={{ fontFamily:"'Sora'", fontSize:24, fontWeight:700, color:C.sweet, margin:"0 0 8px" }}>
              {i18n[lang].transferCreated}
            </h2>
            <div style={{ fontFamily:"'Inter'", fontSize:14, color:C.muted, lineHeight:1.7, marginBottom:20 }}>
              {i18n[lang].transferSuccessBody
                .replace("{units}", transferSuccess.units)
                .replace("{from}", transferSuccess.from.name)
                .replace("{to}", transferSuccess.to.name)}
            </div>
            <div style={{ fontFamily:"'DM Mono'", fontSize:12, color:C.sweet, marginBottom:16 }}>
              ✓ {i18n[lang].transferOrderGenerated.replace("{id}", transferSuccess.id ?? ("TRF-"+Date.now().toString(36).toUpperCase().slice(0,5)))}
            </div>
            <button onClick={() => setTransferSuccess(null)} style={{
              background:C.text, color:C.bg, border:"none", borderRadius:10,
              padding:"12px 36px", fontFamily:"'Inter'", fontWeight:700, fontSize:14, cursor:"pointer"
            }}>{i18n[lang].done}</button>
          </div>
        </div>
      )}

      {/* ── LAUNCH CAMPAIGN — Agent 7 Execution Engine ── */}
      <div className="glass" style={{ borderRadius:20, padding:"32px", marginBottom:32 }}>
        <SectionLabel>{i18n[lang].launchCampaignTitle}</SectionLabel>
        <div style={{ fontFamily:"'Inter'", fontSize:13, color:C.muted, marginBottom:20, fontWeight:300 }}>
          {i18n[lang].launchCampaignDesc}
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:14 }}>
          {camps.map((camp, i) => {
            const launched = campaignLaunched.includes(i);
            const zColor = camp.target === "ghost" ? C.ghost : camp.target === "chaos" ? C.chaos : C.money;
            return (
              <div key={camp.type} style={{
                background: launched ? zColor+"0a" : C.bg,
                border:`1.5px solid ${launched ? zColor : C.border}`,
                borderRadius:14, padding:"22px", transition:"all 0.2s"
              }}>
                <div style={{ color:zColor, marginBottom:10 }}>
                  <camp.icon size={28} />
                </div>
                <div style={{ fontFamily:"'Sora'", fontSize:15, color:C.text, fontWeight:600, marginBottom:2 }}>{i18n[lang][camp.labelKey]}</div>
                <Tag color={zColor} small>{zoneInfo[camp.target]?.label}</Tag>
                <div style={{ fontFamily:"'Inter'", fontSize:12, color:C.muted, lineHeight:1.5, margin:"10px 0", minHeight:36 }}>{i18n[lang][camp.descKey]}</div>
                <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:11, marginBottom:4 }}>
                  <span style={{ color:C.muted }}>{i18n[lang].discount}</span>
                  <span style={{ color:zColor, fontWeight:600 }}>{camp.discount}</span>
                </div>
                <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"'DM Mono'", fontSize:11, marginBottom:12 }}>
                  <span style={{ color:C.muted }}>{i18n[lang].estImpact}</span>
                  <span style={{ color:C.sweet, fontWeight:500 }}>{camp.estImpact ?? i18n[lang][camp.estKey]}</span>
                </div>
                {launched ? (
                  <div style={{ fontFamily:"'DM Mono'", fontSize:11, color:C.sweet, textAlign:"center", padding:"8px", background:C.sweet+"12", borderRadius:6 }}>
                    ✓ {i18n[lang].campaignLive}
                  </div>
                ) : (
                  <button onClick={() => { setCampaignLaunched(prev => [...prev, i]); api.post("/campaigns", { type: camp.type }); }} style={{
                    width:"100%", background:zColor, color:C.bg, border:"none",
                    borderRadius:8, padding:"10px", fontFamily:"'Inter'", fontWeight:700,
                    fontSize:13, cursor:"pointer", transition:"all 0.2s"
                  }}>
                    {i18n[lang].launchCampaignAction}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}


export default Replenish;
