// Talks to the Sarthi backend. Every call resolves to null when the backend is down, slow or says no,
// so callers can fall back to the app's built-in behaviour.
const BASE = "/api";

async function request(method, path, body, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;                       // backend down or slow: callers fall back to local behaviour
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: (path, timeoutMs) => request("GET", path, undefined, timeoutMs),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),

  // multipart upload with real progress (fetch cannot report upload progress)
  upload(path, file, onProgress) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.open("POST", BASE + path);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress((e.loaded / e.total) * 100); };
      xhr.onload = () => {
        try { resolve(xhr.status < 300 ? JSON.parse(xhr.responseText) : null); } catch { resolve(null); }
      };
      xhr.onerror = () => resolve(null);
      xhr.send(form);
    });
  },

  // server-sent events; returns a function that closes the stream
  stream(path, onMessage, onDone) {
    const es = new EventSource(BASE + path);
    let finished = false;
    const close = () => { es.close(); if (!finished) { finished = true; if (onDone) onDone(); } };
    es.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* ignore malformed line */ } };
    es.addEventListener("done", close);
    es.onerror = close;
    return () => { finished = true; es.close(); };
  },
};

// Fill a translated template: fill("Total {n} units", { n: 5 }) -> "Total 5 units"
export const fill = (template, params = {}) =>
  Object.entries(params).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), String(template ?? ""));
