#!/usr/bin/env python3
"""Verifications hors materiel de audio2wave_gl.py (aucun micro, projecteur ni ffmpeg).

    python check_gl.py

  1. lecteur de frames : faux producteur livrant par blocs de 4 Ko, aucune frame melangee,
     frame partielle de fin de flux jetee, latest-wins;
  2. features : salves de sinus 60 Hz -> un beat par salve, aucun faux positif sur bruit
     constant, silence = niveaux nuls, latence de detection mesuree;
  3. rendu : une frame en contexte moderngl standalone -> PNG non vide, et image neutre
     (identique a la video) quand l'audio est coupe.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import audio2wave_gl as gl

gl.require_deps()
np = gl.np

# Le halo holographique est actif par defaut (il deforme et eclaire le fond autour du logo): les
# verifications de rendu qui comparent des pixels le coupent, et check_holo() le rallume explicitement.
HOLO_DEFAULT_ON = gl.DEFAULT_PARAMS["holo_on"]
gl.DEFAULT_PARAMS["holo_on"] = 0.0

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'ECHEC'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# --- 1. lecteur de frames ------------------------------------------------------------

class FakeProducer:
    """Flux de frames dont chaque octet vaut le numero de frame; livre par petits blocs."""

    def __init__(self, frame_size: int, n_frames: int, extra: int = 0, block: int = 4096):
        self.data = b"".join(bytes([i % 251 + 1]) * frame_size for i in range(n_frames))
        self.data += bytes([250]) * extra          # frame partielle de fin de flux
        self.off = 0
        self.block = block

    def read(self, n: int) -> bytes:
        chunk = self.data[self.off:self.off + min(n, self.block)]
        self.off += len(chunk)
        return chunk


def check_reader() -> None:
    print("Lecteur de frames")
    frame_size = 30 * 20 * 3          # volontairement pas multiple de 4096
    seen: list[bytes] = []

    def on_frame(mv):
        seen.append(bytes(mv))

    reader = gl.FrameReader(FakeProducer(frame_size, 40, extra=1000), frame_size, on_frame)
    reader.start()
    reader.join(5)
    mixed = [i for i, f in enumerate(seen) if len(set(f)) != 1]
    check(reader.ended, "fin de flux detectee")
    check(len(seen) == 40, "40 frames entieres publiees (la partielle est jetee)", f"{len(seen)} recues")
    check(not mixed, "aucune frame ne melange des octets de deux frames")
    check([f[0] for f in seen] == [i % 251 + 1 for i in range(40)], "ordre des frames conserve")
    last = reader.take()
    check(last is not None and len(last) == frame_size and len(set(last)) == 1,
          "latest-wins: la derniere frame entiere est disponible")
    check(reader.take() is None, "pas de nouvelle frame apres consommation")


class FakeStream:
    """Flux rawvideo infini: chaque frame est faite d'un seul octet `value`, livre par blocs de
    4 Ko (donc une bascule de flux tombe forcement au milieu d'une frame)."""

    def __init__(self, value: int, dead: bool = False, block: int = 4096):
        self.value, self.dead, self.block = value, dead, block
        self.closed = False

    def readinto(self, mv) -> int:
        if self.closed or self.dead:
            return 0
        n = min(len(mv), self.block)
        mv[:n] = bytes([self.value]) * n
        time.sleep(0.0004)
        return n

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, value: int, dead: bool = False):
        self.stdout = FakeStream(value, dead)
        self.returncode = 1 if dead else None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.stdout.closed = True

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class FakeLive:
    RESTART_GRACE_S = 0.05

    @staticmethod
    def producer_warmup_seconds(args) -> float:
        return 0.15


def check_manager() -> None:
    print("Remplacement du producteur a chaud")
    from types import SimpleNamespace as NS

    frame_size = 10000                       # pas multiple de 4096
    spawned: list[FakeProc] = []

    def spawn(args):
        proc = FakeProc(args.value, dead=getattr(args, "dead", False))
        spawned.append(proc)
        return proc

    # a) bascule de flux du lecteur: partielle jetee, aucune frame melangee
    class Once:
        def __init__(self, n_bytes, value):
            self.left, self.value = n_bytes, value

        def read(self, n):
            n = min(n, self.left, 4096)
            self.left -= n
            return bytes([self.value]) * n

    a, b = Once(frame_size * 3 + 4500, 1), Once(frame_size * 2, 2)
    seen: list[bytes] = []
    reader = gl.FrameReader(a, frame_size, lambda mv: seen.append(bytes(mv)), hold_on_eof=True)
    reader.switch_stream(b)      # demande posee AVANT: appliquee a la fin du flux a
    reader.start()
    deadline = time.time() + 5
    while len(seen) < 5 and time.time() < deadline:
        time.sleep(0.01)
    reader.stop()
    vals = [f[0] for f in seen]
    check(vals == [1, 1, 1, 2, 2], "switch_stream: 3 frames du flux A, partielle jetee, 2 du flux B", str(vals))
    check(all(len(set(f)) == 1 for f in seen), "switch_stream: aucune frame melangee")

    # b) ProducerManager: remplacement, echec, fusion des demandes
    status: dict = {}
    args1 = NS(value=1)
    mgr = gl.ProducerManager(FakeLive, args1, frame_size, status, spawn=spawn)
    frames: list[int] = []
    mixed = []

    def on_frame(mv):
        data = bytes(mv)
        if len(set(data)) != 1:
            mixed.append(data[:1])
        frames.append(data[0])

    mgr.reader.on_frame = on_frame
    args1.value = 7            # la GUI d'audio2wave mute ses options en place: le gestionnaire ne doit pas le voir
    check(mgr.args is not args1 and mgr.args.value == 1,
          "le gestionnaire garde sa propre copie des options (sinon plus aucun redemarrage depuis la GUI)")
    args1.value = 1
    mgr.start()
    time.sleep(0.3)
    check(frames and set(frames) == {1}, "flux initial servi", f"{len(frames)} frames")

    mgr.request_restart(NS(value=2))
    deadline = time.time() + 5
    while 2 not in frames and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    first2 = frames.index(2) if 2 in frames else -1
    check(first2 > 0 and set(frames[first2:]) == {2}, "apres le remplacement, plus aucune frame de l'ancien flux")
    check(not mixed, "aucune frame melangee pendant le remplacement")
    check(mgr.restarts == 1 and spawned[0].poll() is not None and spawned[1].poll() is None,
          "ancien producteur termine, nouveau actif", f"restarts={mgr.restarts}")

    before = len(frames)
    mgr.request_restart(NS(value=9, dead=True))            # reglage refuse par ffmpeg
    time.sleep(0.8)
    check(mgr.restarts == 1 and mgr.producer is spawned[1] and "refuse" in status.get("live", ""),
          "producteur qui meurt pendant la chauffe: ancien conserve + message", status.get("live", ""))
    check(len(frames) > before and set(frames[before:]) == {2}, "le flux continue pendant l'echec")

    count = len(spawned)
    for v in (3, 4, 5):
        mgr.request_restart(NS(value=v))
    deadline = time.time() + 5
    while (not frames or frames[-1] != 5) and time.time() < deadline:
        time.sleep(0.02)
    check(frames[-1] == 5 and len(spawned) - count <= 2,
          "demandes rapprochees fusionnees: la derniere l'emporte", f"{len(spawned) - count} lancement(s)")
    mgr.stop()
    time.sleep(0.2)
    check(all(p.poll() is not None for p in spawned), "arret: aucun producteur laisse en vie")

    # couleurs: un nom a moitie tape ("t" pour "teal") ne doit pas relancer ffmpeg
    if gl.ffmpeg_color_names():
        check(all(gl.is_ffmpeg_color(c) for c in ("teal", "Teal", "white", "0xff8800", "#FF8800", "red@0.5", "0xff880080",
                                                  "random")), "couleurs valides: noms, 0x, #, alpha")
        check(not any(gl.is_ffmpeg_color(c) for c in ("t", "te", "", "0xff", "#12345", "zzzzzz", "grey")),
              "couleurs invalides: nom partiel, vide, hex incomplet, inconnu (grey)")
        cur = NS(colors="grey", bg_color="black")
        check(gl.invalid_colors(NS(colors="t", bg_color="black"), cur) == ["t"], "saisie partielle detectee")
        check(gl.invalid_colors(NS(colors="grey", bg_color="black"), cur) == [],
              "valeur deja en service toleree (defaut 'grey' d'audio2wave)")
        check(gl.invalid_colors(NS(colors="red|t", bg_color="nav"), cur) == ["t", "nav"],
              "plusieurs couleurs (|) et fond verifies")
        check(gl.invalid_colors(NS(colors="red|blue", bg_color="0x102030"), cur) == [], "liste valide acceptee")


def check_gui() -> None:
    print("GUI (fenetre d'audio2wave_live integree + panneaux casual-overlay)")
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
    except Exception as exc:
        print(f"  (ignore: pas d'affichage tkinter disponible: {exc})")
        return
    import copy
    import queue
    import threading
    from types import SimpleNamespace as NS

    import gl_gui

    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    args = gl.build_live_args(live, "Micro", (1280, 720), 30, None)
    requests: list = []
    manager = NS(producer=None, restarts=0, status={}, args=copy.copy(args))

    def request_restart(new_args):        # comme ProducerManager: la demande devient l'etat courant
        requests.append(new_args)
        manager.args = new_args

    manager.request_restart = request_restart
    manager.suspended, manager.resumed, manager.reader = False, [], object()
    manager.suspend = lambda: setattr(manager, "suspended", True)
    manager.resume = lambda new_args: (setattr(manager, "suspended", False), manager.resumed.append(new_args))

    class SineStream:                     # faux micro pour les modes Snap / Ridge (PCM s16le mono, 48 kHz)
        def __init__(self):
            self.t = 0

        def read(self, n):
            import array
            import math
            time.sleep(n / 2 / 48000)
            k = n // 2
            data = array.array("h", [int(12000 * math.sin(2 * math.pi * 220 * (self.t + i) / 48000)) for i in range(k)])
            self.t += k
            return data.tobytes()

    def fake_capture(a, module):
        stream = SineStream()
        return NS(stdout=stream, terminate=lambda: None, wait=lambda: None), module.LiveCapture(stream, module.chunk_size(a))

    analyzer = NS(latest=lambda: {"bass": 0.5, "mid": 0.3, "high": 0.2, "beat": 1.0})
    audio = NS(synthetic=True, name="synthetique", analyzer=analyzer)
    params = gl.load_params()
    s = NS(params=params, manager=manager, live_args=args, render_size=(1280, 720), audio=audio,
           commands=queue.SimpleQueue(), status={}, stop_event=threading.Event(),
           finished_event=threading.Event(), mode="live", pysrc=None, mode_args={}, reader=manager.reader,
           capture_factory=fake_capture)
    original_gain = args.gain
    out: dict = {}

    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    def find_scale(root, label_text):
        """Curseur de la ligne dont le label vaut `label_text` (dans la grille d'audio2wave)."""
        labels = [w for w in root.grid_slaves()
                  if w.winfo_class() == "Label" and w.cget("text") == label_text]
        info = labels[0].grid_info()
        # Colonne voisine du label: les cadres de casual-overlay (a droite) s'etendent sur
        # plusieurs lignes et apparaissent aussi dans grid_slaves(row=...).
        for w in root.grid_slaves(row=info["row"], column=int(info["column"]) + 1):
            if w.winfo_class() == "Scale":
                return w
            for sub in walk(w):
                if sub.winfo_class() == "Scale":
                    return sub
        return None

    def mode_stage(c):
        """Appelee a chaque reconstruction de la fenetre apres la premiere (bascule Snap / Ridge / Live)."""
        root = c["root"]
        stage = out["stage"]
        texts = [w.cget("text") for w in walk(root) if w.winfo_class() in ("Label", "Button")]
        if stage in ("snap", "ridge"):
            out[stage] = dict(mode=s.mode, src=s.pysrc.name if s.pysrc else None, suspended=manager.suspended,
                              banner=any(t.startswith(stage.upper()) for t in texts),
                              presets=any(t.startswith(f"PRESETS {stage.upper()}") for t in texts),
                              buttons=("Live" in texts, "Snap" in texts, "Ridge" in texts),
                              overlay=any(t.startswith("OVERLAY") for t in texts),
                              height=root.winfo_reqheight(), reader_ok=s.reader is s.pysrc.reader)

            def later(tries=0):
                # La premiere image d'un mode arrive apres un "temps par photo" (4 s par defaut pour Snap).
                if s.pysrc.reader.frames_read <= 5 and tries < 100:
                    root.after(200, lambda: later(tries + 1))
                    return
                out[stage]["frames"] = s.pysrc.reader.frames_read
                if stage == "snap" and "fs_frames" not in out:
                    # un preset (ex. 'club') coche Plein ecran : run() remplace son visionneur ; le flux doit survivre
                    s.pysrc.args.fullscreen = True
                    base = s.pysrc.reader.frames_read

                    def after_fs(tries=0):
                        if s.pysrc.reader.frames_read < base + 6 and tries < 150:
                            root.after(200, lambda: after_fs(tries + 1))
                            return
                        out["fs_frames"] = s.pysrc.reader.frames_read - base
                        out["stage"] = "ridge"
                        c["switch"]("ridge")
                    after_fs()
                    return
                out["stage"] = "ridge" if stage == "snap" else "live"
                c["switch"]("ridge" if stage == "snap" else "live")
            root.after(200, later)
        else:
            out["live_back"] = dict(mode=s.mode, src=s.pysrc, suspended=manager.suspended,
                                    resumed=[r.device for r in manager.resumed], reader_ok=s.reader is manager.reader,
                                    banner=any(t.startswith("LIVE") for t in texts),
                                    gain=find_scale(c["live_host"], "Gain (dB)") is not None)
            c["close"]()
            s.finished_event.set()

    def on_ready(c):
        root = c["root"]
        out["builds"] = out.get("builds", 0) + 1
        if out["builds"] > 1:
            mode_stage(c)
            return

        def step1():
            out["snap_ridge"] = any(w.winfo_class() == "Button" and w.cget("text") in ("Snap", "Ridge")
                                    for w in walk(root))
            out["size_ro"] = all(str(w.cget("state")) == "readonly" for w in walk(root)
                                 if w.winfo_class() == "Entry" and int(w.cget("width")) == 6)
            out["presets"] = any(w.winfo_class() == "Label" and w.cget("text").startswith("PRESETS LIVE")
                                 for w in walk(root))
            out["automation"] = sum(1 for w in walk(root) if w.winfo_class() == "Button"
                                    and w.cget("text") == "courbe")
            scale = find_scale(c["live_host"], "Gain (dB)")
            for v in (5, 8, 12):                                  # rafale: un seul redemarrage attendu
                scale.set(v)
            root.after(1100, step2)

        def step2():
            out["burst"] = len(requests)
            out["gain"] = requests[-1].gain if requests else None
            out["copy"] = bool(requests) and requests[-1] is not args and args.gain == original_gain \
                or (bool(requests) and requests[-1] is not args)
            out["size"] = requests[-1].size if requests else None
            cb = next(w for w in walk(root) if w.winfo_class() == "Checkbutton" and w.cget("text") == "Plein ecran")
            cb.invoke()                                           # plein ecran seul: ffmpeg inchange
            root.after(900, step3)

        def step3():
            out["no_restart_fs"] = len(requests) == out["burst"]
            try:
                out["fs_cmd"] = s.commands.get_nowait()
            except queue.Empty:
                out["fs_cmd"] = None
            radio = next(w for w in walk(root) if w.winfo_class() == "Radiobutton" and w.cget("text") == "radio")
            radio.invoke()
            root.after(900, step4)

        def live_states(labels):
            import gui_gates as gg
            host = c["live_host"]
            return {lb: {str(x.cget("state")) for w in gg.row_of_label(host, lb) for x in gg.leaves(w)
                         if x.winfo_class() in ("Scale", "Menubutton", "Radiobutton")} for lb in labels}

        def step4():
            import gui_gates as gg
            labels = ("Forme", "Lissage", "Echelle frequences", "Espace entre barres", "Barres/points", "Gain (dB)")
            out["g_live_radio"] = live_states(labels)
            host = c["live_host"]
            gg.find(host, "Radiobutton", "analyzer").invoke()
            root.update()
            out["g_live_bar"] = live_states(labels)
            gg.find(host, "Radiobutton", "line").invoke()
            root.update()
            out["g_live_line"] = live_states(labels)
            gg.find(host, "Radiobutton", "radio").invoke()      # retour a l'etat attendu par la suite
            root.update()
            out["style"] = requests[-1].style if len(requests) > out["burst"] else None
            c["x_var"].set(0.2)
            c["fx_on_vars"][1].set(0)
            c["master_var"].set(1.4)
            c["logo_var"].set("")
            c["apply_logo_path"]()
            c["color_var"].set("zz")
            out["color_bad"] = params["logo_glow_color"]
            c["color_var"].set("#FF0000")
            out["color_ok"] = params["logo_glow_color"]
            out["x"], out["fx1"], out["master"] = params["logo_x"], params["fx_on"][1], params["master"]
            import gui_gates as gg

            def scales(container):
                return {str(x.cget("state")) for x in gg.leaves(container) if x.winfo_class() == "Scale"}

            def row_states(label):
                return {str(x.cget("state")) for w in gg.row_of_label(c["pattern_box"], label, panels=False)
                        for x in gg.leaves(w) if x.winfo_class() in ("Scale", "Entry")}
            c["holo_var"].set(0)
            out["holo_off"] = params["holo_on"]
            out["g_holo_off"] = scales(c["holo_box"])
            c["holo_var"].set(1)
            out["holo_on"] = params["holo_on"]
            out["g_holo_on"] = scales(c["holo_box"])
            tab_m, tab_c = c["tabs"][4], c["tabs"][5]
            c["melt_var"].set(0)
            c["cell_var"].set(0)
            out["g_fx_off"] = (scales(tab_m), scales(tab_c))
            c["melt_var"].set(1)
            c["cell_var"].set(1)
            out["g_fx_on"] = (scales(tab_m), scales(tab_c))
            c["melt_var"].set(0)
            c["cell_var"].set(0)
            out["g_pal"] = {}
            for pal in ("classic", "duo", "test"):
                c["palette_var"].set(pal)
                out["g_pal"][pal] = {lb: row_states(lb) for lb in ("Couleurs 1 / 2", "Angle du degrade", "Teinte", "Vitesse")}
            c["palette_var"].set("classic")
            out["tabs"] = [c["notebook"].tab(i, "text") for i in range(c["notebook"].index("end"))]
            c["bg_var"].set("pattern")                   # pire cas: bloc motif ouvert + effets du logo delies
            c["link_var"].set(0)
            root.update_idletasks()
            out["win_height"] = root.winfo_reqheight()
            c["link_var"].set(1)
            c["bg_var"].set("live")
            root.update_idletasks()
            out["logo"] = params["logo_path"]
            out["pattern_hidden"] = c["pattern_box"].winfo_manager()
            c["bg_var"].set("pattern")
            out["bg_param"] = params["bg_mode"]
            out["pattern_shown"] = c["pattern_box"].winfo_manager()
            c["bg_color1_var"].set("#102030")
            out["bg_c1"] = params["bg_color1"]
            out["image_shown"] = c["image_box"].winfo_manager()
            c["source_var"].set("text")
            out["source_param"] = params["logo_source"]
            out["text_shown"] = c["text_box"].winfo_manager()
            out["image_hidden"] = c["image_box"].winfo_manager()
            c["notebook"].select(1)                      # onglet Logo: le champ texte doit etre affiche pour recevoir le clavier
            root.update()
            tw = c["text_widget"]
            tw.delete("1.0", "end")
            tw.insert("1.0", "HELLO\nWORLD")
            tw.focus_force()                  # les evenements clavier vont au widget qui a le focus
            root.update()
            tw.event_generate("<KeyRelease>", keysym="d")
            out["typed"] = params["text_content"]
            c["font_var"].set("Impact")
            out["font"] = params["text_font"]
            s.status["msg"] = "test"
            # automations: etat de l'editeur d'audio2wave -> params (synchro toutes les 200 ms)
            auto = c["automation"]
            out["auto_registered"] = set(auto.state) == set(gl.AUTOMATION_SPECS)
            auto.state["bg_hue"]["enabled"].set(False)
            auto.state["logo_x"]["enabled"].set(True)
            auto.state["bg_tile"]["period"].set(33.0)
            c["auto_master_var"].set(0)
            root.after(600, step4b)

        def step4b():
            data = params["_automation"]
            out["sync_enabled"] = (data["bg_hue"]["enabled"], data["logo_x"]["enabled"])
            out["sync_period"] = data["bg_tile"]["period"]
            out["sync_master"] = params["auto_master"]
            # les curseurs suivent la valeur calculee par le moteur (ici posee a la main)
            c["auto_master_var"].set(1)
            c["automation"].state["bg_speed"]["enabled"].set(True)
            root.after(300, lambda: None)
            params["bg_speed"] = 1.23
            root.after(400, step4c)

        def step4c():
            out["follow"] = round(c["auto_vars"]["bg_speed"].get(), 3)
            params["bg_hue"] = 0.3
            # "Ambiance par defaut" remet l'etat d'origine
            next(w for w in walk(root) if w.winfo_class() == "Button" and w.cget("text") == "Ambiance par defaut").invoke()
            root.after(500, step4d)

        def step4d():
            # Message du gestionnaire de flux, pose APRES les redemarrages que provoquent les clics de style plus haut
            # (leur anti-rebond de 400 ms ecraserait sinon le message dans la ligne de statut).
            manager.status["live"] = "message du gestionnaire"
            data = params["_automation"]
            out["reset"] = (data["logo_x"]["enabled"], data["bg_hue"]["enabled"], data["bg_tile"]["period"])
            compact = [w for w in walk(root) if w.winfo_class() == "Button" and w.cget("text") == "∿"]
            out["compact_buttons"] = len(compact)
            root.after(300, step4e)

        def step4e():
            # presets decorreles : overlay (logo + effets) et fond (source + motif), tous deux modifiables
            op, bp = c["overlay_presets"], c["bg_presets"]
            obook, bbook = op["book"], bp["book"]
            params["sensitivity"] = 2.2
            c["bg_var"].set("live")
            c["palette_var"].set("classic")
            params["bg_hue"] = 0.11
            op["load"]("neon")
            out["p_neon"] = (params["bg_mode"], params["bg_palette"], params["holo_intensity"], params["fx_link"],
                             params["logo_glow_color"], params["fxl_on"][3], params["bg_hue"])
            out["p_widgets"] = (c["bg_var"].get(), c["link_var"].get(), c["fxl_on_vars"][3].get(),
                                c["color_var"].get(), c["logo_fx_box"].winfo_manager())
            out["p_sens"] = params["sensitivity"]
            bp["load"]("neon")
            out["b_neon"] = (params["bg_mode"], params["bg_palette"], params["holo_intensity"], params["logo_glow_color"],
                             params["fx_link"], c["bg_var"].get())
            bp["load"]("default")
            out["b_default"] = (params["bg_mode"], params["bg_palette"], params["holo_intensity"], c["bg_var"].get())
            op["load"]("default")
            out["p_default"] = (params["bg_mode"], params["fx_link"], params["holo_intensity"], params["fx_on"])
            out["p_default_widgets"] = (c["bg_var"].get(), c["link_var"].get(), c["holo_var"].get())
            # un ancien preset overlay qui contenait des cles du fond ne les applique plus
            old = gl.overlay_preset_values({"bg_hue": 0.9, "bg_mode": "pattern", "holo_intensity": 2.0,
                                            "_automation": {"bg_hue": {"enabled": False}, "logo_x": {"enabled": True}}},
                                           {"bg_hue": 0.3, "bg_mode": "live", "_automation": gl.default_automation()})
            out["p_old_bg"] = (old["bg_hue"], old["bg_mode"], old["holo_intensity"], old["_automation"]["bg_hue"]["enabled"],
                               old["_automation"]["logo_x"]["enabled"])
            params["bg_hue"] = 0.42
            params["text_content"] = "MON TEXTE"
            c["save_name_var"].set("Mon Look")
            op["save"]()
            bp["save_var"].set("Mon Fond")
            bp["save"]()
            saved = json.loads(gl.OVERLAY_PRESETS_PATH.read_text(encoding="utf-8")) if gl.OVERLAY_PRESETS_PATH.exists() else {}
            sbg = json.loads(gl.BACKGROUND_PRESETS_PATH.read_text(encoding="utf-8")) if gl.BACKGROUND_PRESETS_PATH.exists() else {}
            ml, mf = saved.get("mon look", {}), sbg.get("mon fond", {})
            out["p_saved"] = ("mon look" in saved and "sensitivity" not in ml and "_automation" in ml
                              and not any(k.startswith("bg_") for k in ml)
                              and not any(k.startswith("bg_") for k in ml.get("_automation", {}))
                              and ml.get("text_content") == "MON TEXTE")
            out["b_saved"] = ("mon fond" in sbg and mf.get("bg_hue") == 0.42 and "text_content" not in mf
                              and "holo_intensity" not in mf and "_automation" in mf
                              and all(k.startswith("bg_") for k in mf["_automation"]))
            params["bg_hue"] = 0.0
            params["text_content"] = "AUTRE"
            op["load"]("mon look")
            out["p_reload"] = (params["text_content"], c["text_widget"].get("1.0", "end-1c"), params["bg_hue"])
            bp["load"]("mon fond")
            out["b_reload"] = (params["bg_hue"], c["auto_vars"]["bg_hue"].get(), params["text_content"])
            # les integres se modifient, se suppriment et se restaurent
            op["var"].set("neon")
            params["holo_intensity"] = 1.9
            op["update"]()
            op["load"]("default")
            op["load"]("neon")
            out["p_edit_builtin"] = (params["holo_intensity"], obook.is_modified("neon"))
            out["p_delete_builtin"] = (obook.delete("sobre"), "sobre" in obook.names())
            obook.restore_builtins()
            op["refresh"]()
            op["load"]("neon")
            out["p_restore"] = (params["holo_intensity"], "sobre" in obook.names(), "mon look" in obook.names())
            params["holo_intensity"] = 0.5
            op["var"].set("default")
            op["update"]()
            out["p_default_msg"] = op["msg"].get()
            params["holo_intensity"] = 1.0
            op["load"]("default")
            out["p_default_edit"] = params["holo_intensity"]
            out["p_default_reset"] = (obook.delete("default"), obook.all()["default"])
            op["load"]("default")
            out["p_default_back"] = params["holo_intensity"]
            c["save_name_var"].set("aaa")
            op["save"]()
            out["p_menu"] = [c["overlay_menu"]["menu"].entrycget(i, "label")
                             for i in range(c["overlay_menu"]["menu"].index("end") + 1)]
            out["p_delete"] = obook.delete("mon look")
            bbook.delete("sobre")
            out["b_menu"] = bbook.names()
            bbook.restore_builtins()
            out["b_restored"] = "sobre" in bbook.names()
            out["b_default_first"] = bbook.names()[0]
            params["holo_intensity"] = 1.0
            root.after(200, step5)

        def step5():
            # Fenetre adaptative : cote a cote quand elle est large, empilee sinon, ascenseur quand elle est basse
            lw, rp, pb = c["live_wrap"], c["right_panel"], c["page_bar"]

            def geo(size):
                root.geometry(size)
                root.update()
                root.update()
                return (c["layout"]["name"], pb.winfo_ismapped(), lw.winfo_x(), lw.winfo_y(), lw.winfo_width(), lw.winfo_height(),
                        rp.winfo_x(), rp.winfo_y(), rp.winfo_width(), rp.winfo_height(), root.winfo_width(), root.winfo_height())
            out["lay_big"] = geo("1900x1000")
            out["lay_mid"] = geo("1250x800")
            out["lay_small"] = geo("1000x420")
            out["lay_back"] = geo("1800x900")
            texts = [w.cget("text") for w in walk(root) if w.winfo_class() == "Label"]
            out["live_msg"] = "message du gestionnaire" in texts       # dans la ligne de statut d'audio2wave
            out["mine_msg"] = any("Automations: ambiance par defaut" in t and "fps" in t for t in texts)
            out["stage"] = "snap"
            c["switch"]("snap")               # bascule de source : la fenetre est reconstruite (on_ready rappelee)

        step1()

    import json
    import tempfile as _tf
    real_presets_path = gl.OVERLAY_PRESETS_PATH
    real_bg_presets_path = gl.BACKGROUND_PRESETS_PATH
    _tmp_presets = Path(_tf.mkdtemp(prefix="casual_overlay_presets_"))
    gl.OVERLAY_PRESETS_PATH = _tmp_presets / "overlay_presets.json"        # jamais les vrais fichiers
    gl.BACKGROUND_PRESETS_PATH = _tmp_presets / "background_presets.json"
    try:
        gl_gui.run_gui(s, live, on_ready)
    finally:
        gl.OVERLAY_PRESETS_PATH = real_presets_path
        gl.BACKGROUND_PRESETS_PATH = real_bg_presets_path
    p = out.get("p_neon", ())
    check(p == ("live", "classic", 1.5, 0.0, "#ff3df2", 1, 0.11),
          "preset overlay integre charge (logo, effets, halo) SANS toucher au fond", str(p))
    w = out.get("p_widgets", ())
    check(w[:4] == ("live", 0, 1, "#ff3df2") and w[4] == "grid" if len(w) == 5 else False,
          "preset overlay: les widgets suivent (lier, effets du logo, couleur), le fond reste celui d'avant", str(w))
    check(out.get("p_sens") == 2.2, "preset overlay: la sensibilite du kick (micro) n'est pas touchee")
    bn = out.get("b_neon", ())
    check(bn == ("pattern", "duo", 1.5, "#ff3df2", 0.0, "pattern"),
          "preset de fond: change le motif et le widget, sans toucher au logo ni aux effets", str(bn))
    check(out.get("b_default") == ("live", "classic", 1.5, "live"), "preset de fond 'default': fond d'origine, halo intact",
          str(out.get("b_default")))
    d = out.get("p_default", ())
    check(d == ("live", 1.0, 1.0, [1, 1, 1, 1, 1]) and out.get("p_default_widgets") == ("live", 1, int(gl.DEFAULT_PARAMS["holo_on"] >= 0.5)),
          "preset overlay 'default': retour aux reglages d'origine du logo et des effets, widgets compris", str(d))
    check(out.get("p_old_bg") == (0.3, "live", 2.0, True, True),
          "un preset overlay (ancien) qui contient des reglages du fond ne les applique plus", str(out.get("p_old_bg")))
    check(out.get("p_saved"), "sauvegarde overlay: minuscules, sans sensibilite, sans fond ni automations du fond")
    check(out.get("b_saved"), "sauvegarde du fond: seulement les cles bg_* et leurs automations")
    r = out.get("p_reload", ())
    check(r == ("MON TEXTE", "MON TEXTE", 0.0), "rechargement d'un preset overlay: texte retrouve, fond non touche", str(r))
    r = out.get("b_reload", ())
    check(len(r) == 3 and r[0] == 0.42 and abs(r[1] - 0.42) < 1e-9 and r[2] == "MON TEXTE",
          "rechargement d'un preset de fond: valeur et curseur retrouves, texte non touche", str(r))
    check(out.get("p_edit_builtin") == (1.9, True), "un preset integre se modifie (version utilisateur qui le remplace)",
          str(out.get("p_edit_builtin")))
    check(out.get("p_delete_builtin") == ("deleted", False), "un preset integre se supprime", str(out.get("p_delete_builtin")))
    check(out.get("p_restore") == (1.5, True, True),
          "Restaurer: integres d'origine rendus (modifie et supprime), presets utilisateur gardes", str(out.get("p_restore")))
    check("point de depart" in out.get("p_default_msg", "") and out.get("p_default_edit") == 0.5,
          "'default' est modifiable", str(out.get("p_default_msg")))
    dr = out.get("p_default_reset", ())
    check(dr and dr[0] == "reset" and dr[1] == {} and out.get("p_default_back") == 1.0,
          "supprimer 'default' le remet a sa version d'origine (il ne disparait jamais)", str(dr))
    check(out.get("p_menu", [])[:1] == ["default"] and {"aaa", "sobre", "neon", "chaos", "mon look"} <= set(out.get("p_menu", [])),
          "menu des presets overlay: default en tete meme avec un preset 'aaa'", str(out.get("p_menu")))
    check(out.get("p_delete") == "deleted", "suppression d'un preset utilisateur")
    check(out.get("b_default_first") == "default" and "sobre" not in out.get("b_menu", ["sobre"]) and out.get("b_restored"),
          "presets de fond: default en tete, suppression et restauration d'un integre", str(out.get("b_menu")))
    lv = {k: out.get(k, {}) for k in ("g_live_radio", "g_live_bar", "g_live_line")}
    off, on = {"disabled"}, {"normal"}
    check(all(lv["g_live_radio"].get(lb) == off for lb in ("Forme", "Lissage", "Echelle frequences", "Espace entre barres"))
          and lv["g_live_radio"].get("Barres/points") == on and lv["g_live_radio"].get("Gain (dB)") == on,
          "Live, style radio: forme, lissage, echelle des frequences et espace entre barres grises ; barres et gain actifs",
          str(lv["g_live_radio"]))
    check(all(lv["g_live_bar"].get(lb) == on for lb in ("Forme", "Lissage", "Echelle frequences", "Espace entre barres")),
          "Live, analyzer en barres: tout est actif", str(lv["g_live_bar"]))
    check(lv["g_live_line"].get("Espace entre barres") == off and lv["g_live_line"].get("Lissage") == on
          and lv["g_live_line"].get("Forme") == on, "Live, analyzer en ligne: l'espace entre barres est grise, le reste actif")
    check(out.get("g_holo_off") == off and out.get("g_holo_on") == on, "halo coupe: ses curseurs sont grises, rallumes avec la case",
          f"{out.get('g_holo_off')} {out.get('g_holo_on')}")
    check(out.get("g_fx_off") == (off, off) and out.get("g_fx_on") == (on, on),
          "fonte et cellules coupees: leurs curseurs sont grises, actifs une fois cochees", f"{out.get('g_fx_off')} {out.get('g_fx_on')}")
    gp = out.get("g_pal", {})
    check(gp.get("classic", {}).get("Couleurs 1 / 2") == off and gp.get("classic", {}).get("Angle du degrade") == off
          and gp.get("classic", {}).get("Teinte") == on and gp.get("duo", {}).get("Couleurs 1 / 2") == on
          and gp.get("duo", {}).get("Angle du degrade") == on and gp.get("test", {}).get("Teinte") == off
          and gp.get("test", {}).get("Vitesse") == off and gp.get("test", {}).get("Couleurs 1 / 2") == off,
          "palette du fond: couleurs et angle seulement en Duo ; en Banc de test tous les reglages du motif sont grises", str(gp))
    big, mid, small, back = (out.get(k, ()) for k in ("lay_big", "lay_mid", "lay_small", "lay_back"))
    check(len(big) == 12 and big[0] == "wide" and not big[1] and big[6] > big[2] + big[4] - 5 and big[7] == big[3]
          and big[9] > 700 and big[8] > 800, "fenetre large: LIVE et OVERLAY cote a cote, l'overlay prend la place en plus", str(big))
    check(len(mid) == 12 and mid[0] == "stack" and mid[7] >= mid[3] + mid[5] - 5 and mid[8] > 1100,
          "fenetre moyenne: OVERLAY sous LIVE, a toute la largeur", str(mid))
    check(len(small) == 12 and small[0] == "stack" and small[1] and small[8] <= small[10],
          "fenetre basse: ascenseur de page, rien ne depasse en largeur", str(small))
    check(len(back) == 12 and back[0] == "wide" and not back[1] and back[5] <= back[11],
          "retour a une grande fenetre: cote a cote, plus d'ascenseur, pas de hauteur residuelle", str(back))
    check(out.get("snap_ridge") is True, "boutons Snap / Ridge de la fenetre d'audio2wave conserves (bascule de source)")
    for name in ("snap", "ridge"):
        m = out.get(name, {})
        check(m.get("mode") == name and m.get("src") == name and m.get("suspended") is (True)
              and m.get("reader_ok") and m.get("frames", 0) > 5,
              f"mode {name}: source active, ffmpeg live suspendu, images du mode lues par le rendu",
              f"{m.get('frames')} images")
        check(m.get("banner") and m.get("presets") and m.get("overlay") and m.get("buttons") == (True, name != "snap", name != "ridge"),
              f"mode {name}: panneau d'audio2wave (bandeau, presets, boutons de bascule) + partie OVERLAY", str(m.get("buttons")))
        check(m.get("height", 9999) <= 700, f"mode {name}: fenetre sous 700 px de haut (ascenseur dans le panneau)",
              str(m.get("height")))
    check(out.get("fs_frames", 0) >= 6, "mode snap: un preset qui change Plein ecran (visionneur remplace) n'interrompt pas le flux",
          str(out.get("fs_frames")))
    b = out.get("live_back", {})
    check(b.get("mode") == "live" and b.get("src") is None and b.get("suspended") is False
          and b.get("resumed") == ["Micro"] and b.get("reader_ok") and b.get("banner") and b.get("gain"),
          "retour au mode Live : ffmpeg relance sur l'entree courante, lecteur d'origine, panneau Live reconstruit", str(b))
    check(out.get("size_ro"), "taille de fenetre d'audio2wave figee (= taille du rendu)")
    check(out.get("presets") and out.get("automation", 0) >= 1,
          "presets et automations d'audio2wave presents", f"{out.get('automation')} curseurs automatables")
    check(out.get("burst") == 1, "rafale de reglages live: un seul redemarrage (anti-rebond d'audio2wave)",
          str(out.get("burst")))
    check(out.get("gain") == 12.0 and out.get("copy"), "le redemarrage porte la derniere valeur, sur une copie")
    check(out.get("size") == "1280x720", "la taille du rendu reste fixee")
    check(out.get("no_restart_fs") and out.get("fs_cmd") == "fullscreen=0",
          "case Plein ecran: commande la fenetre GL sans redemarrer ffmpeg")
    check(out.get("style") == "radio", "style radio (GUI d'audio2wave) -> producteur remplace", str(out.get("style")))
    check(out.get("x") == 0.2 and out.get("fx1") == 0 and abs(out.get("master", 0) - 1.4) < 1e-9,
          "reglages logo/effets ecrits tout de suite dans les parametres")
    check(out.get("holo_off") == 0.0 and out.get("holo_on") == 1.0,
          "halo holographique: la case ecrit holo_on")
    check(out.get("tabs") == ["Effets", "Logo", "Aura du logo", "Fonte du logo", "Cellules", "Affichage"],
          "partie OVERLAY rangee en onglets", str(out.get("tabs")))
    check(out.get("win_height", 9999) <= 700, "GUI compacte: fenetre sous 700 px de haut (dans le pire cas: motif + effets du logo delies)",
          str(out.get("win_height")))
    check(out.get("logo") == "", "logo vide = aucun logo")
    check(out.get("color_bad") == gl.DEFAULT_PARAMS["logo_glow_color"] and out.get("color_ok") == "#ff0000",
          "couleur du contour: invalide ignoree, valide appliquee")
    check(out.get("pattern_hidden") == "" and out.get("bg_param") == "pattern" and out.get("pattern_shown") == "grid",
          "selecteur Fond: le bloc motif apparait et ecrit bg_mode")
    check(out.get("bg_c1") == "#102030", "couleur 1 du motif ecrite dans les parametres")
    check(out.get("image_shown") == "grid" and out.get("source_param") == "text"
          and out.get("text_shown") == "grid" and out.get("image_hidden") == "",
          "selecteur Source: le bloc texte remplace le bloc image et ecrit logo_source")
    check(out.get("typed") == "HELLO\nWORLD" and out.get("font") == "Impact",
          "texte tape (plusieurs lignes) et police ecrits dans les parametres", repr(out.get("typed")))
    check(out.get("auto_registered"), "tous les reglages automatisables ont leur editeur de courbe (audio2wave)",
          f"{out.get('compact_buttons')} boutons")
    check(out.get("sync_enabled") == (False, True) and out.get("sync_period") == 33.0 and out.get("sync_master") == 0.0,
          "editeur -> moteur: cases '~', periode et interrupteur general copies dans params")
    check(out.get("follow") == 1.23, "les curseurs automatises suivent la valeur calculee par le moteur")
    default_bg = gl.AUTOMATION_SPECS["bg_tile"][4]
    check(out.get("reset") == (False, True, float(default_bg)), "bouton 'Ambiance par defaut': etat d'origine restaure")
    check(out.get("live_msg"), "message du gestionnaire de flux affiche dans le statut d'audio2wave")
    check(out.get("mine_msg"), "ligne de statut de casual-overlay: fps et messages")


# --- 2. features audio ---------------------------------------------------------------

def run_signal(signal, rate: int, hop: int = 512, sensitivity: float = 1.0):
    """Fait defiler le signal comme le fil d'analyse: (temps des beats, niveaux finaux)."""
    ex = gl.FeatureExtractor(rate, sensitivity=sensitivity)
    ring = gl.RingBuffer()
    beats, levels = [], {}
    for i in range(0, len(signal) - hop, hop):
        ring.write(signal[i:i + hop])
        t = (i + hop) / rate
        levels, beat = ex.process(ring.latest(ex.win_size), hop / rate, t)
        if beat:
            beats.append(t)
    return beats, levels


def kick_bursts(rate: int, seconds: float, amp: float = 0.5, noise: float = 0.002):
    rng = np.random.default_rng(5)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    ph = t % 0.5
    env = np.where(ph < 0.1, np.sin(np.pi * ph / 0.1), 0.0)        # salve de 100 ms toutes les 500 ms
    return (amp * env * np.sin(2 * np.pi * 60 * t) + noise * rng.standard_normal(n)).astype(np.float32)


def check_features() -> None:
    print("Features audio")
    for rate in (48000, 44100):
        secs = 20
        beats, _ = run_signal(kick_bursts(rate, secs), rate)
        bursts = int(secs / 0.5)
        # les 0.5 premieres secondes servent de chauffe: la premiere salve peut etre manquee
        check(bursts - 1 <= len(beats) <= bursts, f"{rate} Hz: un beat par salve",
              f"{len(beats)} beats pour {bursts} salves")
        gaps = np.diff(beats)
        check(len(gaps) > 0 and abs(float(np.median(gaps)) - 0.5) < 0.02, f"{rate} Hz: periode ~500 ms",
              f"mediane {np.median(gaps) * 1000:.0f} ms")

    # Robustesse au niveau du micro: meme salve, 30 dB plus faible.
    beats_quiet, _ = run_signal(kick_bursts(48000, 20, amp=0.5 * 10 ** (-30 / 20), noise=0.00002), 48000)
    check(len(beats_quiet) >= 18, "niveau micro -30 dB: kicks toujours detectes (auto-gain)",
          f"{len(beats_quiet)} beats")

    # Kick pose sur une basse continue (55 Hz) : limite connue a sensibilite 1.0, la
    # sensibilite (touches haut/bas) doit permettre de le rattraper.
    rng2 = np.random.default_rng(4)
    tt = np.arange(48000 * 20) / 48000
    env = np.where((tt % 0.5) < 0.1, np.sin(np.pi * (tt % 0.5) / 0.1), 0.0)
    onbass = (0.15 * np.sin(2 * np.pi * 55 * tt) + 0.3 * env * np.sin(2 * np.pi * 60 * tt)
              + 0.002 * rng2.standard_normal(len(tt))).astype(np.float32)
    beats_hi, _ = run_signal(onbass, 48000, sensitivity=2.5)
    check(len(beats_hi) >= 30, "kick sur basse continue: detecte avec sensibilite 2.5", f"{len(beats_hi)}/40")

    # Faux positifs.
    rng = np.random.default_rng(9)
    for amp in (0.05, 0.3):
        noise = (amp * rng.standard_normal(48000 * 60)).astype(np.float32)
        beats, _ = run_signal(noise, 48000)
        # 0.3 = bruit quasi ecrete, hors cas realiste: on tolere 1 declenchement par minute.
        check(len(beats) <= (0 if amp < 0.1 else 1), f"bruit blanc constant (amplitude {amp}) sur 60 s: "
              "pas de faux positif", f"{len(beats)} beats")
    tone = (0.3 * np.sin(2 * np.pi * 60 * np.arange(48000 * 20) / 48000)).astype(np.float32)
    beats, _ = run_signal(tone, 48000)
    check(len(beats) == 0, "sinus 60 Hz continu: aucun faux positif", f"{len(beats)} beats")

    # Silence: niveaux nuls (sinon l'auto-gain amplifierait le bruit du micro).
    quiet = (0.0001 * rng.standard_normal(48000 * 10)).astype(np.float32)
    beats, levels = run_signal(quiet, 48000)
    check(not beats and all(levels[k] < 0.05 for k in ("bass", "mid", "high", "rms")),
          "silence: aucun beat, niveaux ~0", f"{ {k: round(levels[k], 3) for k in ('bass', 'mid', 'high', 'rms')} }")

    # Bandes: un sinus 60 Hz alimente les basses, un sinus 5 kHz les aigus.
    for freq, band in ((60, "bass"), (500, "mid"), (5000, "high")):
        s = (0.3 * np.sin(2 * np.pi * freq * np.arange(48000 * 3) / 48000)).astype(np.float32)
        _, lv = run_signal(s, 48000)
        others = [lv[b] for b in ("bass", "mid", "high") if b != band]
        check(lv[band] > 0.8 and max(others) < 0.2, f"sinus {freq} Hz -> bande {band} seule",
              f"{band}={lv[band]:.2f} autres<={max(others):.2f}")

    # Latence de detection (logicielle): debut de salve -> beat.
    rate = 48000
    sig = gl.synth_audio_block(0, rate * 8, rate, 120.0)          # vrai kick: attaque instantanee
    beats, _ = run_signal(sig, rate)
    lat = [(b % 0.5) * 1000 for b in beats[1:]]
    print(f"  latence logicielle kick -> beat: mediane {np.median(lat):.0f} ms "
          f"(max {max(lat):.0f} ms; hop 10,7 ms + fenetre 2048, hors capture et rendu)")
    check(len(beats) >= 14, "synthese audio (kick 120 BPM): kicks detectes", f"{len(beats)}/16")
    check(np.median(lat) < 50, "latence de detection < 50 ms")

    # Ring: latest() apres bouclage.
    ring = gl.RingBuffer(size=1024)
    ring.write(np.arange(700, dtype=np.float32))
    ring.write(np.arange(700, 1500, dtype=np.float32))
    out = ring.latest(300)
    check(np.array_equal(out, np.arange(1200, 1500, dtype=np.float32)), "RingBuffer: ordre correct apres bouclage")


# --- 3. rendu standalone ---------------------------------------------------------------

def read_target(ctx, fbo, size):
    data = fbo.read(components=3)
    return np.frombuffer(data, dtype=np.uint8).reshape(size[1], size[0], 3)[::-1]   # haut en premier


def check_render() -> None:
    print("Rendu OpenGL (contexte standalone)")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    stream = gl.SyntheticVideoStream(size[0], size[1], fps=1000)
    frame = b"".join(stream.read(1 << 20) for _ in range(1))
    while len(frame) < size[0] * size[1] * 3:
        frame += stream.read(size[0] * size[1] * 3 - len(frame))
    video = np.frombuffer(frame, np.uint8).reshape(size[1], size[0], 3)

    logo_path = gl.DEFAULT_LOGO
    logo = gl.load_logo(logo_path) if logo_path.is_file() else None
    target_tex = ctx.texture(size, 4)
    fbo = ctx.framebuffer(color_attachments=[target_tex])

    # Image neutre au repos, sans logo.
    r = gl.Renderer(ctx, size, None)
    r.video_tex.write(frame, alignment=1)
    idle = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 10.0}
    r.draw(fbo, size, idle, gl.load_params() | {"fx_on": [1] * 5}, 1.0)
    out = read_target(ctx, fbo, size)
    diff = np.abs(out.astype(int) - video.astype(int))
    check(diff.mean() < 1.0 and diff.max() <= 3, "audio coupe: image identique a la video (aucun effet)",
          f"ecart moyen {diff.mean():.2f}, max {diff.max()}")

    # Effets en plein kick: l'image doit changer sans etre noire ni saturee.
    loud = {"bass": 1.0, "mid": 0.6, "high": 0.9, "rms": 0.8, "beat": 1.0, "since_beat": 0.05}
    params = {"fx_on": [1] * 5, "fx_int": [1.0] * 5, "master": 1.0, "sensitivity": 1.0}
    r.draw(fbo, size, loud, params, 1.0)
    out2 = read_target(ctx, fbo, size)
    change = np.abs(out2.astype(int) - video.astype(int)).mean()
    check(change > 1.0, "kick: l'image est deformee", f"ecart moyen {change:.1f}")
    check(20 < out2.mean() < 235, "kick: image ni noire ni saturee", f"moyenne {out2.mean():.0f}")

    # Chaque effet est visible isolement.
    for i, name in enumerate(gl.FX_NAMES[:4]):
        p = dict(params, fx_on=[1 if j == i else 0 for j in range(5)])
        state = dict(loud, since_beat=0.3, beat=0.22) if name == "ripple" else loud
        r.draw(fbo, size, state, p, 1.0)
        d = np.abs(read_target(ctx, fbo, size).astype(int) - video.astype(int)).mean()
        check(d > 0.3, f"effet {name} visible isolement", f"ecart moyen {d:.2f}")

    # Fond genere (motif): rendu dans le shader, sans flux video.
    def render(rr, state, p, t=1.0):
        rr.draw(fbo, size, state, p, t)
        return read_target(ctx, fbo, size).astype(int)

    pat = dict(params, fx_on=[0] * 5, bg_mode="pattern")          # effets coupes: on juge le motif seul
    rp = gl.Renderer(ctx, size, None)
    rp.draw(fbo, size, idle, pat, 0.0)                           # initialise l'horloge du motif
    base = render(rp, idle, pat, 0.0)
    check(base.std() > 20 and abs(base - video.astype(int)).mean() > 10,
          "motif: image non vide, distincte de la video", f"ecart-type {base.std():.0f}")
    left_px, right_px = base[:, :size[0] // 8].mean(axis=(0, 1)), base[:, -size[0] // 8:].mean(axis=(0, 1))
    check(abs(left_px - right_px).max() > 40, "motif classic: le degrade horizontal varie de gauche a droite")
    moved = render(rp, idle, pat, 1.5)                            # 1,5 s plus tard, vitesse 1
    check(abs(moved - base).mean() > 5, "motif: defile au cours du temps", f"ecart {abs(moved - base).mean():.1f}")
    frozen = gl.Renderer(ctx, size, None)
    p0 = dict(pat, bg_speed=0.0, bg_flip=0.0)
    frozen.draw(fbo, size, idle, p0, 0.0)
    f0 = render(frozen, idle, p0, 0.0)
    f1 = render(frozen, idle, p0, 2.0)
    check(abs(f1 - f0).max() == 0, "vitesse 0 et cadence 0: motif fige")
    big = render(gl.Renderer(ctx, size, None), idle, dict(p0, bg_tile=200.0), 0.0)
    small = render(gl.Renderer(ctx, size, None), idle, dict(p0, bg_tile=40.0), 0.0)

    def edges(img):          # nombre de sauts brusques le long d'une ligne = nombre de carreaux traverses
        line = img[size[1] // 2, :, :].sum(axis=1)
        return int((abs(np.diff(line)) > 40).sum())

    check(edges(small) > edges(big) * 2, "taille des carreaux: plus petit = plus de carreaux",
          f"{edges(big)} -> {edges(small)} transitions")
    flat = render(gl.Renderer(ctx, size, None), idle, dict(p0, bg_checker=0.0), 0.0)
    check(abs(flat - render(gl.Renderer(ctx, size, None), idle, dict(p0, bg_checker=0.0, bg_tile=30.0), 0.0)).max() == 0,
          "contraste du damier a 0: la taille des carreaux n'a plus d'effet")
    duo = dict(p0, bg_palette="duo", bg_color1="#ff0000", bg_color2="#0000ff", bg_checker=0.0, bg_react=0.0)
    d = render(gl.Renderer(ctx, size, None), idle, duo, 0.0)
    check(d[:, :, 1].max() < 5 and d[:, :, 0].max() > 200 and d[:, :, 2].max() > 200,
          "palette duo rouge/bleu: aucun vert, les deux couleurs presentes")
    # Palette "test": identique octet pour octet au flux de test SyntheticVideoStream, image par image.
    stream = gl.SyntheticVideoStream(size[0], size[1])
    tp = dict(p0, bg_palette="test", bg_hue=0.3, bg_tile=150.0, bg_checker=0.9, bg_react=1.0)   # ces reglages sont ignores
    rt_pal = gl.Renderer(ctx, size, None)
    rt_pal.draw(fbo, size, idle, tp, 0.0)
    worst = 0
    for n in (0, 1, 14, 15, 16, 85, 100, 300):
        stream._n = n
        want = np.frombuffer(stream._frame(), np.uint8).reshape(size[1], size[0], 3).astype(int)
        rt_pal._bg_clock = (n + 0.5) / 30.0
        worst = max(worst, int(abs(render(rt_pal, idle, tp, 0.0) - want).max()))
    check(worst == 0, "palette 'Banc de test': identique octet pour octet a SyntheticVideoStream (8 images)",
          f"ecart max {worst}")
    hue = render(gl.Renderer(ctx, size, None), idle, dict(duo, bg_hue=1.0 / 3.0), 0.0)
    check(hue[:, :, 2].max() < 5 and hue[:, :, 1].max() > 200 and hue[:, :, 0].max() > 200,
          "teinte +1/3 (120 deg): rouge -> vert, bleu -> rouge (plus de bleu)")
    dark = render(gl.Renderer(ctx, size, None), idle, dict(p0, bg_react=1.0), 0.0)
    flash = render(gl.Renderer(ctx, size, None), dict(idle, beat=1.0), dict(p0, bg_react=1.0), 0.0)
    calm = render(gl.Renderer(ctx, size, None), dict(idle, beat=1.0), dict(p0, bg_react=0.0), 0.0)
    check(flash.mean() > dark.mean() + 3 and abs(calm - dark).max() == 0,
          "reaction au kick: flash avec reactivite, rien a 0", f"{dark.mean():.0f} -> {flash.mean():.0f}")
    flips = render(gl.Renderer(ctx, size, None), dict(idle, beats=1), dict(p0, bg_react=1.0), 0.0)
    check(abs(flips - dark).mean() > 3, "reaction au kick: le damier bascule a chaque kick")
    logo_on_pat = gl.Renderer(ctx, size, logo, str(logo_path)) if logo is not None else None
    if logo_on_pat is not None:
        with_logo = render(logo_on_pat, idle, dict(pat, logo_scale=0.4), 0.0)
        check(abs(with_logo - base).mean() > 1, "le logo s'incruste aussi sur le motif")

    # Texte incruste (source "text"): genere par apply_logo_request, affiche comme un logo.
    from types import SimpleNamespace as NS
    text_params = dict(params, logo_source="text", text_content="HELLO", text_scale=0.3, text_color="#ff0000",
                       logo_x=0.5, logo_y=0.5, logo_opacity=1.0, fx_on=[0] * 5, logo_glow=0.0, bg_mode="live")
    fake = NS(params=text_params, status={})
    rt = gl.Renderer(ctx, size, None)
    rt.logo_requested = None
    gl.apply_logo_request(fake, rt)
    check(rt.has_logo and rt.logo_kind == "text" and fake.status["logo"].startswith("Texte"),
          "apply_logo_request: source texte -> texture generee", fake.status.get("logo", ""))
    rt.video_tex.write(frame, alignment=1)
    with_text = render(rt, idle, text_params, 0.0)
    mask = abs(with_text - video.astype(int)).sum(axis=2) > 120
    ys, xs = np.nonzero(mask)
    check(len(ys) > 200, "le texte s'affiche sur l'image", f"{len(ys)} pixels modifies")
    if len(ys):
        height_frac = (ys.max() - ys.min()) / size[1]
        check(0.08 < height_frac < 0.3 and abs((ys.min() + ys.max()) / 2 / size[1] - 0.5) < 0.06
              and abs((xs.min() + xs.max()) / 2 / size[0] - 0.5) < 0.06,
              "texte centre, hauteur proportionnelle a la taille du texte", f"hauteur {height_frac:.2f}")
    original_render_text, calls = gl.render_text, []
    gl.render_text = lambda *a, **k: (calls.append(1), original_render_text(*a, **k))[1]
    try:
        gl.apply_logo_request(fake, rt)                               # rien n'a change
        unchanged = len(calls)
        text_params["logo_x"] = 0.3                                   # position: pas de regeneration
        gl.apply_logo_request(fake, rt)
        moved = len(calls)
        text_params["text_content"] = "HELLO WORLD"                   # texte: regeneration
        gl.apply_logo_request(fake, rt)
        retyped = len(calls)
    finally:
        gl.render_text = original_render_text
    check((unchanged, moved, retyped) == (0, 0, 1),
          "texture regeneree seulement quand le texte/la police/la couleur changent", f"{(unchanged, moved, retyped)}")
    text_params["text_content"] = "  "
    gl.apply_logo_request(fake, rt)
    check(not rt.has_logo and fake.status["logo"] == "Texte vide", "texte vide: plus de logo")
    text_params.update(logo_source="image", logo_path=str(logo_path))
    if logo is not None:
        gl.apply_logo_request(fake, rt)
        check(rt.has_logo and rt.logo_kind == "image", "retour a la source image")

    # Logo.
    if logo is not None:
        r2 = gl.Renderer(ctx, size, logo, str(logo_path))
        r2.video_tex.write(frame, alignment=1)
        params = dict(params, logo_scale=0.4, logo_x=0.5, logo_y=0.5)
        r2.draw(fbo, size, idle, params, 1.0)
        out3 = read_target(ctx, fbo, size)
        cx, cy, hw, hh = gl.logo_layout(size[0], size[1], r2.logo_aspect, 0.4, (0.5, 0.5))
        x0, x1 = int((cx - hw) * size[0]), int((cx + hw) * size[0])
        y0, y1 = int((cy - hh) * size[1]), int((cy + hh) * size[1])
        inside = np.abs(out3[y0:y1, x0:x1].astype(int) - video[y0:y1, x0:x1].astype(int)).mean()
        outside = np.abs(out3[:y0].astype(int) - video[:y0].astype(int)).mean() if y0 > 0 else 0.0
        check(inside > 2.0, "logo incruste dans son rectangle", f"ecart moyen {inside:.1f}")
        check(outside < 1.0, "video intacte hors du logo", f"ecart moyen {outside:.2f}")
        out_png = Path(tempfile.gettempdir()) / "casual_overlay_check.png"
        r2.draw(fbo, size, loud, params, 1.0)
        gl.PIL_Image.fromarray(np.ascontiguousarray(read_target(ctx, fbo, size))).save(out_png)
        print(f"  rendu de controle (kick, logo, effets): {out_png}")
        check(out_png.stat().st_size > 5000, "PNG de rendu non vide")

        # Le logo ne sort jamais du cadre, meme en pleine pulsation et en bord de cadre.
        ok = True
        for pos in ((0.5, 0.5), (0.05, 0.05), (0.98, 0.5), (0.5, 0.99)):
            for scale in (0.2, 0.6, 0.95):
                for pulse in (0.0, 0.12, 0.5):
                    cx, cy, hw, hh = gl.logo_layout(1920, 1080, r2.logo_aspect, scale, pos, pulse, (0.02, -0.02))
                    ok &= cx - hw >= -1e-6 and cx + hw <= 1 + 1e-6 and cy - hh >= -1e-6 and cy + hh <= 1 + 1e-6
        check(ok, "logo_layout: jamais hors cadre (pulse, jitter, bords)")

    # Rechargement de shader: une erreur garde l'ancien programme.
    before = r.post_prog
    bad = Path(tempfile.mkdtemp())
    for f in gl.SHADER_DIR.iterdir():
        (bad / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (bad / "post.frag").write_text("#version 330\nvoid main() { erreur }", encoding="utf-8")
    r.shader_dir = bad
    try:
        r.reload_shaders()
        check(False, "shader invalide refuse")
    except gl.ShaderError:
        check(r.post_prog is before, "shader invalide: ShaderError, ancien programme conserve")


def check_text() -> None:
    print("Texte a incruster / plein ecran par defaut")
    img = gl.render_text("CASUAL RAVERS", "auto", "#ff0000", "center", 128)
    check(img is not None and img.ndim == 3 and img.shape[2] == 4 and img.dtype == np.uint8,
          "render_text: tableau RGBA uint8")
    a, rgb = img[:, :, 3], img[:, :, :3]
    check(a.max() == 255 and (a == 0).mean() > 0.3, "texte opaque sur fond transparent")
    check((rgb.astype(int) <= a[:, :, None]).all(), "alpha premultiplie (rgb <= alpha)")
    solid = a == 255
    check(rgb[solid][:, 0].min() > 240 and rgb[solid][:, 1:].max() == 0, "couleur du texte respectee (rouge pur)")
    check(a[:5].max() == 0 and a[:, :5].max() == 0 and a[-5:].max() == 0 and a[:, -5:].max() == 0,
          "marge transparente autour du texte (le contour lumineux n'est pas coupe)")
    wide = gl.render_text("CASUAL RAVERS CASUAL RAVERS", "auto", "#ffffff", "center", 128)
    two = gl.render_text("CASUAL\nRAVERS", "auto", "#ffffff", "center", 128)
    check(wide.shape[1] > img.shape[1] * 1.5, "texte plus long = image plus large")
    check(two.shape[0] > img.shape[0] * 1.5, "deux lignes = image plus haute")
    check(gl.render_text("") is None and gl.render_text("  \n ") is None, "texte vide ou blanc: aucune image")
    check(gl.render_text("abc", "police-inexistante") is not None, "police inconnue: repli, pas d'exception")
    big = gl.render_text("W" * 200, "auto", "#ffffff", "center", 256)
    check(max(big.shape[:2]) <= 2048, "texte tres long: image plafonnee a 2048 px", str(big.shape[:2]))
    left = gl.render_text("A\nBBBBBB", "auto", "#fff", "left", 96)
    right = gl.render_text("A\nBBBBBB", "auto", "#fff", "right", 96)
    check(not np.array_equal(left, right), "alignement gauche/droite different sur plusieurs lignes")
    fonts = gl.available_fonts()
    print(f"  polices disponibles: {len(fonts)} ({', '.join(list(fonts)[:5])}...)")
    check(len(fonts) > 0 and all(p.is_file() for p in fonts.values()), "polices Windows courantes detectees")
    sample = next(iter(fonts.values()))
    check(gl.render_text("x", str(sample)) is not None, "police donnee par un chemin de fichier")

    # mise en page: la taille d'un texte est sa HAUTEUR (un carre ecraserait une ligne)
    cx, cy, hw, hh = gl.logo_layout(1920, 1080, 4.0, 0.2, (0.5, 0.5), fit="height")
    check(abs(hh * 2 * 1080 - 0.2 * 1080) < 1.0 and abs(hw * 1920 / (hh * 1080) - 4.0) < 1e-6,
          "fit=height: hauteur = taille x hauteur d'image, ratio conserve")
    cx, cy, hw, hh = gl.logo_layout(1920, 1080, 30.0, 0.2, (0.5, 0.5), fit="height")
    check(2 * hw <= 0.95 + 1e-6, "fit=height: un texte tres long est reduit pour tenir dans le cadre")
    ok = True
    for pos in ((0.5, 0.5), (0.02, 0.02), (0.99, 0.5)):
        for scale in (0.05, 0.3, 0.6):
            for aspect in (0.5, 4.0, 40.0):
                for pulse in (0.0, 0.12, 0.5):
                    cx, cy, hw, hh = gl.logo_layout(1920, 1080, aspect, scale, pos, pulse, (0.02, -0.02), "height")
                    ok &= cx - hw >= -1e-6 and cx + hw <= 1 + 1e-6 and cy - hh >= -1e-6 and cy + hh <= 1 + 1e-6
    check(ok, "fit=height: jamais hors cadre (pulse, jitter, bords)")

    base = dict(gl.DEFAULT_PARAMS, logo_source="text")
    check(gl.logo_signature(base) == gl.logo_signature(dict(base, logo_x=0.1, logo_scale=0.9, text_scale=0.5,
                                                            logo_opacity=0.2, logo_glow=3.0)),
          "signature du logo: position/taille/opacite/effets ne regenerent pas la texture")
    check(len({gl.logo_signature(dict(base, text_content="a")), gl.logo_signature(dict(base, text_content="b")),
               gl.logo_signature(dict(base, text_font="Impact")), gl.logo_signature(dict(base, text_color="#000000")),
               gl.logo_signature(dict(base, text_align="left")),
               gl.logo_signature(dict(base, logo_source="image"))}) == 6,
          "signature du logo: texte, police, couleur, alignement et source la regenerent")

    # plein ecran par defaut: seulement s'il y a un second moniteur (sinon la GUI serait masquee)
    check(gl.resolve_fullscreen(None, None, False) is False, "un seul ecran: fenetre normale par defaut")
    check(gl.resolve_fullscreen(None, None, True) is True, "deux ecrans: plein ecran par defaut")
    check(gl.resolve_fullscreen(None, 1, False) is True, "--monitor explicite: plein ecran")
    check(gl.resolve_fullscreen(True, None, False) is True and gl.resolve_fullscreen(False, None, True) is False,
          "--fullscreen / --no-fullscreen l'emportent")
    check(gl.hide_cursor(True, True) is False and gl.hide_cursor(True, False) is True
          and gl.hide_cursor(False, False) is False,
          "curseur masque uniquement en plein ecran hors moniteur principal")


def wait_until(cond, timeout: float = 8.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def check_logo_video() -> None:
    print("Logo anime (video ffmpeg)")
    import shutil
    import subprocess
    from types import SimpleNamespace as NS
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        check(False, "ffmpeg/ffprobe disponibles")
        return
    tmp = Path(tempfile.mkdtemp(prefix="casual_overlay_video_"))

    def make(name: str, *args: str) -> Path:
        out = tmp / name
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(out)],
                       capture_output=True, timeout=60)
        return out

    alpha = make("alpha.webm", "-f", "lavfi", "-i", "color=c=red:s=64x32:r=20:d=1", "-vf",
                 "format=yuva420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lt(X,32),255,0)'",
                 "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0")
    green = make("green.mp4", "-f", "lavfi", "-i", "color=c=0x00ff00:s=64x32:r=20:d=1", "-vf",
                 "drawbox=x=16:y=8:w=32:h=16:color=red:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p")
    moving = make("moving.mp4", "-f", "lavfi", "-i", "testsrc2=s=64x32:r=20:d=1", "-c:v", "libx264",
                  "-pix_fmt", "yuv420p")
    check(alpha.is_file() and green.is_file() and moving.is_file(), "fichiers de test generes (webm alpha, mp4)")
    if not (alpha.is_file() and green.is_file() and moving.is_file()):
        return

    check(gl.probe_video(str(alpha))[:2] == (64, 32), "ffprobe: taille du fichier")
    try:
        gl.probe_video(str(tmp / "rien.txt"))
        check(False, "fichier invalide: erreur claire")
    except RuntimeError:
        check(True, "fichier invalide: erreur claire")
    big = gl.logo_video_command("x.webm", (100, 50), "vp9", "#00ff00")
    check("libvpx-vp9" in big and "colorkey=0x00ff00:0.3:0.1" in ",".join(big) and "-stream_loop" in big,
          "commande: decodeur libvpx pour vp9, colorkey, boucle")

    def first_frame(video):
        wait_until(lambda: video.reader.frames_read > 0)
        frame = video.reader.take()
        w, h = video.size
        if frame is None:
            return None
        full = np.frombuffer(frame, np.uint8).reshape(h, w, 4)
        last_full["v"] = full
        return full

    last_full: dict = {}
    v = gl.LogoVideo(str(alpha))
    f = first_frame(v)
    full = last_full["v"]
    check(f is not None and f[16, 8, 3] > 250 and f[16, 56, 3] < 5, "webm VP9 : alpha conserve (moitie opaque, moitie transparente)")
    check(f is not None and f[16, 8, 0] > 200 and f[16, 8, 1] < 60, "alpha DROIT: la couleur n'est pas premultipliee")
    t0 = time.monotonic()
    looped = wait_until(lambda: v.reader.frames_read > 26, 6.0)
    check(looped, "lecture en boucle au-dela de la duree du fichier (1 s, 20 images)",
          f"{v.reader.frames_read} images en {time.monotonic() - t0:.1f} s")
    v.stop()
    check(v.proc.poll() is not None, "arret: ffmpeg termine")

    v = gl.LogoVideo(str(green), "#00ff00")
    f = first_frame(v)
    check(f is not None and f[2, 2, 3] < 5 and f[16, 32, 3] > 250, "detourage par couleur: fond vert transparent, carre rouge opaque")
    v.stop()
    v = gl.LogoVideo(str(green))
    f = first_frame(v)
    check(f is not None and f[2, 2, 3] == 255, "sans detourage: fichier opaque (MP4 sans alpha)")
    v.stop()

    v = gl.LogoVideo(str(moving))
    a = first_frame(v)
    wait_until(lambda: v.reader.frames_read > 8)
    b = first_frame(v)
    check(a is not None and b is not None and not np.array_equal(a, b), "l'image change au fil du temps (animation)")
    v.stop()

    # rendu: le logo video apparait, premultiplie cote shader, sans halo sombre
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    r.video_tex.write(bytes([0, 0, 255]) * (size[0] * size[1]))       # fond bleu pur
    params = dict(gl.DEFAULT_PARAMS, logo_source="video", logo_video=str(alpha), logo_key="",
                  logo_x=0.5, logo_y=0.5, logo_scale=0.6, logo_glow=0.0, logo_pulse=0.0, logo_jitter=0.0, master=0.0)
    s = NS(params=params, status={})
    r.logo_requested = None
    gl.apply_logo_request(s, r)
    check(r.logo_video is not None and r.logo_kind == "video" and s.status["logo"].startswith("Video:"),
          "apply_logo_request: source video chargee", s.status.get("logo", ""))
    state = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    wait_until(lambda: r.logo_video.reader.frames_read > 0)
    r.draw(fbo, size, state, params, 0.0)
    img = read_target(ctx, fbo, size)
    cx, cy = size[0] // 2, size[1] // 2
    hw, hh = int(0.6 * size[1] / 2 * 0.5), int(0.6 * size[1] / 2 * 0.25)
    left, right = img[cy, cx - hw // 2], img[cy, cx + hw // 2]
    check(left[0] > 200 and left[2] < 60, "rendu: moitie opaque du logo anime = rouge", str(left))
    check(right[2] > 240 and right[0] < 20, "rendu: moitie transparente laisse voir le fond", str(right))
    # le changement de source libere la video (plus de ffmpeg residuel)
    proc = r.logo_video.proc
    params["logo_source"] = "image"
    params["logo_path"] = ""
    gl.apply_logo_request(s, r)
    check(r.logo_video is None and proc.poll() is not None, "changement de source: video arretee")
    # deux signatures: le detourage regenere, la position non
    base = dict(gl.DEFAULT_PARAMS, logo_source="video", logo_video="a.webm")
    check(gl.logo_signature(base) == gl.logo_signature(dict(base, logo_x=0.2, logo_scale=0.9, logo_opacity=0.3))
          and gl.logo_signature(base) != gl.logo_signature(dict(base, logo_key="#00ff00"))
          and gl.logo_signature(base) != gl.logo_signature(dict(base, logo_video="b.webm")),
          "signature video: fichier et detourage la regenerent, pas la position")
    shutil.rmtree(tmp, ignore_errors=True)


def check_holo() -> None:
    print("Halo holographique")
    d = gl.DEFAULT_PARAMS
    check(HOLO_DEFAULT_ON == 1.0 and d["holo_react"] == 0.0 and (d["holo_intensity"], d["holo_reach"], d["holo_warp"],
                                                                   d["holo_dust"], d["holo_speed"]) == (1.0, 1.3, 1.0, 1.0, 0.15),
          "par defaut: actif, NON audioreactif")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return

    def make(size):
        fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
        r = gl.Renderer(ctx, size, None)
        yy, xx = np.mgrid[0:size[1], 0:size[0]]
        cell = max(size[1] // 10, 4)
        chk = ((xx // cell + yy // cell) % 2).astype(np.uint8)
        bg = np.stack([20 + 60 * chk, 15 + 30 * chk, 50 + 90 * chk], axis=2).astype(np.uint8)   # damier: la deformation se voit
        r.video_tex.write(np.ascontiguousarray(bg).tobytes())
        r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
        return r, fbo

    size = (640, 360)
    r, fbo = make(size)
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    loud = {"bass": 1.0, "mid": 0.5, "high": 0.5, "rms": 0.8, "beat": 1.0, "since_beat": 0.0, "beats": 4}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.25, logo_opacity=1.0, logo_glow=0.0,
                logo_pulse=0.0, logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1])

    def render(state=quiet, frames=1, rr=None, ff=None, sz=size, **kw):
        rr, ff = rr or r, ff or fbo
        for i in range(frames):
            rr.draw(ff, sz, state, dict(base, **kw), 0.05 * (i + 1))
        return read_target(ctx, ff, sz).astype(int)

    off = render(holo_on=0.0)
    on = render(holo_on=1.0, frames=30)
    white = (off[:, :, 0] > 240) & (off[:, :, 1] > 240) & (off[:, :, 2] > 240)
    ys, xs = np.nonzero(white)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    inside = np.zeros(size[::-1], bool)
    inside[y0:y1 + 1, x0:x1 + 1] = True
    diff = np.abs(on - off).max(axis=2)
    check(np.array_equal(on[inside], off[inside]), "le logo reste INTACT (aucun pixel du logo ne change)")
    check((diff[~inside] > 8).sum() > 6000, "le fond est modifie autour du logo", f"{(diff[~inside] > 8).sum()} px")
    # portee: bien au-dela du logo (au moins une fois sa largeur de chaque cote)
    lw = x1 - x0 + 1
    far = np.zeros(size[::-1], bool)
    far[:, :max(x0 - lw, 0)] = True
    far[:, x1 + lw + 1:] = True
    far |= np.zeros(size[::-1], bool)
    check((diff[far] > 8).sum() > 500, "portee : des pixels modifies a plus d'une largeur de logo de celui-ci",
          f"{(diff[far] > 8).sum()} px (logo {lw} px de large)")
    check(np.abs(diff[:, :4]).max() <= 40 and diff[:2, :].mean() < 8, "le halo s'eteint loin du logo (bords de l'image calmes)")

    # deformation seule / lumiere seule / poussiere seule
    warp_only = render(holo_on=1.0, frames=30, holo_intensity=0.0, holo_dust=0.0)
    light_only = render(holo_on=1.0, frames=30, holo_warp=0.0, holo_dust=0.0)
    dust_only = render(holo_on=1.0, frames=30, holo_warp=0.0, holo_intensity=0.0)
    check((np.abs(warp_only - off).max(axis=2)[~inside] > 8).sum() > 2000 and np.array_equal(warp_only[inside], off[inside]),
          "deformation seule : le fond (damier) est deforme, sans lumiere ajoutee en plus")
    lum = lambda a: a.sum(axis=2)
    check((lum(light_only) - lum(off))[~inside].min() >= 0 and (lum(light_only) - lum(off)).max() > 150,
          "lumiere seule : ajoute de la lumiere (jamais d'ombre), sans deformer")
    check((np.abs(dust_only - off).max(axis=2)[~inside] > 30).sum() > 100, "poussiere seule : des etoiles apparaissent")
    none = render(holo_on=1.0, frames=30, holo_intensity=0.0, holo_dust=0.0, holo_warp=0.0)
    check(np.array_equal(none, off), "intensite, deformation et poussiere a 0 = identique a coupe")
    g0 = render(holo_on=1.0, frames=30, holo_dust=0.0, holo_warp=0.0, holo_intensity=1.0)
    check((np.abs(g0 - off).max(axis=2) > 8).sum() > 3000, "la lumiere irisee est colorée autour du logo")

    # portee reglable
    def reach_area(reach):
        img = render(holo_on=1.0, frames=30, holo_reach=reach, holo_dust=0.0, holo_warp=0.0)
        return int((np.abs(img - off).max(axis=2) > 8).sum())

    small, big = reach_area(0.4), reach_area(2.0)
    check(big > small * 1.5, "holo_reach : plus grand = halo plus etendu", f"{small} -> {big} px")

    # non audioreactif par defaut (kick / basses), audioreactif si on le demande
    r._holo_t = 0.0
    calm = render(quiet, frames=20, holo_on=1.0)
    r._holo_t = 0.0
    shout = render(loud, frames=20, holo_on=1.0)
    check(np.array_equal(calm, shout), "holo_react = 0 : insensible a l'audio (kick, basses)")
    r._holo_t = 0.0
    shout2 = render(loud, frames=20, holo_on=1.0, holo_react=1.0)
    check(np.abs(shout2 - calm).max() > 30, "holo_react > 0 : reagit au kick et aux basses")
    # anime par le temps seulement
    r._holo_t = 0.0
    t0 = render(quiet, frames=1, holo_on=1.0)
    later = render(quiet, frames=60, holo_on=1.0)
    check((np.abs(later - t0).max(axis=2)[~inside] > 20).sum() > 500, "l'animation avance avec le temps")
    r._holo_t = 0.0
    a1 = render(quiet, frames=20, holo_on=1.0, holo_speed=0.15)
    r._holo_t = 0.0
    a2 = render(quiet, frames=20, holo_on=1.0, holo_speed=0.6)
    check(np.abs(a2 - a1).max() > 30, "la vitesse change l'animation")

    # activer / couper : la couche logo ne doit pas rester en filtre a mipmaps sans mipmaps (texture noire)
    white_count = int(white.sum())
    back = render(holo_on=0.0)
    w2 = (back[:, :, 0] > 240) & (back[:, :, 1] > 240) & (back[:, :, 2] > 240)
    check(int(w2.sum()) == white_count and np.array_equal(back, off), "couper le halo apres l'avoir allume : image identique, logo intact")

    # independant de la resolution : meme proportion de l'image touchee a 320x180 et 640x360
    r2, fbo2 = make((320, 180))
    o2 = render(holo_on=0.0, rr=r2, ff=fbo2, sz=(320, 180))
    n2 = render(holo_on=1.0, frames=30, rr=r2, ff=fbo2, sz=(320, 180), holo_dust=0.0)
    frac_small = (np.abs(n2 - o2).max(axis=2) > 8).mean()
    n1 = render(holo_on=1.0, frames=30, holo_dust=0.0)
    frac_big = (np.abs(n1 - off).max(axis=2) > 8).mean()
    check(abs(frac_small - frac_big) < 0.12, "la portee ne depend pas de la resolution de l'ecran",
          f"{frac_small:.2f} (320x180) contre {frac_big:.2f} (640x360)")

    # la portee suit la position et la taille du logo
    moved = render(holo_on=1.0, frames=30, logo_x=0.2, logo_y=0.3, holo_dust=0.0, holo_warp=0.0)
    mo = render(holo_on=0.0, logo_x=0.2, logo_y=0.3)
    md = np.abs(moved - mo).max(axis=2) > 8
    ysm, xsm = np.nonzero(md)
    check(abs(xsm.mean() - 0.2 * size[0]) < 0.12 * size[0] and abs(ysm.mean() - 0.3 * size[1]) < 0.15 * size[1],
          "le halo suit le logo (position)", f"centre du halo ({xsm.mean():.0f}, {ysm.mean():.0f})")

    # sans logo : aucun effet ; logo texte : fonctionne aussi
    r.set_logo(None, "")
    check(np.array_equal(render(holo_on=1.0, frames=5), render(holo_on=0.0)), "sans logo: aucun halo")
    from types import SimpleNamespace as NS
    s = NS(params=dict(base, logo_source="text", text_content="HELLO", text_scale=0.2, holo_on=1.0), status={})
    r.logo_requested = None
    gl.apply_logo_request(s, r)
    t_on = render(holo_on=1.0, frames=30, logo_source="text", text_content="HELLO", text_scale=0.2)
    t_off = render(holo_on=0.0, logo_source="text", text_content="HELLO", text_scale=0.2)
    check((np.abs(t_on - t_off).max(axis=2) > 8).sum() > 4000, "logo texte: halo aussi")
    r.set_logo(None, "")

    # logo video
    import shutil
    if shutil.which("ffmpeg"):
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix="casual_overlay_holo_"))
        src = tmp / "sq.mp4"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=0x00ff00:s=64x64:r=20:d=1", "-vf", "drawbox=x=16:y=16:w=32:h=32:color=white:t=fill",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], capture_output=True, timeout=60)
        if src.is_file():
            params = dict(base, logo_source="video", logo_video=str(src), logo_key="#00ff00", holo_on=1.0)
            s = NS(params=params, status={})
            r.logo_requested = None
            gl.apply_logo_request(s, r)
            wait_until(lambda: r.logo_video.reader.frames_read > 0)
            for i in range(30):
                r.draw(fbo, size, quiet, params, 0.05 * (i + 1))
            img = read_target(ctx, fbo, size).astype(int)
            r.draw(fbo, size, quiet, dict(params, holo_on=0.0), 2.0)
            ref = read_target(ctx, fbo, size).astype(int)
            check((np.abs(img - ref).max(axis=2) > 8).sum() > 3000, "logo video detoure: halo autour de la forme decoupee")
            r.set_logo(None, "")
        shutil.rmtree(tmp, ignore_errors=True)


def check_melt() -> None:
    print("Fonte acide du logo")
    d = gl.DEFAULT_PARAMS
    check(d["melt_on"] == 0.0 and d["melt_react"] == 0.0, "par defaut: coupee et NON audioreactive")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    chk = ((xx // 36 + yy // 36) % 2).astype(np.uint8)
    r.video_tex.write(np.ascontiguousarray(np.stack([20 + 40 * chk, 15 + 20 * chk, 60 + 60 * chk], axis=2).astype(np.uint8)).tobytes())
    r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    loud = {"bass": 1.0, "mid": 0.5, "high": 0.5, "rms": 0.8, "beat": 1.0, "since_beat": 0.0, "beats": 4}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.3, logo_opacity=1.0, logo_glow=0.0,
                logo_pulse=0.0, logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1], holo_on=0.0)

    def render(phase=0.0, state=quiet, **kw):
        pr = dict(base, **kw)
        r.draw(fbo, size, state, pr, 1.0)
        r._melt_t, r._melt_clock = phase, 0.0
        r.draw(fbo, size, state, pr, 1.0)                  # meme instant: dt = 0, la phase posee est celle rendue
        return read_target(ctx, fbo, size).astype(int)

    off = render(melt_on=0.0)
    white = (off[:, :, 0] > 240) & (off[:, :, 1] > 240) & (off[:, :, 2] > 240)
    ys, xs = np.nonzero(white)
    box = np.zeros(size[::-1], bool)
    box[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = True
    n_white = int(white.sum())
    check(np.array_equal(render(0.5, melt_on=0.0), off), "coupee: aucun effet, meme au plus fort du cycle")
    check(np.array_equal(render(0.0, melt_on=1.0), off) and np.array_equal(render(1.0, melt_on=1.0), off),
          "debut et fin de cycle: logo intact (identique a coupe)")
    check(np.array_equal(render(0.5, melt_on=1.0, melt_depth=0.0), off), "profondeur 0 = intact")
    nologo = render(0.0, melt_on=0.0, logo_opacity=0.0)
    cover = lambda img: int((np.abs(img - nologo).max(axis=2) > 25).sum())     # pixels ou le logo (ou l'acide) est visible
    deep = render(0.5, melt_on=1.0)
    check(cover(deep) < 0.4 * n_white, "au plus fort du cycle, la majeure partie du logo a disparu",
          f"{n_white} -> {cover(deep)} px")
    mid = render(0.3, melt_on=1.0)
    check(0.1 * n_white < cover(mid) < 0.9 * n_white, "a mi-parcours, le logo est partiellement dissous",
          f"{cover(mid)}/{n_white} px")
    acid = (mid[:, :, 1] > 100) & (mid[:, :, 2] < 0.6 * mid[:, :, 1])
    check(acid.sum() > 200, "lisiere acide (vert-jaune) autour des trous", f"{int(acid.sum())} px")
    check(np.array_equal(mid[~box], off[~box]), "la fonte ne deborde jamais du rectangle du logo (le fond n'est pas touche)")
    check(np.array_equal(render(0.3, melt_on=1.0), render(0.7, melt_on=1.0)),
          "le logo se reforme comme il a fondu (cycle symetrique)")
    check(np.array_equal(render(0.3, quiet, melt_on=1.0), render(0.3, loud, melt_on=1.0)), "melt_react = 0: insensible a l'audio")
    check(abs(render(0.3, melt_on=1.0, melt_scale=3.0) - mid).max() > 60, "le grain change la forme des trous")
    check(abs(render(0.3, melt_on=1.0, melt_color="#ff2030") - mid).max() > 60, "la couleur de l'acide est reglable")

    def after_frames(state, **kw):
        r._melt_t, r._melt_clock = 0.0, 0.0
        pr = dict(base, melt_on=1.0, melt_period=4.0, **kw)
        for i in range(40):
            r.draw(fbo, size, state, pr, 2.0 + 0.05 * i)
        return read_target(ctx, fbo, size).astype(int)

    check(abs(after_frames(loud, melt_react=1.0) - after_frames(quiet, melt_react=1.0)).max() > 60,
          "melt_react > 0: le cycle s'accelere avec les basses")


def check_gates() -> None:
    print("Reglages incompatibles grises (modes d'audio2wave)")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as exc:
        print(f"  (ignore: pas d'affichage tkinter disponible: {exc})")
        return
    import threading

    import gl_gui
    import gui_colors
    import gui_gates as gg
    import py_modes

    live_dir = gl.DEFAULT_A2W_DIR
    gl.load_live(live_dir)
    ev = threading.Event()

    def states(host, label):
        return {str(x.cget("state")) for w in gg.row_of_label(host, label) for x in gg.leaves(w)
                if x.winfo_class() in ("Scale", "Menubutton", "Entry", "Checkbutton")}

    built: dict = {}

    def build(name):
        mod = py_modes.load_mode(live_dir, name)
        a = py_modes.make_args(mod, name, "x", (1280, 720))
        host = gl_gui.HostFrame(root)
        host.pack()
        if name == "snap":
            mod.build_gui(a, (1280, 720), {}, {"capture": None}, ev, ev, root=host, on_switch_mode=lambda m: None)
        else:
            mod.build_gui(a, (1280, 720), {}, ev, ev, root=host, on_switch_mode=lambda m: None)
        gl_gui.tidy_mode_gui(host, name)
        g = gg.install_mode_gates(host, name, "#888888")
        root.update()
        built[name] = (a, gui_colors.add_color_helpers(host, "#5fd4c8", "#0b1018"))
        root.update()
        return host, g

    host, _ = build("snap")
    off, on = {"disabled"}, {"normal"}
    pencil = {lb: states(host, lb) for lb in ("Epaisseur du trait", "Echelle", "Filtre colonne", "Crossover Hz", "Video interieure",
                                              "Rayon du halo", "Points / colonnes", "Gain manuel")}
    check(pencil["Epaisseur du trait"] == on and pencil["Video interieure"] == on and pencil["Points / colonnes"] == on
          and pencil["Echelle"] == off and pencil["Filtre colonne"] == off and pencil["Crossover Hz"] == off,
          "Snap, style pencil: trait et videos actifs, echelle / filtre / crossover (rekordbox, simple) grises", str(pencil))
    check(pencil["Rayon du halo"] == off and pencil["Gain manuel"] == off,
          "Snap: rayon du halo grise tant que le halo n'est pas coche, gain manuel grise en gain automatique")
    gg.find(host, "Checkbutton", "Halo sur les kicks").invoke()
    gg.find(host, "Checkbutton", "Gain automatique").invoke()
    root.update()
    check(states(host, "Rayon du halo") == on and states(host, "Gain manuel") == on and
          str(gg.find(host, "Checkbutton", "Temps reel").cget("state")) == "normal",
          "Snap: cocher le halo active son rayon et 'Temps reel' ; decocher le gain automatique active le gain manuel")
    gg.find(host, "Radiobutton", "rekordbox").invoke()
    root.update()
    rek = {lb: states(host, lb) for lb in ("Epaisseur du trait", "Echelle", "Crossover Hz", "Video interieure", "Rayon du halo")}
    rek["Sinusoide"] = {str(gg.find(host, "Checkbutton", "Sinusoide").cget("state"))}
    check(rek["Epaisseur du trait"] == off and rek["Video interieure"] == off and rek["Sinusoide"] == off and rek["Rayon du halo"] == off
          and rek["Echelle"] == on and rek["Crossover Hz"] == on, "Snap, style rekordbox: options du crayon grisees, echelle et crossover actifs",
          str(rek))
    gg.find(host, "Radiobutton", "simple").invoke()
    root.update()
    check(states(host, "Crossover Hz") == off and states(host, "Echelle") == on, "Snap, style simple: crossover grise (rekordbox seul), echelle active")
    gg.find(host, "Radiobutton", "rekordbox").invoke()
    root.update()
    cross = [x for w in gg.row_of_label(host, "Crossover Hz") for x in gg.leaves(w) if x.winfo_class() == "Entry"]
    size_entries = [x for w in gg.row_of_label(host, "Taille du rendu") for x in gg.leaves(w) if x.winfo_class() == "Entry"]
    check(cross and all(str(x.cget("state")) == "normal" for x in cross) and size_entries
          and all(str(x.cget("state")) == "readonly" for x in size_entries),
          "Snap: les champs du crossover restent modifiables, ceux de la taille du rendu sont figes",
          f"{[str(x.cget('state')) for x in cross]} {[str(x.cget('state')) for x in size_entries]}")
    # Aides a la saisie des couleurs (pastilles + selecteur + menu de noms), sur les champs d'audio2wave
    sargs, sfields = built["snap"]
    f = sfields.get("Couleurs")
    check(f is not None and "Couleur de fond" in sfields, "Snap: pastilles de couleur sur 'Couleurs' et 'Couleur de fond'")
    if f is not None:
        f.set_segment(0, "red")
        check(f.swatch_colors() == ["#ff0000"] and sargs.colors == "red",
              "Snap: choisir une couleur remplit le champ ET l'applique (Entree simulee)", f"{f.swatch_colors()} {sargs.colors}")
        f.add_segment()
        f.set_segment(1, "0x00ff00")
        f.set_segment(2, "#0000ff")
        check(f.swatch_colors() == ["#ff0000", "#00ff00", "#0000ff"] and sargs.colors == "red|0x00ff00|#0000ff",
              "Snap: trois couleurs separees par | (rekordbox), une pastille chacune", f"{f.swatch_colors()} {sargs.colors}")
        f.write(["inconnue"])
        check(f.swatch_colors() == ["#555555"], "couleur illisible: pastille grise '?', pas d'erreur")

    host.destroy()

    host, _ = build("ridge")
    rargs, rfields = built["ridge"]
    check("Couleur du trait" in rfields and "Couleur de fond" in rfields, "Ridge: pastilles sur 'Couleur du trait' et 'Couleur de fond'")
    rfields["Couleur du trait"].set_segment(0, "teal")
    check(rargs.colors == "teal" and rfields["Couleur du trait"].swatch_colors() == ["#008080"], "Ridge: couleur appliquee",
          f"{rargs.colors}")
    ridge = states(host, "Gain manuel (dB)")
    gg.find(host, "Checkbutton", "Gain automatique").invoke()
    root.update()
    check(ridge == off and states(host, "Gain manuel (dB)") == on, "Ridge: gain manuel grise tant que le gain est automatique")
    # Leur refresh() (toutes les 200 ms) ferme leur fenetre et cesse de se reprogrammer des que finished_event est positionne :
    # on le laisse tourner avant de detruire, sinon un job programme viserait un widget detruit (bruit Tcl) ou, sans destroy,
    # la boucle d'evenements d'un test suivant ne se terminerait jamais.
    ev.set()
    for _ in range(8):
        root.update()
        time.sleep(0.05)
    root.destroy()


def check_cells() -> None:
    print("Cellules organiques du logo")
    d = gl.DEFAULT_PARAMS
    check(d["cell_on"] == 0.0 and d["cell_react"] == 0.0, "par defaut: coupees et NON audioreactives")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    chk = ((xx // 36 + yy // 36) % 2).astype(np.uint8)
    r.video_tex.write(np.ascontiguousarray(np.stack([20 + 40 * chk, 15 + 20 * chk, 60 + 60 * chk], axis=2).astype(np.uint8)).tobytes())
    # logo test: carre plein, moitie gauche rouge, moitie droite bleue (les couleurs doivent rester lisibles)
    logo = np.zeros((64, 64, 4), np.uint8)
    logo[:, :32] = (255, 0, 0, 255)
    logo[:, 32:] = (0, 0, 255, 255)
    r.set_logo(logo, "x")
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    loud = {"bass": 1.0, "mid": 0.5, "high": 0.5, "rms": 0.8, "beat": 1.0, "since_beat": 0.0, "beats": 4}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.4, logo_opacity=1.0, logo_glow=0.0,
                logo_pulse=0.0, logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1], holo_on=0.0)

    def render(t_cells=0.0, state=quiet, **kw):
        pr = dict(base, **kw)
        r.draw(fbo, size, state, pr, 1.0)
        r._cell_t = t_cells
        r.draw(fbo, size, state, pr, 1.0)
        return read_target(ctx, fbo, size).astype(int)

    off = render(cell_on=0.0)
    nologo = render(cell_on=0.0, logo_opacity=0.0)
    cover = lambda img: np.abs(img - nologo).max(axis=2) > 25
    shape = cover(off)
    ys, xs = np.nonzero(shape)
    box = np.zeros(size[::-1], bool)
    box[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = True
    check(np.array_equal(render(1.0, cell_on=0.0, cell_amount=1.0), off), "coupees: aucun effet")
    check(np.array_equal(render(1.0, cell_on=1.0, cell_amount=0.0), off), "intensite 0 = logo d'origine")
    on = render(1.0, cell_on=1.0)
    check(int((np.abs(on - off).max(axis=2) > 25).sum()) > 3000 and cover(on).sum() < cover(off).sum(),
          "le logo est decompose en cellules (des ecarts apparaissent entre elles)",
          f"{int((np.abs(on - off).max(axis=2) > 25).sum())} px changes")
    check(np.array_equal(on[~box], off[~box]), "rien ne deborde du rectangle du logo (le fond n'est pas touche)")
    left, right = on[shape & (xx < (xs.min() + xs.max()) // 2 - 10)], on[shape & (xx > (xs.min() + xs.max()) // 2 + 10)]
    check(left[:, 0].mean() > left[:, 2].mean() + 30 and right[:, 2].mean() > right[:, 0].mean() + 30,
          "les cellules gardent les couleurs du logo (rouge a gauche, bleu a droite)")
    green = render(1.0, cell_on=1.0, cell_ink="#00ff00")
    greener = int(((green[:, :, 1] - on[:, :, 1]) > 40).sum())          # le contour (seul a changer) vire au vert
    check(greener > 30 and int((np.abs(green - on)[:, :, [0, 2]].max(axis=2) > 120).sum()) < greener * 3,
          "le contour des cellules prend la couleur choisie", f"{greener} px plus verts")
    check(np.abs(render(2.5, cell_on=1.0) - on).max() > 60, "les cellules derivent avec le temps")
    small = cover(render(1.0, cell_on=1.0, cell_fusion=0.25)).sum()
    big = cover(render(1.0, cell_on=1.0, cell_fusion=1.8)).sum()
    check(big > small * 1.3, "fusion: petit = bulles isolees (moins de matiere), grand = masse fusionnee", f"{small} -> {big} px")
    fine = render(1.0, cell_on=1.0, cell_scale=2.5)
    check(np.abs(fine - on).max() > 60, "la taille des cellules change leur nombre")
    check(np.array_equal(render(1.0, quiet, cell_on=1.0), render(1.0, loud, cell_on=1.0)), "cell_react = 0: insensible a l'audio")
    # le logo n'occupe que la moitie gauche de son rectangle : les cellules ne doivent apparaitre QUE dessus
    half = np.zeros((64, 64, 4), np.uint8)
    half[:, :32] = (255, 255, 255, 255)
    r.set_logo(half, "x")
    cx = (xs.min() + xs.max()) // 2
    h_on = render(1.0, cell_on=1.0, cell_amount=1.0, cell_fusion=1.2)
    h_nologo = render(1.0, cell_on=0.0, logo_opacity=0.0)
    empty_side = (np.abs(h_on - h_nologo).max(axis=2) > 25) & box & (xx > cx + 8)
    full_side = (np.abs(h_on - h_nologo).max(axis=2) > 25) & box & (xx < cx - 8)
    check(int(empty_side.sum()) == 0 and int(full_side.sum()) > 2000,
          "les cellules reprennent la forme du logo, pas la zone qu'il occupe",
          f"{int(empty_side.sum())} px hors logo, {int(full_side.sum())} px sur le logo")
    r.set_logo(logo, "x")
    check(np.abs(render(1.0, loud, cell_on=1.0, cell_react=1.0) - render(1.0, quiet, cell_on=1.0, cell_react=1.0)).max() > 40,
          "cell_react > 0: la fusion pulse au kick")


def check_layers() -> None:
    print("Effets decorreles fond / logo")
    p = dict(gl.DEFAULT_PARAMS)
    bg, logo = gl.layer_effects(dict(p, fx_on=[1, 0, 1, 1, 1], fx_int=[0.5, 1, 1, 1, 1], master=2.0,
                                     fxl_on=[0, 1, 1, 1], fxl_int=[1, 0.25, 1, 1]))
    check(bg[:2] == [1.0, 0.0] and logo == bg, "lies: le logo reprend les effets du fond (interrupteur x intensite x global)")
    bg, logo = gl.layer_effects(dict(p, fx_link=0.0, fx_on=[1, 0, 1, 1, 1], fx_int=[0.5, 1, 1, 1, 1], master=2.0,
                                     fxl_on=[0, 1, 1, 1], fxl_int=[1, 0.25, 1, 1]))
    check(bg[0] == 1.0 and logo[0] == 0.0 and logo[1] == 0.5, "separes: chaque couche a ses propres effets")
    q = dict(p, fx_on=[1, 0, 1, 0, 1], fx_int=[0.3, 0.6, 0.9, 1.2, 1.0], fxl_on=[1, 1, 1, 1], fxl_int=[1.0] * 4)
    gl.set_fx_link(q, False)
    check(q["fx_link"] == 0.0 and q["fxl_on"] == [1, 0, 1, 0] and q["fxl_int"] == [0.3, 0.6, 0.9, 1.2],
          "delier copie les reglages du fond (rien ne saute)")
    q["fxl_int"][0] = 0.7
    gl.set_fx_link(q, False)
    check(q["fxl_int"][0] == 0.7, "delier deux fois ne recopie pas (reglages du logo conserves)")
    gl.set_fx_link(q, True)
    check(q["fx_link"] == 1.0, "lier")
    gl.set_param(q, "fxl_int2", 0.4)
    check(gl.get_param(q, "fxl_int2") == 0.4 and q["fxl_int"][2] == 0.4 and gl.get_param(q, "fx_int2") == 0.9,
          "get/set_param: fxl_intN et fx_intN sont distincts")

    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    grad = np.zeros((size[1], size[0], 3), np.uint8)                 # fond: degrades, pour que l'ondulation se voie
    grad[:, :, 0] = (np.arange(size[0]) * 255 // size[0])[None, :] // 2
    grad[:, :, 1] = (np.arange(size[1]) * 255 // size[1])[:, None]
    grad[:, :, 2] = 80 + (np.arange(size[0]) // 40 % 2)[None, :] * 100
    r.video_tex.write(np.ascontiguousarray(grad).tobytes())
    square = np.full((64, 64, 4), 255, np.uint8)                     # logo: carre blanc opaque
    r.set_logo(square, "x")
    state = {"bass": 1.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.3, logo_opacity=1.0, logo_glow=0.0,
                logo_pulse=0.0, logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1])

    def render(**kw):
        r.draw(fbo, size, state, dict(base, **kw), 1.0)
        return read_target(ctx, fbo, size).astype(int)

    a = render()
    mask_a = (a[:, :, 0] > 240) & (a[:, :, 1] > 240) & (a[:, :, 2] > 240)
    ys, xs = np.nonzero(mask_a)
    check(mask_a.sum() > 5000, "logo blanc visible au repos", f"{mask_a.sum()} px")
    outside = np.ones(size[::-1], bool)
    outside[max(ys.min() - 25, 0):ys.max() + 26, max(xs.min() - 25, 0):xs.max() + 26] = False

    def diff(img, region=None):
        d = np.abs(img - a).max(axis=2)
        return int((d[region] > 4).sum()) if region is not None else int((d > 4).sum())

    # logo seul deforme (fond sans effet): le fond hors du logo est strictement intact
    sep_logo = render(fx_link=0.0, fx_on=[0, 0, 0, 0, 1], fxl_on=[1, 0, 0, 0], fxl_int=[2.0, 1, 1, 1])
    mask = (sep_logo[:, :, 0] > 240) & (sep_logo[:, :, 1] > 240) & (sep_logo[:, :, 2] > 240)
    check(diff(sep_logo, outside) == 0, "wobble sur le logo seul: le fond (hors logo) n'a pas bouge")
    check((mask != mask_a).sum() > 100, "wobble sur le logo seul: le contour du logo ondule",
          f"{(mask != mask_a).sum()} px de contour changes")
    # fond seul deforme: le logo garde sa forme exacte
    sep_bg = render(fx_link=0.0, fx_on=[1, 0, 0, 0, 1], fx_int=[2.0, 1, 1, 1, 1], fxl_on=[0, 0, 0, 0])
    mask = (sep_bg[:, :, 0] > 240) & (sep_bg[:, :, 1] > 240) & (sep_bg[:, :, 2] > 240)
    check(diff(sep_bg, outside) > 2000, "wobble sur le fond seul: le fond ondule", f"{diff(sep_bg, outside)} px")
    check((mask != mask_a).sum() == 0, "wobble sur le fond seul: le logo garde exactement sa forme")
    # lies: le meme wobble deforme les deux
    linked = render(fx_link=1.0, fx_on=[1, 0, 0, 0, 1], fx_int=[2.0, 1, 1, 1, 1])
    mask = (linked[:, :, 0] > 240) & (linked[:, :, 1] > 240) & (linked[:, :, 2] > 240)
    check(diff(linked, outside) > 2000 and (mask != mask_a).sum() > 100,
          "lies: le wobble deforme le fond ET le logo (comportement d'avant)")
    # glitch: couche logo seule, avec son propre tirage de bandes
    sep_glitch = render(fx_link=0.0, fx_on=[0, 0, 0, 0, 1], fxl_on=[0, 0, 0, 1], fxl_int=[1, 1, 1, 2.0])
    state_beat = dict(state, beat=1.0)
    r.draw(fbo, size, state_beat, dict(base, fx_link=0.0, fx_on=[0, 0, 0, 0, 1], fxl_on=[0, 0, 0, 1],
                                       fxl_int=[1, 1, 1, 2.0]), 1.0)
    g = read_target(ctx, fbo, size).astype(int)
    # le glitch decale les bandes horizontalement (jusqu'a ~100 px): on juge les lignes hors du logo
    rows = np.ones(size[::-1], bool)
    rows[max(ys.min() - 2, 0):ys.max() + 3, :] = False
    check(diff(g, rows) == 0 and diff(g) > 50, "glitch sur le logo seul: seules les lignes du logo bougent",
          f"{diff(g)} px changes dont {diff(g, rows)} sur les lignes du fond")
    # transparence: un logo deforme ne laisse ni frange noire ni trou (alpha premultiplie)
    edge = sep_logo[(mask_a & ~mask)]
    check(sep_logo.max() <= 255 and (edge.min() if edge.size else 255) > 0, "couche logo deformee: pas de pixel noir aux bords")
    # sans logo: la couche est vide, l'image est celle du fond
    r.set_logo(None, "")
    none = render(fx_link=0.0, fxl_on=[1, 1, 1, 1], fxl_int=[2.0] * 4)
    check(diff(none) > 0 and (none[outside] == a[outside]).all(), "sans logo: seule la couche fond est rendue")


def check_automation() -> None:
    print("Automations")
    import copy
    default = gl.default_automation()
    specs = gl.AUTOMATION_SPECS
    check(set(default) == set(specs) and all(
        len(e["points"]) == gl.AUTOMATION_POINTS and all(0.0 <= v <= 1.0 for v in e["points"])
        and e["period"] >= 3.0 for e in default.values()), "automations par defaut: courbes de 12 points, periodes >= 3 s")
    check(all(spec[1] < spec[2] for spec in specs.values())
          and all(key in gl.DEFAULT_PARAMS or key.startswith(("fx_int", "fxl_int")) for key in specs),
          "plages valides (min < max) et cles connues de params")
    enabled = [k for k, e in default.items() if e["enabled"]]
    check(len(enabled) >= 10 and not any(default[k]["enabled"] for k in ("logo_x", "logo_y", "logo_scale",
                                                                           "text_scale", "logo_opacity")),
          "par defaut: effets et fond animes, position/taille/opacite du logo laisses fixes", f"{len(enabled)} actives")
    periods = [default[k]["period"] for k in enabled]
    check(len(set(periods)) == len(periods), "periodes des automations actives toutes differentes (pas de repetition)")

    def fresh():
        p = copy.deepcopy(gl.DEFAULT_PARAMS)
        p["_automation"] = gl.default_automation()
        return p

    engine, p = gl.AutomationEngine(), fresh()
    lo, hi, period = specs["bg_hue"][1], specs["bg_hue"][2], specs["bg_hue"][4]
    engine.step(p, 100.0)
    check(abs(p["bg_hue"] - (lo + hi) / 2) < 1e-9, "debut de cycle: milieu de la plage (sinus)", f"{p['bg_hue']:.3f}")
    engine.step(p, 100.0 + period / 4)
    check(abs(p["bg_hue"] - hi) < 1e-9, "quart de periode: maximum de la plage", f"{p['bg_hue']:.3f}")
    engine.step(p, 100.0 + period)
    check(abs(p["bg_hue"] - (lo + hi) / 2) < 1e-9, "une periode plus tard: retour au point de depart")
    check(p["logo_x"] == 0.5 and p["logo_scale"] == gl.DEFAULT_PARAMS["logo_scale"],
          "reglages non automatises inchanges")
    check(specs["fx_int0"][1] <= p["fx_int"][0] <= specs["fx_int0"][2], "fx_int0 pilote l'intensite du wobble (liste)")

    # toutes les automations: dans leur plage, sans saut brutal (simulation 10 min a 60 Hz)
    engine, p = gl.AutomationEngine(), fresh()
    for key in specs:
        p["_automation"][key]["enabled"] = True
    worst, prev, inside = 0.0, {}, True
    for i in range(36000):
        engine.step(p, 10.0 + i / 60.0)
        for key, spec in specs.items():
            v = gl.get_param(p, key)
            inside &= spec[1] - 1e-9 <= v <= spec[2] + 1e-9
            if key in prev:
                worst = max(worst, abs(v - prev[key]) / (spec[2] - spec[1]))
            prev[key] = v
    check(inside, "10 min de simulation: toutes les valeurs restent dans leur plage")
    check(worst < 0.03, "pas de saut brutal entre deux images (courbes interpolees)", f"pire saut {worst * 100:.2f} % de la plage")

    # interrupteur general: fige, et chaque courbe repart de son debut a la reprise
    engine, p = gl.AutomationEngine(), fresh()
    engine.step(p, 0.0)
    engine.step(p, 5.0)
    frozen = p["bg_hue"]
    p["auto_master"] = 0.0
    engine.step(p, 50.0)
    check(p["bg_hue"] == frozen, "auto_master = 0: valeurs figees")
    p["auto_master"] = 1.0
    engine.step(p, 300.0)
    check(abs(p["bg_hue"] - (lo + hi) / 2) < 1e-9, "reprise: la courbe repart de son debut")
    p["_automation"]["bg_hue"]["enabled"] = False
    engine.step(p, 310.0)
    value_off = p["bg_hue"]
    engine.step(p, 320.0)
    check(p["bg_hue"] == value_off, "automation coupee: la valeur n'est plus touchee")

    # fusion et persistance
    merged = gl.merge_automation({"bg_hue": {"points": [1, 2], "period": -3, "enabled": True},
                                  "inconnue": {"points": [0.0] * 12}, "logo_x": {"enabled": True}})
    check(merged["bg_hue"]["points"] == default["bg_hue"]["points"] and merged["bg_hue"]["period"] == specs["bg_hue"][4]
          and "inconnue" not in merged and merged["logo_x"]["enabled"] is True and set(merged) == set(specs),
          "merge_automation: entrees invalides remplacees, cles inconnues ignorees, manquantes completees")
    original = gl.PARAMS_PATH
    gl.PARAMS_PATH = Path(tempfile.mkdtemp()) / "gl_params.json"
    try:
        p = gl.load_params()
        p["_automation"] = gl.merge_automation({"bg_tile": {"enabled": False, "period": 12.5,
                                                            "points": [0.1] * 12}})
        p["auto_master"] = 0.0
        gl.save_params(p)
        back = gl.load_params()
        check(back["_automation"]["bg_tile"] == {"enabled": False, "period": 12.5, "points": [0.1] * 12}
              and back["auto_master"] == 0.0, "automations et interrupteur general sauves puis recharges")
    finally:
        gl.PARAMS_PATH = original


def check_params() -> None:
    print("Reglages (touche P)")
    original = gl.PARAMS_PATH
    gl.PARAMS_PATH = Path(tempfile.mkdtemp()) / "sous" / "gl_params.json"
    try:
        check(gl.load_params() == gl.DEFAULT_PARAMS, "sans fichier: valeurs par defaut")
        params = gl.load_params()
        params["fx_on"][3] = 0
        params["master"] = 1.4
        params["sensitivity"] = 2.0
        gl.save_params(params)
        check(gl.load_params() == params, "sauvegarde puis rechargement identiques")
        gl.PARAMS_PATH.write_text("{ pas du json", encoding="utf-8")
        check(gl.load_params() == gl.DEFAULT_PARAMS, "fichier corrompu: retour aux valeurs par defaut")
    finally:
        gl.PARAMS_PATH = original


def main() -> None:
    check_reader()
    check_features()
    check_text()
    check_logo_video()
    check_holo()
    check_melt()
    check_cells()
    check_gates()
    check_layers()
    check_automation()
    check_params()
    check_manager()
    check_gui()
    check_render()
    print()
    if failures:
        print(f"{len(failures)} verification(s) en echec: {', '.join(failures)}")
        sys.exit(1)
    print("Toutes les verifications passent.")


if __name__ == "__main__":
    main()
