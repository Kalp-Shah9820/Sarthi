import { C } from "../theme.js";
import { 
  Home, LayoutDashboard, Package, RefreshCw, Store, MessageSquare, Bell, Settings,
  Zap, AlertTriangle, Wind, TrendingDown, Info, Eye, CheckCircle, Search, 
  Box, BarChart3, Warehouse, Users, TrendingUp, MapPin, Leaf, Plane, Ship, Train, Tag as LucideTag, Gift, X, Mic, Globe
} from "lucide-react";

const IconMap = {
  Home,
  LayoutDashboard,
  Package,
  RefreshCw,
  Store,
  MessageSquare,
  Bell,
  Settings,
  Zap,
  AlertTriangle,
  Wind,
  TrendingDown,
  Info,
  Eye,
  CheckCircle,
  Search,
  Box,
  BarChart3,
  Warehouse,
  Users,
  TrendingUp,
  MapPin,
  Leaf,
  Plane,
  Ship,
  Train,
  Tag: LucideTag,
  Gift,
  X,
  Mic,
  Globe
};

function SarthiIcon({ name, size = 20, color, strokeWidth = 2, ...props }) {
  const IconComponent = IconMap[name] || Info;
  return <IconComponent size={size} color={color || "currentColor"} strokeWidth={strokeWidth} {...props} />;
}

function Tag({ color, children, small }) {
  return (
    <span style={{
      display:"inline-block", background: color+"18", border:`1px solid ${color}44`,
      color, borderRadius:6, padding: small ? "3px 10px" : "6px 14px",
      fontFamily:"'DM Mono'", fontSize: small ? 11 : 12, letterSpacing:1.5, textTransform:"uppercase",
      fontWeight:600
    }}>{children}</span>
  );
}

function Divider() {
  return <div style={{ height:1, background: C.border, margin:"32px 0" }} />;
}

function SectionLabel({ children }) {
  return (
    <div style={{ fontFamily:"'DM Mono'", fontSize:15, letterSpacing:3, textTransform:"uppercase", color:C.muted, marginBottom:24, fontWeight:500 }}>
      {children}
    </div>
  );
}

// ─── CUSTOM TOOLTIP ────────────────────────────────────────────────────────────
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background:"#1a1a1a", border:`1px solid ${C.border}`, borderRadius:8, padding:"10px 14px" }}>
      <div style={{ color:C.muted, fontFamily:"'DM Mono'", fontSize:15, marginBottom:4 }}>{label}</div>
      {payload.map((p,i) => (
        <div key={i} style={{ color:p.color||C.text, fontFamily:"'DM Mono'", fontSize:15 }}>{p.name}: {p.value}</div>
      ))}
    </div>
  );
}

export { Tag, Divider, SectionLabel, CustomTooltip, SarthiIcon };
