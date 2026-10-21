"use strict";
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var session = "";
  try { session = sessionStorage.getItem("cr_session") || ""; } catch (e) { session = ""; }
  var last = null, abortSse = null, held = {}, strobeTimer = null, wake = null, previewTimer = null, previewUrl = "";

  function buzz(ms) { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) { /* tant pis */ } }

  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + session },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      if (r.status === 401) { lost("Connexion remplacee ou fermee"); return null; }
      if (!r.ok) { return r.json().then(function (j) { 0; }, function () { 0; }); }
      return null;
    }).catch(function () { setDot(false); });
  }

  // ---- connexion
  function setDot(on) { $("dot").className = "dot " + (on ? "on" : "off"); }
  function showLogin(msg) {
    $("app").hidden = true; $("login").hidden = false; $("loginmsg").textContent = msg || "";
  }
  function showApp() { $("login").hidden = true; $("app").hidden = false; }
  function lost(msg) {
    session = "";
    try { sessionStorage.removeItem("cr_session"); } catch (e) { /* rien */ }
    if (abortSse) abortSse.abort();
    setDot(false); showLogin(msg);
  }
  function login(body) {
    return fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().then(function (j) {
          if (r.ok && j.session) {
            session = j.session;
            try { sessionStorage.setItem("cr_session", session); } catch (e) { /* rien */ }
            showApp(); openSse(); return;
          }
          showLogin(r.status === 429 ? "Trop d'essais, patiente un peu" : (j.error || "Code incorrect"));
        });
      }).catch(function () { showLogin("Ordi injoignable (meme Wi-Fi ?)"); });
  }

  // ---- flux d'etat (SSE lu par fetch : EventSource ne sait pas envoyer d'en-tete)
  function openSse() {
    if (abortSse) abortSse.abort();
    var ctl = new AbortController();
    abortSse = ctl;
    fetch("/events", { headers: { "Authorization": "Bearer " + session }, signal: ctl.signal }).then(function (r) {
      if (r.status === 401) { lost("Connexion fermee"); return; }
      setDot(true);
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (res) {
          if (res.done) throw new Error("fin");
          buf += dec.decode(res.value, { stream: true });
          var cut;
          while ((cut = buf.indexOf("\n\n")) >= 0) { onEvent(buf.slice(0, cut)); buf = buf.slice(cut + 2); }
          return pump();
        });
      }
      return pump();
    }).catch(function () {
      if (ctl.signal.aborted || !session) return;
      setDot(false);
      setTimeout(function () { if (session && abortSse === ctl) openSse(); }, 1500);
    });
  }
  function onEvent(block) {
    var name = "message", data = "";
    block.split("\n").forEach(function (l) {
      if (l.indexOf("event:") === 0) name = l.slice(6).trim();
      else if (l.indexOf("data:") === 0) data += l.slice(5).trim();
    });
    if (name === "state") { try { render(JSON.parse(data)); } catch (e) { /* trame illisible */ } }
    else if (name === "bye") lost("Un autre telephone a pris la main");
  }

  // ---- affichage
  var dragging = {};
  function render(st) {
    var firstMsg = last === null || st.msg !== last.msg;
    last = st;
    $("bpm").textContent = st.bpm ? st.bpm + " BPM" : "-- BPM";
    $("phase").textContent = st.phase ? (st.phase.label || st.phase.name || "...") : "...";
    $("title").textContent = st.projection ? st.set.name || "CASUAL REMOTE" : "projection fermee";
    var box = $("scenes"), sig = JSON.stringify(st.set.scenes);
    if (box.dataset.sig !== sig) {
      box.dataset.sig = sig; box.textContent = "";
      st.set.scenes.forEach(function (sc) {
        var b = document.createElement("button");
        b.dataset.i = sc.i;
        var n = document.createElement("span"); n.className = "num"; n.textContent = String(sc.i + 1);
        b.appendChild(n); b.appendChild(document.createTextNode(sc.n));
        box.appendChild(b);
      });
    }
    Array.prototype.forEach.call(box.children, function (b) { b.className = (Number(b.dataset.i) === st.set.active) ? "on" : ""; });
    $("vj-toggle").textContent = st.vj.running ? "■ VJ" : "▶ VJ";
    $("vj-toggle").className = "big " + (st.vj.running ? "amber" : "green");
    $("lock").textContent = st.vj.locked ? "Verrou ●" : "Verrou";
    $("lock").className = "big amber" + (st.vj.locked ? " lit" : "");
    $("remaining").textContent = st.vj.running ? st.vj.remaining : (st.vj.locked ? "scene verrouillee" : "VJ en pause");
    Object.keys(st.masters).forEach(function (k) {
      var el = $("m-" + k);
      if (!el) return;
      if (!dragging[k]) el.value = st.masters[k];
      var o = $("o-" + k);
      if (o) o.textContent = Math.round(st.masters[k] * 100) + "%";
    });
    Object.keys(st.autos).forEach(function (k) { var el = $("a-" + k); if (el) el.checked = !!st.autos[k]; });
    $("v-phase").textContent = st.voyants.phase || "";
    $("v-strobe").textContent = st.voyants.strobe || "";
    $("v-sources").textContent = st.voyants.sources || "";
    $("buildbar").style.width = Math.round(st.build * 100) + "%";
    if (document.activeElement !== $("text")) $("text").value = st.text;
    $("preview-on").checked = !!st.preview.on;
    $("preview-note").textContent = st.preview.note || "";
    managePreview(st.preview.on);
  }

  // ---- commandes
  document.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.parentNode.id === "scenes") { buzz(20); api("/api/scene", { index: Number(b.dataset.i) }); }
    else if (b.dataset.tab) tab(b.dataset.tab);
  });
  function tab(name) {
    ["scenes", "macro", "boum", "infos"].forEach(function (t) { $("tab-" + t).hidden = (t !== name); });
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) { b.className = (b.dataset.tab === name) ? "on" : ""; });
    buzz(10);
  }
  function tap(id, fn) { $(id).addEventListener("click", function () { buzz(20); fn(); }); }
  tap("vj-toggle", function () { api("/api/vj", { action: "toggle" }); });
  tap("vj-next", function () { api("/api/vj", { action: "next" }); });
  tap("vj-back", function () { api("/api/vj", { action: "back" }); });
  tap("lock", function () { api("/api/lock"); });
  tap("one", function () { api("/api/one"); });
  [1, 2, 3, 4].forEach(function (n) { tap("mem-" + n, function () { api("/api/memory", { slot: n }); }); });
  tap("send-text", function () { api("/api/text", { text: $("text").value }); });
  ["auto_phase", "strobe_auto", "bpm_auto"].forEach(function (k) {
    $("a-" + k).addEventListener("change", function (e) { api("/api/auto", { key: k, value: e.target.checked }); });
  });
  $("preview-on").addEventListener("change", function (e) { api("/api/preview", { on: e.target.checked }); });

  // curseurs : 15 envois par seconde au plus, le dernier est toujours envoye
  ["react_master", "fx_master", "overlay_master", "bg_master", "hue_master", "tempo"].forEach(function (k) {
    var el = $("m-" + k), timer = null, pending = false, lastSent = 0;
    function send() { timer = null; pending = false; lastSent = Date.now(); api("/api/master", { key: k, value: Number(el.value) }); }
    el.addEventListener("input", function () {
      dragging[k] = true;
      var o = $("o-" + k); if (o) o.textContent = Math.round(el.value * 100) + "%";
      var wait = 66 - (Date.now() - lastSent);
      if (wait <= 0) send(); else if (!timer) { pending = true; timer = setTimeout(send, wait); }
    });
    el.addEventListener("change", function () { if (timer) { clearTimeout(timer); } send(); setTimeout(function () { dragging[k] = false; }, 600); });
  });

  // strobe maintenu : on renvoie « appui » toutes les 500 ms, l'ordi relache seul sans nouvelles
  var sb = $("strobe");
  function strobeOn(e) { e.preventDefault(); if (strobeTimer) return; sb.classList.add("held"); buzz(15); api("/api/strobe", { down: true }); strobeTimer = setInterval(function () { api("/api/strobe", { down: true }); }, 500); }
  function strobeOff() { if (!strobeTimer) return; clearInterval(strobeTimer); strobeTimer = null; sb.classList.remove("held"); api("/api/strobe", { down: false }); }
  sb.addEventListener("pointerdown", strobeOn);
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) { sb.addEventListener(ev, strobeOff); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) strobeOff(); });
  sb.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  // montee : appui court = lancer (ou relacher si deja en cours), appui long (0,4 s) = DROP
  var dr = $("drop"), dropTimer = null, dropLong = false;
  dr.addEventListener("pointerdown", function (e) {
    e.preventDefault(); dropLong = false; dr.classList.add("held");
    dropTimer = setTimeout(function () { dropLong = true; buzz([60, 40, 120]); api("/api/build", { action: "drop" }); }, 400);
  });
  function dropUp() {
    if (dropTimer === null) return;
    clearTimeout(dropTimer); dropTimer = null; dr.classList.remove("held");
    if (!dropLong) { buzz(20); api("/api/build", { action: "start" }); }
  }
  ["pointerup", "pointerleave", "pointercancel"].forEach(function (ev) { dr.addEventListener(ev, dropUp); });
  dr.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  // ---- apercu (facultatif) : une image toutes les 5 s, recuperee avec le jeton puis affichee en blob
  function managePreview(on) {
    var img = $("preview");
    if (!on) { clearInterval(previewTimer); previewTimer = null; img.hidden = true; return; }
    if (previewTimer) return;
    function pull() {
      fetch("/preview.jpg", { headers: { "Authorization": "Bearer " + session } }).then(function (r) { return r.ok ? r.blob() : null; }).then(function (b) {
        if (!b) return;
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(b); img.src = previewUrl; img.hidden = false;
      }).catch(function () { /* pas d'image cette fois */ });
    }
    pull(); previewTimer = setInterval(pull, 5000);
  }

  // ---- ecran allume
  function keepAwake() {
    try { if (navigator.wakeLock && !document.hidden) navigator.wakeLock.request("screen").then(function (w) { wake = w; }, function () { /* refuse */ }); } catch (e) { /* non supporte */ }
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { keepAwake(); if (session && abortSse && abortSse.signal.aborted) openSse(); } });

  // ---- demarrage
  $("go").addEventListener("click", function () { login({ code: $("code").value.replace(/\s+/g, "") }); });
  $("code").addEventListener("keydown", function (e) { if (e.key === "Enter") $("go").click(); });
  var m = /[#&]k=([A-Za-z0-9_-]+)/.exec(location.hash || "");
  if (m) { try { history.replaceState(null, "", location.pathname); } catch (e) { /* rien */ } login({ k: m[1] }); }
  else if (session) { showApp(); openSse(); }
  else showLogin("");
  keepAwake();
})();
