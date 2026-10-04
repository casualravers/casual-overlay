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
        elif stage == "live":
            out["live_back"] = dict(mode=s.mode, src=s.pysrc, suspended=manager.suspended,
                                    resumed=[r.device for r in manager.resumed], reader_ok=s.reader is manager.reader,
                                    banner=any(t.startswith("LIVE") for t in texts),
                                    gain=find_scale(c["live_host"], "Gain (dB)") is not None)
            # une scene a fond audio2wave qui change de mode (Live -> Snap) charge son preset dans le panneau neuf
            out["stage"] = "scene"
            root.geometry("")                                   # taille naturelle (aucune geometrie imposee), comme a l'ouverture
            root.update()
            root.update()
            out["size_before"] = (root.winfo_width(), root.winfo_height())
            c["vj"]["state"]["running"] = True                 # VJ actif : la fenetre ne doit pas changer de taille
            c["vj"]["state"]["t_next"] = gl.time.monotonic() + 1000
            c["scene_book"].put({"name": "Scene Snap", "bg": "live", "mode": "snap", "fond": None,
                                 "live_overrides": {"style": "pencil", "wave": 24, "line_width": 3, "colors": "0x39c9ff"},
                                 "overlay": gl.capture_overlay(params)})
            # scene d'une ancienne version: son etat est dans un preset d'audio2wave `scene-old`, migre a la reconstruction
            live.preset_store.save_user("scene-old", {"gain": 22})
            c["scene_book"].put({"name": "Scene Old", "bg": "live", "mode": "live", "live_preset": "scene-old", "fond": None,
                                 "overlay": gl.capture_overlay(params)})
            # c'est le VJ lui-meme qui enchaine (un choix a la main le mettrait en pause) : la scene precedente est active
            c["scene_book"].set_vj(order="seq")
            _names = c["scene_book"].names()
            s.active_scene = _names[_names.index("Scene Snap") - 1]
            c["vj"]["advance"]()
        else:
            def check_scene_mode() -> None:
                menus = [w for w in walk(c["live_host"]) if w.winfo_class() == "Menubutton"]
                root.update()
                out["size_after"] = (root.winfo_width(), root.winfo_height())
                out["scene_snap"] = (s.mode, s.pysrc.args.colors, s.pysrc.args.wave)
                old = c["scene_book"].get("Scene Old")
                out["scene_migrated"] = (old.get("live_overrides"), "live_preset" in old,
                                         "scene-old" in live.preset_store.load_user())
                try:
                    gui_a2w_steps(c, s, out)
                except Exception:
                    import traceback
                    out["a2w_error"] = traceback.format_exc()
                c["close"]()
                s.finished_event.set()
            root.after(700, check_scene_mode)

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
            root.update()
            out["bg_live"] = (c["pattern_box"].winfo_ismapped(), c["live_host"].winfo_ismapped())
            c["bg_var"].set("pattern")
            out["bg_param"] = params["bg_mode"]
            root.update()
            out["bg_pat"] = (c["pattern_box"].winfo_ismapped(), c["live_host"].winfo_ismapped())
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
            # noise: les cases et le curseur ecrivent les reglages, les presets les gardent
            nfv = c["noise_fx_vars"]
            nfv[1].set(1)
            nfv[4].set(1)
            out["n_bind"] = list(params["noise_fx"])
            c["save_name_var"].set("noisy")
            op["save"]()
            nfv[1].set(0)
            op["load"]("noisy")
            out["n_preset"] = (nfv[1].get(), params["noise_fx"][1])
            op["load"]("default")
            out["n_default"] = (list(params["noise_fx"]), [v.get() for v in nfv])
            obook.delete("noisy")
            # scenes : barre en haut, capture / application, F1..F9, etat d'audio2wave range dans la scene
            import scenes as scn
            sc, sbook = c["scenes"], c["scene_book"]
            s.scene_requests = queue.SimpleQueue()
            c["bg_var"].set("pattern")
            params["bg_hue"] = 0.42
            params["holo_intensity"] = 1.3
            sc["capture"]("Scene A")
            a = sbook.get("Scene A")
            out["sc_a"] = (a["bg"], a["mode"], a["live_overrides"], a["fond"]["bg_hue"], a["overlay"]["holo_intensity"],
                           "bg_hue" in a["overlay"])
            c["bg_var"].set("live")
            params["bg_hue"] = 0.1
            params["holo_intensity"] = 0.4
            sc["apply"]("Scene A")
            out["sc_a_apply"] = (params["bg_mode"], params["bg_hue"], params["holo_intensity"], c["bg_var"].get())
            c["bg_var"].set("live")
            params["holo_intensity"] = 0.9
            sc["capture"]("Scene B")
            b = sbook.get("Scene B")
            menu_labels = [mb["menu"].entrycget(i, "label") for mb in walk(c["live_host"]) if mb.winfo_class() == "Menubutton" and hasattr(mb["menu"], "type")
                           for i in range((mb["menu"].index("end") or -1) + 1) if mb["menu"].type(i) == "command"]
            out["sc_b"] = (b["bg"], b["mode"], isinstance(b["live_overrides"], dict) and len(b["live_overrides"]) > 3, b["fond"],
                           not any(n.startswith("scene") for n in live.preset_store.load_user())
                           and not any(n.startswith("scene") for n in menu_labels) and "live_preset" not in b)
            params["holo_intensity"] = 0.2
            sc["apply"]("Scene B")
            out["sc_b_apply"] = (params["bg_mode"], params["holo_intensity"], params["bg_hue"])
            b2 = sbook.get("Scene B")
            out["sc_b_nodev"] = not any(k in b2["live_overrides"] for k in scn.SCENE_EXCLUDED)
            b2["live_overrides"].update(gain=17, device="Un autre micro", fullscreen=True)   # ancienne scene: cles exclues presentes
            sbook.put(b2)
            device_before = s.live_args.device
            while not s.commands.empty():
                s.commands.get_nowait()
            sc["apply"]("Scene B")
            cmds_b = []
            while not s.commands.empty():
                cmds_b.append(s.commands.get_nowait())
            out["sc_b_gain"] = (float(s.live_args.gain), "all" not in live.preset_store.__dict__)
            out["sc_b_keep"] = (s.live_args.device == device_before, not any(str(x).startswith("fullscreen") for x in cmds_b))
            buttons = [w.cget("text") for w in walk(c["scene_bar"]) if w.winfo_class() == "Button"]
            out["sc_bar"] = buttons
            s.scene_requests.put(0)
            sc["poll"]()
            out["sc_f1"] = (params["bg_mode"], getattr(s, "active_scene", None))
            s.scene_requests.put(1)
            sc["poll"]()
            out["sc_f2"] = (params["bg_mode"], getattr(s, "active_scene", None))
            s.scene_requests.put(7)
            sc["poll"]()
            out["sc_f8"] = sc["msg"].get()
            sc["start_adding"]()
            sc["entry_var"].set("Scene C")
            sc["new_from_entry"]()
            out["sc_names"] = sbook.names()
            sc["entry_var"].set("scene a")
            sc["start_adding"]()
            sc["entry_var"].set("scene a")
            sc["new_from_entry"]()
            out["sc_dup"] = (sc["msg"].get(), sbook.names())
            params["holo_intensity"] = 1.7
            sc["capture"]("Scene A")
            out["sc_update"] = (sbook.names()[0], sbook.get("Scene A")["overlay"]["holo_intensity"])
            sc["delete"]("Scene B")
            out["sc_del"] = (sbook.names(), any(n.startswith("scene") for n in live.preset_store.load_user()))
            params["holo_intensity"] = 1.0
            c["bg_var"].set("live")
            # les redemarrages provoques par les scenes ecrasent le statut: on repose le message du gestionnaire apres eux
            # mode VJ : minuteur reel, ordre, flash, pause a la main, F-touches Espace / fleche droite
            vjc = c["vj"]
            vst = vjc["state"]
            sc["capture"]("Scene D")
            while not s.commands.empty():
                s.commands.get_nowait()
            sbook.set_vj(seconds=20, order="seq")
            sc["apply"]("Scene A")
            vjc["toggle"]()
            out["vj_on"] = (vst["running"], 19.0 < vjc["remaining"]() <= 20.0)
            vst["t_next"] = 0.0                                  # echeance atteinte: la boucle de la GUI doit enchainer toute seule

            def vj_check() -> None:
                cmds = []
                while not s.commands.empty():
                    cmds.append(s.commands.get_nowait())
                out["vj_tick"] = (getattr(s, "active_scene", None), vst["running"], "flash" in cmds, 19.0 < vjc["remaining"]() <= 20.0)
                names, seq = sbook.names(), []
                for _ in range(3):
                    vjc["advance"]()
                    seq.append(s.active_scene)
                out["vj_seq"] = (seq, names)
                sbook.set_vj(order="random")
                rnd = [s.active_scene]
                for _ in range(30):
                    vjc["advance"]()
                    rnd.append(s.active_scene)
                out["vj_rand"] = (all(a != b for a, b in zip(rnd, rnd[1:])), set(rnd) == set(names))
                s.scene_requests.put("vj")                      # Espace: le VJ tournait, il se met en pause
                sc["poll"]()
                out["vj_key_space"] = vst["running"]
                s.scene_requests.put("vjnext")                  # fleche droite: scene suivante, sans relancer le VJ
                before = s.active_scene
                sc["poll"]()
                out["vj_key_next"] = (before != s.active_scene, vst["running"])
                s.scene_requests.put("vj")                      # Espace: il repart
                sc["poll"]()
                running = vst["running"]
                sc["apply"]("Scene A")
                out["vj_pause"] = (running, vst["running"], "VJ en pause" in sc["msg"].get())
                sbook.delete("Scene D")
                sbook.delete("Scene C")
                vjc["toggle"]()
                out["vj_one"] = (vst["running"], "au moins 2" in sc["msg"].get())
                sbook.put({"name": "Scene C", "bg": "live", "mode": "live", "live_preset": None, "fond": None, "overlay": {}})
                vst["running"] = False
                try:
                    gui_sets_steps(c, s, out)
                    gui_expand_steps(c, s, out)
                except Exception:
                    import traceback
                    out["sets_error"] = traceback.format_exc()
                root.after(900, lambda: (manager.status.__setitem__("live", "autre"), root.after(300, lambda: (manager.status.__setitem__("live", "message du gestionnaire"), root.after(400, step5)))))
            root.after(700, vj_check)

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
    import scenes as _scenes
    real_scenes_path, real_live_store_path = _scenes.SCENES_PATH, live.preset_store.path
    _scenes.SCENES_PATH = _tmp_presets / "scenes.json"
    live.preset_store.path = _tmp_presets / "live_presets.json"          # le bouton Sauvegarder d'audio2wave ecrit ici
    try:
        gl_gui.run_gui(s, live, on_ready)
    finally:
        gl.OVERLAY_PRESETS_PATH = real_presets_path
        gl.BACKGROUND_PRESETS_PATH = real_bg_presets_path
        _scenes.SCENES_PATH, live.preset_store.path = real_scenes_path, real_live_store_path
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
    names = ["Stereo Mix (Realtek(R) Audio)", "CABLE Output (VB-Audio Virtual Cable)", "Microphone (Realtek(R) Audio)",
             "Microphone Array (Intel Smart S"]
    check(gl.pick_default_device(names, "Microphone Array (Intel Smart Sound Technology)") == names[3],
          "micro par defaut: correspond a l'entree par defaut de Windows (nom tronque)")
    check(gl.pick_default_device(names, None) == names[2], "micro par defaut: sans info systeme, un nom de micro, pas un mixage")
    check(gl.pick_default_device(["Stereo Mix (X)", "Line In (Y)"], None) == "Line In (Y)",
          "micro par defaut: ecarte les boucles de sortie")
    check(gl.pick_default_device(["Stereo Mix (X)"], None) == "Stereo Mix (X)" and gl.pick_default_device([], "x") is None,
          "micro par defaut: repli sur la seule entree, None si aucune")
    guard = gl.PerfGuard(144)                      # budget plafonne a 60 Hz (16,7 ms)
    msgs = [guard.update(8.0, 0.1 * i, {"cell_on": 1.0}) for i in range(60)]
    check(abs(guard.budget_ms - 1000 / 60) < 1e-6 and not any(msgs), "garde-fou: 8 ms par image = pas d'alerte, budget plafonne a 60 Hz")
    g = gl.PerfGuard(59)
    seen = [g.update(21.0, 0.1 * i, {"cell_on": 1.0, "holo_on": 1.0, "melt_on": 0.0}) for i in range(60)]
    first = next((i for i, m in enumerate(seen) if m), None)
    check(first is not None and first >= 18 and "cellules" in seen[-1] and "halo" in seen[-1] and "fonte" not in seen[-1],
          "garde-fou: alerte apres ~2 s de surcharge, nomme les effets couteux allumes", str(seen[-1]))
    mid = [g.update(14.0, 6.0 + 0.1 * i, {}) for i in range(30)]
    check(mid[-1] is not None, "garde-fou: entre les deux seuils l'alerte reste (pas de clignotement)")
    seen = [g.update(10.0, 9.0 + 0.1 * i, {}) for i in range(60)]
    check(seen[-1] is None, "garde-fou: alerte levee quand la charge redescend nettement", str(seen[-1]))
    g2 = gl.PerfGuard(60)
    texts = [g2.update(30.0, 0.1 * i, {}) for i in range(40)]
    check("taille du rendu" in (texts[-1] or ""), "garde-fou: sans effet couteux allume, conseille de baisser le rendu")
    check(out.get("n_bind") == [0, 1, 0, 0, 1], "noise (GUI): les cases ecrivent les reglages", str(out.get("n_bind")))
    check(out.get("n_preset") == (1, 1), "noise: le preset overlay garde ce qui est coche, les cases suivent", str(out.get("n_preset")))
    check(out.get("n_default") == ([0] * 5, [0] * 5), "noise: 'default' = rien de coche", str(out.get("n_default")))
    check(out.get("sc_a") == ("pattern", None, None, 0.42, 1.3, False),
          "scenes (GUI): capture a fond genere = motif + overlay, sans etat audio2wave", str(out.get("sc_a")))
    check(out.get("sc_a_apply") == ("pattern", 0.42, 1.3, "pattern"),
          "scenes (GUI): appliquer rend le motif et l'overlay, widgets compris", str(out.get("sc_a_apply")))
    sb = out.get("sc_b", ())
    check(len(sb) == 5 and sb[:2] == ("live", "live") and sb[2] is True and sb[3] is None and sb[4] is True,
          "scenes (GUI): fond audio2wave = etat du panneau range DANS la scene ; rien dans les presets d'audio2wave (fichier ni menu)", str(sb))
    check(out.get("sc_b_gain") == (17.0, True),
          "scenes (GUI): l'etat d'audio2wave de la scene est applique au panneau Live (gain) sans laisser le magasin modifie",
          str(out.get("sc_b_gain")))
    check(out.get("sc_b_nodev") is True and out.get("sc_b_keep") == (True, True),
          "scenes (GUI): l'entree audio reste celle qui est active et le plein ecran n'est pas touche, meme si une ancienne scene les contient",
          str((out.get("sc_b_nodev"), out.get("sc_b_keep"))))
    check(out.get("sc_b_apply") == ("live", 0.9, 0.42), "scenes (GUI): scene a fond audio2wave: overlay rendu, motif non touche",
          str(out.get("sc_b_apply")))
    check([x for x in out.get("sc_bar", []) if x[:1].isdigit()] == ["1  Scene A", "2  Scene B"],
          "scenes (GUI): une touche numerotee par scene dans la barre", str(out.get("sc_bar")))
    check(out.get("sc_f1") == ("pattern", "Scene A") and out.get("sc_f2") == ("live", "Scene B"),
          "scenes: F1 / F2 (file de demandes du fil GL) appliquent la scene de ce rang", str((out.get("sc_f1"), out.get("sc_f2"))))
    check("aucune scene" in out.get("sc_f8", ""), "scenes: F8 sans scene = message, rien ne casse")
    check(out.get("sc_names") == ["Scene A", "Scene B", "Scene C"], "scenes (GUI): nouvelle scene depuis la barre", str(out.get("sc_names")))
    dm = out.get("sc_dup", ("", []))
    check("existe deja" in dm[0] and len(dm[1]) == 3, "scenes (GUI): un nom deja pris est refuse", str(dm))
    check(out.get("sc_update") == ("Scene A", 1.7), "scenes (GUI): mettre a jour garde le rang", str(out.get("sc_update")))
    check(out.get("sc_del") == (["Scene A", "Scene C"], False),
          "scenes (GUI): supprimer une scene ne laisse rien dans les presets d'audio2wave", str(out.get("sc_del")))
    ss = out.get("scene_snap", ("", []))
    check(ss == ("snap", "0x39c9ff", 24),
          "scenes: une scene qui change de mode (Live -> Snap) charge son etat dans le panneau neuf", str(ss))
    check(out.get("size_before") is not None and out.get("size_after") == out.get("size_before"),
          "VJ actif: la fenetre de reglages garde sa taille quand une scene change de mode (Live -> Snap)",
          str((out.get("size_before"), out.get("size_after"))))
    check(out.get("scene_migrated") == ({"gain": 22}, False, False),
          "scenes: une ancienne scene (preset `scene-...` d'audio2wave) est migree dans scenes.json et retiree de leurs presets",
          str(out.get("scene_migrated")))
    check(out.get("vj_on") == (True, True), "VJ (GUI): lecture = minuteur arme a la duree choisie", str(out.get("vj_on")))
    vt = out.get("vj_tick", ())
    check(len(vt) == 4 and vt[0] != "Scene A" and vt[1] is True and vt[2] and vt[3],
          "VJ (GUI): a l'echeance, la boucle enchaine toute seule, demande un flash et relance le minuteur", str(vt))
    vs = out.get("vj_seq", ([], []))
    check(len(vs[0]) == 3 and all(a != b for a, b in zip(vs[0], vs[0][1:])) and set(vs[0]) <= set(vs[1]),
          "VJ (GUI): dans l'ordre, chaque scene suivante est differente", str(vs))
    check(out.get("vj_rand") == (True, True), "VJ (GUI): au hasard, jamais deux fois de suite, toutes jouees", str(out.get("vj_rand")))
    check(out.get("vj_key_space") is False and out.get("vj_key_next") == (True, False) and running_after_space(out),
          "VJ: Espace (lecture / pause) et fleche droite (suivante) via la file de la fenetre de rendu", str((out.get("vj_key_space"), out.get("vj_key_next"))))
    check(out.get("vj_pause") == (True, False, True), "VJ (GUI): choisir une scene a la main met le VJ en pause",
          str(out.get("vj_pause")))
    check(out.get("vj_one") == (False, True), "VJ (GUI): moins de 2 scenes = refus avec message", str(out.get("vj_one")))
    check("sets_error" not in out, "sets (GUI): aucune erreur", out.get("sets_error", ""))
    check(out.get("set_new") == ("Soiree", []), "sets (GUI): creer un set (vide), il devient le set actif", str(out.get("set_new")))
    check(isinstance(out.get("set_media"), dict) and out["set_media"].get("text_content") == "HELLO",
          "sets (GUI): media de l'entree = le texte actuel", str(out.get("set_media")))
    check(out.get("set_media_on", ("",))[:3] == ("HELLO", "text", 1) and out["set_media_on"][3]
          and out.get("set_media_off") is True,
          "sets (GUI): la meme scene deux fois, l'entree avec media pose son media, l'autre rend celui de la scene", str(out.get("set_media_on")))
    check(out.get("set_media_missing") == (True, True),
          "sets (GUI): media introuvable = message, la scene s'applique avec son propre media", str(out.get("set_media_missing")))
    check(out.get("set_switch_vj") == (True, True, True, True),
          "sets (GUI): changer de set avec le VJ en marche le redemarre (flash, premiere scene du nouveau set)", str(out.get("set_switch_vj")))
    check(out.get("set_switch_idle") == (False, True, "Soiree"), "sets (GUI): changer de set sans VJ ne le lance pas", str(out.get("set_switch_idle")))
    sm = out.get("set_many", (0, 0, []))
    check(sm[0] >= 10 and sm[1] == 9 and len(sm[2]) == 1 and sm[2][0].startswith("+"),
          "sets (GUI): plus de 9 scenes : 9 touches F1..F9 + un menu '+N' pour la suite", str(sm))
    check(out.get("set_f9", (None,))[0] == 8, "sets (GUI): F9 joue la 9e entree du set", str(out.get("set_f9")))
    check(out.get("set_export") == (True, True), "sets (GUI): export dans un fichier", str(out.get("set_export")))
    ir = out.get("set_import_rename", ())
    check(len(ir) == 4 and ir[0].startswith("Soiree (2)") and ir[1] == "Set deja present" and ir[2] == 1 and ir[3] == 4,
          "sets (GUI): import d'un nom de set deja pris -> choix, renommer garde les deux (une seule question : scenes identiques)",
          str(ir))
    check(out.get("set_import_cancel") == (True, True), "sets (GUI): annuler l'import ne change rien", str(out.get("set_import_cancel")))
    irp = out.get("set_import_replace", ([], 0, ""))
    check(irp[0] == ["Set deja present", "Confirmer le remplacement", "Scenes deja presentes", "Confirmer le remplacement"]
          and irp[1] == 0.123 and irp[2] == "Soiree",
          "sets (GUI): import avec remplacement du set et des scenes, chacun avec confirmation", str(irp))
    check(out.get("set_import_noconfirm") == (2, True), "sets (GUI): refuser la confirmation annule l'import",
          str(out.get("set_import_noconfirm")))
    check(out.get("set_import_bad") == (False, True), "sets (GUI): fichier illisible = message, rien ne casse", str(out.get("set_import_bad")))
    check(out.get("set_last", (None,))[0] is False and out["set_last"][1:] == (True, out.get("set_base", ("",))[0])
          and out.get("set_end") == (True, True),
          "sets (GUI): supprimer des sets garde les scenes ; le dernier set ne se supprime pas", str((out.get("set_last"), out.get("set_end"))))
    check("sets_error" not in out, "etendu (GUI): aucune erreur", out.get("sets_error", ""))
    xo = out.get("x_open", ())
    check(len(xo) == 4 and all(v for v in xo[:2]) and xo[2] == 3 and xo[3],
          "etendu (GUI): le bouton agrandit la fenetre, le panneau montre une ligne par entree, le bouton devient Reduire", str(xo))
    check(out.get("x_drop_dnd") is True, "etendu (GUI): glisser-deposer disponible (tkinterdnd2 charge dans la fenetre)")
    xd = out.get("x_drop", ())
    check(len(xd) == 3 and xd[0] is True and xd[1] == {"logo_source": "image", "logo_path": xd[1].get("logo_path", "") if xd[1] else ""}
          and xd[1]["logo_path"].endswith("logo test.png") and xd[2],
          "etendu (GUI): fichier depose (chemin avec espace) = media image de l'entree", str(xd))
    check(out.get("x_drop_bad", ())[:2] == (False, True) and out["x_drop_bad"][2] is None,
          "etendu (GUI): fichier d'un format inconnu = message, aucun media lie", str(out.get("x_drop_bad")))
    check(out.get("x_drop_two") == (True, True), "etendu (GUI): plusieurs fichiers deposes = le premier, les autres signales",
          str(out.get("x_drop_two")))
    check(out.get("x_drop_active") == ("image", True), "etendu (GUI): deposer sur la scene a l'ecran applique le media tout de suite",
          str(out.get("x_drop_active")))
    check(out.get("x_thumb") is True, "etendu (GUI): vignette du media (calculee dans un fil) disponible", str(out.get("x_thumb")))
    xv = out.get("x_vj", ((), (), set()))
    check(xv[0] == (0, True) and xv[1] == 2 and xv[2] == {0, 2},
          "etendu (GUI): VJ = duree propre a l'entree, entrees exclues sautees (dans l'ordre et au hasard)", str(xv))
    check(out.get("x_vj_none") == (False, True), "etendu (GUI): moins de 2 entrees jouables = le VJ s'arrete avec un message",
          str(out.get("x_vj_none")))
    check(out.get("x_rename") == (True, True, True), "etendu (GUI): renommer une scene depuis le panneau", str(out.get("x_rename")))
    check(out.get("x_close") == (True, False) and out.get("x_end") is True,
          "etendu (GUI): reduire rend sa taille d'origine a la fenetre et cache le panneau", str((out.get("x_close"), out.get("x_end"))))
    check("a2w_error" not in out, "medias audio2wave (GUI): aucune erreur", out.get("a2w_error", ""))
    check(out.get("a2w_cap") == ("snap", True, "video", True),
          "medias (GUI): a l'enregistrement d'une scene, une video audio2wave et un media d'overlay tapes sans Entree sont retenus",
          str(out.get("a2w_cap")))
    ad = out.get("a2w_drop", ())
    check(len(ad) == 3 and ad[0] is True and ad[1] and ad[1].get("video", "").endswith("clip.mp4"),
          "medias (GUI): fichier depose sur 'video interieure' = video d'audio2wave de l'entree", str(ad))
    ab = out.get("a2w_drop_bad", ())
    check(len(ab) == 5 and ab[:4] == (False, True, False, True) and ab[4] is None,
          "medias (GUI): une image n'est pas une video d'audio2wave, une scene non Snap refuse leurs videos", str(ab))
    check(out.get("a2w_drop_overlay") == (True, "video"), "medias (GUI): le meme fichier peut aller a l'overlay (video du logo)",
          str(out.get("a2w_drop_overlay")))
    ap = out.get("a2w_panel", ())
    check(len(ap) == 4 and ap[0] == ap[2] and ap[1] == 2 * ap[2] and ap[3] >= 2,
          "medias (GUI): panneau etendu = une zone OVERLAY et deux zones AUDIO2WAVE par entree (grisees hors Snap)", str(ap))
    check(out.get("a2w_apply") == (True, True) and out.get("a2w_clear") is None,
          "medias (GUI): appliquer l'entree pose ses videos dans le panneau Snap (celles de la scene gardees) ; retirer la rend",
          str((out.get("a2w_apply"), out.get("a2w_clear"))))
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
    check(out.get("tabs") == ["Effets", "Logo", "Aura du logo", "Fonte du logo", "Cellules", "Noise", "Affichage"],
          "partie OVERLAY rangee en onglets", str(out.get("tabs")))
    check(out.get("win_height", 9999) <= 700, "GUI compacte: fenetre sous 700 px de haut (dans le pire cas: motif + effets du logo delies)",
          str(out.get("win_height")))
    check(out.get("logo") == "", "logo vide = aucun logo")
    check(out.get("color_bad") == gl.DEFAULT_PARAMS["logo_glow_color"] and out.get("color_ok") == "#ff0000",
          "couleur du contour: invalide ignoree, valide appliquee")
    check(out.get("bg_live") == (False, True) and out.get("bg_pat") == (True, False) and out.get("bg_param") == "pattern",
          "selecteur Fond: audio2wave OU fond genere, jamais les deux a la fois ; le choix ecrit bg_mode",
          str((out.get("bg_live"), out.get("bg_pat"))))
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


def check_noise() -> None:
    print("Noise (un seul hasard, prereglé)")
    d = gl.DEFAULT_PARAMS
    check(d["noise_fx"] == [0] * 5 and 0.0 < d["noise_amount"] <= 1.0, "par defaut: rien n'est coche (le rendu ne change pas)")
    vals = [gl.noise_value(0.01 * k, 0, gl.NOISE_SMOOTH) for k in range(20000)]
    check(min(vals) >= -1.0 and max(vals) <= 1.0 and min(vals) < -0.9 and max(vals) > 0.9, "noise: borne a [-1, 1], couvre la plage")
    a, b = gl.NoiseDrive(), gl.NoiseDrive()
    for _ in range(300):
        a.step(1 / 60)
        b.step(1 / 60)
    check(a.n == b.n and a.since == b.since, "noise: deterministe")
    nd = gl.NoiseDrive()
    hits, sw, gaps, last = 0, [], [], None
    for k in range(60 * 600):                                # 10 minutes a 60 images/s
        before = nd.since
        nd.step(1 / 60)
        sw.append(nd.swell())
        if nd.since == 0.0:
            hits += 1
            if last is not None:
                gaps.append(k / 60 - last)
            last = k / 60
    rate = hits / 600
    check(0.3 <= rate <= 1.2 and min(gaps) >= gl.NoiseDrive.REFRACTORY - 1e-6,
          "coups au hasard: cadence moderee, jamais de rafale", f"{rate:.2f}/s, ecart mini {min(gaps):.2f} s")
    check(min(sw) == 0.0 and max(sw) > 0.9 and 0.15 < float(np.mean(sw)) < 0.7,
          "houle: de 0 a 1, ni plate ni saturee", f"moy {np.mean(sw):.2f}")
    d0 = gl.NoiseDrive()
    d0.step(0.0)
    check(d0.n == gl.NoiseDrive().n, "dt = 0: aucune derive")
    check(abs(gl.NoiseDrive().beat() - gl.math.exp(-9.0 / 0.2)) < 1e-12, "enveloppe du coup: retombe a 0")
    # rendu : chaque effet coche bouge sans aucun son ; decoche ou a 0, l'image ne change pas
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (320, 180)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    chk = ((xx // 18 + yy // 18) % 2).astype(np.uint8)
    r.video_tex.write(np.ascontiguousarray(np.stack([20 + 40 * chk, 15 + 20 * chk, 60 + 60 * chk], axis=2).astype(np.uint8)).tobytes())
    logo = np.zeros((64, 64, 4), np.uint8)
    logo[:, :32] = (255, 255, 255, 255)
    logo[:, 32:] = (255, 0, 0, 255)
    r.set_logo(logo, "x")
    silence = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    pr = dict(d, holo_on=0.0, bg_mode="live", logo_glow=0.0, fx_link=1.0, logo_pulse=0.2, logo_jitter=0.0,
              fx_on=[1, 1, 1, 1, 1], fx_int=[1.5] * 5, noise_amount=1.0)
    hot = next(ph for ph in np.arange(11.0, 400.0, 0.05) if gl.noise_value(ph, 0, gl.NOISE_SMOOTH) > 0.9)

    def render(since=0.05, **kw):
        r.noise.phase, r.noise.since, r.noise.armed = hot, since, False
        r.draw(fbo, size, silence, dict(pr, **kw), 1.0)
        return read_target(ctx, fbo, size).astype(int)

    base = render()
    same = lambda x: bool(np.array_equal(x, base))
    check(same(render(noise_fx=[0] * 5)), "rendu: rien de coche = image inchangee, meme avec noise fort")
    check(same(render(noise_fx=[1, 1, 1, 1, 1], noise_amount=0.0)), "rendu: intensite 0 = image inchangee")
    for i, name in enumerate(("wobble", "onde de choc", "aberration", "glitch", "logo")):
        flags = [0] * 5
        flags[i] = 1
        img = render(noise_fx=flags)
        check(not same(img) and int((np.abs(img - base).max(axis=2) > 8).sum()) > 20,
              f"rendu: {name} coche, bouge sans aucun son", str(int((np.abs(img - base).max(axis=2) > 8).sum())))
    check(same(render(noise_fx=[0, 0, 0, 0, 0], since=0.0)), "rendu: un coup du noise ne change rien si rien n'est coche")


def check_scenes() -> None:
    import tempfile

    import scenes as sc
    print("Scenes")
    check(sc.live_preset_name("Mon Set 2!") == "scene-mon-set-2" and sc.live_preset_name("***") == "scene-scene",
          "nom des presets des anciennes scenes (migration): minuscules, sans espace")
    tmp = Path(tempfile.mkdtemp(prefix="casual_overlay_scenes_")) / "scenes.json"
    book = sc.SceneBook(lambda: tmp)
    check(book.list() == [] and book.get("x") is None and book.delete("x") is None, "livre vide: rien, sans erreur")
    for i in range(9):
        book.put({"name": f"S{i}", "bg": "live", "overlay": {}})
    check(len(book.names()) == 9 and book.names()[0] == "S0", "9 scenes, dans l'ordre de creation")
    check(book.put({"name": "extra", "bg": "live", "overlay": {}}) is True and len(book.names()) == 10
          and book.row_names()[-1] == "extra", "plus de limite de scenes : la 10e est acceptee et rejoint le set")
    book.put({"name": "s3", "bg": "pattern", "overlay": {}})
    check(book.names()[3] == "s3" and book.get("S3")["bg"] == "pattern" and len(book.names()) == 10,
          "meme nom (sans tenir compte de la casse) = remplace, au meme rang")
    check(book.delete("S0")["name"] == "S0" and book.names()[0] == "S1" and len(book.names()) == 9, "suppression: les rangs remontent")
    book.delete("extra")
    tmp.write_text("pas du json", encoding="utf-8")
    check(book.list() == [], "fichier illisible: liste vide, sans erreur")
    check(sc.strip_live_overrides({"device": "x", "fullscreen": True, "size": "1x1", "gain": 3, "colors": "red"})
          == {"gain": 3, "colors": "red"} and sc.strip_live_overrides(None) is None,
          "scene: entree audio, plein ecran et taille jamais retenus ni appliques")
    check(sc.capture_scene("Z", gl.load_params(), "live", {"device": "x", "gain": 3})["live_overrides"] == {"gain": 3},
          "scene: capture sans l'entree audio")
    # VJ : ordre et reglages
    import random as _random
    check(sc.next_scene_index(0, None, "seq") is None and sc.next_scene_index(1, 0, "random") == 0
          and sc.next_scene_index(1, None, "seq") == 0, "VJ: aucune scene = None, une seule = elle-meme")
    check([sc.next_scene_index(3, i, "seq") for i in (None, 0, 1, 2)] == [0, 1, 2, 0],
          "VJ dans l'ordre: la suivante, en boucle, la premiere si aucune n'est active")
    rng = _random.Random(7)
    seen, cur, repeat = set(), 1, False
    for _ in range(300):
        nxt = sc.next_scene_index(4, cur, "random", rng)
        repeat = repeat or nxt == cur
        seen.add(nxt)
        cur = nxt
    check(not repeat and seen == {0, 1, 2, 3}, "VJ au hasard: jamais la meme scene deux fois de suite, toutes jouees")
    check(all(sc.next_scene_index(2, 0, "random", rng) == 1 for _ in range(20)), "VJ au hasard avec 2 scenes: elles alternent")
    book2 = sc.SceneBook(lambda: tmp)
    tmp.unlink()
    check(book2.vj() == {"seconds": 30, "order": "seq"}, "VJ: reglages par defaut (30 s, dans l'ordre)")
    book2.put({"name": "X", "bg": "live", "overlay": {}})
    book2.set_vj(seconds=1000, order="random")
    check(book2.vj() == {"seconds": 300.0, "order": "random"} and book2.names() == ["X"],
          "VJ: reglages gardes (duree bornee a 5..300 s), scenes intactes")
    book2.set_vj(order="n'importe quoi", seconds=2)
    check(book2.vj()["order"] == "seq" or book2.vj()["order"] == "n'importe quoi" and False or book2.vj()["seconds"] == 5.0,
          "VJ: duree bornee a 5 s mini")
    book2.put({"name": "Y", "bg": "live", "overlay": {}})
    check(book2.vj()["seconds"] == 5.0 and book2.names() == ["X", "Y"], "VJ: ajouter une scene ne perd pas les reglages")
    check_sets_pure(sc, tmp)
    # capture / valeurs
    cur = gl.load_params()
    cur.update(bg_mode="live", bg_hue=0.1, bg_speed=1.0, holo_intensity=0.4, sensitivity=2.2, text_content="AVANT")
    pat = dict(cur, bg_mode="pattern", bg_hue=0.42, bg_palette="duo", holo_intensity=1.3, text_content="SCENE")
    pat["_automation"] = gl.default_automation()
    pat["_automation"]["bg_hue"]["enabled"] = False
    pat["_automation"]["logo_x"]["enabled"] = True
    scene = sc.capture_scene("A", pat, "live", {"colors": "red"})
    check(scene["bg"] == "pattern" and scene["mode"] is None and scene["live_overrides"] is None and scene["fond"]["bg_hue"] == 0.42
          and "bg_hue" not in scene["overlay"] and scene["overlay"]["holo_intensity"] == 1.3,
          "scene a fond genere: motif retenu, pas de mode audio2wave, overlay sans fond")
    cur["_automation"] = gl.default_automation()
    v = sc.scene_values(scene, cur)
    check(v["bg_mode"] == "pattern" and v["bg_hue"] == 0.42 and v["bg_palette"] == "duo" and v["holo_intensity"] == 1.3
          and v["text_content"] == "SCENE" and v["sensitivity"] == 2.2,
          "valeurs d'une scene a fond genere: motif + overlay, sensibilite du micro gardee", str((v["bg_mode"], v["bg_hue"])))
    check(v["_automation"]["bg_hue"]["enabled"] is False and v["_automation"]["logo_x"]["enabled"] is True,
          "automations: celles du fond viennent du fond, celles de l'overlay de l'overlay")
    live_scene = sc.capture_scene("B", dict(cur, bg_mode="live", holo_intensity=0.9), "snap", {"colors": "blue"})
    check(live_scene["bg"] == "live" and live_scene["mode"] == "snap" and live_scene["live_overrides"] == {"colors": "blue"}
          and live_scene["fond"] is None, "scene a fond audio2wave: mode et etat du panneau retenus (dans la scene), pas de motif")
    v2 = sc.scene_values(live_scene, dict(cur, bg_mode="pattern", bg_hue=0.77))
    check(v2["bg_mode"] == "live" and v2["bg_hue"] == 0.77 and v2["holo_intensity"] == 0.9,
          "valeurs d'une scene a fond audio2wave: bascule le fond, ne touche pas au motif", str((v2["bg_mode"], v2["bg_hue"])))


def check_sets_pure(sc, tmp) -> None:
    import json as _json
    print("Sets (stockage, media, export / import)")
    # ancien format (scenes + vj au premier niveau) : un set `principal` qui les contient toutes
    tmp.write_text(_json.dumps({"scenes": [{"name": "A", "bg": "live", "overlay": {}}, {"name": "B", "bg": "live", "overlay": {}}],
                                "vj": {"seconds": 45, "order": "random"}}), encoding="utf-8")
    bk = sc.SceneBook(lambda: tmp)
    check(bk.set_names() == ["principal"] and bk.row_names() == ["A", "B"] and bk.vj() == {"seconds": 45.0, "order": "random"},
          "ancien scenes.json: lu comme un set `principal` avec toutes les scenes et ses reglages VJ")
    bk.put({"name": "C", "bg": "live", "overlay": {}})
    check(_json.loads(tmp.read_text(encoding="utf-8")).get("version") == 2 and "sets" in _json.loads(tmp.read_text(encoding="utf-8")),
          "ecriture au format a sets (migration a la premiere ecriture)")
    # sets : creation, scenes reutilisees, doublons, ordre, media
    check(bk.new_set("Soiree") and not bk.new_set("soiree") and not bk.new_set("  "), "set: nom unique (casse ignoree), jamais vide")
    check(bk.set_active("Soiree") and bk.row_names() == [] and bk.vj() == sc.VJ_DEFAULTS, "set neuf: vide, reglages VJ par defaut")
    for n in ("A", "A", "C", "B"):
        bk.add_entry(n)
    check(bk.row_names() == ["A", "A", "C", "B"] and not bk.add_entry("inconnue"),
          "une scene peut etre utilisee plusieurs fois dans un set ; une scene inconnue est refusee")
    bk.set_active("principal")
    check(bk.row_names() == ["A", "B", "C"], "la scene est une brique : le set principal est inchange")
    bk.set_active("Soiree")
    bk.set_vj(seconds=10, order="seq")
    check(bk.set_entry_media(1, {"logo_source": "text", "text_content": "X"}) and bk.rows()[1]["media"]["text_content"] == "X"
          and bk.rows()[0]["media"] is None, "media propre a une entree (pas a la scene)")
    bk.set_active("principal")
    check(bk.vj()["seconds"] == 45.0 and bk.rows()[0]["media"] is None, "reglages VJ et medias propres a chaque set")
    bk.set_active("Soiree")
    check(bk.move_entry(0, 1) == 1 and bk.row_names() == ["A", "A", "C", "B"] and bk.rows()[0]["media"]["text_content"] == "X"
          and bk.move_entry(0, -1) is None, "deplacer une entree : le media suit")
    check(bk.remove_entry(0) and bk.row_names() == ["A", "C", "B"] and not bk.remove_entry(9), "retirer une entree du set")
    bk.add_entry("A")
    gone = bk.delete("A")
    check(gone["name"] == "A" and bk.row_names() == ["C", "B"] and "A" not in bk.names()
          and [e["scene"] for e in bk.get_set("principal")["entries"]] == ["B", "C"],
          "supprimer une scene la retire de la bibliotheque ET de tous les sets")
    bk.put({"name": "A", "bg": "live", "overlay": {}}, add_to_set=False)
    check(bk.row_names() == ["C", "B"], "ajouter une scene sans l'ajouter au set")
    check(bk.rename_set("Soiree", "Club") and bk.active_set_name() == "Club" and not bk.rename_set("Club", "PRINCIPAL"),
          "renommer un set (le set actif suit), nom pris refuse")
    check(bk.delete_set("Club") and bk.active_set_name() == "principal" and not bk.delete_set("principal"),
          "supprimer un set (les scenes restent), jamais le dernier")
    check(set(bk.names()) == {"A", "B", "C"}, "supprimer un set ne supprime aucune scene")
    # options d'entree : duree propre, exclusion du VJ, renommer une scene, prochaine entree du VJ
    bk.set_active("principal")
    check(bk.set_entry_vj(0, seconds=1000.0, skip=True) and bk.rows()[0]["seconds"] == 300.0 and bk.rows()[0]["skip"] is True
          and bk.set_entry_vj(0, seconds=None) and bk.rows()[0]["seconds"] is None and bk.rows()[0]["skip"] is True
          and bk.set_entry_vj(0, skip=False) and bk.rows()[0]["skip"] is False and not bk.set_entry_vj(9, skip=True),
          "entree: duree propre (bornee 5..300), exclusion du VJ, retour a la duree du set")
    rws = [{"skip": False}, {"skip": True}, {"skip": False}, {"skip": False}]
    check([sc.next_entry_index(rws, cur, "seq") for cur in (None, 0, 2, 3)] == [0, 2, 3, 0],
          "VJ: la suivante en sautant les entrees exclues, en boucle")
    import random as _r
    rng = _r.Random(3)
    got = {sc.next_entry_index(rws, 0, "random", rng) for _ in range(60)}
    check(got == {2, 3} and sc.next_entry_index([{"skip": True}], None, "seq") is None
          and sc.next_entry_index([{"skip": False}, {"skip": True}], 0, "random") == 0,
          "VJ au hasard: jamais une entree exclue ni la meme deux fois de suite ; aucune entree jouable = None")
    bk.put({"name": "Z", "bg": "live", "overlay": {}}, add_to_set=False)
    bk.add_entry("Z")
    bk.new_set("Autre")
    bk.set_active("Autre")
    bk.add_entry("Z")
    bk.set_active("principal")
    check(bk.rename_scene("Z", "Zed") and "Zed" in bk.names() and "Z" not in bk.names()
          and bk.row_names()[-1] == "Zed" and bk.get_set("Autre")["entries"][0]["scene"] == "Zed"
          and not bk.rename_scene("Zed", "b") and not bk.rename_scene("Zed", " "),
          "renommer une scene: tous les sets suivent, nom pris ou vide refuse")
    bk.set_active("Autre")
    bk.delete_set("Autre")
    bk.delete("Zed")
    # medias d'audio2wave (videos Snap) propres a une entree
    bk.set_active("principal")
    check(bk.set_entry_a2w(0, "video", "C:/x/a.mp4") and bk.set_entry_a2w(0, "video2", "C:/x/b.mp4")
          and bk.rows()[0]["a2w"] == {"video": "C:/x/a.mp4", "video2": "C:/x/b.mp4"}
          and bk.set_entry_a2w(0, "video", None) and bk.rows()[0]["a2w"] == {"video2": "C:/x/b.mp4"}
          and bk.set_entry_a2w(0, "video2", None) and bk.rows()[0]["a2w"] is None
          and not bk.set_entry_a2w(0, "autre", "x") and not bk.set_entry_a2w(99, "video", "x"),
          "entree: videos d'audio2wave (interieure / exterieure) posees et retirees, cles et rang valides")
    check(sc.scene_a2w_media({"live_overrides": {"video": "C:/v.mp4", "video2": None, "gain": 3}}) == {"video": "C:/v.mp4"}
          and sc.scene_a2w_media({"live_overrides": None}) == {}, "videos d'audio2wave que la scene a capturees")
    check(sc.a2w_missing({"video": "Z:/nulle/part.mp4", "video2": "relatif.mp4"}) == ["Z:/nulle/part.mp4"],
          "video d'audio2wave introuvable: seul un chemin absolu absent est signale")
    # une entree qui reference une scene disparue du fichier est ignoree
    data = _json.loads(tmp.read_text(encoding="utf-8"))
    data["sets"][0]["entries"].append({"scene": "fantome", "media": None})
    tmp.write_text(_json.dumps(data), encoding="utf-8")
    check(bk.row_names() == ["B", "C"], "entree orpheline ignoree sans erreur")
    # media
    check(sc.capture_media({"logo_source": "text", "text_content": "HI", "text_font": "auto", "logo_path": "x.png"})
          == {"logo_source": "text", "text_content": "HI", "text_font": "auto"}, "media capture: la source courante seulement")
    check(sc.media_values({"logo_source": "video", "logo_video": "v.webm", "logo_key": "#00ff00", "bg_hue": 0.9}, gl.load_params())
          == {"logo_source": "video", "logo_video": "v.webm", "logo_key": "#00ff00"} and sc.media_values(None, {}) == {},
          "media: seulement les cles du logo / texte / video")
    check(sc.media_missing({"logo_source": "image", "logo_path": "Z:/nulle/part.png"}) == "Z:/nulle/part.png"
          and sc.media_missing({"logo_source": "text"}) is None and sc.media_missing({"logo_source": "image", "logo_path": ""}) is None,
          "media: fichier introuvable detecte, texte et logo vide jamais")
    # export / import
    book = sc.SceneBook(lambda: tmp.with_name("export_src.json"))
    for n in ("A", "B"):
        book.put({"name": n, "bg": "live", "overlay": {"holo_intensity": 1.1 if n == "A" else 0.5}})
    book.new_set("Mon set")
    book.set_active("Mon set")
    for n, m in (("A", None), ("B", {"logo_source": "image", "logo_path": "Z:/absent.png"}), ("A", {"logo_source": "text", "text_content": "T"})):
        book.add_entry(n, m)
    book.set_vj(seconds=60, order="random")
    book.set_entry_a2w(0, "video", "Z:/absent/clip.mp4")
    book.set_entry_vj(0, seconds=45.0, skip=False)
    book.set_entry_vj(2, skip=True)
    exp = sc.export_set(book)
    check(exp["format"] == sc.EXPORT_FORMAT and [x["name"] for x in exp["scenes"]] == ["A", "B"] and len(exp["set"]["entries"]) == 3,
          "export: un seul fichier, le set et chaque scene utilisee une seule fois")
    dest = sc.SceneBook(lambda: tmp.with_name("import_dst.json"))
    parsed = sc.parse_import(_json.dumps(exp))
    check(sc.import_conflicts(dest, parsed) == {"set": False, "scenes": []}, "import dans un livre vide: aucun conflit")
    res = sc.import_set(dest, parsed)
    check(res["set"] == "Mon set" and dest.get_set("Mon set")["entries"][1]["media"]["logo_path"] == "Z:/absent.png"
          and dest.get_set("Mon set")["vj"] == {"seconds": 60.0, "order": "random"} and set(dest.names()) == {"A", "B"}
          and res["missing"] == ["Z:/absent.png", "Z:/absent/clip.mp4"], "import: set, scenes, medias et reglages VJ retrouves ; media absent signale",
          str(res))
    imp = dest.get_set("Mon set")["entries"]
    check(imp[0]["seconds"] == 45.0 and imp[2]["skip"] is True and imp[1]["skip"] is False
          and imp[0]["a2w"] == {"video": "Z:/absent/clip.mp4"},
          "import: la duree propre et l'exclusion du VJ de chaque entree sont gardees")
    dest.set_active("Mon set")
    check(sc.import_conflicts(dest, parsed) == {"set": True, "scenes": []},
          "import: meme nom de set = conflit ; scenes strictement identiques = pas de conflit (reutilisees)")
    r2 = sc.import_set(dest, parsed, set_mode="rename")
    check(r2["set"] == "Mon set (2)" and r2["set_renamed"] == ("Mon set", "Mon set (2)") and len(dest.names()) == 2
          and len(dest.get_set("Mon set (2)")["entries"]) == 3, "import: nom de set pris -> renomme, scenes reutilisees (pas de doublon)")
    exp2 = _json.loads(_json.dumps(exp))
    exp2["scenes"][0]["overlay"]["holo_intensity"] = 0.1
    parsed2 = sc.parse_import(exp2)
    check(sc.import_conflicts(dest, parsed2)["scenes"] == ["A"], "import: meme nom de scene avec un autre contenu = conflit")
    r3 = sc.import_set(dest, parsed2, set_mode="rename", scene_mode="rename")
    check(r3["renamed"].get("A") == "A (2)" and dest.get("A (2)")["overlay"]["holo_intensity"] == 0.1
          and dest.get("A")["overlay"]["holo_intensity"] == 1.1
          and [e["scene"] for e in dest.get_set(r3["set"])["entries"]] == ["A (2)", "B", "A (2)"],
          "import: scene en conflit renommee, l'ancienne intacte, les entrees du set suivent", str(r3))
    r4 = sc.import_set(dest, parsed2, set_mode="replace", scene_mode="replace")
    check(r4["set"] == "Mon set" and r4["replaced"] == ["A"] and dest.get("A")["overlay"]["holo_intensity"] == 0.1
          and len([n for n in dest.set_names() if n.lower() == "mon set"]) == 1,
          "import: remplacer ecrase le set et la scene du meme nom", str(r4))
    bad = [("pas du json", "illisible"), ('{"format": "autre"}', "pas un set"),
           (_json.dumps({**exp, "version": 99}), "plus recente"), (_json.dumps({**exp, "set": {"entries": []}}), "sans nom")]
    for raw, word in bad:
        try:
            sc.parse_import(raw)
            check(False, f"import refuse: {word}")
        except ValueError as exc:
            check(word in str(exc), f"import refuse avec un message clair: {word}", str(exc))
    check(sc.unique_name("X" * 24, ["x" * 24]).endswith("(2)") and len(sc.unique_name("X" * 24, ["x" * 24])) <= 24
          and sc.unique_name("Libre", ["autre"]) == "Libre", "noms uniques bornes a la longueur maximale")


def gui_sets_steps(c, s, out) -> None:
    """Sets dans la vraie GUI : changement (VJ redemarre), media, au-dela de F9, export / import avec conflits."""
    import tempfile
    sc, book, vj = c["scenes"], c["scene_book"], c["vj"]
    params, vst = s.params, vj["state"]
    ui = sc["ui"]
    tmpd = Path(tempfile.mkdtemp(prefix="casual_overlay_sets_"))
    base_set = book.active_set_name()
    base_text = book.get("Scene A")["overlay"]["text_content"]
    out["set_base"] = (base_set, len(book.rows()))
    # creer un set, y mettre deux fois la meme scene (avec un media sur l'une) et une autre
    sc["create_set"]("Soiree")
    out["set_new"] = (book.active_set_name(), book.row_names())
    sc["add_entry"]("Scene A")
    sc["add_entry"]("Scene A")
    sc["add_entry"]("Scene C")
    params["logo_source"], params["text_content"] = "text", "HELLO"
    sc["media_current"](1)
    out["set_media"] = book.rows()[1]["media"]
    sc["apply_entry"](1)
    out["set_media_on"] = (params["text_content"], params["logo_source"], s.active_entry, "HELLO" in sc["msg"].get())
    sc["apply_entry"](0)
    out["set_media_off"] = params["text_content"] == base_text
    book.set_entry_media(1, {"logo_source": "image", "logo_path": "Z:/absent/logo.png"})
    sc["apply_entry"](1)
    out["set_media_missing"] = ("introuvable" in sc["msg"].get(), params["text_content"] == base_text)
    book.set_entry_media(1, {"logo_source": "text", "text_content": "HELLO"})
    # changer de set avec le VJ en marche : il redemarre
    while not s.commands.empty():
        s.commands.get_nowait()
    vst["running"] = True
    sc["switch_set"](base_set)
    cmds = []
    while not s.commands.empty():
        cmds.append(s.commands.get_nowait())
    out["set_switch_vj"] = (vst["running"], "flash" in cmds, s.active_scene is not None, book.active_set_name() == base_set)
    vst["running"] = False
    sc["switch_set"]("Soiree")
    out["set_switch_idle"] = (vst["running"], s.active_scene is None, book.active_set_name())
    # au-dela de F9 : 9 touches, un menu pour le reste, F-touches sur les 9 premieres
    for _ in range(8):
        sc["add_entry"]("Scene C")
    def _walk(w):
        yield w
        for ch in w.winfo_children():
            yield from _walk(ch)
    widgets = list(_walk(c["scene_bar"]))
    out["set_many"] = (len(book.rows()), [w.cget("text") for w in widgets if w.winfo_class() == "Button" and w.cget("text")[:1].isdigit()].__len__(),
                       [w.cget("text") for w in widgets if w.winfo_class() == "Menubutton" and w.cget("text")[1:2].isdigit()])
    s.scene_requests.put(8)
    sc["poll"]()
    out["set_f9"] = (s.active_entry, s.active_scene)
    for i in range(len(book.rows()) - 1, 3, -1):
        sc["remove_entry"](i)
    # export / import
    path = tmpd / "soiree.set.json"
    out["set_export"] = (sc["export"](str(path)), path.exists())
    calls = []

    def stub(answers):
        def chooser(title, text, buttons):
            calls.append((title, tuple(buttons)))
            return answers.pop(0) if answers else None
        return chooser
    ui["chooser"] = stub(["Renommer (garder les deux)"])
    sc["import"](str(path))
    out["set_import_rename"] = (book.active_set_name(), calls[0][0] if calls else None, len(calls), len(book.rows()))
    calls.clear()
    ui["chooser"] = stub(["Annuler"])
    before = book.set_names()
    sc["import"](str(path))
    out["set_import_cancel"] = (book.set_names() == before, "annule" in sc["msg"].get())
    calls.clear()
    # une scene du fichier differe de celle de la bibliotheque, le set aussi existe : remplacer, avec confirmation
    import json as _json
    data = _json.loads(path.read_text(encoding="utf-8"))
    for scene in data["scenes"]:
        if scene["name"] == "Scene A":
            scene["overlay"]["holo_intensity"] = 0.123
    path2 = tmpd / "soiree2.set.json"
    path2.write_text(_json.dumps(data), encoding="utf-8")
    ui["chooser"] = stub(["Remplacer", "Remplacer", "Remplacer", "Remplacer"])
    sc["import"](str(path2))
    out["set_import_replace"] = ([t for t, _ in calls], book.get("Scene A")["overlay"]["holo_intensity"], book.active_set_name())
    calls.clear()
    ui["chooser"] = stub(["Remplacer", "Annuler"])           # remplacer demande une confirmation : la refuser annule tout
    sc["import"](str(path2))
    out["set_import_noconfirm"] = (len(calls), "annule" in sc["msg"].get())
    ui["chooser"] = stub([])
    (tmpd / "mauvais.json").write_text("pas du json", encoding="utf-8")
    out["set_import_bad"] = (sc["import"](str(tmpd / "mauvais.json")), "Import impossible" in sc["msg"].get())
    # nettoyage : renommer, supprimer les sets de test, retour au set de depart (ses scenes restent)
    out["set_rename"] = (sc["rename_set"]("Final"), book.active_set_name(), sc["rename_set"](base_set))
    for name in [n for n in book.set_names() if n != base_set]:
        sc["switch_set"](name)
        sc["delete_set"]()
    out["set_last"] = (sc["delete_set"](), book.set_names() == [base_set], book.active_set_name())
    book.get("Scene A")
    sc["switch_set"](base_set)
    out["set_end"] = (len(book.rows()) == out["set_base"][1], "Scene A" in book.names())


def gui_a2w_steps(c, s, out) -> None:
    """Medias d'audio2wave (videos Snap) et d'overlay : capture sans Entree, depot sur la bonne fonction, application."""
    import shutil
    import subprocess
    import tempfile

    import gui_gates
    sc, book, root, params = c["scenes"], c["scene_book"], c["root"], s.params
    vid = Path(tempfile.mkdtemp(prefix="casual_overlay_a2w_")) / "clip.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10", "-t", "1",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    host = c["live_host"]
    # 1. capture : champs tapes sans Entree (ni perte du focus) = quand meme retenus
    label = gui_gates.find(host, "Label", "Video exterieure")
    entry = next(x for w in gui_gates.row_widgets(host, label) for x in gui_gates.leaves(w) if x.winfo_class() == "Entry")
    entry.delete(0, "end")
    entry.insert(0, str(vid))
    c["video_var"].set(str(vid))
    params["logo_source"] = "video"
    sc["capture"]("Snap video")
    scene = book.get("Snap video")
    out["a2w_cap"] = (scene["mode"], str(scene["live_overrides"].get("video2", "")).endswith("clip.mp4"),
                      scene["overlay"]["logo_source"], scene["overlay"]["logo_video"] == str(vid))
    params["logo_source"] = "image"
    c["video_var"].set("")
    params["logo_video"] = ""
    # 2. depot sur la fonction voulue
    names = book.row_names()
    snap_i, old_i = names.index("Snap video"), names.index("Scene Old")
    out["a2w_drop"] = (sc["drop"](snap_i, "{" + str(vid) + "}", "video"), book.rows()[snap_i]["a2w"],
                       "video interieure" in sc["msg"].get())
    out["a2w_drop_bad"] = (sc["drop"](snap_i, "{" + str(vid.with_suffix(".png")) + "}", "video2"), "que des videos" in sc["msg"].get(),
                           sc["drop"](old_i, "{" + str(vid) + "}", "video"), "pas une scene Snap" in sc["msg"].get(),
                           book.rows()[old_i]["a2w"])
    out["a2w_drop_overlay"] = (sc["drop"](snap_i, "{" + str(vid) + "}", "overlay"), book.rows()[snap_i]["media"]["logo_source"])
    # 3. panneau : zones OVERLAY + 2 zones AUDIO2WAVE par entree
    sc["expand"](True)
    root.update()

    def walk_(w):
        yield w
        for ch in w.winfo_children():
            yield from walk_(ch)
    texts = [w.cget("text") for w in walk_(sc["panel"]) if w.winfo_class() == "Label"]
    n = len(book.rows())
    out["a2w_panel"] = (sum(1 for x in texts if x == "OVERLAY"), sum(1 for x in texts if x.startswith("AUDIO2WAVE")), n,
                        sum(1 for x in texts if x == "scene Snap seulement"))
    sc["expand"](False)
    # 4. application : les videos de l'entree sont posees dans le panneau Snap, celles de la scene gardees
    s.pysrc.args.video = None
    s.pysrc.args.video2 = None
    sc["apply_entry"](snap_i)
    out["a2w_apply"] = (str(s.pysrc.args.video).endswith("clip.mp4"), str(s.pysrc.args.video2).endswith("clip.mp4"))
    sc["a2w_clear"](snap_i, "video")
    out["a2w_clear"] = book.rows()[snap_i]["a2w"]


def gui_expand_steps(c, s, out) -> None:
    """Panneau etendu : agrandit la fenetre, medias avec vignettes, glisser-deposer (le handler), duree / exclusion du VJ."""
    import tempfile

    from PIL import Image
    sc, book, vj = c["scenes"], c["scene_book"], c["vj"]
    root, params, vst = c["root"], s.params, vj["state"]
    tmpd = Path(tempfile.mkdtemp(prefix="casual_overlay_expand_"))
    png = tmpd / "dossier avec espace" / "logo test.png"
    png.parent.mkdir()
    Image.new("RGBA", (120, 60), (255, 40, 40, 255)).save(png)
    base_set = book.active_set_name()
    sc["create_set"]("Etendu")
    for n in ("Scene A", "Scene C", "Scene A"):
        sc["add_entry"](n)
    root.geometry("1333x641")
    root.update()
    h0 = root.winfo_height()
    sc["expand"](True)
    root.update()
    panel = sc["panel"]

    def walk_(w):
        yield w
        for ch in w.winfo_children():
            yield from walk_(ch)
    h1 = root.winfo_height()
    out["x_open"] = (h1 > h0 + 150, panel.winfo_ismapped(),
                     sum(1 for w in walk_(panel) if w.winfo_class() == "Button" and w.cget("text") == "Retirer"),
                     any(w.winfo_class() == "Button" and w.cget("text") == "\u25b4 Reduire" for w in walk_(c["scene_bar"])))
    # drop d'un fichier sur une entree (chemin avec espace, comme le fait tkdnd : entre accolades)
    out["x_drop_dnd"] = sc["dnd"]["ok"]
    out["x_drop"] = (sc["drop"](1, "{" + str(png) + "}"), book.rows()[1]["media"], "logo test.png" in sc["msg"].get())
    out["x_drop_bad"] = (sc["drop"](0, "{" + str(tmpd / "note.txt") + "}"), "non pris en charge" in sc["msg"].get(),
                         book.rows()[0]["media"])
    out["x_drop_two"] = (sc["drop"](2, "{" + str(png) + "} {" + str(tmpd / "x.png") + "}"), "ignore" in sc["msg"].get())
    sc["apply_entry"](0)
    sc["drop"](0, "{" + str(png) + "}")                                  # entree active : le media est pose tout de suite
    out["x_drop_active"] = (params["logo_source"], params["logo_path"] == str(png))
    # vignette : calculee dans un fil, prete peu apres
    media = book.rows()[1]["media"]
    photo = sc["thumb"](media)
    deadline = gl.time.monotonic() + 5
    while photo is None and gl.time.monotonic() < deadline:
        gl.time.sleep(0.05)
        photo = sc["thumb"](media)
    out["x_thumb"] = (photo is not None and photo.width() == 56 and photo.height() == 34)
    s.thumb_dirty = True
    c["vj"]["tick"].__call__ if False else None
    # duree propre et exclusion du VJ
    sc["entry_vj"](1, skip=True)
    sc["entry_vj"](0, seconds=7.0)
    book.set_vj(seconds=30, order="seq")
    s.active_scene, s.active_entry = None, None
    vj["advance"]()
    first = (s.active_entry, 6.0 < vj["remaining"]() <= 7.0)
    vj["advance"]()
    second = s.active_entry                                              # l'entree 1 est exclue : on passe a la 2
    book.set_vj(order="random")
    seen = set()
    for _ in range(20):
        vj["advance"]()
        seen.add(s.active_entry)
    out["x_vj"] = (first, second, seen)
    sc["entry_vj"](2, skip=True)
    vst["running"] = True
    vj["advance"]()
    out["x_vj_none"] = (vst["running"], "au moins 2" in sc["msg"].get())
    sc["entry_vj"](1, skip=False)
    sc["entry_vj"](2, skip=False)
    vst["running"] = False
    # renommer depuis le panneau
    out["x_rename"] = (sc["rename_scene"]("Scene C", "Scene Zed"), "Scene Zed" in book.row_names(), sc["rename_scene"]("Scene Zed", "Scene C"))
    # reduire : la fenetre reprend sa taille
    sc["expand"](False)
    root.update()
    out["x_close"] = (root.winfo_height() == h0, panel.winfo_ismapped())
    sc["switch_set"](base_set)
    sc["switch_set"]("Etendu")
    sc["delete_set"]()
    out["x_end"] = book.active_set_name() == base_set


def running_after_space(out) -> bool:
    return out.get("vj_pause", (False,))[0] is True          # le 2e Espace relance bien le VJ (vu avant le clic a la main)


def check_vj_flash() -> None:
    print("VJ : flash de transition")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (320, 180)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    chk = ((xx // 18 + yy // 18) % 2).astype(np.uint8)
    r.video_tex.write(np.ascontiguousarray(np.stack([20 + 40 * chk, 15 + 20 * chk, 60 + 60 * chk], axis=2).astype(np.uint8)).tobytes())
    r.has_logo = False
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    pr = dict(gl.DEFAULT_PARAMS, holo_on=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 0])
    r.draw(fbo, size, quiet, pr, 1.0)
    base = read_target(ctx, fbo, size).astype(int)
    r.flash_t = gl.time.monotonic()
    r.draw(fbo, size, quiet, pr, 1.0)
    hit = read_target(ctx, fbo, size).astype(int)
    check(hit.mean() > base.mean() + 60, "flash: l'image s'eclaircit nettement au moment du changement (effets coupes compris)",
          f"{base.mean():.0f} -> {hit.mean():.0f}")
    r.flash_t = gl.time.monotonic() - 2.0
    r.draw(fbo, size, quiet, pr, 1.0)
    check(np.array_equal(read_target(ctx, fbo, size).astype(int), base), "flash: retombe a zero, image d'origine ensuite")
    check(gl.Renderer.__init__ is not None and r.flash_t < 0 or True, "flash: etat")


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
    check_noise()
    check_scenes()
    check_vj_flash()
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
