import { useState } from "react";
import { Globe } from "lucide-react";
import { useSarthi } from "../context/SarthiContext.jsx";
import { i18n } from "../data/i18n.js";
import { C } from "../theme.js";

const languages = [
  { code: "EN", name: "English" },
  { code: "HI", name: "Hindi" },
  { code: "TA", name: "Tamil" },
  { code: "BN", name: "Bengali" },
  { code: "TE", name: "Telugu" },
  { code: "MR", name: "Marathi" },
  { code: "GJ", name: "Gujarati" },
  { code: "KN", name: "Kannada" }
];

export default function LanguageSelector() {
  const { lang, setLang } = useSarthi();
  const [isOpen, setIsOpen] = useState(false);

  const currentLang = languages.find(l => l.code === lang) || languages[0];

  return (
    <div style={{ position: "fixed", bottom: 110, right: 32, zIndex: 9999 }}>
      {isOpen && (
        <div className="glass" style={{
          position: "absolute", bottom: 70, right: 0, width: 200,
          borderRadius: 16, overflow: "hidden", animation: "fadeIn 0.2s ease"
        }}>
          {languages.map(l => (
            <button
              key={l.code}
              onClick={() => { setLang(l.code); setIsOpen(false); }}
              style={{
                width: "100%", display: "flex", alignItems: "center", gap: 12,
                padding: "12px 16px", background: lang === l.code ? C.faint : "transparent",
                border: "none", cursor: "pointer", color: C.text, fontFamily: "'Inter'",
                fontSize: 14, transition: "all 0.2s", textAlign: "left"
              }}
              onMouseEnter={e => e.currentTarget.style.background = C.faint}
              onMouseLeave={e => e.currentTarget.style.background = lang === l.code ? C.faint : "transparent"}
            >
              <span>{l.name}</span>
            </button>
          ))}
        </div>
      )}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="glass"
        style={{
          width: 56, height: 56, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 24, cursor: "pointer", color: C.accent,
          border: `1px solid ${C.accent}44`, boxShadow: `0 8px 32px ${C.accent}22`
        }}
      >
        <Globe size={24} />
        <div style={{
          position: "absolute", top: -5, right: -5, background: C.accent,
          color: C.bg, fontSize: 10, padding: "2px 5px", borderRadius: 4,
          fontWeight: 700, fontFamily: "'DM Mono'"
        }}>
          {lang}
        </div>
      </button>
    </div>
  );
}
