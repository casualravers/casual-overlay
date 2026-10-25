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
    }).catch(function () { setDot(false); reconnect(); });
  }

  // ---- connexion
  // lastEvent : instant du dernier bloc recu (etat ou ping, le serveur en envoie un toutes les 15 s) ; au-dela de STALE_MS le flux est
  // considere mort (Wi-Fi coupe, ecran verrouille) : on le rouvre avec la session deja ouverte et le bandeau rouge previent.
  var STALE_MS = 20000, lastEvent = 0, connected = false;
  function setDot(on) { connected = on; $("dot").className = "dot " + (on ? "on" : "off"); showOffline(); }
  function showOffline() {
    $("offline").hidden = !session || (connected && Date.now() - lastEvent < STALE_MS);
  }
  function reconnect() {
    if (!session) return;
    setTimeout(function () { if (session && (!connected || Date.now() - lastEvent >= STALE_MS)) openSse(); }, 0);
  }
  var openedAt = 0;
  setInterval(function () {
    if (session && connected && Date.now() - lastEvent >= STALE_MS) { setDot(false); openSse(); }
    else if (session && !connected && Date.now() - openedAt > 8000) openSse();       // tentative restee sans reponse
    showOffline();
  }, 1000);
  window.addEventListener("online", reconnect);
  function showLogin(msg) {
    $("app").hidden = true; $("login").hidden = false; $("loginmsg").textContent = msg || "";
  }
  function showApp() { $("login").hidden = true; $("app").hidden = false; }
  function lost(msg) {
    session = "";
    try { sessionStorage.removeItem("cr_session"); } catch (e) { /* rien */ }
    if (abortSse) abortSse.abort();
    setDot(false); showLogin(msg); $("offline").hidden = true;
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
    abortSse = ctl; openedAt = Date.now();
    fetch("/events", { headers: { "Authorization": "Bearer " + session }, signal: ctl.signal }).then(function (r) {
      if (r.status === 401) { lost("Connexion fermee"); return; }
      lastEvent = Date.now();
      setDot(true);
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (res) {
          if (res.done) throw new Error("fin");
          buf += dec.decode(res.value, { stream: true });
          var cut;
          while ((cut = buf.indexOf("\n\n")) >= 0) { lastEvent = Date.now(); showOffline(); onEvent(buf.slice(0, cut)); buf = buf.slice(cut + 2); }
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
    last = st;
    $("bpm").textContent = st.bpm ? st.bpm + " BPM" : "-- BPM";
    $("phase").textContent = st.phase ? (st.phase.label || st.phase.name || "...") : "...";
    var pctBuild = Math.round(Math.min(Math.max(st.build, 0), 1) * 100);
    $("climb").textContent = st.build > 0 ? "MONTEE " + pctBuild + " %" : "calme";
    $("climb").className = "chip" + (st.build > 0 ? " on" : "");
    $("title").textContent = st.projection ? st.set.name || "CASUAL REMOTE" : "projection fermee";
    var box = $("scenes"), sig = JSON.stringify(st.set.scenes);
    if (box.dataset.sig !== sig) {
      box.dataset.sig = sig; box.textContent = "";
      st.set.scenes.forEach(function (sc) {
        var b = document.createElement("button");
        b.dataset.i = sc.i;
        if (sc.t) { var im = document.createElement("img"); im.className = "thumb"; im.alt = ""; b.appendChild(im); thumb(im, sc.i, sc.t); }
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
    (st.memories || []).forEach(function (n, i) {
      var b = $("mem-" + (i + 1));
      b.textContent = "";
      var num = document.createElement("b"); num.textContent = String(i + 1);
      var nm = document.createElement("small"); nm.textContent = n || "vide";
      b.appendChild(num); b.appendChild(nm);
    });
    if (st.build > 0) climbing = true;
    else if (Date.now() - tappedAt > 1500) { climbing = false; disarm(); }
    showClimb();
    $("buildbar").style.width = Math.round(st.build * 100) + "%";
    if (document.activeElement !== $("text")) $("text").value = st.text;
    $("preview-on").checked = !!st.preview.on;
    $("preview-note").textContent = st.preview.note || "";
    // l'apercu est visible d'office (une fois par session) sauf si l'operateur l'a coupe ; l'ordi le met en pause tout seul si le GPU est charge
    if (!previewAsked) {
      previewAsked = true;
      var off = false; try { off = sessionStorage.getItem("cr_prev") === "off"; } catch (e) { off = false; }
      if (!st.preview.on && !off) api("/api/preview", { on: true });
    }
    managePreview(st.preview.on);
  }

  // ---- vignettes des scenes : une image par scene, recuperee avec le jeton (une a la fois), gardee tant que son fichier ne change pas
  var thumbCache = {}, thumbChain = Promise.resolve(), previewAsked = false;
  function thumb(img, i, token) {
    var key = i + ":" + token;
    if (thumbCache[key]) { img.src = thumbCache[key]; return; }
    thumbChain = thumbChain.then(function () {
      return fetch("/thumb/" + i + ".jpg", { headers: { "Authorization": "Bearer " + session } }).then(function (r) { return r.ok ? r.blob() : null; }).then(function (b) {
        if (!b) { img.hidden = true; return; }
        thumbCache[key] = URL.createObjectURL(b); img.src = thumbCache[key];
      });
    }).catch(function () { img.hidden = true; });
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
  $("preview-on").addEventListener("change", function (e) {
    try { sessionStorage.setItem("cr_prev", e.target.checked ? "on" : "off"); } catch (x) { /* rien */ }
    api("/api/preview", { on: e.target.checked });
  });
  // confirmation du drop : coche par defaut, retenue pour la session
  var confirmDrop = true;
  try { confirmDrop = sessionStorage.getItem("cr_confirm") !== "0"; } catch (e) { confirmDrop = true; }
  $("confirm-drop").checked = confirmDrop;
  $("confirm-drop").addEventListener("change", function (e) {
    confirmDrop = e.target.checked;
    try { sessionStorage.setItem("cr_confirm", confirmDrop ? "1" : "0"); } catch (x) { /* rien */ }
  });

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
  var dr = $("drop"), climbing = false, tappedAt = 0, armed = false, armTimer = null;
  function disarm() { armed = false; if (armTimer) { clearTimeout(armTimer); armTimer = null; } }
  function showClimb() {
    var pct = climbing && last ? Math.round(Math.min(Math.max(last.build, 0), 1) * 100) : 0;
    dr.firstChild.nodeValue = climbing ? (armed ? "REAPPUIE = DROP" : "DROP  " + pct + " %") : "MONTEE";
    dr.lastChild.textContent = climbing ? (armed ? "pour confirmer" : (confirmDrop ? "2 appuis pour lacher" : "appuie pour lacher")) : "appuie pour monter";
    dr.className = "huge " + (climbing ? "amber" : "pink") + (armed ? " armed" : "");
    dr.style.backgroundImage = climbing ? "linear-gradient(90deg, #ff3d9a " + pct + "%, #ffc633 " + pct + "%)" : "";
  }
  dr.addEventListener("click", function () {
    tappedAt = Date.now();
    if (climbing && confirmDrop && !armed) {
      armed = true; buzz(40); armTimer = setTimeout(function () { disarm(); showClimb(); }, 2500);       // 1er appui : arme, 2,5 s pour confirmer
    } else if (climbing) {
      disarm(); buzz([60, 40, 120]); api("/api/build", { action: "drop" }); climbing = false;
    } else { buzz(20); api("/api/build", { action: "start" }); climbing = true; }
    showClimb();
  });
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
  // retour au premier plan (ecran deverrouille) : le flux a pu mourir sans erreur, on le rouvre s'il est silencieux depuis plus de 3 s
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) return;
    keepAwake();
    if (session && (!connected || Date.now() - lastEvent > 3000)) { setDot(false); openSse(); }
  });

  // ---- demarrage
  $("go").addEventListener("click", function () { login({ code: $("code").value.replace(/\s+/g, "") }); });
  $("code").addEventListener("keydown", function (e) { if (e.key === "Enter") $("go").click(); });
  var m = /[#&]k=([A-Za-z0-9_-]+)/.exec(location.hash || "");
  if (m) { try { history.replaceState(null, "", location.pathname); } catch (e) { /* rien */ } login({ k: m[1] }); }
  else if (session) { showApp(); openSse(); }
  else showLogin("");
  keepAwake();
})();
