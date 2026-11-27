"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { csrf: null, tab: "dashboard", timers: [], prev: null, paths: [], ann: [], es: null };

  async function api(path, opts = {}) {
    const o = { credentials: "same-origin", headers: {}, ...opts };
    if (opts.body !== undefined) { o.method = o.method || "POST"; o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
    if (o.method && o.method !== "GET") o.headers["X-CSRF-Token"] = state.csrf || "";
    const r = await fetch(path, o);
    let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
    if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("sign in required"); }
    if (!r.ok) throw new Error((data && data.error) || ("HTTP " + r.status));
    return data;
  }

  // ---- formatting
  const bytes = (n) => { if (n == null) return "-"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, v = n; while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; } return (i ? v.toFixed(v < 10 ? 2 : 1) : v) + " " + u[i]; };
  const bps = (n) => { if (n == null) return "-"; const u = ["bps", "kbps", "Mbps", "Gbps"]; let i = 0, v = n; while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; } return (i ? v.toFixed(v < 10 ? 2 : 1) : Math.round(v)) + " " + u[i]; };
  const dur = (s) => { if (s == null) return "-"; s = Math.round(s); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`; };
  const clock = (t) => new Date(t * 1000).toLocaleTimeString();
  const short = (h) => h ? h.slice(0, 8) + "…" + h.slice(-4) : "-";
  const el = (tag, props = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(props)) { if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v); } kids.flat().forEach((c) => e.append(c)); return e; };
  const kv = (dl, rows) => { dl.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])); };

  // ---- login
  function showLogin() { stopAll(); $("app").hidden = true; $("login").hidden = false; $("logout").hidden = true; $("password").focus(); }
  async function showApp() { $("login").hidden = true; $("app").hidden = false; $("logout").hidden = false; route(); }

  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("loginMsg").textContent = "";
    try { const r = await api("/api/login", { body: { password: $("password").value } }); state.csrf = r.csrf; $("password").value = ""; await showApp(); }
    catch (err) { $("loginMsg").textContent = err.message; }
  });
  $("logout").addEventListener("click", async () => { try { await api("/api/logout", { body: {} }); } catch (e) { /* already out */ } state.csrf = null; showLogin(); });

  // ---- routing and polling
  function stopAll() { state.timers.forEach(clearInterval); state.timers = []; if (state.es) { state.es.close(); state.es = null; } }
  function every(ms, fn) { fn(); state.timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms)); }

  function route() {
    const tab = (location.hash || "#dashboard").slice(1);
    state.tab = ["dashboard", "paths", "announces", "tools"].includes(tab) ? tab : "dashboard";
    document.querySelectorAll(".tabs a").forEach((a) => { if (a.dataset.tab === state.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== "tab-" + state.tab; });
    stopAll();
    if (state.tab === "dashboard") every(3000, loadStatus);
    if (state.tab === "paths") every(5000, loadPaths);
    if (state.tab === "announces") startAnnounces();
    if (state.tab === "tools") loadSelf();
  }
  window.addEventListener("hashchange", () => { if (!$("app").hidden) route(); });

  // ---- dashboard
  async function loadStatus() {
    let s; try { s = await api("/api/status"); } catch (e) { return; }
    const now = s.time, prev = state.prev; state.prev = { t: now, ifs: Object.fromEntries(s.interfaces.map((i) => [i.name, i])), tot: s.totals };
    const rate = (cur, old, key) => (prev && old && cur[key] != null && old[key] != null && now > prev.t) ? Math.max(0, (cur[key] - old[key]) * 8 / (now - prev.t)) : null;
    kv($("transport"), [["Transport ID", s.transport.id || "-"], ["Uptime", dur(s.transport.uptime)], ["Links", String(s.links ?? "-")]]);
    kv($("totals"), [["Received", `${bytes(s.totals.rxb)}  (${bps(rate(s.totals, prev && prev.tot, "rxb"))})`], ["Sent", `${bytes(s.totals.txb)}  (${bps(rate(s.totals, prev && prev.tot, "txb"))})`]]);
    $("interfaces").replaceChildren(...s.interfaces.map((i) => {
      const old = prev && prev.ifs[i.name];
      const rows = el("dl", { class: "kv" });
      kv(rows, [["Received", `${bytes(i.rxb)}  (${bps(rate(i, old, "rxb"))})`], ["Sent", `${bytes(i.txb)}  (${bps(rate(i, old, "txb"))})`], ["Bitrate", bps(i.bitrate)],
        ["Announces in/out", `${(i.incoming_announce_frequency ?? 0).toFixed(2)} / ${(i.outgoing_announce_frequency ?? 0).toFixed(2)} per min`]]
        .concat(i.clients != null ? [["Clients", String(i.clients)]] : []).concat(i.held_announces ? [["Held announces", String(i.held_announces)]] : []));
      return el("article", { class: "iface" }, el("header", {}, el("span", { class: "dot" + (i.status ? " up" : ""), title: i.status ? "up" : "down" }), el("h3", { text: i.short_name || i.name }),
        i.ifac ? el("span", { class: "pill", text: "IFAC" }) : ""), el("div", { class: "type", text: i.type }), rows);
    }));
  }

  // ---- paths
  async function loadPaths() { try { state.paths = await api("/api/paths"); renderPaths(); } catch (e) { /* signed out */ } }
  function renderPaths() {
    const q = $("pathFilter").value.trim().toLowerCase();
    const rows = state.paths.filter((p) => !q || [p.hash, p.via, p.interface].some((x) => x && String(x).toLowerCase().includes(q)));
    $("pathRows").replaceChildren(...rows.map((p) => {
      const b = el("button", { type: "button", class: "small", text: "Refresh" }); b.addEventListener("click", async () => { b.disabled = true; try { await api("/api/path-request", { body: { hash: p.hash } }); b.textContent = "Requested"; } catch (e) { b.textContent = "Failed"; } });
      return el("tr", {}, el("td", { class: "hash", text: p.hash }), el("td", { text: String(p.hops ?? "-") }), el("td", { class: "hash", text: p.via ? short(p.via) : "-" }), el("td", { text: p.interface || "-" }), el("td", { text: dur(p.expires_in) }), el("td", {}, b));
    }));
    $("pathCount").textContent = `${rows.length} of ${state.paths.length} known paths`;
  }
  $("pathFilter").addEventListener("input", renderPaths);

  // ---- announces
  function annNode(a) {
    const data = a.app_data && (a.app_data.text !== null && a.app_data.text !== "" ? a.app_data.text : a.app_data.hex ? "0x" + a.app_data.hex : "");
    return el("article", { class: "ann" }, el("div", { class: "meta", text: `${clock(a.time)}  ·  ${a.hops == null ? "?" : a.hops} hop${a.hops === 1 ? "" : "s"}` }), el("div", { class: "h", text: a.destination }), data ? el("div", { class: "data", text: data }) : "");
  }
  function renderAnn() {
    const q = $("annFilter").value.trim().toLowerCase();
    const rows = state.ann.filter((a) => !q || a.destination.includes(q) || (a.app_data && a.app_data.text && a.app_data.text.toLowerCase().includes(q)));
    $("annList").replaceChildren(...rows.slice(0, 200).map(annNode));
  }
  async function startAnnounces() {
    try { state.ann = await api("/api/announces"); } catch (e) { return; }
    renderAnn(); $("annState").textContent = "Live: new announces appear as they arrive.";
    const es = new EventSource("/api/announces/stream"); state.es = es;
    es.onmessage = (m) => { try { state.ann.unshift(JSON.parse(m.data)); state.ann.length = Math.min(state.ann.length, 300); renderAnn(); } catch (e) { /* ignore a bad frame */ } };
    es.onerror = () => { $("annState").textContent = "Connection lost; retrying..."; };
    es.onopen = () => { $("annState").textContent = "Live: new announces appear as they arrive."; };
  }
  $("annFilter").addEventListener("input", renderAnn);

  // ---- tools
  async function loadSelf() { try { const s = await api("/api/self"); $("selfHash").textContent = s.destination; $("selfName").textContent = s.name; } catch (e) { /* signed out */ } }
  function toolMsg(text, ok) { const m = $("toolMsg"); m.textContent = text; m.className = "msg " + (ok ? "ok" : "bad"); }
  $("announceForm").addEventListener("submit", async (e) => { e.preventDefault(); try { const r = await api("/api/announce", { body: { app_data: $("announceData").value } }); toolMsg("Announced " + r.destination, true); } catch (err) { toolMsg(err.message, false); } });
  $("pathForm").addEventListener("submit", async (e) => { e.preventDefault(); try { const r = await api("/api/path-request", { body: { hash: $("pathHash").value.trim() } }); toolMsg("Path requested for " + r.requested + ". It shows up under Paths if someone answers.", true); } catch (err) { toolMsg(err.message, false); } });

  // ---- start
  (async () => {
    try {
      const s = await fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json());
      $("foot").textContent = "rnsd-webui " + s.version + (s.demo ? " (demo data)" : "");
      if (s.authenticated) { state.csrf = s.csrf; showApp(); } else showLogin();
    } catch (e) { $("login").hidden = false; $("loginMsg").textContent = "Cannot reach the server."; }
  })();
})();
