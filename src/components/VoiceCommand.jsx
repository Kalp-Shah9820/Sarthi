import { useState, useEffect, useRef } from "react";
import { Mic } from "lucide-react";
import { C } from "../theme.js";
import { i18n } from "../data/i18n.js";
import { useSarthi } from "../context/SarthiContext.jsx";
import { api } from "../api/client.js";

const skuAliases = {
  "lays": "SKU001",
  "classic": "SKU001",
  "namkeen": "SKU002",
  "haldirams": "SKU002",
  "colgate": "SKU003",
  "toothpaste": "SKU003",
  "surf": "SKU004",
  "excel": "SKU004",
  "detergent": "SKU004",
  "fortune": "SKU005",
  "oil": "SKU005",
  "sunflower": "SKU005",
  "tata": "SKU006",
  "salt": "SKU006"
};

export default function VoiceCommand({ onCommand }) {
  const { lang, refreshAfterRun } = useSarthi();
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");
  const recognitionRef = useRef(null);

  useEffect(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = true;
      recognitionRef.current.lang = lang === "EN" ? "en-IN" : "hi-IN";

      recognitionRef.current.onresult = (event) => {
        const current = event.resultIndex;
        const resultTranscript = event.results[current][0].transcript;
        setTranscript(resultTranscript);
        
        if (event.results[current].isFinal) {
          handleFinalTranscript(resultTranscript.toLowerCase());
        }
      };

      recognitionRef.current.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current.onerror = (event) => {
        console.error("Speech Recognition Error:", event.error);
        setError("Error: " + event.error);
        setIsListening(false);
      };
    } else {
      setError(i18n[lang].voiceUnsupported);
    }
  }, [lang]);

  // the backend understands the sentence against the real catalogue; offline, the keyword parsing below is used
  const handleFinalTranscript = async (text) => {
    console.log("Final Transcript:", text);
    const cmd = await api.post("/voice/intent", { text, lang });
    if (cmd) { onCommand(cmd); if (cmd.type === "PROCURE") refreshAfterRun(); } else localParse(text);
    setTimeout(() => setTranscript(""), 3000);
  };

  const localParse = (text) => {
    // Simple Keyword Parsing
    let matchedSku = null;
    for (const [alias, id] of Object.entries(skuAliases)) {
      if (text.includes(alias)) {
        matchedSku = id;
        break;
      }
    }

    const check = (key) => text.includes(i18n[lang][key]) || text.includes(i18n["EN"][key]) || text.includes(i18n["HI"][key]);

    if (check("increase") && check("safetyStock") && matchedSku) {
      onCommand({ type: "PROCURE", skuId: matchedSku, text });
    } else if (check("show") && (check("zone") || text.includes("inventory") || text.includes("इन्वेंट्री")) && matchedSku) {
        onCommand({ type: "NAVIGATE_SKU", skuId: matchedSku, text });
    } else if (check("risk") && matchedSku) {
        onCommand({ type: "SKU_DETAIL", skuId: matchedSku, text });
    } else if (check("switchTo")) {
        const targetLang = (check("hindi") || text.includes("हिन्दी")) ? "HI" : "EN";
        onCommand({ type: "SET_LANG", lang: targetLang, text });
    } else if (check("warRoom") || check("scenario") || check("disruption")) {
        onCommand({ type: "NAVIGATE", path: "sandbox", text });
    } else {
        // Fallback or suggest
        onCommand({ type: "UNKNOWN", text });
    }
  };

  const toggleListen = () => {
    if (isListening) {
      recognitionRef.current?.stop();
    } else {
      setTranscript("");
      setError("");
      recognitionRef.current?.start();
      setIsListening(true);
    }
  };

  return (
    <div style={{ position: "fixed", bottom: 32, right: 32, zIndex: 10000, display: "flex", flexDirection: "column-reverse", alignItems: "flex-end", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <button
          onClick={toggleListen}
          style={{
            width: 64, height: 64, borderRadius: "50%",
            background: isListening ? C.chaos : C.text,
            border: "none", cursor: "pointer", display: "flex",
            alignItems: "center", justifyContent: "center", fontSize: 28,
            color: isListening ? C.text : C.bg,
            boxShadow: `0 8px 32px ${isListening ? C.chaos : C.accent}44`,
            animation: isListening ? "pulse-red 1.5s infinite" : "none",
            transition: "all 0.3s"
          }}
        >
          {isListening ? "●" : <Mic size={28} />}
        </button>
        {error && (
          <div className="glass" style={{ padding: "8px 16px", borderRadius: 8, color: C.chaos, fontSize: 12, fontFamily: "'DM Mono'" }}>
            {error}
          </div>
        )}
      </div>

      {(transcript || isListening) && (
        <div className="glass" style={{ 
          padding: "12px 20px", borderRadius: 12, border: `1px solid ${C.accent}44`,
          fontFamily: "'DM Mono'", fontSize: 13, background: C.bg + "cc",
          maxWidth: 300, animation: "fadeIn 0.2s ease"
        }}>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>
            {isListening ? i18n[lang].voiceListening : i18n[lang].commandCapture}
          </div>
          <div style={{ color: isListening ? C.text : C.sweet }}>
            {transcript || "..."}
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse-red {
          0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(235, 87, 87, 0.7); }
          70% { transform: scale(1.1); box-shadow: 0 0 0 15px rgba(235, 87, 87, 0); }
          100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(235, 87, 87, 0); }
        }
      `}</style>
    </div>
  );
}
