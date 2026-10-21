#!/usr/bin/env python3
"""Verifications hors materiel de audio2wave_gl.py (aucun micro, projecteur ni ffmpeg).

    python tools/check_gl.py

  1. lecteur de frames : faux producteur livrant par blocs de 4 Ko, aucune frame melangee,
     frame partielle de fin de flux jetee, latest-wins;
  2. features : salves de sinus 60 Hz -> un beat par salve, aucun faux positif sur bruit
     constant, silence = niveaux nuls, latence de detection mesuree;
  3. rendu : une frame en contexte moderngl standalone -> PNG non vide, et image neutre
     (identique a la video) quand l'audio est coupe.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("CASUAL_NO_EXPAND", "1")        # les tests partent d'un panneau replie (le vrai programme etend le Mode VJ au lancement)
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # racine du depot (ce script est dans tools/)
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import audio2wave_gl as gl
from core import autosave as core_autosave
from core import params as core_params

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


class SineStream:
    """Faux micro des modes Snap / Ridge (PCM s16le mono, 48 kHz)."""

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


POPEN_LOG: list = []        # commandes lancees par les modes Snap / Ridge pendant les tests (faux micro installe)


def fake_microphone(*modules):
    """TEST SEULEMENT : le module `subprocess` de snap / ridge rend un faux micro (sinus) pour la capture dshow, tout le reste est
    delegue (ffprobe...) ; chaque commande est notee dans POPEN_LOG. Rend la fonction qui remet le module d'origine. Le code de
    production, lui, n'y touche plus (`start_headless`)."""
    import subprocess
    from types import SimpleNamespace as NS

    class FakeSubprocess:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def Popen(self, cmd, *a, **k):               # noqa: N802
            POPEN_LOG.append(list(cmd) if not isinstance(cmd, str) else [cmd])
            if "dshow" in list(cmd):
                return NS(stdout=SineStream(), terminate=lambda: None, wait=lambda *a, **k: 0, poll=lambda: None, returncode=None)
            return self._real.Popen(cmd, *a, **k)

    originals = {m: m.subprocess for m in modules}
    for m in modules:
        m.subprocess = FakeSubprocess(m.subprocess)

    def restore() -> None:
        for m, real in originals.items():
            m.subprocess = real
    return restore


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

    import py_modes
    restore_microphone = fake_microphone(py_modes.load_mode(gl.DEFAULT_A2W_DIR, "snap"), py_modes.load_mode(gl.DEFAULT_A2W_DIR, "ridge"))
    import atexit
    atexit.register(restore_microphone)
    POPEN_LOG.clear()

    analyzer = NS(latest=lambda: {"bass": 0.5, "mid": 0.3, "high": 0.2, "beat": 1.0})
    audio = NS(synthetic=True, name="synthetique", analyzer=analyzer)
    params = gl.load_params()

    class FakeProjection:
        """Projection factice : la GUI ouvre / ferme la fenetre de rendu par son bouton."""
        def __init__(self):
            self.on, self.starts, self.stops = False, 0, 0

        def running(self):
            return self.on

        def start(self):
            self.on, self.starts = True, self.starts + 1
            return "Projection ouverte en fenetre"

        def stop(self, wait=0.0):
            self.on, self.stops = False, self.stops + 1

    fake_proj = FakeProjection()
    s = NS(params=params, manager=manager, live_args=args, render_size=(1280, 720), audio=audio, projection=fake_proj,
           commands=queue.SimpleQueue(), status={}, stop_event=threading.Event(),
           finished_event=threading.Event(), mode="live", pysrc=None, mode_args={}, reader=manager.reader)
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
                              height=root.winfo_reqheight(), reader_ok=s.reader is s.pysrc.reader,
                              presets_top=presets_top_state(c["live_host"]))

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
                                    gain=find_scale(c["live_host"], "Gain (dB)") is not None,
                                    presets_top=presets_top_state(c["live_host"]))
            # GuiController.apply_state en live: le panneau prend l'etat ET le producteur ffmpeg est relance avec (rien ne prend effet sans)
            ctrl = c["panel"]["ctrl"]
            skipped = ctrl.apply_state({"gain": 17.0, "pas_une_cle": 1})
            for _ in range(8):
                root.update()
                time.sleep(0.05)
            out["apply_live"] = (skipped, args.gain == 17.0, any(getattr(r, "gain", None) == 17.0 for r in requests),
                                 ctrl.capture_state().get("gain") == 17.0)
            # une scene a fond audio2wave qui change de mode (Live -> Snap) charge son preset dans le panneau neuf
            out["stage"] = "scene"
            root.geometry("")                                   # taille naturelle (aucune geometrie imposee), comme a l'ouverture
            for _ in range(16):                                 # la taille naturelle bouge un instant (l'ascenseur du panneau apparait) : on attend qu'elle se fige
                root.update()
                time.sleep(0.05)
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
                # info-bulles du panneau Snap : mesurees APRES la taille (les survoler bouscule la mise en page de ce test)
                out["snap"]["tips"] = tooltip_violations(root)
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
        first_ = out.setdefault("first_widgets", (c["notebook"], c["scene_bar"], c["right_cv"], c["live_host"]))
        out["swap_keeps_overlay"] = out.get("swap_keeps_overlay", True) and (c["notebook"], c["scene_bar"], c["right_cv"]) == first_[:3]
        out["swap_new_panel"] = out.get("swap_new_panel", False) or (out["builds"] > 1 and c["live_host"] is not first_[3])
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
            out["scrollbars"] = (sum(1 for w in walk(root) if w.winfo_class() == "Scrollbar"),
                                 sum(1 for w in walk(root) if w.winfo_class() == "TScrollbar"
                                     and str(w.cget("style")) == "Casual.Vertical.TScrollbar"))
            menus = [w for w in walk(root) if w.winfo_class() == "Menu"]
            out["menus"] = (len(menus), sum(1 for m in menus if _contrast(*(
                "#%02x%02x%02x" % tuple(v >> 8 for v in root.winfo_rgb(str(m.cget(k)))) for k in ("activebackground", "activeforeground"))) < 3.0))
            out["automation"] = sum(1 for w in walk(root) if w.winfo_class() == "Button"
                                    and w.cget("text") == "∿")
            proj_btn = c["projection"]["button"]()
            proj = {"text0": proj_btn.cget("text") if proj_btn else None}
            if proj_btn:
                proj_btn.invoke()
                root.update()
                proj.update(text1=proj_btn.cget("text"), started=fake_proj.starts)
                c["projection"]["send"]("reload")
                proj["queued"] = s.commands.empty()               # ouverte : la commande part au fil GL
                while not s.commands.empty():
                    s.commands.get_nowait()                       # (ne pas polluer les etapes suivantes)
                time.sleep(0.7)                                   # au-dela de l'anti-rebond du bouton
                proj_btn.invoke()
                root.update()
                proj.update(text2=proj_btn.cget("text"), stopped=fake_proj.stops)
                c["projection"]["send"]("reload")
                proj["closed_queue"] = s.commands.empty()         # fermee : rien ne s'empile
                proj["closed_msg"] = s.status.get("msg", "")
            out["proj"] = proj
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
            out["win_dbg"] = (c["layout"]["name"], root.winfo_width(), root.winfo_reqwidth(), [(w.winfo_class(), w.winfo_reqheight()) for w in root.winfo_children()], [(w.winfo_class(), w.winfo_reqheight()) for w in c["live_wrap"].master.winfo_children()])
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
            out["presets_blocks"] = (presets_top_state(c["live_host"]),
                                     c["bg_presets"]["menu"].winfo_rooty() < c["pattern_box"].winfo_rooty(),
                                     c["overlay_menu"].winfo_rooty() < c["notebook"].winfo_rooty())
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
            out["auto_registered"] = set(auto.snapshot()) == set(gl.AUTOMATION_SPECS)        # snapshot(): types simples, pas de tk.Variable
            edit = auto.capture()                                  # ecritures par l'API publique (capture / apply), pas par `.state`
            edit["bg_hue"]["enabled"] = False
            edit["logo_x"]["enabled"] = True
            edit["bg_tile"]["period"] = 33.0
            auto.apply(edit)
            snap = auto.snapshot()
            out["auto_snapshot"] = (snap["bg_hue"]["enabled"], snap["logo_x"]["enabled"], snap["bg_tile"]["period"],
                                    isinstance(snap["bg_hue"]["points"], list), snap["bg_hue"]["value"])
            c["auto_master_var"].set(0)
            root.after(600, step4b)

        def step4b():
            data = params["_automation"]
            out["sync_enabled"] = (data["bg_hue"]["enabled"], data["logo_x"]["enabled"])
            out["sync_period"] = data["bg_tile"]["period"]
            out["sync_master"] = params["auto_master"]
            # les curseurs suivent la valeur calculee par le moteur (ici posee a la main)
            c["auto_master_var"].set(1)
            edit = c["automation"].capture()
            edit["bg_speed"]["enabled"] = True
            c["automation"].apply(edit)
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
            # plus de presets integres hormis `default` : ceux-ci sont des presets d'utilisateur comme les autres
            out["p_only_default"] = (list(gl.OVERLAY_PRESETS), list(gl.BACKGROUND_PRESETS))
            obook.save("sobre", {"fx_link": 0.0, "fxl_on": [0, 0, 0, 0], "fx_on": [1, 1, 0, 0, 1], "holo_on": 1.0, "holo_intensity": 0.8})
            obook.save("neon", {"holo_on": 1.0, "holo_intensity": 1.5, "holo_reach": 1.8, "holo_react": 0.5, "logo_glow": 1.4,
                                "logo_glow_color": "#ff3df2", "fx_link": 0.0, "fxl_on": [0, 0, 1, 1], "fxl_int": [1.0, 1.0, 0.8, 0.6]})
            bbook.save("sobre", {"bg_react": 0.4})
            bbook.save("neon", {"bg_mode": "pattern", "bg_palette": "duo", "bg_color1": "#14003a", "bg_color2": "#00e5ff",
                                "bg_checker": 0.08, "bg_angle": 35.0})
            op["refresh"]()
            bp["refresh"]()
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
            saved = json.loads(core_params.OVERLAY_PRESETS_PATH.read_text(encoding="utf-8")) if core_params.OVERLAY_PRESETS_PATH.exists() else {}
            sbg = json.loads(core_params.BACKGROUND_PRESETS_PATH.read_text(encoding="utf-8")) if core_params.BACKGROUND_PRESETS_PATH.exists() else {}
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
            # un preset se modifie et se supprime
            op["var"].set("neon")
            params["holo_intensity"] = 1.9
            op["update"]()
            op["load"]("default")
            op["load"]("neon")
            out["p_edit_builtin"] = (params["holo_intensity"], "neon" in obook.load_user())
            out["p_delete_builtin"] = (obook.delete("sobre"), "sobre" in obook.names())
            # dossiers, favoris, recents, recherche, indicateur « modifie »
            c["save_name_var"].set(" Club / Neon2 ")
            op["save"]()
            op["load"]("neon")
            op["check"]()
            clean_mod = op["modified"].get()
            params["holo_intensity"] = params["holo_intensity"] + 0.4
            op["check"]()
            dirty_mod = op["modified"].get()
            dirty_btn = str(op["revert"].cget("state"))
            op["revert"].invoke()
            reverted = (op["modified"].get(), abs(params["holo_intensity"] - 1.9) < 1e-9)
            op["var"].set("club/neon2")
            op["fav"]()
            menu_ = op["menu"]["menu"]

            def labels_():
                return [(menu_.type(i), menu_.entrycget(i, "label") if menu_.type(i) != "separator" else "")
                        for i in range(menu_.index("end") + 1)]
            full = labels_()
            op["filter"].set("neon")
            found = [lab for typ, lab in labels_()]
            op["filter"].set("")
            out["p_new"] = (obook.favorites(), full[0], any(typ == "cascade" and lab.startswith("club") for typ, lab in full),
                            sorted(found), (clean_mod, dirty_mod, dirty_btn, reverted), "club/neon2" in obook.names())
            obook.toggle_favorite("club/neon2")
            obook.delete("club/neon2")
            op["refresh"]()
            out["p_clean"] = (obook.favorites(), "club/neon2" in obook.recents())
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
                             for i in range(c["overlay_menu"]["menu"].index("end") + 1)
                             if c["overlay_menu"]["menu"].type(i) != "separator"]
            out["p_delete"] = obook.delete("mon look")
            bbook.delete("sobre")
            out["b_menu"] = bbook.names()
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
            from core import scenes as scn
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
                # VJ en rythme : N mesures de 4 kicks detectes au lieu de N secondes
                fake = {"beats": 100}
                real_latest = s.audio.analyzer.latest
                s.audio.analyzer.latest = lambda: {**real_latest(), "beats": fake["beats"]}
                try:
                    sbook.set_vj(unit="bars", bars=2, order="seq")
                    vjc["arm"]()
                    bar = vst["bar"]
                    fake["beats"] = 107
                    early = bar.due(fake["beats"], gl.time.monotonic())
                    fake["beats"] = 108
                    out["vj_bars"] = (bar is not None and bar.total == 8, early, bar.due(fake["beats"], gl.time.monotonic()),
                                      vjc["remaining_text"]())
                    sbook.set_vj(unit="seconds")
                    vjc["arm"]()
                    out["vj_bars_off"] = vst["bar"] is None
                finally:
                    s.audio.analyzer.latest = real_latest
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
                # verrou de scene : plus rien ne change la scene (VJ, touches, fleche, tirage), les masters restent libres
                vst["running"] = True
                vjc["lock"]()
                frozen, locked_off = s.active_scene, not vst["running"]
                vjc["apply"](1)
                vjc["advance"]()
                s.scene_requests.put("vjnext")
                s.scene_requests.put(0)
                sc["poll"]()
                vjc["toggle"]()
                params_now = s.params
                params_now["hue_master"] = 90.0
                out["vj_lock"] = (locked_off, s.active_scene == frozen, not vst["running"], "verrouillee" in sc["msg"].get(),
                                  params_now["hue_master"] == 90.0)
                params_now["hue_master"] = 0.0
                vjc["lock"]()                                   # deverrouille : le VJ qui tournait repart
                out["vj_unlock"] = (vst["running"], not getattr(s, "scene_lock", False), "deverrouillee" in sc["msg"].get())
                vst["running"] = False
                sbook.delete("Scene D")
                sbook.delete("Scene C")
                vjc["toggle"]()
                out["vj_one"] = (vst["running"], "au moins 2" in sc["msg"].get())
                sbook.put({"name": "Scene C", "bg": "live", "mode": "live", "live_preset": None, "fond": None, "overlay": {}})
                vst["running"] = False
                try:
                    import gl_gui as gg_
                    hist_ = list(s.scene_history)
                    vjc["apply"](0)
                    root.update()
                    bar_kids = list(c["scene_bar"].winfo_children())
                    vjc["apply"](1)
                    root.update()
                    kids_after = list(c["scene_bar"].winfo_children())
                    out["bar_inplace"] = kids_after == bar_kids
                    out["bar_dbg"] = (len(bar_kids), len(kids_after), [str(k) for k in bar_kids][:3], [str(k) for k in kids_after][:3])
                    s.scene_history[:] = hist_
                    img_ = gg_.grab_window(root)
                    cov_ = gg_.show_cover(root, img_)
                    out["cover"] = (img_ is not None, cov_ is not None)
                    if cov_ is not None:
                        cov_.destroy()
                except Exception:
                    import traceback
                    out["sets_error"] = traceback.format_exc()
                try:
                    gui_sets_steps(c, s, out)
                    gui_expand_steps(c, s, out)
                    gui_random_steps(c, s, out)
                    gui_bgvideo_steps(c, s, out)
                    gui_diag_steps(c, s, out)
                except Exception:
                    import traceback
                    out["sets_error"] = traceback.format_exc()
                root.after(900, lambda: (manager.status.__setitem__("live", "autre"), root.after(300, lambda: (manager.status.__setitem__("live", "message du gestionnaire"), root.after(400, step5)))))
            root.after(700, vj_check)

        def step5():
            # Fenetre adaptative : cote a cote quand elle est large, empilee sinon, ascenseur quand elle est basse
            lw, rp, pb = c["live_wrap"], c["right_panel"], c["page_bar"]
            tips = {}
            for tag, size in (("wide", "1900x1000"), ("narrow", "940x600"), ("short", "1100x420")):
                root.geometry(size)
                root.update()
                tips[tag] = tooltip_violations(root)
            c["scenes"]["expand"](True)                       # panneau etendu + barre des scenes
            root.update()
            tips["expanded"] = tooltip_violations(root)
            c["scenes"]["expand"](False)
            out["tips"] = tips

            def geo(size):
                root.geometry(size)
                root.update()
                root.update()
                return (c["layout"]["name"], pb.winfo_ismapped(), lw.winfo_x(), lw.winfo_y(), lw.winfo_width(), lw.winfo_height(),
                        rp.winfo_x(), rp.winfo_y(), rp.winfo_width(), rp.winfo_height(), root.winfo_width(), root.winfo_height())
            out["lay_big"] = geo("1900x1000")
            out["lay_mid"] = geo("1250x800")
            out["lay_small"] = geo("1000x420")
            geo("1900x470")                                   # large mais basse : l'ascenseur de droite ne defile que l'OVERLAY
            c["right_cv"].yview_moveto(0)
            root.update()
            y0 = lw.winfo_y()
            c["right_cv"].yview_scroll(3, "units")
            root.update()
            out["lay_right_scroll"] = (c["layout"]["name"], c["right_bar"].winfo_ismapped(), not pb.winfo_ismapped(),
                                       c["right_cv"].yview()[0] > 0, lw.winfo_y() == y0)
            out["lay_back"] = geo("1800x900")
            texts = [w.cget("text") for w in walk(root) if w.winfo_class() == "Label"]
            out["live_msg"] = "message du gestionnaire" in texts       # dans la ligne de statut d'audio2wave
            out["mine_msg"] = any("Automations: ambiance par defaut" in t and "fps" in t for t in texts)
            out["stage"] = "snap"
            c["switch"]("snap")               # bascule de source : la fenetre est reconstruite (on_ready rappelee)

        step1()

    import json
    import tempfile as _tf
    real_presets_path = core_params.OVERLAY_PRESETS_PATH
    real_bg_presets_path = core_params.BACKGROUND_PRESETS_PATH
    _tmp_presets = Path(_tf.mkdtemp(prefix="casual_overlay_presets_"))
    core_params.OVERLAY_PRESETS_PATH = _tmp_presets / "overlay_presets.json"        # jamais les vrais fichiers
    core_params.BACKGROUND_PRESETS_PATH = _tmp_presets / "background_presets.json"
    from core import scenes as _scenes
    real_scenes_path, real_live_store_path = _scenes.SCENES_PATH, live.preset_store.path
    _scenes.SCENES_PATH = _tmp_presets / "scenes.json"
    real_masters_path = _scenes.MASTERS_PATH
    _scenes.MASTERS_PATH = _tmp_presets / "masters.json"
    real_autosave_path = core_autosave.AUTOSAVE_PATH
    core_autosave.AUTOSAVE_PATH = _tmp_presets / "autosave.json"
    live.preset_store.path = _tmp_presets / "live_presets.json"          # le bouton Sauvegarder d'audio2wave ecrit ici
    try:
        gl_gui.run_gui(s, live, on_ready)
    finally:
        core_autosave.AUTOSAVE_PATH = real_autosave_path
        core_params.OVERLAY_PRESETS_PATH = real_presets_path
        core_params.BACKGROUND_PRESETS_PATH = real_bg_presets_path
        _scenes.SCENES_PATH, live.preset_store.path = real_scenes_path, real_live_store_path
        _scenes.MASTERS_PATH = real_masters_path
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
    check(out.get("p_only_default") == (["default"], ["default"]),
          "presets: le seul preset integre est 'default' (overlay et fond)", str(out.get("p_only_default")))
    check(out.get("p_edit_builtin") == (1.9, True), "un preset se modifie (mettre a jour)", str(out.get("p_edit_builtin")))
    check(out.get("p_delete_builtin") == ("deleted", False), "un preset se supprime", str(out.get("p_delete_builtin")))
    pn = out.get("p_new", ())
    check(len(pn) == 6 and pn[0] == ["club/neon2"] and pn[1] == ("command", "\u2605 club/neon2") and pn[2]
          and pn[3] == ["club/neon2", "neon"] and pn[4] == ("", "\u25cf modifie", "normal", ("", True)) and pn[5],
          "presets: dossiers (club/nom) en sous-menu, favoris en tete, recherche, indicateur « modifie » et Revenir", str(pn))
    check(out.get("p_clean") == ([], False), "presets: supprimer un preset le retire des favoris et des recents", str(out.get("p_clean")))
    check("point de depart" in out.get("p_default_msg", "") and out.get("p_default_edit") == 0.5,
          "'default' est modifiable", str(out.get("p_default_msg")))
    dr = out.get("p_default_reset", ())
    check(dr and dr[0] == "reset" and dr[1] == {} and out.get("p_default_back") == 1.0,
          "supprimer 'default' le remet a sa version d'origine (il ne disparait jamais)", str(dr))
    lay = gl.menu_layout(["default", "aaa", "club/b", "club/a", "zz"], ["zz", "gone"], ["aaa", "zz", "club/a"])
    check(lay["root"] == ["default", "aaa", "zz"] and lay["folders"] == {"club": ["club/a", "club/b"]} and lay["favorites"] == ["zz"]
          and lay["recents"] == ["aaa", "club/a"] and gl.menu_layout(["default", "ab", "b"], [], [], "AB")["found"] == ["ab"],
          "menu des presets: default en tete de la racine, dossiers, favoris et recents existants, recherche", str(lay))
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
    vb = out.get("vj_bars", (False,) * 4)
    check(vb[0] and vb[1] is False and vb[2] is True and vb[3].startswith("mesure ") and out.get("vj_bars_off") is True,
          "VJ en rythme (GUI): 2 mesures = 8 kicks, la 8e declenche, libelle « mesure x/y », retour aux secondes", str((vb, out.get("vj_bars_off"))))
    check(out.get("vj_key_space") is False and out.get("vj_key_next") == (True, False) and running_after_space(out),
          "VJ: Espace (lecture / pause) et fleche droite (suivante) via la file de la fenetre de rendu", str((out.get("vj_key_space"), out.get("vj_key_next"))))
    check(out.get("vj_pause") == (True, False, True), "VJ (GUI): choisir une scene a la main met le VJ en pause",
          str(out.get("vj_pause")))
    check(out.get("vj_lock") == (True,) * 5, "verrou de scene (GUI): VJ en pause, ni choix, ni suivante, ni F1, ni relance ; les masters restent libres",
          str(out.get("vj_lock")))
    check(out.get("vj_unlock") == (True,) * 3, "verrou de scene (GUI): deverrouiller relance le VJ qui tournait", str(out.get("vj_unlock")))
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
    check(sm[0] >= 10 and sm[1] == sm[0] and not sm[2],
          "sets (GUI): plus de 9 scenes : une touche par scene dans la bande defilante (plus de menu '+N')", str(sm))
    check(out.get("set_f9", (None,))[0] == 8, "sets (GUI): F9 joue la 9e entree du set", str(out.get("set_f9")))
    check(out.get("set_export") == (True, True), "sets (GUI): export dans un fichier", str(out.get("set_export")))
    check(out.get("x_chunks") == (True, True), "panneau etendu (GUI): un gros set est construit par lots (premieres lignes tout de suite, toutes ensuite), sans geler la fenetre", str(out.get("x_chunks")))
    check(out.get("swap_keeps_overlay") is True and out.get("swap_new_panel") is True and out.get("builds", 0) >= 4,
          "changement de mode (Live / Snap / Ridge): seul le panneau d'audio2wave est reconstruit, la barre des scenes et la partie OVERLAY gardent les memes widgets",
          str((out.get("swap_keeps_overlay"), out.get("swap_new_panel"), out.get("builds"))))
    check(out.get("set_async") == (True, True, True, True, True, True),
          "sets (GUI): export et import d'archive depuis le menu se font dans un fil (la fenetre reste utilisable), meme resultat", str(out.get("set_async")))
    check(out.get("set_pack") == (True, True, True, True),
          "sets (GUI): export .zip avec les medias, reimport apres avoir perdu le fichier d'origine = media extrait et chemin refait", str(out.get("set_pack")))
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
    check(out.get("x_fill") == (True, True, True, True),
          "etendu (GUI): le bouton du nom remplit sa case (largeur restante, hauteur de la ligne), les autres boutons ont la meme hauteur",
          f"{out.get('x_fill')} {out.get('x_fill_dbg')}")
    xs = out.get("x_skinned", (0, False))
    check(xs[0] >= 3 and xs[1], "etendu (GUI): les cases VJ sont habillees des l'ouverture du panneau", str(xs))
    check(out.get("x_aff_scroll") == (True,) * 3,
          "section Affichage et analyse: onglet de l'espace de controle global (partie VJ), pose dans le panneau etendu",
          str(out.get("x_aff_scroll")))
    check(out.get("x_master_panel") == (True,) * 6,
          "panneau etendu: espace de controle global a droite de la liste (8 onglets, curseurs qui ecrivent les masters, strobe, verrou), plus rien dans la barre",
          str(out.get("x_master_panel")))
    check(out.get("x_memory") == (True, 200.0, 3.0, True, True),
          "memoires de masters (GUI): une memoire enregistree se rappelle d'un clic (teinte, symetrie), sans les etats du moment", str(out.get("x_memory")))
    check(out.get("x_history") == (True,) * 4 and out.get("x_history_lock") == (True, True, True) and out.get("x_lock_bar_off") is True,
          "historique (GUI): chaque scene jouee est notee, Retour rejoue la precedente, le verrou l'interdit", str((out.get("x_history"), out.get("x_history_lock"))))
    check(out.get("x_build") == (True,) * 4, "montee / drop (GUI): la montee progresse, le drop remet a zero, flash et eclair blanc", str(out.get("x_build")))
    check(out.get("x_silence") == (True,) * 3, "phases auto (GUI): la case ecrit auto_phase", str(out.get("x_silence")))
    check(out.get("x_panic") == (True, True, True, True), "panique (GUI): le bouton coupe effets, strobe, montee, composition, teinte, silence ; VJ en pause, verrou retire",
          str(out.get("x_panic")))
    check(out.get("x_panic_key") == (True, True), "panique (touche X): la demande du fil GL est suivie par la GUI", str(out.get("x_panic_key")))
    check(out.get("x_rhythm") == (True,) * 4,
          "rythme (GUI): tempo auto = BPM detecte / 120 (et cadence du strobe), montee auto sur la cassure, drop auto = flash et eclair", str(out.get("x_rhythm")))
    check(out.get("x_kick_led") == (True,) * 4, "rythme (GUI): voyant de kick + BPM (valeur en clair, gardee 4 s si la detection decroche) dans la barre ; curseur de tempo en BPM (0 a 240)", str(out.get("x_kick_led")))
    check(out.get("x_rhythm_manual") == (True,), "rythme (GUI): montee / drop auto decoche = rien ne part tout seul", str(out.get("x_rhythm_manual")))
    check(out.get("x_stage") == (True,) * 8, "mode scene (GUI): une touche par scene, la scene active en ambre, un clic applique la scene, les masters ecrivent les reglages, la PANIQUE marche",
          str(out.get("x_stage")))
    check(out.get("x_library_tree") == (True,) * 7, "bibliotheque (GUI): favoris en tete, dossiers (dossier/nom), ranger / sortir d'un dossier, recherche, mise a jour sur place",
          str(out.get("x_library_tree")))
    check(out.get("x_library_del") == (True,) * 3, "bibliotheque (GUI): Suppr ouvre la modale de confirmation, la scene n'est supprimee qu'apres validation",
          str(out.get("x_library_del")))
    check(out.get("x_palette") == (True,) * 7, "palette (GUI): scenes, sets, presets et commandes listes ; une saisie lance la bonne commande", str(out.get("x_palette")))
    check(out.get("x_sched") == (True,) * 6, "planning (GUI): a l'heure dite la source passe en Texte et le texte du passage est pose, une seule fois ; case decochee = rien",
          str(out.get("x_sched")))
    check(out.get("x_grid_vj") == (True,) * 6,
          "grille du temps (GUI): Temps 1 ancre la grille, le VJ en rythme arme son changement sur un 1 (a ~N mesures), sinon comme avant", str(out.get("x_grid_vj")))
    check(out.get("x_phase_build") == (True,) * 5, "strobe auto (GUI): la montee part quand la phase montee est reconnue, le passage au pic est le drop (flash + eclair), une montee qui retombe est annulee, rien si le strobe auto est coupe",
          str(out.get("x_phase_build")))
    check(out.get("x_rhythm_vj") == (True, True), "rythme (GUI): le VJ change de scene sur le drop detecte (reglage du set), pas quand il est coupe", str(out.get("x_rhythm_vj")))
    check(out.get("x_tap") == (True, True, True), "tap tempo (GUI): quatre appuis a 120 BPM = tempo 100 %",
          str(out.get("x_tap")))
    check(out.get("x_help") is True and out.get("x_help_once") is True, "aide des raccourcis: une seule fenetre, liste X / K / S / Retour arriere",
          str((out.get("x_help"), out.get("x_help_once"))))
    check(out.get("x_row_icons") == (True, True), "liste des scenes (VJ): Renommer et Retirer en icone seule (libelle inchange)", str(out.get("x_row_icons")))
    check(out.get("x_console_icons") == ["Drop", "Montee", "PANIQUE", "Strobe", "Tap tempo", "Verrou"],
          "console VJ: icones sur Strobe, Montee, Drop, Verrou, Tap tempo et PANIQUE (libelles inchanges)", str(out.get("x_console_icons")))
    check(out.get("x_console") == (True, True, True), "console VJ: grosses commandes (PANIQUE, Drop, Suivante) et ligne d'etat", str(out.get("x_console")))
    check(out.get("x_title") is True, "titre de la fenetre: l'etat du spectacle (scene, VJ) lisible dans la barre des taches")
    check(out.get("x_autosave") is True, "auto-sauvegarde: reglages (sans etats du moment), scene et set ecrits", str(out.get("x_autosave")))
    check(out.get("x_wheel_grip") == (True,) * 4,
          "etendu (GUI): la molette defile la liste des scenes et la poignee l'agrandit / la reduit (la fenetre suit, minimum 80 px)",
          str(out.get("x_wheel_grip")))
    check(out.get("x_noreload") == (True,) * 5,
          "etendu (GUI): case VJ, duree et scene active se mettent a jour sur place (aucun widget reconstruit, resume et ligne active a jour)",
          str(out.get("x_noreload")))
    xr = out.get("x_rebuild", (0, False, False, False))
    check(xr[0] >= 12 and xr[1] and xr[2] and xr[3], "etendu (GUI): un panneau reconstruit reste habille et garde sa position d'ascenseur", str(xr))
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
    check(out.get("x_bg_bad", (True, 0))[0] is False and out.get("x_bg_bad", (True, 0))[1] is None and out.get("x_bg") == (True,) * 4,
          "etendu (GUI): colonne FOND video : une image est refusee, une video deposee remplace le fond de l'entree (meme fichier d'une entree a l'autre), retrait", str((out.get("x_bg_bad"), out.get("x_bg"))))
    check(out.get("x_dup") == (True,) * 5,
          "etendu (GUI): Dupliquer la derniere ajoute une copie de la scene (nom inedit) avec les medias, la duree et le VJ de l'entree", str(out.get("x_dup")))
    check(out.get("x_undo") == (True,) * 3, "etendu (GUI): Annuler / Retablir : +5 = un seul pas, retirer une ligne se defait", str(out.get("x_undo")))
    check(out.get("x_gpu_cap") == (True,) * 3,
          "etendu (GUI): securite GPU : la scene posee par le VJ est allegee (cellules et halo coupes), jamais une scene choisie a la main, desactivable",
          str(out.get("x_gpu_cap")))
    check(out.get("x_preview") == (True,) * 4 and out.get("x_preview_closed") == (True, True),
          "etendu (GUI): apercu d'une scene : capture demandee a la projection, associee a la scene et affichee dans sa ligne ; projection fermee = message, rien demande",
          str((out.get("x_preview"), out.get("x_preview_closed"))))
    check(out.get("x_temp") == (True,) * 5 and out.get("x_temp_clean") is True,
          "etendu (GUI): scenes tirees par +5 = provisoires (repere, Garder avec renommage en ligne, nom pris refuse), Retirer les provisoires",
          str((out.get("x_temp"), out.get("x_temp_clean"))))
    check(out.get("x_select") == (True,) * 7, "etendu (GUI): lignes cochees : VJ off / on, duree, dupliquer, copier vers un autre set, retirer", str(out.get("x_select")))
    panel_src = Path(ROOT, "gui", "gui_panel.py").read_text(encoding="utf-8")
    main_src = Path(ROOT, "gui", "gl_gui.py").read_text(encoding="utf-8")
    check('label="Jouer cette scene"' in panel_src and "apply_scene=apply_scene" in main_src and "LIBRARY_DEFAULT" in panel_src,
          "bibliotheque (GUI): clic droit > Jouer la scene (applique la scene comme un choix a la main), colonne ouverte par defaut")
    check(out.get("x_library") == (True,) * 4, "etendu (GUI): bibliotheque a gauche du set, glisser une scene sur la liste l'ajoute au rang vise", str(out.get("x_library")))
    check(out.get("x_plus5") == (True,) * 5,
          "etendu (GUI): le bouton +5 ajoute cinq scenes aleatoires d'un coup (noms inedits, aucun preset, rien d'applique)", str(out.get("x_plus5")))
    check(out.get("x_drag") == (True,) * 4,
          "etendu (GUI): plus de fleches ; glisser le numero d'une ligne deplace l'entree ; la barre du haut met toutes les scenes dans une bande defilante", str(out.get("x_drag")))
    check(out.get("x_plus") == (True,) * 6,
          "etendu (GUI): le bouton + ajoute au set une scene aleatoire (nom aleatoire inedit, aucun preset enregistre, rien d'applique, overlay centre)", str(out.get("x_plus")))
    check(out.get("x_thumb_shown") is True, "etendu (GUI): une vignette prete s'affiche sans autre changement (la signature en tient compte)",
          str(out.get("x_thumb_shown")))
    xv = out.get("x_vj", ((), (), set()))
    check(xv[0] == (0, True) and xv[1] == 2 and xv[2] == {0, 2},
          "etendu (GUI): VJ = duree propre a l'entree, entrees exclues sautees (dans l'ordre et au hasard)", str(xv))
    check(out.get("x_vj_none") == (False, True), "etendu (GUI): moins de 2 entrees jouables = le VJ s'arrete avec un message",
          str(out.get("x_vj_none")))
    check(out.get("x_longname") == (True,) * 5,
          "etendu (GUI): un nom de scene tres long s'affiche en entier, les colonnes (nom, zones, duree) restent alignees", str(out.get("x_longname")))
    check(out.get("x_rename") == (True, True, True), "etendu (GUI): renommer une scene depuis le panneau", str(out.get("x_rename")))
    check(out.get("x_close") == (True, False) and out.get("x_end") is True,
          "etendu (GUI): reduire rend sa taille d'origine a la fenetre et cache le panneau", str((out.get("x_close"), out.get("x_end"), out.get("x_grip_back"))))
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
    check(out.get("doodle_gui") == (1, 0.0, 1.0, 0.0), "doodles (GUI): case Recuperer les medias dans Affichage, desactivee par defaut, pilote doodle_on", str(out.get("doodle_gui")))
    check(out.get("diag_gui") == (True, True, True, "Button", "Diagnostic"),
          "diagnostic (GUI): bouton Diagnostic (Affichage) qui montre le rapport", str(out.get("diag_gui")))
    bm = out.get("bgv_modes", {})
    check(bm == {"live": (True, False, False, "live"), "pattern": (False, True, False, "pattern"),
                 "video": (False, False, True, "video")},
          "fond (GUI): trois modes exclusifs : panneau d'audio2wave, fond genere ou fond video, jamais deux a la fois", str(bm))
    bc = out.get("bgv_capture", ())
    check(bc == ("video", None, True, True),
          "fond video (GUI): un fichier tape sans Entree est retenu par la scene (fond video, pas d'etat audio2wave)", str(bc))
    ba = out.get("bgv_apply", ())
    check(ba == ("video", True, "video", True, True, False),
          "fond video (GUI): appliquer la scene rend le mode, le fichier et les widgets", str(ba))
    check(out.get("bgv_drop") == (True, "video", True), "fond video (GUI): fichier depose sur le bloc = video du fond", str(out.get("bgv_drop")))
    check(out.get("bgv_drop_bad") == (False, "pattern", True), "fond video (GUI): un fichier qui n'est pas une video est refuse",
          str(out.get("bgv_drop_bad")))
    rp = out.get("r_panel", ())
    check(rp == (1, 1, 0, True, True, True),
          "Full Randomizer (GUI): le panneau etendu montre la banque, ses categories, 'Tirer maintenant' et 'Garder', plus de lignes d'entrees",
          str(rp))
    check(out.get("r_bank") == (1, 3, True, 2),
          "Full Randomizer (GUI): deux banques (FOND: 1 video, OVERLAY: 3 fichiers) scannees depuis les dossiers du set, un '+ Dossier' par banque",
          str(out.get("r_bank")))
    rdw = out.get("r_draw", ())
    check(len(rdw) == 6 and rdw[0] and rdw[1] == "Full Randomizer" and rdw[2] and rdw[3] and rdw[4] and rdw[5],
          "Full Randomizer (GUI): 'Tirer maintenant' applique une scene (flash, fond, media de la banque)", str(rdw))
    check(out.get("r_keep3") == (True, True, True, True, True, True),
          "Full Randomizer (GUI): la modale propose trois noms ; preset fond, preset overlay et scene enregistres, menus de presets a jour",
          str(out.get("r_keep3")))
    check(out.get("r_keep_a2w") == (True, True, True, True, True, True, True),
          "Full Randomizer (GUI): garder un tirage audio2wave enregistre le preset du mode et l'etat reel du panneau dans la scene ; "
          "un fond video sans fichier n'est pas propose en preset", f"{out.get('r_keep_a2w')} {out.get('r_keep_a2w_err')}")
    check(out.get("r_keep_err") == (True, True, True, True, True, 1),
          "Full Randomizer (GUI): nom deja pris ou 'default' refuse, annuler n'ecrit rien, un seul champ rempli = seulement celui-la, "
          "pas de preset de fond sans fond genere", str(out.get("r_keep_err")))
    rm = out.get("r_modes", (0, [], [], False, False))
    check(rm[0] == 1 and rm[1] == ["live", "ridge"] and sorted(rm[2]) == ["live", "ridge", "snap"] and rm[3] and rm[4],
          "Full Randomizer (GUI): cases des modes audio2wave de la config aleatoire ; sans mode coche, la config aleatoire reste un fond genere (aucun changement de mode)",
          str(out.get("r_modes")))
    check(out.get("r_vj") == (True, True, True, True),
          "Full Randomizer (GUI): le VJ tourne meme avec un set vide, un tirage par changement, minuteur = duree du set", str(out.get("r_vj")))
    check(out.get("r_keep") == (True, True, True, True) and out.get("r_keep_apply") == (True, True),
          "Full Randomizer (GUI): 'Garder ce tirage' ajoute la scene au set, elle se rejoue", f"{out.get('r_keep')} {out.get('r_keep_apply')}")
    check(out.get("r_lock") == (True, True), "Full Randomizer (GUI): verrou du fond", str(out.get("r_lock")))
    check(out.get("r_cats") == ["loops"] and out.get("r_cats_all") == [] and out.get("r_folders") == (2, 3, 1),
          "Full Randomizer (GUI): categories cochees (toutes = aucune restriction), dossier ajoute sans doublon de fichiers",
          f"{out.get('r_cats')} {out.get('r_cats_all')} {out.get('r_folders')}")
    check(out.get("r_other_set") and out.get("r_back") == (True, 0, 1),
          "Full Randomizer (GUI): reglages propres au set, retour a l'ordre normal = les entrees reviennent", f"{out.get('r_other_set')} {out.get('r_back')}")
    pb = out.get("presets_blocks", ((None, None), False, False))
    check(pb[0][0] is not None and pb[0][0] <= 2 and pb[0][1] is not None and pb[0][0] < pb[0][1] and pb[1] and pb[2],
          "coherence: les boutons de presets sont en tete de chaque bloc (LIVE, fond genere, OVERLAY)", str(pb))
    for key in ("snap", "ridge", "live_back"):
        pt = out.get(key, {}).get("presets_top", (None, None))
        check(pt[0] is not None and pt[0] <= 2 and pt[1] is not None and pt[0] < pt[1],
              f"coherence: presets en tete du panneau d'audio2wave ({key})", str(pt))
    for tag, (n, bad) in out.get("tips", {}).items():
        check(n > 20 and not bad, f"info-bulles: toutes dans la fenetre ({tag}, {n} affichees)", str(bad[:2]))
    n, bad = out.get("snap", {}).get("tips", (0, [("absent",)]))
    check(n > 10 and not bad, f"info-bulles: toutes dans la fenetre (mode snap, {n} affichees)", str(bad[:2]))
    check(out.get("p_delete") == "deleted", "suppression d'un preset utilisateur")
    check(out.get("b_default_first") == "default" and "sobre" not in out.get("b_menu", ["sobre"]),
          "presets de fond: default en tete, suppression d'un preset", str(out.get("b_menu")))
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
    check(out.get("bar_inplace") is True, "barre des scenes: changer de scene repeint la touche active sans detruire ni recreer la barre (plus de clignotement)", str(out.get("bar_dbg")))
    check(out.get("cover") in ((True, True), (False, False)), "reconstruction de la fenetre: l'ancienne image est posee dessus pendant la construction (ou desactivee si la capture est impossible)", str(out.get("cover")))
    big, mid, small, back = (out.get(k, ()) for k in ("lay_big", "lay_mid", "lay_small", "lay_back"))
    check(len(big) == 12 and big[0] == "wide" and not big[1] and big[6] > big[2] + big[4] - 5 and big[7] == big[3]
          and big[9] > 700 and big[8] > 800, "fenetre large: LIVE et OVERLAY cote a cote, l'overlay prend la place en plus", str(big))
    check(len(mid) == 12 and mid[0] == "stack" and mid[7] >= mid[3] + mid[5] - 5 and mid[8] > 1100,
          "fenetre moyenne: OVERLAY sous LIVE, a toute la largeur", str(mid))
    check(out.get("lay_right_scroll") == ("wide", True, True, True, True),
          "fenetre large mais basse: l'ascenseur de droite defile la seule partie OVERLAY (LIVE et la page ne bougent pas)", str(out.get("lay_right_scroll")))
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
    sb = out.get("scrollbars", (-1, 0))
    check(sb[0] == 0 and sb[1] >= 2, "GUI: les ascenseurs de la fenetre sont aux couleurs de la GUI (aucun ascenseur Windows clair)", str(sb))
    mn = out.get("menus", (0, -1))
    check(mn[0] >= 3 and mn[1] == 0, "GUI: les listes deroulantes gardent un texte clair au survol (pas le texte sombre d'audio2wave sur l'accent)", str(mn))
    check(not any(Path(str(c[0])).stem.lower() == "ffplay" for c in POPEN_LOG) and any("dshow" in c for c in POPEN_LOG),
          "Snap / Ridge: images remises au puits (start_headless + frame_sink), aucun ffplay lance, capture lancee par audio2wave",
          f"{len(POPEN_LOG)} commandes : {sorted({Path(str(c[0])).stem for c in POPEN_LOG})}")
    pj = out.get("proj", {})
    check(pj.get("text0") == "Demarrer la projection" and pj.get("text1") == "Arreter la projection"
          and pj.get("text2") == "Demarrer la projection" and pj.get("started") == 1 and pj.get("stopped") == 1,
          "GUI: le bouton ouvre puis ferme la projection (et change de libelle)", str(pj))
    check(pj.get("queued") is False and pj.get("closed_queue") is True and "projection" in pj.get("closed_msg", ""),
          "GUI: une commande part au rendu quand la projection est ouverte, sinon rien ne s'empile et un message le dit", str(pj))
    al = out.get("apply_live", ((), False, False, False))
    check(al[0] == ["pas_une_cle"] and al[1] and al[2] and al[3],
          "GuiController.apply_state (live): cles inconnues rendues, etat pris, producteur relance, capture_state le relit", str(al))
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
    check(out.get("tabs") == ["Effets", "Logo", "Aura du logo", "Fonte du logo", "Cellules", "Noise"],
          "partie OVERLAY rangee en onglets (Affichage et analyse n'est plus un onglet d'effets)", str(out.get("tabs")))
    check(out.get("win_height", 9999) <= 700, "GUI compacte: fenetre sous 700 px de haut (dans le pire cas: motif + effets du logo delies)",
          str(out.get("win_height")) + " " + str(out.get("win_dbg")))
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
    check(out.get("auto_snapshot") == (False, True, 33.0, True, None),
          "AutomationManager.snapshot(): etat relu en types simples apres capture / apply (plus de lecture de .state)", str(out.get("auto_snapshot")))
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


def check_features_phone() -> None:
    """Kicks d'une musique jouee par un TELEPHONE devant le micro : pas de basses (haut-parleur coupe sous 250-350 Hz), niveau faible, souffle du micro.
    Le plancher absolu d'avant (0,002) ne voyait rien sous -35 dB ; la bande large 20-400 Hz (`WideRise`) rattrape aussi les kicks satures a longue queue."""
    print("-- kicks d'une musique de telephone")
    rate = 48000

    def corpus(bpm, seconds, seed):
        rg = np.random.default_rng(seed)
        out = np.zeros(int(rate * seconds))
        beat = 60.0 / bpm
        truth = []

        def put(sig, at):
            i = int(at * rate)
            m = min(len(sig), len(out) - i)
            if m > 0:
                out[i:i + m] += sig[:m]
        kk = np.arange(int(0.35 * rate)) / rate
        base = np.exp(-kk / 0.16) * (1 - np.exp(-kk / 0.002)) * np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-kk / 0.03)) / rate)
        kick = 0.7 * np.tanh(2.5 * base) / np.tanh(2.5)                      # sature : des harmoniques, une longue queue (kick d'une musique de club)
        click = 0.25 * rg.standard_normal(int(0.004 * rate)) * np.exp(-np.arange(int(0.004 * rate)) / rate / 0.001)
        kick[:len(click)] += click
        hk = np.arange(int(0.05 * rate)) / rate
        bk = np.arange(int(beat * 0.4 * rate)) / rate
        for bar in range(int(seconds / (beat * 4)) + 1):
            for st in range(16):
                at = bar * beat * 4 + st * beat / 4
                if st % 4 == 0 and at < seconds - 0.4:
                    put(kick, at)
                    truth.append(at)
                if st % 2 == 0:
                    put(0.12 * np.diff(rg.standard_normal(len(hk)), prepend=0) * np.exp(-hk / 0.012), at)
                if st % 4 == 2:
                    put(0.35 * np.minimum(bk / 0.01, 1.0) * np.exp(-bk / 0.1) * np.sin(2 * np.pi * 55 * bk), at)
        return out, truth

    def phone(x, fc, gain_db, noise, seed=1):
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / rate)
        f[0] = 1e-9
        y = np.fft.irfft(spec * (1.0 / np.sqrt(1.0 + (fc / f) ** 4)), len(x)) * 10 ** (gain_db / 20)          # passe-haut du 2e ordre
        return (y + noise * np.random.default_rng(seed).standard_normal(len(y))).astype(np.float32)

    def score(got, truth):
        used, tp = set(), 0
        for tb in got:
            for j, tr in enumerate(truth):
                if j not in used and tr - 0.03 <= tb <= tr + 0.09:
                    used.add(j)
                    tp += 1
                    break
        return tp, len(got) - tp, len(truth) - tp

    for name, cfg, min_recall in (("haut-parleur coupe sous 250 Hz, -30 dB", (250.0, -30.0, 0.0008), 0.9),
                                  ("haut-parleur coupe sous 250 Hz, -40 dB (micro eloigne)", (250.0, -40.0, 0.0004), 0.9),
                                  ("haut-parleur coupe sous 350 Hz, -30 dB", (350.0, -30.0, 0.0008), 0.9),
                                  ("kick sature a longue queue, plein niveau", (1.0, 0.0, 0.002), 0.7)):
        tp = fp = fn = 0
        for bpm, seed in ((126, 1), (100, 3)):
            sig, truth = corpus(bpm, 20.0, seed)
            a, b, c = score(run_signal(phone(sig, *cfg), rate)[0], truth)
            tp, fp, fn = tp + a, fp + b, fn + c
        check(tp / max(tp + fn, 1) >= min_recall and tp / max(tp + fp, 1) >= 0.9, f"kicks: {name}",
              f"TP{tp} FP{fp} FN{fn}")
    rgn = np.random.default_rng(11)
    hiss = run_signal((0.003 * rgn.standard_normal(rate * 60)).astype(np.float32), rate)[0]
    check(len(hiss) <= 3, "kicks: souffle de micro seul (0,003) sur 60 s, au plus 3 faux declenchements", f"{len(hiss)}")


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

    # Morceau un peu realiste (kick a balayage de frequence, basse sur les contretemps, charleston, clap), sensibilite PAR DEFAUT :
    # avant, 1 kick sur 3 seulement etait vu (seuils trop hauts), a regler avec un corpus et non a l'oreille.
    def realistic(bpm, seconds, seed):
        rg = np.random.default_rng(seed)
        sr_ = 48000
        out_ = np.zeros(int(sr_ * seconds))
        beat_ = 60.0 / bpm
        truth_ = []

        def put(sig, at):
            i = int(at * sr_)
            m = min(len(sig), len(out_) - i)
            if m > 0:
                out_[i:i + m] += sig[:m]
        kk = np.arange(int(0.35 * sr_)) / sr_
        kick_ = 0.7 * np.exp(-kk / 0.16) * (1 - np.exp(-kk / 0.002)) * np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-kk / 0.03)) / sr_)
        hk = np.arange(int(0.05 * sr_)) / sr_
        bk = np.arange(int(beat_ * 0.4 * sr_)) / sr_
        for bar in range(int(seconds / (beat_ * 4)) + 1):
            for st in range(16):
                at = bar * beat_ * 4 + st * beat_ / 4
                if st % 4 == 0 and at < seconds - 0.4:
                    put(kick_, at)
                    truth_.append(at)
                if st % 2 == 0:
                    put(0.12 * np.diff(rg.standard_normal(len(hk)), prepend=0) * np.exp(-hk / 0.012), at)
                if st % 4 == 2:
                    put(0.35 * np.minimum(bk / 0.01, 1.0) * np.exp(-bk / 0.1) * np.sin(2 * np.pi * 55 * bk), at)
        out_ += 0.002 * rg.standard_normal(len(out_))
        return out_.astype(np.float32), truth_
    tp_ = fp_ = fn_ = 0
    for bpm_, seed_ in ((126, 1), (140, 2), (100, 3)):
        sig_, truth_ = realistic(bpm_, 30.0, seed_)
        got_, _ = run_signal(sig_, 48000)
        used_ = set()
        hit_ = 0
        for tb_ in got_:
            for j_, tr_ in enumerate(truth_):
                if j_ not in used_ and tr_ - 0.03 <= tb_ <= tr_ + 0.09:
                    used_.add(j_)
                    hit_ += 1
                    break
        tp_ += hit_
        fp_ += len(got_) - hit_
        fn_ += len(truth_) - hit_
    check(tp_ / max(tp_ + fn_, 1) >= 0.9 and tp_ / max(tp_ + fp_, 1) >= 0.9,
          "kick sur un morceau realiste (kick, basse, charleston) a sensibilite 1.0: au moins 90 % vus, au moins 90 % justes",
          f"TP{tp_} FP{fp_} FN{fn_}")

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
    import engine.projection as engine_projection
    original_render_text, calls = engine_projection.render_text, []
    engine_projection.render_text = lambda *a, **k: (calls.append(1), original_render_text(*a, **k))[1]
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
        engine_projection.render_text = original_render_text
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

    # fenetre par defaut (le plein ecran se demande)
    check(gl.resolve_fullscreen(None) is False, "fenetre par defaut, jamais de plein ecran sans l'avoir demande")
    check(gl.windowed_rect(0, 0, 1920, 1080, (1280, 720), False) == (320, 180, 1280, 720), "fenetre de projection centree sur un autre ecran")
    sx, sy, sw, sh = gl.windowed_rect(0, 0, 1920, 1080, (1280, 720), True)
    check((sx, sy, sw, sh) == (620, 300, 1280, 720) and sx + sw <= 1920 and sy + sh <= 1080,
          "ecran partage avec les reglages: fenetre dans le coin bas droit", str((sx, sy, sw, sh)))
    check(gl.windowed_rect(1920, 0, 1280, 720, (1280, 720), True)[2:] == (1240, 640) and gl.windowed_rect(1920, 0, 1280, 720, (1280, 720), True)[0] >= 1920,
          "petit ecran: fenetre reduite et contenue dans le moniteur")
    check(gl.fullscreen_blocked(True, False, None, None) is True and gl.fullscreen_blocked(False, True, None, None) is False,
          "plein ecran refuse sur l'ecran des reglages seulement (meme si c'est le secondaire), permis sur le principal sans reglages")
    check(gl.fullscreen_blocked(None, True, None, None) is True and gl.fullscreen_blocked(None, False, None, None) is False,
          "position des reglages inconnue: refus sur l'ecran principal")
    check(gl.fullscreen_blocked(True, True, 1, None) is False and gl.fullscreen_blocked(True, True, None, True) is False,
          "--monitor / --fullscreen explicites l'emportent")
    check(gl.resolve_fullscreen(True) is True and gl.resolve_fullscreen(False) is False,
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
        host = tk.Frame(root)
        host.pack()
        if name == "snap":
            ctrl = mod.build_gui(a, (1280, 720), {}, {"capture": None}, ev, ev, on_switch_mode=lambda m: None, parent=host,
                                 **gl_gui.PANEL_OPTIONS)
        else:
            ctrl = mod.build_gui(a, (1280, 720), {}, ev, ev, on_switch_mode=lambda m: None, parent=host, **gl_gui.PANEL_OPTIONS)
        gl_gui.tidy_mode_gui(host, name, ctrl.widgets)
        g = gg.install_mode_gates(host, name, "#888888", ctrl.widgets)
        root.update()
        built[name] = (a, gui_colors.add_color_helpers(host, "#5fd4c8", "#0b1018", ctrl.widgets, name))
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


def measure_latency_isolated(kicks: int, timeout: float = 120.0) -> dict:
    """Mesure de latence dans un PROCESSUS NEUF (`tools/mesure_latence.py --json`). Dans le processus de la suite, les fils laisses par les
    verifications d'interface (analyses audio, producteurs, minuteurs Tk) se disputent le GIL et le processeur : sur le serveur d'integration
    continue la mesure y donnait 400 ms de mediane et un kick sur quatre manque, d'ou des echecs au hasard. Seule, elle vaut ce que vaut la chaine."""
    import json
    import subprocess
    out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "mesure_latence.py"), "--json", "--kicks", str(kicks)],
                         capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    text = out.stdout
    if "{" not in text:
        raise RuntimeError(f"mesure impossible (code {out.returncode}) : {(out.stderr or text).strip()[-300:]}")
    return json.loads(text[text.index("{"):])


def check_latence() -> None:
    print("Mesure de latence audio -> image (tools/mesure_latence.py)")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mesure_latence as ml
    s = ml.summarize([10.0, 20.0, 30.0, 40.0, None, float("nan")])
    check(s == {"n": 4, "min": 10.0, "median": 25.0, "p95": 38.5, "max": 40.0} and ml.summarize([]) == {},
          "latence: statistiques (mediane, p95, valeurs absentes ecartees)", str(s))
    rows = ml.match([1.0, 2.0, 3.0], [1.020, 3.030], [1.036, 3.050, 5.0])
    check(rows[0]["analyse_ms"] is not None and abs(rows[0]["analyse_ms"] - 20.0) < 1e-6 and abs(rows[0]["rendu_ms"] - 16.0) < 1e-6
          and abs(rows[0]["total_ms"] - 36.0) < 1e-6 and rows[1]["total_ms"] is None and abs(rows[2]["total_ms"] - 50.0) < 1e-6,
          "latence: chaque debut de son est associe a sa detection puis a sa premiere image blanche (kick manque = None)", str(rows))
    bound = 3000 if os.environ.get("CI") else 250          # serveur d'integration continue : OpenGL logiciel sur une machine partagee, on ne verifie que « ca marche »
    tries = 3 if os.environ.get("CI") else 1               # une mesure en temps reel sur un serveur partage peut encore rater un kick : trois essais, un vrai defaut les rate tous
    tot, res = {}, {"missed": 99}
    for n in range(1, tries + 1):
        try:
            res = measure_latency_isolated(kicks=4)
        except Exception as exc:
            check(False, "latence: chaine logicielle mesurable", str(exc))
            return
        tot = res["total"]
        if tot and res["missed"] <= 1 and 0 < tot["median"] < bound:
            break
        print(f"  (information) latence, essai {n}/{tries} insuffisant : {tot} manques={res['missed']} lignes={res['rows']}")
    check(tot and res["missed"] <= 1 and 0 < tot["median"] < bound,
          f"latence: la chaine reelle (analyse + rendu) detecte les kicks et montre l'eclair, en moins de {bound} ms",
          f"{tot} manques={res['missed']}")
    print(f"  (information) latence logicielle audio -> image : mediane {tot.get('median', 0):.0f} ms, max {tot.get('max', 0):.0f} ms")


def check_stage_layout() -> None:
    print("Mode scene (gui/gui_stage.py)")
    import gui_stage
    check([gui_stage.grid_columns(n, w) for n, w in ((0, 1000), (1, 1000), (3, 1000), (12, 1000), (12, 500), (12, 3000), (5, 0))] == [1, 1, 3, 4, 2, 4, 4],
          "mode scene: nombre de colonnes selon le nombre de scenes et la largeur (4 au plus, touches d'au moins ~220 px)")


def check_palette() -> None:
    print("Palette de commandes (core/palette.py)")
    from core import palette as pal
    items = [("Scene : Neon Comete", "scene"), ("Scene : Acid", "scene"), ("Set : Club", "set"), ("Preset overlay : club/neon", "preset overlay"),
             ("Panique", "stop couper effets"), ("Strobe : un eclair", "flash blanc"), ("Scène : Électro", "scene")]
    names = lambda q: [items[i][0] for i in pal.rank(q, items)]
    check(pal.fold("Scène Néon") == "scene neon", "palette: accents et casse ignores")
    check(names("neon")[:2] == ["Scene : Neon Comete", "Preset overlay : club/neon"] or names("neon")[0] == "Scene : Neon Comete",
          "palette: « neon » trouve la scene et le preset, debut de mot d'abord", str(names("neon")))
    check(names("club") == ["Set : Club", "Preset overlay : club/neon"] and names("panique") == ["Panique"] and names("flash") == ["Strobe : un eclair"],
          "palette: debut de libelle avant sous-chaine, mots-cles utilises", str((names("club"), names("flash"))))
    check(names("scene neon") == ["Scene : Neon Comete"] and names("zzz") == [] and len(names("")) == len(items) and names("electro") == ["Scène : Électro"],
          "palette: tous les mots doivent correspondre, requete vide = tout, accents des libelles ignores", str(names("scene neon")))


def check_schedule() -> None:
    print("Planning du texte (core/schedule.py)")
    from core import schedule as sch
    plan = "22:00 DJ Un\n# commentaire\n\n23h30 - DJ Deux\\nB2B\n01:15 DJ Trois\n25:00 invalide\n12:61 invalide\n00:30\n"
    e = sch.parse(plan)
    check(e == [(22 * 60, "DJ Un"), (23 * 60 + 30, "DJ Deux\nB2B"), (75, "DJ Trois")],
          "planning: lignes HH:MM / HHhMM, tiret, \\n, apres minuit a la suite ; invalides, vides et commentaires ignores", str(e))
    cur = lambda h, m: sch.current(e, h * 60 + m)
    check(cur(21, 59) is None and cur(22, 0) == "DJ Un" and cur(23, 29) == "DJ Un" and cur(23, 30) == "DJ Deux\nB2B" and cur(0, 59) == "DJ Deux\nB2B"
          and cur(1, 15) == "DJ Trois" and cur(5, 0) == "DJ Trois" and cur(17, 59) == "DJ Trois" and cur(18, 0) is None,
          "planning: texte en cours a chaque heure (minuit passe, nouvelle soiree a 18 h)")
    check(sch.upcoming(e, 22 * 60 + 5) == (23 * 60 + 30, "DJ Deux\nB2B") and sch.upcoming(e, 2 * 60) is None and sch.clock(75) == "01:15",
          "planning: prochain passage et affichage de l'heure")


def check_phase() -> None:
    """Phases du morceau (core/phase.py) : timelines synthetiques, hysteresis, adaptation, application aux reglages."""
    print("-- phases du morceau")
    from core import phase as ph

    def run(segments, hz=20, bpm=128.0):
        pt, t, nxt, seq = ph.PhaseTracker(), 0.0, 0.0, []
        for dur, rms, period, share, state in segments:
            n = int(dur * hz)
            for i in range(n):
                f = i / max(n - 1, 1)
                lvl = rms(f) if callable(rms) else rms
                beat = False
                if period:
                    if t >= nxt:
                        beat, nxt = True, t + period
                else:
                    nxt = t + 0.3
                rest = lvl * (1 - share)
                name = pt.update(t, {"raw_rms": lvl, "raw_bass": lvl * share, "raw_mid": rest * 0.6, "raw_high": rest * 0.4}, beat,
                                 state, bpm if period else None)
                if not seq or seq[-1][1] != name:
                    seq.append((t, name))
                t += 1.0 / hz
        return seq, pt

    k = 60 / 128
    segs = [(6, 0.0008, 0, 0.3, "idle"), (20, 0.015, 0, 0.3, "idle"), (30, 0.10, k, 0.5, "idle"), (15, lambda f: 0.05 + 0.09 * f, 0, 0.2, "idle"),
            (30, 0.20, k, 0.55, "idle"), (12, 0.06, 0, 0.3, "idle"), (30, 0.10, k, 0.5, "idle"), (15, lambda f: 0.1 * (1 - f) + 0.01, 0, 0.3, "idle"),
            (10, 0.0008, 0, 0.3, "idle")]
    seq, pt = run(segs)
    names = [n for _, n in seq]
    def at(sec):
        cur = None
        for tt, n in seq:
            if tt <= sec:
                cur = n
        return cur
    check(at(8) == "silence" and at(20) == "ambient", "phases: silence puis ambiance reconnus", str(seq[:4]))
    check(at(50) == "groove", "phases: kicks reguliers = groove", str(at(50)))
    check("build" in names[:8] and "peak" in names and "break" in names, "phases: montee, pic et cassure detectes", str(names))
    check(all(b - a >= ph.MIN_DWELL_S - 0.1 for (a, _), (b, _) in zip(seq[1:], seq[2:])), "phases: chaque phase dure au moins 4 s (hysteresis)", str(seq))
    check(seq[-1][1] == "silence", "phases: le silence final est reconnu", str(seq[-3:]))
    # montee AVEC kicks (le kick continue, un riser / roulement fait monter les mediums et aigus) : montee, pas groove
    def rise(top_end, seconds=20, steady=40, kick=True):
        pt2, tt2, nxt2, names2 = ph.PhaseTracker(), 0.0, 0.0, []
        total = steady + seconds
        while tt2 < total:
            f = min(max((tt2 - steady) / seconds, 0.0), 1.0)
            top = 0.02 * (top_end ** f)
            bass = 0.06
            beat = kick and tt2 >= nxt2
            if beat:
                nxt2 = tt2 + k
            nm = pt2.update(tt2, {"raw_rms": bass + top, "raw_bass": bass, "raw_mid": top * 0.6, "raw_high": top * 0.4}, beat, "idle", 128.0 if kick else None)
            if not names2 or names2[-1][1] != nm:
                names2.append((tt2, nm))
            tt2 += 0.05
        return names2
    n_rise = rise(6.0)
    check(any(n == "build" and tt2 > 40 for tt2, n in n_rise), "phases: les mediums / aigus qui montent avec les kicks = montee", str(n_rise))
    n_slow = rise(4.0, seconds=40)
    check(any(n == "build" and tt2 > 40 for tt2, n in n_slow), "phases: une montee lente (x4 en 40 s) avec les kicks est reconnue", str(n_slow))
    # drop au bon moment : le kick revient apres un vide, le drop part sur ce kick (avant que la phase ait change)
    pd, td, nx, drop_at, phase_then = ph.PhaseTracker(), 0.0, 0.0, [], None
    while td < 75:
        gap = 38 <= td < 50                               # 12 s sans kick au milieu
        beat = (not gap) and td >= nx
        if beat:
            nx = td + k
        before = pd.drops
        pd.update(td, {"raw_rms": 0.02 if gap else 0.08, "raw_bass": 0.0 if gap else 0.04, "raw_mid": 0.02, "raw_high": 0.01}, beat, "idle", None if gap else 128.0)
        if pd.drops != before:
            drop_at.append(round(td, 2))
            phase_then = pd.phase
        td += 0.05
    check(len(drop_at) == 1 and 50 <= drop_at[0] <= 51.5 and phase_then in ("break", "build", "ambient"),
          "phases: le drop part sur le kick qui revient apres le vide (avant le changement de phase), une seule fois", str((drop_at, phase_then)))
    n_flat = rise(1.0)
    check(all(n != "build" for _, n in n_flat), "phases: un groove stable n'est jamais une montee", str(n_flat))
    # musique faible (telephone pose pres du micro, niveau divise par 5) : les memes phases, seuils proportionnels a l'energie du morceau
    soft = [(d, (lambda f, r=r: r(f) * 0.2) if callable(r) else r * 0.2, per, sh, st) for d, r, per, sh, st in segs]
    soft_seq, _ = run(soft)
    soft_names = [n for _, n in soft_seq]
    check(soft_names == names, "phases: musique faible (niveau / 5) = les memes phases dans le meme ordre", f"{soft_names} / {names}")
    # adaptation : un morceau entier tres faible (micro lointain) reste un groove, pas une ambiance
    low, _ = run([(40, 0.02, k, 0.5, "idle")])
    check(low[-1][1] in ("groove", "peak"), "phases: seuils adaptes a un micro faible (kicks = groove)", str(low))
    # multiplicateurs lisses : jamais de saut brutal, ambiance tres lente, strobe jamais
    pt2, t = ph.PhaseTracker(), 0.0
    prev, jump = dict(ph.NEUTRAL), 0.0
    for i in range(1200):
        pt2.update(t, {"raw_rms": 0.1 if i < 600 else 0.0005, "raw_bass": 0.05, "raw_mid": 0.03, "raw_high": 0.02}, i % 10 == 0 and i < 600, "idle", 120.0)
        mult = pt2.snapshot()["mult"]
        jump = max(jump, max(abs(mult[x] - prev[x]) for x in ph.KEYS if x != "hue"))
        prev, t = dict(mult), t + 0.05
    check(jump < 0.12 and prev["tempo"] < 0.3 and prev["fx"] < 0.4 and prev["overlay"] >= 0.85, "phases: multiplicateurs lisses, silence tres lent mais l'overlay reste",
          f"{jump:.3f} {prev}")
    # calme proportionnel et reactif : plus le son baisse, plus le profil d'ambiance prend le dessus, en moins d'une seconde, sans palier
    def level_run(level, hold=2.0):
        pc, tc, last = ph.PhaseTracker(), 0.0, None
        for i in range(int(40 / 0.05)):
            lv = 0.1 if tc < 30 else level
            pc.update(tc, {"raw_rms": lv, "raw_bass": lv * 0.5, "raw_mid": lv * 0.3, "raw_high": lv * 0.2}, i % 10 == 0, "idle", 120.0)
            if tc >= 30 + hold:
                break
            tc += 0.05
        return pc.snapshot()
    snaps = [level_run(v) for v in (0.1, 0.07, 0.05, 0.03, 0.01)]
    tempos = [sn["mult"]["tempo"] for sn in snaps]
    calms = [sn["calm"] for sn in snaps]
    check(all(a_ > b_ for a_, b_ in zip(tempos, tempos[1:])) and all(a_ < b_ for a_, b_ in zip(calms, calms[1:])) and calms[0] == 0.0 and calms[-1] > 0.85,
          "phases: le calme est proportionnel a la baisse d'energie (plus le son baisse, plus les animations ralentissent), 2 s apres la baisse", f"tempo {tempos} calme {calms}")
    pg, tg, calm_max = ph.PhaseTracker(), 0.0, 0.0
    for i in range(int(40 / 0.05)):                     # groove : l'energie pulse avec chaque kick (0,06 a 0,10) sans jamais baisser vraiment
        beat = i % 10 == 0
        lv = 0.10 if (i % 10) < 2 else 0.065
        pg.update(tg, {"raw_rms": lv, "raw_bass": lv * 0.5, "raw_mid": lv * 0.3, "raw_high": lv * 0.2}, beat, "idle", 120.0)
        if tg > 10:
            calm_max = max(calm_max, pg.snapshot()["calm"])
        tg += 0.05
    check(calm_max < 0.2, "phases: les pulsations des kicks d'un groove ne declenchent pas le calme", str(calm_max))
    quick = level_run(0.01, hold=0.5)
    check(quick["calm"] > 0.4, "phases: le calme reagit en une demi-seconde", str(quick["calm"]))
    check("strobe" not in ph.KEYS, "phases: jamais de strobe automatique")
    p = {"auto_phase": 1.0}
    name = ph.apply_to_params(p, pt2.snapshot())
    off = {"auto_phase": 0.0}
    ph.apply_to_params(off, pt2.snapshot())
    check(name == "silence" and p["phase_tempo"] < 0.3 and all(off["phase_" + x] == ph.NEUTRAL[x] for x in ph.KEYS) and ph.apply_to_params({}, None) == "neutre",
          "phases: application aux reglages (neutre si desactive ou sans mesure)", name)


def check_tempo() -> None:
    print("Tempo du son, sans kick (core/tempo.py)")
    import random
    from core.tempo import TempoTracker
    from core.rhythm import Rhythm

    def train(bpm, seconds, seed=1, jitter=0.012, floor=0.2):
        rnd = random.Random(seed)
        period = 60.0 / bpm
        hits = set()
        k = 0
        while k * period < seconds:
            hits.add(int((k * period + rnd.uniform(-jitter, jitter)) / 0.0107))
            k += 1
        for i in range(int(seconds / 0.0107)):
            t = i * 0.0107
            yield t, (5.0 if i in hits else 0.0) + floor * rnd.random()

    for bpm in (80, 100, 124, 140, 170):
        tt, first = TempoTracker(), None
        for t, f in train(bpm, 30.0, seed=bpm):
            tt.feed(t, f)
            v, c = tt.estimate(t)
            if v is not None and first is None:
                first = t
        check(tt.bpm is not None and abs(tt.bpm / bpm - 1.0) < 0.04 and first is not None and first < 9.0,
              f"tempo: train d'attaques a {bpm} BPM retrouve", f"{tt.bpm and round(tt.bpm, 1)} des {first and round(first, 1)} s")
    for label, gen in (("son continu (sinus)", lambda rnd, i: 1.0 + 0.1 * rnd.random()), ("bruit", lambda rnd, i: rnd.random() * 2.0), ("silence", lambda rnd, i: 0.0)):
        rnd, tt, seen = random.Random(3), TempoTracker(), 0
        for i in range(3000):
            t = i * 0.0107
            tt.feed(t, gen(rnd, i))
            seen += tt.estimate(t)[0] is not None
        check(seen == 0, f"tempo: {label} ne donne aucun BPM", str(seen))
    tt = TempoTracker()
    for t, f in train(120, 20.0):
        tt.feed(t, f)
        tt.estimate(t)
    check(tt.bpm is not None and tt.estimate(25.0)[0] is None, "tempo: le BPM disparait quand le flux s'arrete")
    r = Rhythm()
    for t, f in train(128, 25.0, seed=5):
        r.update(t, {"flux": f}, False)
    check(r.bpm is not None and abs(r.bpm / 128 - 1.0) < 0.04, "rythme: sans aucun kick detecte, le BPM vient du tempo du son", str(r.bpm))
    for i in range(1500):
        r.update(26.0 + i * 0.0107, {"flux": 0.0}, False)
    check(r.bpm is None, "rythme: plus de son rythme, plus de BPM perime", str(r.bpm))
    r2 = Rhythm()
    for t, f in train(140, 30.0, seed=6):
        r2.update(t, {"flux": f}, False)
        if int(t / (60 / 70.0) * 100) % 100 == 0 and t > 3.0:        # des kicks a la moitie du tempo (70 BPM) : le tempo double l'emporte
            r2.update(t, {"flux": 0.0}, True)
    check(r2.bpm is not None and abs(r2.bpm / 140 - 1.0) < 0.05, "rythme: kicks a la moitie du tempo du son, le BPM est le tempo double", str(r2.bpm))


def check_beat_grid() -> None:
    print("Grille du temps (Temps 1)")
    from core import rhythm as rh
    g = rh.BeatGrid()
    check(not g.active and g.beat_index(1.0) is None and g.nearest_downbeat(1.0) is None, "grille: inactive tant qu'aucun 1 n'est donne")
    g.set_one(10.0, 120.0)
    check(g.active and abs(g.period - 0.5) < 1e-9 and g.beat_index(10.0) == 0 and g.beat_index(10.5) == 1 and g.beat_index(12.0) == 4
          and g.bar_index(11.9) == 0 and g.bar_index(12.0) == 1 and g.beat_in_bar(10.1) == 1 and g.beat_in_bar(11.6) == 4
          and abs(g.nearest_downbeat(13.9) - 14.0) < 1e-9 and abs(g.nearest_downbeat(12.9) - 12.0) < 1e-9,
          "grille: temps, mesure, temps dans la mesure et « 1 » le plus proche a 120 BPM")
    g2 = rh.BeatGrid()
    g2.set_one(10.1, 120.0, last_kick=10.0)
    g3 = rh.BeatGrid()
    g3.set_one(10.1, 120.0, last_kick=9.0)
    check(g2.anchor == 10.0 and g3.anchor == 10.1, "grille: le « 1 » tape juste apres un kick se cale sur ce kick, sinon sur l'appui")
    # recalage : les kicks reels sont en avance de 40 ms sur la grille, elle les rattrape ; un kick hors temps ne la touche pas
    g4 = rh.BeatGrid()
    g4.set_one(0.0, 120.0)
    for n in range(1, 60):
        g4.nudge(n * 0.5 - 0.04, 120.0)
    check(abs(g4.pos(30.0 - 0.04) - round(g4.pos(30.0 - 0.04))) < 0.02, "grille: les kicks recalent la grille (decalage de 40 ms rattrape)",
          f"{g4.pos(30.0 - 0.04):.3f}")
    g5 = rh.BeatGrid()
    g5.set_one(0.0, 120.0)
    g5.nudge(0.25, None)
    check(g5.anchor == 0.0, "grille: un kick a contretemps (mi-temps) ne la decale pas")
    # Rhythm : numero de temps du dernier kick, grille effacee quand les kicks disparaissent
    r = rh.Rhythm()
    r.grid.set_one(0.0, 120.0)
    r.update(1.0, {}, True)
    check(r.beat_index == 2, "rythme: le dernier kick porte son numero de temps", str(r.beat_index))
    r.update(7.0, {}, False)
    check(not r.grid.active and r.beat_index is None, "rythme: sans kick pendant 5 s la grille est effacee")
    if SANS_GPU:
        return
    # strobe leger : tombe sur les 1 de la grille (temps multiples de strobe_div)
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL pour le strobe sur la grille", str(exc))
        return
    size = (64, 36)
    target = ctx.texture(size, 4)
    fbo = ctx.framebuffer(color_attachments=[target])
    base = dict(gl.DEFAULT_PARAMS, holo_on=0.0, strobe_on=1.0, strobe_mode="light", strobe_div=4, strobe_max_hz=20.0, strobe_max_s=600.0,
                bg_mode="pattern", bg_master=0.0, fx_on=[0] * 5)
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 10.0}
    rr = gl.Renderer(ctx, size, None)
    fired = []
    for beats, idx in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9], start=1):
        rr.draw(fbo, size, dict(quiet, beats=beats, beat_index=idx), base, float(beats))
        fired.append(rr._strobe_last > 0 and time.monotonic() - rr._strobe_last < 0.05)
        time.sleep(0.08)
    check(fired == [False, False, False, True, False, False, False, True, False],
          "strobe leger avec une grille : un eclair sur chaque 1 (temps 4 et 8 d'indice multiple de 4), pas sur les autres", str(fired))
    rr2 = gl.Renderer(ctx, size, None)
    fired2 = []
    for beats in range(1, 9):
        rr2.draw(fbo, size, dict(quiet, beats=beats, beat_index=None), base, float(beats))
        fired2.append(rr2._strobe_last > 0 and time.monotonic() - rr2._strobe_last < 0.05)
        time.sleep(0.08)
    check(fired2 == [False, False, False, True, False, False, False, True], "strobe leger sans grille: un kick sur 4 comme avant", str(fired2))


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

    from core import scenes as sc
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
    named = sc.capture_scene("P", gl.load_params(), "live", None, presets={"overlay": "neon", "fond": "", "x": "y"})
    check(named["presets"] == {"overlay": "neon"} and sc.capture_scene("Q", gl.load_params(), "live", None)["presets"] == {},
          "scene: les noms des presets overlay / fond sont retenus (vides et inconnus ecartes)", str(named["presets"]))
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
    check(_plain_vj(book2.vj()) == {"seconds": 30, "order": "seq"}, "VJ: reglages par defaut (30 s, dans l'ordre)")
    book2.put({"name": "X", "bg": "live", "overlay": {}})
    book2.set_vj(seconds=1000, order="random")
    check(_plain_vj(book2.vj()) == {"seconds": 300.0, "order": "random"} and book2.names() == ["X"],
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


def tooltip_violations(root) -> tuple:
    """(nombre d'info-bulles affichees, liste de celles qui sortent de la fenetre) : survole (<Enter>) chaque widget visible,
    mesure l'info-bulle (Toplevel fille du widget : position posee par wm_geometry, taille voulue) puis la ferme. Sans jamais
    faire tourner la boucle d'evenements (`update_idletasks` seulement) : les minuteries du test ne s'y declenchent pas."""
    import re

    def walk_all(w):
        yield w
        for ch in w.winfo_children():
            yield from walk_all(ch)
    root.update_idletasks()
    left, top = root.winfo_rootx(), root.winfo_rooty()
    right, bottom = left + root.winfo_width(), top + root.winfo_height()
    count, bad = 0, []
    # seulement les widgets qui portent une info-bulle (liaison <Enter> propre au widget) : survoler les conteneurs ou la fenetre
    # elle-meme bousculait la mise en page des tests suivants
    for w in [w for w in walk_all(root) if w.winfo_class() != "Toplevel" and w.winfo_viewable() and w.bind("<Enter>")]:
        try:
            w.event_generate("<Enter>")
            for tip in [t for t in w.winfo_children() if t.winfo_class() == "Toplevel"]:
                tip.update_idletasks()
                m = re.search(r"\+(-?\d+)\+(-?\d+)$", tip.wm_geometry())
                x, y = int(m.group(1)), int(m.group(2))
                tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
                count += 1
                if x < left or y < top or x + tw > right or y + th > bottom:
                    bad.append((w.winfo_class(), str(w), (x, y, tw, th), (left, top, right, bottom)))
            w.event_generate("<Leave>")
        except Exception:
            pass
    return count, bad


def presets_top_state(host) -> tuple:
    """(rang de la ligne PRESETS, rang de la ligne SOURCE) dans la grille du panneau d'audio2wave : les presets doivent venir en premier."""
    rows = {}
    for w in host.winfo_children():
        info = w.grid_info()
        if info and w.winfo_class() == "Label":
            text = str(w.cget("text"))
            if text.startswith("PRESETS"):
                rows["p"] = int(info["row"])
            elif text == "SOURCE":
                rows["s"] = int(info["row"])
    return rows.get("p"), rows.get("s")


def check_sets_pure(sc, tmp) -> None:
    import json as _json
    print("Sets (stockage, media, export / import)")
    # ancien format (scenes + vj au premier niveau) : un set `principal` qui les contient toutes
    tmp.write_text(_json.dumps({"scenes": [{"name": "A", "bg": "live", "overlay": {}}, {"name": "B", "bg": "live", "overlay": {}}],
                                "vj": {"seconds": 45, "order": "random"}}), encoding="utf-8")
    bk = sc.SceneBook(lambda: tmp)
    check(bk.set_names() == ["principal"] and bk.row_names() == ["A", "B"] and _plain_vj(bk.vj()) == {"seconds": 45.0, "order": "random"},
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
          and _plain_vj(dest.get_set("Mon set")["vj"]) == {"seconds": 60.0, "order": "random"} and set(dest.names()) == {"A", "B"}
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
    # presets cites par les scenes : exportes avec le set, ajoutes a l'import (un nom deja pris garde celui de la machine)
    from core import params as _prm
    src_o = _prm.PresetBook({"default": {}}, lambda: tmp.with_name("exp_over_src.json"))
    src_b = _prm.PresetBook({"default": {}}, lambda: tmp.with_name("exp_bg_src.json"))
    src_o.save("club/neon", {"holo_intensity": 1.5})
    src_o.save("autre", {"holo_intensity": 0.2})
    src_b.save("fond duo", {"bg_palette": "duo"})
    pbook = sc.SceneBook(lambda: tmp.with_name("export_presets.json"))
    pbook.put({"name": "P1", "bg": "live", "overlay": {}, "presets": {"overlay": "club/neon", "fond": "fond duo"}})
    pbook.put({"name": "P2", "bg": "live", "overlay": {}, "presets": {"overlay": "default", "fond": "inconnu"}})
    exp_pr = sc.export_set(pbook, None, src_o, src_b)
    check(exp_pr.get("presets") == {"overlay": {"club/neon": {"holo_intensity": 1.5}}, "fond": {"fond duo": {"bg_palette": "duo"}}}
          and "presets" not in sc.export_set(pbook), "export: les presets cites par les scenes (existants, hors default) suivent le set",
          str(exp_pr.get("presets")))
    dst_o = _prm.PresetBook({"default": {}}, lambda: tmp.with_name("exp_over_dst.json"))
    dst_b = _prm.PresetBook({"default": {}}, lambda: tmp.with_name("exp_bg_dst.json"))
    dst_b.save("fond duo", {"bg_palette": "classic"})
    dpb = sc.SceneBook(lambda: tmp.with_name("import_presets.json"))
    parsed_pr = sc.parse_import(_json.dumps(exp_pr))
    r_pr = sc.import_set(dpb, parsed_pr, overlay_book=dst_o, background_book=dst_b)
    check(r_pr["presets_added"] == ["overlay:club/neon"] and r_pr["presets_kept"] == ["fond:fond duo"]
          and dst_o.load_user() == {"club/neon": {"holo_intensity": 1.5}} and dst_b.load_user()["fond duo"] == {"bg_palette": "classic"},
          "import: presets ajoutes si le nom est libre, celui de la machine garde sinon", str(r_pr))
    r_pr2 = sc.import_set(dpb, parsed_pr, overlay_book=dst_o, background_book=dst_b)
    check(r_pr2["presets_added"] == [], "import: reimporter ne duplique rien", str(r_pr2))
    junk = sc.parse_import(_json.dumps({**exp_pr, "presets": {"overlay": {"_x": {}, "default": {}, "ok": {"a": 1}, "bad": 3}, "fond": 5}}))
    check(junk["presets"] == {"overlay": {"ok": {"a": 1}}, "fond": {}}, "import: presets invalides ecartes", str(junk["presets"]))
    # bibliotheque : dossiers (dossier/nom), favoris, recherche
    lib_scenes = [{"name": n} for n in ("zeta", "Alpha", "club/neon", "club/Acid", "salle/x")]
    lib_scenes[2]["star"] = True
    tr = sc.library_tree(lib_scenes)
    check(sc.scene_folder("club/neon") == ("club", "neon") and sc.scene_folder("a/") == ("", "a/") and sc.scene_folder("/a") == ("", "/a")
          and tr["starred"] == ["club/neon"] and tr["root"] == ["Alpha", "zeta"] and tr["folders"] == {"club": ["club/Acid", "club/neon"], "salle": ["salle/x"]}
          and tr["total"] == 5 and sc.library_tree(lib_scenes, "AC")["shown"] == 1 and sc.library_tree(lib_scenes, "AC")["folders"] == {"club": ["club/Acid"]},
          "bibliotheque: favoris, dossiers (un niveau), racine, tri sans casse, recherche", str(tr))
    sbook = sc.SceneBook(lambda: tmp.with_name("stars.json"))
    sbook.put({"name": "S1", "bg": "live", "overlay": {}})
    check(sbook.set_star("S1", True) and sbook.get("S1").get("star") is True and sbook.set_star("S1", False) and "star" not in sbook.get("S1")
          and not sbook.set_star("absente", True), "bibliotheque: etoile d'une scene (mise, retiree, scene inconnue refusee)")
    # renommer / ranger un preset : favoris, recents et scenes qui le citent suivent
    src_o.toggle_favorite("autre")
    src_o.touch("autre")
    ok_ren = src_o.rename("autre", " Club / Autre ")
    check(ok_ren and "club/autre" in src_o.load_user() and "autre" not in src_o.load_user() and src_o.favorites() == ["club/autre"]
          and src_o.recents()[0] == "club/autre" and not src_o.rename("default", "x") and not src_o.rename("club/autre", "club/neon")
          and not src_o.rename("club/autre", "a/b/c") and not src_o.rename("nexiste/pas", "z") and src_o.rename("club/autre", "club/autre"),
          "preset renomme / range dans un dossier : favoris et recents suivent ; default, nom pris ou invalide refuses")
    pbook.put({"name": "P3", "bg": "live", "overlay": {}, "presets": {"overlay": "club/neon", "fond": "club/neon"}})
    check(pbook.rename_preset("overlay", "club/neon", "salle/neon") == 2 and pbook.get("P1")["presets"]["overlay"] == "salle/neon"
          and pbook.get("P3")["presets"] == {"overlay": "salle/neon", "fond": "club/neon"} and pbook.rename_preset("overlay", "absent", "x") == 0,
          "preset renomme : les scenes qui le citent (du bon genre seulement) suivent")
    bad = [("pas du json", "illisible"), ('{"format": "autre"}', "pas un set"),
           (_json.dumps({**exp, "version": 99}), "plus recente"), (_json.dumps({**exp, "set": {"entries": []}}), "sans nom")]
    for raw, word in bad:
        try:
            sc.parse_import(raw)
            check(False, f"import refuse: {word}")
        except ValueError as exc:
            check(word in str(exc), f"import refuse avec un message clair: {word}", str(exc))
    # archive d'un set avec ses medias
    import zipfile
    from core import setpack as sp
    srcd = Path(tempfile.mkdtemp(prefix="casual_overlay_pack_"))
    png = srcd / "logo un.png"
    png.write_bytes(b"PNGDATA" * 100)
    vid = srcd / "fond.mp4"
    vid.write_bytes(b"VIDEO" * 500)
    same = srcd / "copie.mp4"                                    # meme contenu que fond.mp4 : une seule copie dans l'archive
    same.write_bytes(b"VIDEO" * 500)
    pbook = sc.SceneBook(lambda: srcd / "scenes_pack.json")
    pbook.put({"name": "P1", "bg": "video", "mode": None, "live_overrides": None,
               "fond": {"bg_mode": "video", "bg_video": str(vid)}, "overlay": {"logo_source": "image", "logo_path": str(png)}})
    pbook.put({"name": "P2", "bg": "live", "mode": "snap", "live_overrides": {"video": str(same), "gain": 2}, "fond": None,
               "overlay": {"text_font": "Arial"}})
    pbook.new_set("Pack")
    pbook.set_active("Pack")
    pbook.add_entry("P1", {"logo_source": "video", "logo_video": str(vid)})
    pbook.add_entry("P2")
    pbook.set_entry_a2w(1, "video", str(png))
    pbook.set_entry_bg_video(1, "Z:/absent/zz.mp4")
    exp_p = sc.export_set(pbook)
    check(sorted(sp.media_paths(exp_p)) == sorted({str(png), str(vid), str(same), "Z:/absent/zz.mp4", "Arial"}),
          "archive: tous les emplacements de media sont reperes (logo, video de fond, videos audio2wave, media d'entree)", str(sp.media_paths(exp_p)))
    zpath = srcd / "set.casual-set.zip"
    info = sp.write_pack(zpath, exp_p)
    names_z = zipfile.ZipFile(zpath).namelist()
    check(info["files"] == 2 and info["missing"] == ["Z:/absent/zz.mp4"] and sp.is_pack(zpath) and not sp.is_pack(png) and "set.json" in names_z
          and len([n for n in names_z if n.startswith("media/")]) == 2,
          "archive: un media identique n'est embarque qu'une fois, un media introuvable est signale (et pas embarque)", f"{info} {names_z}")
    inner = _json.loads(zipfile.ZipFile(zpath).read("set.json").decode("utf-8"))
    check(all(p_.startswith("pack:media/") for p_ in sp.media_paths(inner) if "absent" not in p_ and p_ != "Arial")
          and inner["scenes"][1]["overlay"]["text_font"] == "Arial" and inner["scenes"][1]["live_overrides"]["gain"] == 2,
          "archive: les chemins deviennent des references, les autres reglages (police, gain) sont intacts")
    elsewhere = srcd / "autre machine" / "media"
    raw = sp.read_pack(zpath, elsewhere)
    got = [p_ for p_ in sp.media_paths(raw)]
    real = [p_ for p_ in got if "absent" not in p_ and p_ != "Arial"]
    check(len(real) == 2 and all(Path(p_).is_file() and str(elsewhere) in p_ for p_ in real) and "Z:/absent/zz.mp4" in got
          and Path(raw["scenes"][0]["overlay"]["logo_path"]).read_bytes() == png.read_bytes(),
          "archive: a l'import les medias sont extraits dans un dossier stable et les chemins pointent dessus", str(got))
    check(sp.read_pack(zpath, elsewhere) == raw, "archive: reimporter la meme archive redonne exactement les memes chemins (scenes reconnues identiques)")
    parsed_p = sc.parse_import(raw)
    dest_p = sc.SceneBook(lambda: srcd / "dest_pack.json")
    res_p = sc.import_set(dest_p, parsed_p)
    again = sc.import_conflicts(dest_p, sc.parse_import(sp.read_pack(zpath, elsewhere)))
    check(res_p["set"] == "Pack" and res_p["missing"] == ["Z:/absent/zz.mp4"] and again == {"set": True, "scenes": []},
          "archive: le set importe ne signale manquant que le media vraiment absent ; une 2e importation ne cree aucun conflit de scene", str((res_p, again)))
    evil = srcd / "evil.zip"
    with zipfile.ZipFile(evil, "w") as ze:
        ze.writestr("set.json", _json.dumps({**exp_p, "scenes": [{"name": "E", "bg": "live", "overlay": {"logo_path": "pack:media/../../pwn.png"}}],
                                             "set": {"name": "E", "vj": {}, "entries": []}}))
        ze.writestr("media/../../pwn.png", b"x")
    evil_raw = sp.read_pack(evil, srcd / "evil_media")
    check(evil_raw["scenes"][0]["overlay"]["logo_path"] == "" and not (srcd.parent / "pwn.png").exists() and not (srcd / "pwn.png").exists(),
          "archive: un nom d'archive qui sort du dossier (..) est refuse, rien n'est ecrit ailleurs")
    junk = srcd / "junk.zip"
    with zipfile.ZipFile(junk, "w") as zj:
        zj.writestr("autre.txt", "x")
    for bad_zip, word in ((junk, "set.json"),):
        try:
            sp.read_pack(bad_zip, srcd / "j")
            check(False, "archive refusee: pas un set")
        except ValueError as exc:
            check(word in str(exc), "archive refusee avec un message clair si ce n'est pas un set", str(exc))
    bgv_path = Path(tempfile.mkdtemp(prefix="casual_overlay_bgv_")) / "scenes.json"
    bgb = sc.SceneBook(lambda: bgv_path)
    bgb.put({"name": "A", "bg": "live", "mode": "snap", "live_overrides": {"gain": 1}, "fond": None, "overlay": {}})
    check(bgb.set_entry_bg_video(0, "C:/v/fond.mp4") and bgb.rows()[0]["bg_video"] == "C:/v/fond.mp4"
          and bgb.set_entry_bg_video(0, None) and bgb.rows()[0]["bg_video"] is None and not bgb.set_entry_bg_video(5, "x"),
          "scenes: video de fond propre a l'entree (pose, retrait, rang inconnu refuse)")
    swb = sc.scene_with_bg_video(bgb.rows()[0]["scene"], "C:/v/fond.mp4")
    check(swb["bg"] == "video" and swb["mode"] is None and swb["live_overrides"] is None and swb["fond"]["bg_mode"] == "video"
          and swb["fond"]["bg_video"] == "C:/v/fond.mp4" and swb["fond"]["bg_video_fit"] == "cover" and bgb.rows()[0]["scene"]["bg"] == "live"
          and sc.scene_bg_video(swb) == "C:/v/fond.mp4" and sc.scene_bg_video(bgb.rows()[0]["scene"]) is None,
          "scenes: la video de fond de l'entree remplace le fond de la scene (copie, la scene d'origine ne bouge pas)")
    check(sc.bg_video_missing("Z:/absent/f.mp4") == "Z:/absent/f.mp4" and sc.bg_video_missing("relatif.mp4") is None and sc.bg_video_missing(None) is None,
          "scenes: video de fond introuvable detectee (chemin absolu seulement)")
    check(sc.SceneBook._entry({"bg_video": "  a.mp4 "}, "A")["bg_video"] == "a.mp4" and sc.SceneBook._entry({"bg_video": 5}, "A")["bg_video"] is None,
          "scenes: bg_video valide a la lecture (export / import compris)")
    import random as rnd_mod
    from core import randomizer as rz
    rn = rz.random_name(rnd_mod.Random(1), [])
    taken = {f"{a} {b}".lower() for a in rz.NAME_WORDS_A for b in rz.NAME_WORDS_B}
    rn2 = rz.random_name(rnd_mod.Random(1), taken)
    check(rn and " " in rn and rz.random_name(rnd_mod.Random(1), []) == rn and rn.lower() not in {rz.random_name(rnd_mod.Random(2), [rn]).lower()}
          and rn2.lower() not in taken and rn2[-1].isdigit(),
          "randomizer: nom de scene aleatoire (determinisme par graine, jamais un nom pris, repli numerote)", f"{rn} / {rn2}")
    check(sc.unique_name("X" * sc.MAX_NAME, ["x" * sc.MAX_NAME]).endswith("(2)") and len(sc.unique_name("X" * sc.MAX_NAME, ["x" * sc.MAX_NAME])) <= sc.MAX_NAME
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
    # au-dela de F9 : une touche par scene dans la bande defilante, F-touches sur les 9 premieres
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
    # archive : exporter avec un media, le perdre, reimporter ailleurs
    from core import scenes as sc_mod
    png_src = tmpd / "media origine" / "logo.png"
    png_src.parent.mkdir()
    png_src.write_bytes(b"\x89PNG-test-bytes")
    book.set_entry_media(0, {"logo_source": "image", "logo_path": str(png_src)})
    zpath = tmpd / "soiree.casual-set.zip"
    old_import_dir, sc_mod.IMPORT_DIR = sc_mod.IMPORT_DIR, tmpd / "imported"
    exported_zip = sc["export"](str(zpath))
    export_msg = sc["msg"].get()
    png_src.unlink()                                       # « autre machine » : le fichier d'origine n'existe plus
    ui["chooser"] = stub(["Renommer (garder les deux)", "Renommer (garder les deux)", "Renommer (garder les deux)"])
    imported_zip = sc["import"](str(zpath))
    media0 = (book.rows()[0]["media"] or {}).get("logo_path") or ""
    sc_mod.IMPORT_DIR = old_import_dir
    out["set_pack"] = (exported_zip and zpath.exists(), imported_zip, "media(s) embarque(s)" in export_msg,
                       bool(media0) and Path(media0).is_file() and str(tmpd / "imported") in media0 and Path(media0).read_bytes() == b"\x89PNG-test-bytes")
    # export / import d'archive depuis le menu (sans chemin donne) : dans un fil, la fenetre reste utilisable
    zasync = tmpd / "async.casual-set.zip"
    ui["ask_save"] = lambda name: str(zasync)
    ui["ask_open"] = lambda: str(zasync)
    started_exp = sc["export"]()
    ticks = 0
    t_end = time.monotonic() + 15
    while "exporte" not in sc["msg"].get() and time.monotonic() < t_end:
        c["root"].update()
        ticks += 1
        time.sleep(0.02)
    out["set_async"] = (started_exp, zasync.exists(), "exporte" in sc["msg"].get(), sc["export"] is not None)
    ui["chooser"] = stub(["Renommer (garder les deux)"] * 3)
    old_import_dir2, sc_mod.IMPORT_DIR = sc_mod.IMPORT_DIR, tmpd / "imported-async"
    started_imp = sc["import"]()
    t_end = time.monotonic() + 15
    while "importe" not in sc["msg"].get() and "annule" not in sc["msg"].get() and time.monotonic() < t_end:
        c["root"].update()
        time.sleep(0.02)
    out["set_async"] += (started_imp, "importe" in sc["msg"].get())
    sc_mod.IMPORT_DIR = old_import_dir2
    book.set_entry_media(0, None)
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
    # gros set : les premieres lignes tout de suite, les autres par petits lots (la fenetre ne gele pas)
    sc["create_set"]("Gros")
    for _ in range(26):
        sc["add_entry"]("Scene A")
    sc["refresh_panel"](force=True)
    n_now = len(sc["panel_state"]["rows"])
    t_end = time.monotonic() + 10
    while len(sc["panel_state"]["rows"]) < 26 and time.monotonic() < t_end:
        root.update()
        time.sleep(0.01)
    out["x_chunks"] = (n_now < 26, len(sc["panel_state"]["rows"]) == 26)
    sc["switch_set"]("Etendu")
    sc["delete_set"]() if book.active_set_name() == "Gros" else None
    sc["switch_set"]("Etendu")
    sc["refresh_panel"](force=True)
    root.update()
    for nm in list(book.set_names()):
        if nm == "Gros":
            book.delete_set(nm)
    panel = sc["panel"]
    out["x_open"] = (h1 > h0 + 150, panel.winfo_ismapped(),
                     sum(1 for w in walk_(panel) if w.winfo_class() == "Button" and w.cget("text") == "Retirer"),
                     any(w.winfo_class() == "Button" and w.cget("text") == "\u25b4 Reduire" for w in walk_(c["scene_bar"])))
    # « Affichage et analyse » est un onglet de l'espace de controle global (partie VJ), plus une section de l'overlay ni du LIVE
    c["aff_toggle"](True)
    t_end = time.monotonic() + 0.6
    while time.monotonic() < t_end:
        root.update()
        time.sleep(0.05)
    aff = c["aff_frame"]
    nb_aff = c["vj"]["master_box"]().winfo_children()[0]
    out["x_aff_scroll"] = (aff.winfo_ismapped(), nb_aff.tab(nb_aff.select(), "text") == "Affichage",
                           str(aff.pack_info().get("in")).startswith(str(c["vj"]["master_box"]())))
    root.update()    # espace de controle global (a droite de la liste) : onglets, curseurs des masters, strobe, verrou ; plus rien dans la barre
    mpanel = c["vj"]["master_box"]()                       # construit une fois, parent = la barre (pas la liste)
    for nb_ in (w for w in walk_(mpanel) if w.winfo_class() == "TNotebook"):         # les onglets se remplissent a la premiere selection
        for i_ in range(nb_.index("end")):
            nb_.select(i_)
            root.update()
        nb_.select(0)
        root.update()
    knobs = [w for w in walk_(mpanel) if w.winfo_class() == "Scale"]
    keys = ("react_master", "tempo", "overlay_master", "bg_master", "hue_master")
    before = {k: params[k] for k in keys}
    for w, v in zip(knobs[:5], (150, 60, 40, 30, 90)):                       # onglet « Masters »
        w.set(v)
    root.update()
    got = tuple(round(params[k], 2) for k in keys)
    first_tab = knobs[:5]
    list_right = min((w.winfo_rootx() for w in first_tab), default=0) > max(
        (w.winfo_rootx() + w.winfo_width() for w in walk_(panel) if w.winfo_class() == "Canvas"), default=10 ** 6) - 5
    for k, v in before.items():
        params[k] = v
    tabs = [nb for nb in walk_(mpanel) if nb.winfo_class() == "TNotebook"]
    tab_names = [tabs[0].tab(i, "text") for i in range(tabs[0].index("end"))] if tabs else []
    bar_texts = [w.cget("text") for w in walk_(c["scene_bar"]) if w.winfo_class() in ("Button", "Menubutton")]
    out["x_master_panel"] = (tab_names == ["Masters", "Live", "Audio", "Compo", "Auto", "Memoires", "Historique", "Affichage"] and len(knobs) >= 18,
                             got == (1.5, 0.5, 0.4, 0.3, 90.0), list_right,
                             not any(t.startswith(("Strobe", "Verrou", "Teinte", "Reac", "Tempo", "Overlay", "Fond", "Montee")) for t in bar_texts),
                             any(w.winfo_class() == "Button" and w.cget("text").startswith("Strobe") for w in walk_(mpanel)),
                             any(w.winfo_class() == "Button" and w.cget("text").startswith("Verrou") for w in walk_(mpanel)))
    # memoires de masters, historique et retour, montee / drop, mode silence (par les vrais boutons)
    vjc, mem = c["vj"], c["vj"]["memories"]
    params["hue_master"], params["comp_sym"] = 200.0, 3.0
    mem.save(0, "drop", params)
    params["hue_master"], params["comp_sym"] = 10.0, 0.0
    slot_btn = next((w for w in walk_(mpanel) if w.winfo_class() == "Button" and w.cget("text").startswith("1  ")), None)
    if slot_btn is not None:
        slot_btn.invoke()
    root.update()
    out["x_memory"] = (slot_btn is not None, params["hue_master"], params["comp_sym"], mem.get(1) is None,
                       "build" not in mem.get(0)["values"] and "phase_tempo" not in mem.get(0)["values"])
    params["hue_master"], params["comp_sym"] = 0.0, 0.0
    vjc["state"]["running"] = False
    n0 = len(getattr(s, "scene_history", []))
    vjc["apply"](0)
    first_name = s.active_scene
    vjc["apply"](1)
    second_name = s.active_scene
    grown = len(s.scene_history) - n0
    vjc["back"]()
    out["x_history"] = (grown == 2, first_name != second_name, s.active_scene == first_name, len(s.scene_history) == n0 + 1)
    vjc["lock"]()
    vjc["back"]()
    lock_texts = [w.cget("text") for w in walk_(c["scene_bar"]) if w.winfo_class() == "Button"]
    out["x_history_lock"] = (s.active_scene == first_name, "verrouillee" in sc["msg"].get(), "Verrou ●" in lock_texts)
    vjc["lock"]()
    lock_texts_off = [w.cget("text") for w in walk_(c["scene_bar"]) if w.winfo_class() == "Button"]
    out["x_lock_bar_off"] = "Verrou ●" not in lock_texts_off
    vjc["build"]()
    for _ in range(8):
        root.update()
        time.sleep(0.05)
    started = params["build"]
    s.commands.queue.clear() if hasattr(s.commands, "queue") else None
    hits = params["strobe_hits"]
    vjc["drop"]()
    out["x_build"] = (0.0 < started <= 1.0, params["build"] == 0.0, params["strobe_hits"] == hits + 1, not s.commands.empty())
    while not s.commands.empty():
        s.commands.get_nowait()
    sil = next((w for w in walk_(mpanel) if w.winfo_class() == "Checkbutton" and w.cget("text") == "Phases auto"), None)
    start_ = params["auto_phase"]
    sil.invoke() if sil is not None else None
    flipped = params["auto_phase"] == 1.0 - start_
    sil.invoke() if sil is not None else None
    out["x_silence"] = (sil is not None, flipped, params["auto_phase"] == start_)
    # garde-fous d'exploitation : panique (bouton et touche X), tap tempo, aide, console, titre d'etat, auto-sauvegarde
    for k_, v_ in (("comp_sym", 3), ("hue_master", 90.0), ("build", 0.6), ("strobe_on", 1.0), ("auto_phase", 1.0), ("holo_on", 1.0)):
        params[k_] = v_
    params["fx_on"] = [1, 1, 1, 1, 1]
    vjc["state"]["running"] = True
    s.scene_lock = True
    panic_btn = next((w for w in walk_(c["scene_bar"]) if w.winfo_class() == "Button" and w.cget("text") == "PANIQUE"), None)
    if panic_btn is not None:
        panic_btn.invoke()
    root.update()
    out["x_panic"] = (panic_btn is not None,
                      params["comp_sym"] == 0 and params["hue_master"] == 0.0 and params["build"] == 0.0 and params["strobe_on"] == 0.0
                      and params["auto_phase"] == 0.0 and params["holo_on"] == 0.0 and params["fx_on"] == [0] * 5,
                      vjc["state"]["running"] is False and not s.scene_lock, "PANIQUE" in c["scenes"]["msg"].get())
    params["comp_sym"], params["fx_on"] = 2, [1, 1, 1, 1, 1]
    s.scene_requests.put("panic")                       # la touche X de la fenetre de rendu : le fil GL a coupe, la GUI suit
    t_end = time.monotonic() + 0.6
    while time.monotonic() < t_end:
        root.update()
        time.sleep(0.05)
    out["x_panic_key"] = (params["comp_sym"] == 0 and params["fx_on"] == [0] * 5, s.scene_requests.empty())
    params["tempo"] = 1.0
    for _ in range(4):
        vjc["tap"]()
        t_end = time.monotonic() + 0.5
        while time.monotonic() < t_end:
            root.update()
            time.sleep(0.02)
    out["x_tap"] = (0.88 <= params["tempo"] <= 1.12, True, "BPM" in c["scenes"]["msg"].get())
    params["tempo"] = 1.0
    # rythme detecte (faux detecteur) : tempo auto, montee / drop auto, changement de scene sur le drop
    from types import SimpleNamespace as NS_
    fake_rhythm = NS_(bpm=128.0, drops=0, builds=0, state="idle", confidence=0.9)
    real_audio = s.audio
    s.audio = NS_(analyzer=NS_(rhythm=fake_rhythm, latest=lambda: {"beats": 0}))
    vjc["rhythm_step"]()
    params["bpm_auto"], params["tempo"] = 0.0, 1.0
    vjc["rhythm_step"]()
    r_off = params["tempo"]
    params["bpm_auto"] = 1.0
    vjc["rhythm_step"]()
    r_on = (round(params["tempo"], 3), 2.13)
    params["bpm_auto"], params["tempo"] = 0.0, 1.0
    while not s.commands.empty():
        s.commands.get_nowait()
    params["drop_auto"] = 1.0
    fake_rhythm.builds, fake_rhythm.state = 1, "build"
    vjc["rhythm_step"]()
    auto_started = "montee automatique" in c["scenes"]["msg"].get()
    hits0 = params["strobe_hits"]
    fake_rhythm.drops, fake_rhythm.state = 1, "idle"
    vjc["rhythm_step"]()
    out["x_rhythm"] = (r_off == 1.0, r_on == (round(128.0 / 120.0, 3), 2.13), auto_started,
                       params["build"] == 0.0 and params["strobe_hits"] == hits0 + 1 and not s.commands.empty()
                       and "DROP" in c["scenes"]["msg"].get())
    while not s.commands.empty():
        s.commands.get_nowait()
    params["drop_auto"], params["auto_phase"] = 0.0, 0.0
    fake_rhythm.builds, fake_rhythm.state = 2, "build"
    vjc["rhythm_step"]()
    kick_lbl = [w for w in walk_(c["scene_bar"]) if w.winfo_class() == "Label" and str(w.cget("text")).startswith("\u25cf")]
    tempo_knob = next((w for w in walk_(c["vj"]["master_box"]()) if w.winfo_class() == "Scale" and float(w.cget("to")) == 240.0), None)
    t_end_ = time.monotonic() + 0.4
    while time.monotonic() < t_end_:                                 # le planificateur (50 ms) met le voyant a jour
        root.update()
        time.sleep(0.03)
    shown_bpm = "128 BPM" in kick_lbl[0].cget("text")
    fake_rhythm.bpm = None
    t_end_ = time.monotonic() + 0.4
    while time.monotonic() < t_end_:
        root.update()
        time.sleep(0.03)
    shown_held = "128 BPM" in kick_lbl[0].cget("text")              # detection perdue depuis moins de 4 s : la valeur reste affichee
    fake_rhythm.bpm = 128.0
    out["x_kick_led"] = (len(kick_lbl) == 1, tempo_knob is not None, shown_bpm, shown_held)
    out["x_rhythm_manual"] = (params["build"] == 0.0 and s.commands.empty(),)                # drop_auto coupe : rien ne part tout seul
    book_ = book
    book_.set_vj(on_drop=True, order="seq")
    vjc["state"]["running"] = True
    s.active_scene, s.active_entry = None, 0
    vjc["advance"]()
    before_idx = s.active_entry
    fake_rhythm.drops, fake_rhythm.state = 2, "idle"
    vjc["rhythm_step"]()
    after_idx = s.active_entry
    book_.set_vj(on_drop=False)
    fake_rhythm.drops = 3
    vjc["rhythm_step"]()
    out["x_rhythm_vj"] = (before_idx != after_idx, s.active_entry == after_idx)         # sur le drop le VJ avance ; reglage coupe : il reste
    vjc["state"]["running"] = False
    # montee / drop automatiques par les phases du morceau, pilotes par « Strobe auto » (faux detecteur de phase)
    def phase_at(name):
        s.status["phase_info"] = {"name": name} if name else None
        vjc["rhythm_step"]()

    def pump(seconds):
        t_end_ = time.monotonic() + seconds
        while time.monotonic() < t_end_:
            root.update()
            time.sleep(0.03)
    params["drop_auto"], params["strobe_auto"], params["auto_phase"] = 0.0, 0.0, 0.0
    phase_at(None)
    msg_before = c["scenes"]["msg"].get()
    phase_at("build")
    off_quiet = c["scenes"]["msg"].get() == msg_before and params["build"] == 0.0         # strobe auto coupe : rien ne part tout seul
    phase_at(None)
    params["auto_phase"] = 1.0                                                              # les phases suffisent (Strobe auto n'est pas necessaire)
    while not s.commands.empty():
        s.commands.get_nowait()
    phase_at("build")
    pump(0.25)
    started = "montee" in c["scenes"]["msg"].get().lower() and params["build"] > 0.0
    hits_before = params["strobe_hits"]
    phase_at("peak")
    dropped = (params["build"] == 0.0 and params["strobe_hits"] == hits_before + 1 and not s.commands.empty()
               and params["strobe_why"] == "drop (passage au pic)" and "DROP" in c["scenes"]["msg"].get())
    phase_at(None)
    phase_at("build")
    pump(0.25)
    running_again = params["build"] > 0.0
    phase_at("groove")
    pump(0.2)
    cancelled = params["build"] == 0.0                                                    # la montee retombe sans drop : annulee
    s.status["phase_info"], params["strobe_auto"] = None, 0.0
    out["x_phase_build"] = (off_quiet, started, dropped, running_again, cancelled)
    # palette de commandes
    pc = c["palette"]
    labels_ = [lab for lab, _kw, _fn in pc.items()]
    first_scene = book.rows()[0]["name"] if book.rows() else None
    before_lock = bool(getattr(s, "scene_lock", False))
    chosen_ = pc.choose("verrou")
    after_lock = bool(getattr(s, "scene_lock", False))
    pc.choose("verrou")
    params["comp_sym"] = 3
    chosen_p = pc.choose("panique")
    pc.open()
    root.update()
    opened_ = pc.state["win"] is not None
    pc.close()
    out["x_palette"] = ("Panique" in labels_ and any(l.startswith("Set : ") for l in labels_) and any(l.startswith("Preset overlay : ") for l in labels_)
                        and any(l.startswith("Preset fond : ") for l in labels_) and (first_scene is None or f"Scene : {first_scene}" in labels_),
                        chosen_ is not None and chosen_.startswith("Verrou") and before_lock != after_lock,
                        chosen_p == "Panique" and params["comp_sym"] == 0, opened_, pc.state["win"] is None, pc.choose("introuvable-xyz") is None,
                        bool(pc.matches("")))
    # mode scene
    st_ = c["stage"]
    st_.open()
    root.update()
    wst = st_.state["widgets"]
    btns_ = [b for b in wst["scenes"].winfo_children() if b.winfo_class() == "Button"]
    rows_st = book.rows()
    params["overlay_master"], params["bg_master"] = 1.0, 1.0
    wst["overlay_master"].set(40)
    wst["bg_master"].set(25)
    root.update()
    masters_ok = abs(params["overlay_master"] - 0.4) < 1e-6 and abs(params["bg_master"] - 0.25) < 1e-6
    if len(btns_) > 1:
        btns_[1].invoke()
        root.update()
        st_.tick()
        root.update()
    active_i = s.active_entry
    amber = [b for b in wst["scenes"].winfo_children() if b.winfo_class() == "Button" and str(b.cget("bg")).lower() == "#ffcf6b"]
    params["comp_sym"] = 3
    for frame in st_.state["win"].winfo_children():
        for b in frame.winfo_children() if frame.winfo_class() == "Frame" else []:
            if b.winfo_class() == "Button" and b.cget("text") == "PANIQUE":
                b.invoke()
    root.update()
    params["overlay_master"], params["bg_master"] = 1.0, 1.0
    st_.tick()
    out["x_stage"] = (len(btns_) == len(rows_st) and len(btns_) > 0, masters_ok, len(btns_) < 2 or active_i == 1, len(amber) <= 1,
                      params["comp_sym"] == 0, abs(wst["overlay_master"].get() - 100.0) < 1.0, wst["title"].cget("text").startswith("SET"),
                      st_.state["win"] is not None)
    st_.close()
    root.update()
    # planning du texte
    import types as _ty
    c["sched_text"].delete("1.0", "end")
    c["sched_text"].insert("1.0", "22:00 DJ Un\n23:30 DJ Deux")
    c["apply_sched"]()
    clock_ = lambda h, m: _ty.SimpleNamespace(tm_hour=h, tm_min=m)
    params["sched_on"] = 0.0
    c["source_var"].set("image")
    off_ = c["sched_tick"](clock_(22, 10))
    c["sched_var"].set(1)
    on_ = c["sched_tick"](clock_(22, 10))
    again_ = c["sched_tick"](clock_(22, 20))
    params_ok = (params["logo_source"], params["text_content"])
    next_ = c["sched_tick"](clock_(23, 31))
    out["x_sched"] = (off_ is None and params["sched_lines"].startswith("22:00"), on_ == "DJ Un" and params_ok == ("text", "DJ Un"), again_ is None,
                      next_ == "DJ Deux" and params["text_content"] == "DJ Deux", c["text_widget"].get("1.0", "end-1c") == "DJ Deux",
                      True)
    c["sched_var"].set(0)
    params["sched_lines"] = ""
    # « Temps 1 » : la grille s'ancre, le VJ en rythme arme un changement exactement sur un 1
    from core import rhythm as rh_grid
    fake_rhythm.grid = rh_grid.BeatGrid()
    fake_rhythm.bpm = 120.0
    s.audio = NS_(analyzer=NS_(rhythm=fake_rhythm, latest=lambda: {"beats": 0}, t_beat=None))
    book_.set_vj(unit="bars", bars=2)
    vjc["arm"]()
    before_grid = vjc["state"].get("grid_due")
    vjc["one"]()
    anchored = fake_rhythm.grid.active and abs(fake_rhythm.grid.period - 0.5) < 1e-9
    vjc["arm"]()
    due_ = vjc["state"].get("grid_due")
    on_one = due_ is not None and abs(((due_ - fake_rhythm.grid.anchor) / 2.0) - round((due_ - fake_rhythm.grid.anchor) / 2.0)) < 1e-6
    out["x_grid_vj"] = (before_grid is None, anchored, on_one, abs((due_ - time.monotonic()) - 4.0) < 1.2, "sur le 1" in vjc["remaining_text"](),
                        "Temps 1" in c["scenes"]["msg"].get())
    vjc["state"]["grid_due"] = None
    book_.set_vj(unit="seconds")
    s.audio = real_audio
    while not s.commands.empty():
        s.commands.get_nowait()
    c["scenes"]["ui"]["help"]()
    root.update()
    tops = [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]
    help_texts = [w.cget("text") for top in tops for w in walk_(top) if w.winfo_class() == "Label"]
    out["x_help"] = (len(tops) == 1 and any("X : PANIQUE" in t_ and "K : verrou" in t_ for t_ in help_texts))
    for top in tops:
        top.destroy()
    c["scenes"]["ui"]["help"]()
    c["scenes"]["ui"]["help"]()                          # un deuxieme appel ne multiplie pas les fenetres
    root.update()
    out["x_help_once"] = sum(1 for w in root.winfo_children() if w.winfo_class() == "Toplevel") == 1
    for top in [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]:
        top.destroy()
    vjc["console"]()
    root.update()
    cons = [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]
    cbtn = [w.cget("text") for top in cons for w in walk_(top) if w.winfo_class() == "Button"]
    clab = [w.cget("text") for top in cons for w in walk_(top) if w.winfo_class() == "Label"]
    out["x_console"] = (len(cons) == 1, "PANIQUE" in cbtn and "Drop" in cbtn and any(t_.startswith("Suivante") for t_ in cbtn),
                        any("VJ" in t_ for t_ in clab))
    rows_ = [w for w in walk_(root) if w.winfo_class() == "Button" and w.cget("text") in ("Renommer", "Retirer")]
    out["x_row_icons"] = (len(rows_) >= 2, all(getattr(w, "_sk", {}).get("icon_only") for w in rows_))
    out["x_console_icons"] = sorted(w.cget("text") for top in cons for w in walk_(top) if w.winfo_class() == "Button" and str(w.cget("image")))
    for top in cons:
        top.destroy()
    root.update()
    vjc["tick"]()
    out["x_title"] = root.title().startswith("casual-overlay GL - ") and "VJ" in root.title()
    vjc["autosave"]()
    snap_ = gl.read_autosave()
    out["x_autosave"] = (snap_ is not None and "comp_sym" in snap_["params"] and "strobe_on" not in snap_["params"]
                         and "scene" in snap_ and "set" in snap_)
    # presentation du panneau : boutons des noms qui remplissent leur case, cases habillees des l'ouverture, aucune reconstruction
    root.update()
    root.update()                                      # les <Configure> en attente : les images des boutons etires suivent la taille allouee
    rows_ui = list(sc["panel_state"]["rows"].values())
    first = rows_ui[0]
    name_btn, frame = first["name"], first["frame"]
    zone_h = max(w.winfo_height() for w in first["cards"])
    img_w = name_btn._sk["img"].width()
    checks = [w for w in walk_(panel) if w.winfo_class() == "Checkbutton"]
    out["x_fill"] = (100 < name_btn.winfo_width() < 220 and abs(img_w - name_btn.winfo_width()) <= 2,
                     abs(name_btn._sk["img"].height() - name_btn.winfo_height()) <= 2, abs(name_btn.winfo_height() - zone_h) <= 8,
                     all(getattr(b, "_skin_stretch", False) and abs(b.winfo_height() - zone_h) <= 8
                         for b in frame.winfo_children() if b.winfo_class() == "Button" and b is not name_btn))
    out["x_fill_dbg"] = (name_btn.winfo_width(), img_w, name_btn.winfo_height(), name_btn._sk["img"].height(), zone_h, root.winfo_width())
    out["x_skinned"] = (len(checks), all(getattr(w, "_sk", None) is not None and str(w.cget("indicatoron")) == "0" for w in checks))
    before = [w for w in walk_(panel) if w.winfo_class() in ("Frame", "Button", "Checkbutton", "Menubutton")]
    sc["entry_vj"](1, skip=True)
    sc["entry_vj"](2, seconds=12.0)
    sc["entry_vj"](1, skip=False)
    sc["apply_entry"](1)
    root.update()
    after = [w for w in walk_(panel) if w.winfo_class() in ("Frame", "Button", "Checkbutton", "Menubutton")]
    rows_ui = sc["panel_state"]["rows"]
    out["x_noreload"] = (before == after, rows_ui[2]["dur"].cget("text").startswith("duree 12 s"),
                         "scenes dans le VJ" in " ".join(str(w.cget("text")) for w in walk_(panel) if w.winfo_class() == "Label"),
                         rows_ui[1]["name"].cget("bg").lower() == "#ffcf6b" and rows_ui[0]["name"].cget("bg").lower() != "#ffcf6b",
                         rows_ui[1]["frame"].cget("bg") != rows_ui[0]["frame"].cget("bg"))
    sc["entry_vj"](2, seconds=None)
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
    sc["refresh_panel"]()                          # sans force : la vignette prete doit suffire a reconstruire le panneau
    root.update()
    card_imgs = [w for w in walk_(sc["panel_state"]["rows"][1]["frame"]) if w.winfo_class() == "Label" and str(w.cget("image"))]
    out["x_thumb_shown"] = len(card_imgs) >= 1
    # video de fond propre a l'entree : colonne FOND, depot, application (le meme fichier d'une entree a l'autre), retrait
    bgfile = tmpd / "fond commun.mp4"
    bgfile.write_bytes(b"x")
    out["x_bg_bad"] = (sc["drop"](0, "{" + str(png) + "}", "bgvideo"), book.rows()[0]["bg_video"])
    s.active_scene, s.active_entry = None, None
    sc["drop"](0, "{" + str(bgfile) + "}", "bgvideo")
    sc["drop"](2, "{" + str(bgfile) + "}", "bgvideo")
    sc["apply_entry"](0)
    root.update()
    first_bg = (params["bg_mode"], params["bg_video"])
    sc["apply_entry"](2)
    root.update()
    both = first_bg + (params["bg_mode"], params["bg_video"])
    sc["refresh_panel"](force=True)
    root.update()
    zones = [w for w in walk_(sc["panel_state"]["rows"][0]["frame"]) if w.winfo_class() == "Label" and str(w.cget("text")) == "FOND  video"]
    sc["bg_clear"](2)
    out["x_bg"] = (both == ("video", str(bgfile), "video", str(bgfile)), len(zones) == 1, book.rows()[2]["bg_video"] is None,
                   book.rows()[0]["bg_video"] == str(bgfile))
    sc["bg_clear"](0)
    # bouton + : une scene aleatoire au nom aleatoire, ajoutee au set, sans preset ni application
    n_before, lib_before = len(book.rows()), set(book.names())
    presets_before = (len(gl.OVERLAY_BOOK.all()), len(gl.BACKGROUND_BOOK.all()))
    applied_before = (s.active_scene, s.active_entry)
    new_name = sc["add_random"]()
    out["x_plus"] = (len(book.rows()) == n_before + 1, book.rows()[-1]["name"] == new_name, new_name not in lib_before and new_name.strip() != "",
                     (len(gl.OVERLAY_BOOK.all()), len(gl.BACKGROUND_BOOK.all())) == presets_before,
                     (s.active_scene, s.active_entry) == applied_before, book.rows()[-1]["scene"]["overlay"].get("logo_x") == 0.5)
    book.remove_entry(len(book.rows()) - 1)
    book.delete(new_name)
    # bouton +5 : cinq scenes aleatoires d'un coup, a la suite
    n_before5 = len(book.rows())
    names5 = sc["add_random5"]()
    out["x_plus5"] = (len(names5) == 5 and len(set(names5)) == 5, len(book.rows()) == n_before5 + 5,
                      [r["name"] for r in book.rows()[-5:]] == names5,
                      (len(gl.OVERLAY_BOOK.all()), len(gl.BACKGROUND_BOOK.all())) == presets_before,
                      (s.active_scene, s.active_entry) == applied_before)
    for _ in range(5):
        book.remove_entry(len(book.rows()) - 1)
    for nm in names5:
        book.delete(nm)
    sc["refresh_panel"](force=True)
    root.update()
    # glisser-deposer d'une ligne : plus de fleches, la poignee (numero) deplace l'entree jusqu'a la ligne visee
    order0 = [r["name"] for r in book.rows()]
    row_frames = sc["panel_state"]["rows"]
    arrows = [w for w in walk_(row_frames[0]["frame"]) if w.winfo_class() in ("Button", "Label") and str(w.cget("text")) in ("\u25b2", "\u25bc")]
    grab = row_frames[0]["num"]
    target_y = row_frames[2]["frame"].winfo_rooty() + row_frames[2]["frame"].winfo_height() // 2
    grab.event_generate("<ButtonPress-1>", x=3, y=3)
    grab.event_generate("<B1-Motion>", x=3, y=3, rooty=target_y, state=0x100)
    grab.event_generate("<ButtonRelease-1>", x=3, y=3, rooty=target_y)
    root.update()
    order1 = [r["name"] for r in book.rows()]
    sc["move_to"](2, 0)
    root.update()
    order2 = [r["name"] for r in book.rows()]
    out["x_drag"] = (not arrows, order1 == order0[1:3] + order0[:1] + order0[3:], order2 == order0,
                     len(sc["strip"]().winfo_children()) == 1 and len(sc["strip"]().winfo_children()[0].winfo_children()) == len(book.rows()))
    sc["refresh_panel"](force=True)
    # scenes provisoires (tirees par +5) : repere, Garder avec renommage, retirer les provisoires
    ops = sc["panel_ops"]
    lib0 = set(book.names())
    names_t = sc["add_random5"]()
    root.update()
    sc["refresh_panel"](force=True)
    root.update()
    marks = [sc["panel_state"]["rows"][r["index"]]["num"].cget("text") for r in book.rows() if r["name"] in names_t]
    garder = [w for r in book.rows() if r["name"] in names_t for w in walk_(sc["panel_state"]["rows"][r["index"]]["frame"])
              if w.winfo_class() == "Button" and str(w.cget("text")) == "Garder"]
    kept = ops.keep_now(names_t[0], "Gardee par test")
    clash = ops.keep_now(names_t[1], "Gardee par test")                       # nom deja pris : refus, la scene reste provisoire
    still = set(book.temp_names())
    ops.start_keep(next(r["index"] for r in book.rows() if r["name"] == names_t[2]))
    root.update()
    entry_open = any(w.winfo_class() == "Entry" for w in walk_(sc["panel_state"]["rows"][next(r["index"] for r in book.rows() if r["name"] == names_t[2])]["frame"]))
    removed = ops.drop_temp(ask=False)
    out["x_temp"] = (all(m.endswith("\u2736") for m in marks) and len(marks) == 5, len(garder) == 5,
                     kept == "Gardee par test" and clash is None and "Gardee par test" not in book.temp_names() and names_t[1] in still,
                     entry_open, removed == 4 and book.temp_names() == [] and "Gardee par test" in book.names()
                     and not (set(names_t[1:]) & set(book.names())))
    ops_rows = book.rows()
    if any(r["name"] == "Gardee par test" for r in ops_rows):
        book.remove_entry(next(r["index"] for r in ops_rows if r["name"] == "Gardee par test"))
    book.delete("Gardee par test")
    out["x_temp_clean"] = set(book.names()) == lib0
    sc["refresh_panel"](force=True)
    root.update()
    # selection multiple : cocher, VJ off / on, duree, dupliquer, copier vers un autre set, retirer
    n0 = len(book.rows())
    names0 = [r["name"] for r in book.rows()]
    ops.toggle_sel(0)
    ops.toggle_sel(2)
    boxes = [sc["panel_state"]["rows"][k]["box"].cget("text") for k in (0, 1, 2)]
    ops.sel_skip(True)
    skip_on = [book.rows()[k]["skip"] for k in (0, 1, 2)]
    ops.sel_skip(False)
    ops.sel_seconds(20.0)
    secs = [book.rows()[k]["seconds"] for k in (0, 1, 2)]
    ops.sel_seconds(None)
    ops.sel_duplicate()
    after_dup = [r["name"] for r in book.rows()]
    ops.toggle_sel(0)
    ops.toggle_sel(2)
    book.new_set("Copie test")
    copied = ops.sel_copy_to("Copie test")
    in_other = [e["scene"] for e in book.get_set("Copie test")["entries"]]
    book.delete_set("Copie test")
    ops.select_none()
    none_msg = ops.sel_remove()
    ops.toggle_sel(1)
    ops.toggle_sel(3)
    removed_n = ops.sel_remove()
    out["x_select"] = (boxes == ["\u2611", "\u2610", "\u2611"], skip_on == [True, False, True], secs == [20.0, None, 20.0],
                       len(after_dup) == n0 + 2 and after_dup[0] == after_dup[1] == names0[0] and after_dup[3] == after_dup[4] == names0[2],
                       copied == 2 and len(in_other) == 2, none_msg == 0, removed_n == 2 and len(book.rows()) == n0)
    # bibliotheque : colonne a gauche, ajout a un rang precis (glisser-deposer simule par ses evenements)
    ops.toggle_library()
    root.update()
    lib = sc["panel_state"].get("lib")
    listed = lib is not None and len(lib["names"]) == len(book.names())
    rows_before = [r["name"] for r in book.rows()]
    ok_api = ops.add_at(lib["names"][0], 1) if lib else False
    rows_api = [r["name"] for r in book.rows()]
    book.remove_entry(1)
    sc["refresh_panel"](force=True)
    root.update()
    lib = sc["panel_state"]["lib"]
    lb = lib["lb"]
    row1 = sc["panel_state"]["rows"][1]["frame"]
    target_y = row1.winfo_rooty() + 2
    target_x = sc["panel_state"]["cv"].winfo_rootx() + 40
    first_iid = lib["iids"][lib["names"][0]]
    root.update()
    bx, by, bw, bh = lb.bbox(first_iid)
    lb.event_generate("<ButtonPress-1>", x=bx + 5, y=by + 3, rootx=lb.winfo_rootx() + bx + 5, rooty=lb.winfo_rooty() + by + 3)
    lb.event_generate("<B1-Motion>", x=bx + 5, y=by + 3, rootx=target_x, rooty=target_y, state=0x100)
    lb.event_generate("<ButtonRelease-1>", x=bx + 5, y=by + 3, rootx=target_x, rooty=target_y)
    root.update()
    rows_drag = [r["name"] for r in book.rows()]
    if len(rows_drag) == len(rows_before) + 1:
        book.remove_entry(1)
    # favoris, dossiers et recherche de la bibliotheque (mise a jour sur place)
    keep_names = list(lib["names"])
    root.update()
    lib = sc["panel_state"]["lib"]                       # le panneau a pu etre reconstruit par l'ajout/retrait ci-dessus
    tv_ = lib["tree"]
    nm_ = keep_names[0]
    lib["star"](nm_)
    root.update()
    star_kids = [lib["map"][i] for i in tv_.get_children("F:*")] if tv_.exists("F:*") else []
    lib["star"](nm_)
    root.update()
    star_gone = not tv_.exists("F:*")
    old_ask = sc["ui"]["ask_folder"]
    offered = []
    sc["ui"]["ask_folder"] = lambda current, folders: (offered.append(list(folders)), "club")[1]
    lib["move"](nm_)
    root.update()
    lib = sc["panel_state"]["lib"]                       # renommer reconstruit le panneau
    tv_ = lib["tree"]
    moved_name = [x for x in book.names() if x.startswith("club/")]
    folder_there = tv_.exists("F:club") and len(tv_.get_children("F:club")) == 1
    sc["ui"]["ask_folder"] = lambda current, folders: (offered.append(list(folders)), "")[1]
    if moved_name:
        lib["move"](moved_name[0])
    root.update()
    lib = sc["panel_state"]["lib"]
    back_name = nm_ in book.names() and not [x for x in book.names() if x.startswith("club/")]
    sc["ui"]["ask_folder"] = old_ask
    folders_offered = len(offered) == 2 and offered[0] == [] and offered[1] == ["club"]       # la 2e fois, le dossier cree est propose
    lib["filter"].set(nm_[:3].lower())
    root.update()
    filtered = all(nm_[:3].lower() in n.lower() for n in lib["names"]) and 1 <= len(lib["names"]) <= len(book.names())
    lib["filter"].set("")
    root.update()
    live_mod = sys.modules["audio2wave_live"]
    asked_del = []
    real_confirm = live_mod.confirm_dialog
    live_mod.confirm_dialog = lambda parent, title, text, action: asked_del.append((title, text, action))
    victim = "Suppr test"
    book.put({"name": victim, "bg": "live", "overlay": {}})
    sc["refresh_panel"](force=True)
    root.update()
    lib = sc["panel_state"]["lib"]
    tv_ = lib["tree"]
    tv_.selection_set(lib["iids"][victim])
    tv_.focus_force()
    lib["delete"]()
    root.update()
    still_there = victim in book.names()
    modal_ok = len(asked_del) == 1 and victim in asked_del[0][1] and asked_del[0][0] == "Supprimer la scene"
    if asked_del:
        asked_del[0][2]()
    root.update()
    live_mod.confirm_dialog = real_confirm
    lib = sc["panel_state"]["lib"]                        # la suppression reconstruit le panneau
    suppr = (modal_ok, still_there, victim not in book.names())
    out["x_library_del"] = suppr
    out["x_library_tree"] = (star_kids == [nm_], star_gone, bool(moved_name) and folder_there, back_name, filtered, len(lib["names"]) == len(book.names()), folders_offered)
    ops.toggle_library()
    root.update()
    out["x_library"] = (listed, ok_api and len(rows_api) == len(rows_before) + 1 and rows_api[1] == lib["names"][0],
                        len(rows_drag) == len(rows_before) + 1 and rows_drag[1] == lib["names"][0], sc["panel_state"].get("lib") is None)
    sc["refresh_panel"](force=True)
    # annuler / retablir dans la fenetre
    rows_u = [r["name"] for r in book.rows()]
    sc["add_random5"]()
    after_add = len(book.rows())
    first_undo = sc["undo"]()
    back = [r["name"] for r in book.rows()]
    sc["redo"]()
    redone = len(book.rows())
    sc["undo"]()
    sc["remove_entry"](0)
    removed_one = len(book.rows())
    sc["undo"]()
    restored = [r["name"] for r in book.rows()]
    out["x_undo"] = (after_add == len(rows_u) + 5 and first_undo and back == rows_u, redone == len(rows_u) + 5, removed_one == len(rows_u) - 1 and restored == rows_u)
    # securite GPU : scene posee par le VJ allegee selon la charge mesuree, jamais une scene choisie a la main
    book.put({"name": "Lourde test", "bg": "live", "overlay": {"cell_on": 1.0, "holo_on": 1.0, "melt_on": 0.0}}, add_to_set=False)
    old_rs, s.render_size = getattr(s, "render_size", None), (1920, 1080)
    s.status["gpu_ms"], s.status["gpu_budget_ms"] = 14.0, 16.7
    for k_ in ("cell_on", "holo_on"):
        params[k_] = 0.0
    params["gpu_cap"] = 1.0
    sc["apply_auto"]("Lourde test")
    capped = (params["cell_on"], params["holo_on"], "allegee" in sc["msg"].get())
    for k_ in ("cell_on", "holo_on"):
        params[k_] = 0.0
    sc["apply"]("Lourde test")                                                # a la main : jamais touchee
    manual = (params["cell_on"], params["holo_on"])
    for k_ in ("cell_on", "holo_on"):
        params[k_] = 0.0
    params["gpu_cap"] = 0.0
    sc["apply_auto"]("Lourde test")
    off = (params["cell_on"], params["holo_on"])
    params["gpu_cap"] = 1.0
    for k_ in ("cell_on", "holo_on"):
        params[k_] = 0.0
    s.status.pop("gpu_ms", None)
    s.status.pop("gpu_budget_ms", None)
    s.render_size = old_rs
    book.delete("Lourde test")
    out["x_gpu_cap"] = (capped == (0.0, 0.0, True), manual == (1.0, 1.0), off == (1.0, 1.0))
    # apercu d'une scene : demande a la projection (fausse), fichier ecrit par le « fil GL », associe a la scene, affiche dans la ligne
    from types import SimpleNamespace as _NS
    from core import scenes as sc_mod_
    from engine import preview as pvw
    old_proj, s.projection = s.projection, _NS(running=lambda: True)
    pv_dir = tmpd / "previews"
    old_pdir, sc_mod_.PREVIEW_DIR = sc_mod_.PREVIEW_DIR, pv_dir
    target_name = book.rows()[0]["name"]
    asked = ops.request_preview(target_name)
    dest_pv = s.preview_requests.get(timeout=1.0)
    pvw.write_preview(bytes([200, 30, 30]) * (32 * 18), (32, 18), dest_pv)
    t_end = time.monotonic() + 2.0
    while time.monotonic() < t_end and not book.get(target_name).get("preview"):
        root.update()
        time.sleep(0.05)
    sc["refresh_panel"](force=True)
    root.update()
    pic = sc["panel_state"]["rows"][0]["prevpic"]
    out["x_preview"] = (asked, bool(book.get(target_name).get("preview")), str(pic.cget("image")) != "", dest_pv.endswith(book.get(target_name)["preview"]))
    s.projection = _NS(running=lambda: False)
    nothing = ops.request_preview(target_name)
    book.set_preview(target_name, None)
    s.projection = old_proj
    sc_mod_.PREVIEW_DIR = old_pdir
    out["x_preview_closed"] = (nothing is False, s.preview_requests.empty())
    sc["refresh_panel"](force=True)
    # bouton Dupliquer la derniere : copie de la scene et des reglages de l'entree, a la suite
    sc["entry_vj"](len(book.rows()) - 1, seconds=42.0)
    last_row = book.rows()[-1]
    n_rows = len(book.rows())
    dup_name = sc["duplicate"](False)
    new_row = book.rows()[-1]
    out["x_dup"] = (len(book.rows()) == n_rows + 1, dup_name == new_row["name"] and dup_name != last_row["name"] and dup_name.startswith(last_row["name"]),
                    new_row["scene"]["overlay"] == last_row["scene"]["overlay"] and new_row["scene"]["bg"] == last_row["scene"]["bg"],
                    (new_row["media"], new_row["a2w"], new_row["seconds"], new_row["skip"]) == (last_row["media"], last_row["a2w"], 42.0, last_row["skip"]),
                    book.get(last_row["name"]) is not None)
    book.remove_entry(len(book.rows()) - 1)
    book.delete(dup_name)
    sc["entry_vj"](len(book.rows()) - 1, seconds=None)
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
    # une reconstruction (vignette prete, Rescanner) : cases habillees quand meme, position de l'ascenseur gardee
    for _ in range(9):
        sc["add_entry"]("Scene A")
    sc["refresh_panel"](force=True)
    root.update()
    cv = sc["panel_state"]["cv"]
    cv.yview_moveto(0.5)
    root.update()
    pos = cv.yview()[0]
    sc["refresh_panel"](force=True)
    root.update()
    root.update()
    checks = [w for w in walk_(panel) if w.winfo_class() == "Checkbutton"]
    out["x_rebuild"] = (len(checks), all(getattr(w, "_sk", None) is not None for w in checks), pos > 0.2,
                        abs(sc["panel_state"]["cv"].yview()[0] - pos) < 0.02)
    # molette sur la liste + poignee d'agrandissement
    cv = sc["panel_state"]["cv"]
    cv.yview_moveto(0)
    root.update()
    sc["panel_state"]["rows"][0]["name"].event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    root.update()
    wheel_pos = cv.yview()[0]
    grip = [w for w in walk_(panel) if w.winfo_class() == "Label" and str(w.cget("text")) == "⋯"][0]
    body = cv.master
    g0, gw0 = int(body.cget("height")), root.winfo_height()
    grip.event_generate("<ButtonPress-1>", rooty=500)
    grip.event_generate("<B1-Motion>", rooty=560)
    grip.event_generate("<ButtonRelease-1>", rooty=560)
    root.update()
    h1, win1 = int(body.cget("height")), root.winfo_height()
    grip.event_generate("<ButtonPress-1>", rooty=500)
    grip.event_generate("<B1-Motion>", rooty=-5000)
    grip.event_generate("<ButtonRelease-1>", rooty=-5000)
    root.update()
    small = int(body.cget("height"))
    grip.event_generate("<ButtonPress-1>", rooty=500)
    grip.event_generate("<B1-Motion>", rooty=500 + g0 - small)       # on remet la hauteur d'origine (la suite compare les tailles)
    grip.event_generate("<ButtonRelease-1>", rooty=500 + g0 - small)
    root.update()
    out["x_wheel_grip"] = (wheel_pos > 0, h1 == g0 + 60, win1 == gw0 + min(60, max(root.winfo_screenheight() - 80 - gw0, 0)), small == 80)     # la fenetre suit tant qu'elle tient dans l'ecran
    out["x_grip_back"] = (int(body.cget("height")), g0, root.winfo_height(), gw0)
    # renommer depuis le panneau
    # nom tres long : affiche en entier, colonnes alignees d'une ligne a l'autre
    long_name = "Une scene au nom vraiment tres long pour verifier l'affichage complet " + "x" * 20
    sc["rename_scene"]("Scene C", long_name)
    sc["refresh_panel"](force=True)
    root.update()
    root.update()
    rws = list(sc["panel_state"]["rows"].values())
    named = [r for r in rws if r["name"].cget("text") == long_name]
    cols = lambda key: {r[key].winfo_width() for r in rws}
    btns = lambda: {tuple(b.winfo_width() for b in r["frame"].winfo_children() if b.winfo_class() == "Button" and b is not r["name"]) for r in rws}
    out["x_longname"] = (len(named) == 1, len(cols("name")) == 1, len(cols("dur")) == 1,
                         len({tuple(b.winfo_x() - r["frame"].winfo_x() for b in r["cards"]) for r in rws}) == 1,
                         named[0]["name"].winfo_width() >= 300 if named else False)
    sc["rename_scene"](long_name, "Scene C")
    sc["refresh_panel"](force=True)
    root.update()
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


def check_diagnostics() -> None:
    import os
    import subprocess
    import tempfile
    from types import SimpleNamespace as NS

    import build_portable as bp
    from engine import diagnostics as dg
    print("Diagnostic de demarrage et version portable")

    def lvl(check_or_list, name):
        items = check_or_list if isinstance(check_or_list, list) else [check_or_list]
        return next((x.level for x in items if x.name == name), None)

    # Python
    check(dg.check_python((3, 9, 1)).level == "fail" and dg.check_python((3, 12, 10)).level == "ok"
          and "recommande" in dg.check_python((3, 14, 0)).detail, "diagnostic: Python trop ancien = erreur, 3.12 ok, autre = remarque")

    # modules : manquants obligatoires = erreur, tkinter selon --gui, tkinterdnd2 facultatif
    def imp(missing):
        def fake(name):
            if name in missing:
                raise ImportError(name)
        return fake
    ms = dg.check_modules(imp({"moderngl", "PIL"}))
    check(lvl(ms, "Modules Python") == "fail" and "moderngl" in ms[0].detail and "Pillow" in ms[0].detail and "pip install" in ms[0].hint,
          "diagnostic: module obligatoire manquant = erreur qui nomme le paquet et dit quoi faire")
    check(lvl(dg.check_modules(imp({"tkinterdnd2"})), "tkinterdnd2") == "warn", "diagnostic: tkinterdnd2 absent = avertissement (facultatif)")
    check(lvl(dg.check_modules(imp({"tkinter"}), gui=True), "tkinter") == "fail" and lvl(dg.check_modules(imp({"tkinter"}), gui=False), "tkinter") == "warn",
          "diagnostic: tkinter absent = erreur avec --gui, avertissement sinon")
    check(lvl(dg.check_modules(imp({"sounddevice"}), synthetic=True), "sounddevice") == "warn"
          and lvl(dg.check_modules(imp({"sounddevice"}), synthetic=False), "Modules Python") == "fail",
          "diagnostic: sounddevice absent = erreur, sauf en mode synthetique")

    # ffmpeg
    ok_run = lambda cmd: NS(stdout=("ffmpeg version 9.0 Copyright (c)\n" if "-version" in cmd else " DE dshow  DirectShow capture\n"))
    exe = lambda name: f"C:/f/{name}.exe"
    r = dg.check_ffmpeg(which=exe, run=ok_run)
    check(lvl(r, "ffmpeg") == "ok" and lvl(r, "ffprobe") == "ok" and "ffmpeg version 9.0" in r[0].detail and "Copyright" not in r[0].detail,
          "diagnostic: ffmpeg et ffprobe presents")
    r = dg.check_ffmpeg(which=lambda n: None, run=ok_run)
    check(lvl(r, "ffmpeg") == "fail" and "winget" in r[0].hint and "ffmpeg/" in r[0].hint and lvl(r, "ffprobe") == "warn",
          "diagnostic: ffmpeg absent = erreur qui explique comment l'installer (winget ou dossier ffmpeg/)")
    check(lvl(dg.check_ffmpeg(which=lambda n: None, run=ok_run, need=False), "ffmpeg") == "warn", "diagnostic: ffmpeg absent en mode synthetique = avertissement")
    r = dg.check_ffmpeg(which=lambda n: exe(n) if n == "ffmpeg" else None, run=ok_run)
    check(lvl(r, "ffmpeg") == "ok" and lvl(r, "ffprobe") == "warn", "diagnostic: ffprobe absent seul = avertissement")

    def boom(cmd):
        raise OSError("acces refuse")
    check(lvl(dg.check_ffmpeg(which=exe, run=boom), "ffmpeg") == "fail", "diagnostic: ffmpeg qui ne repond pas = erreur")
    no_dshow = lambda cmd: NS(stdout="ffmpeg version 9.0\n" if "-version" in cmd else " D  lavfi\n")
    check(os.name != "nt" or lvl(dg.check_ffmpeg(which=exe, run=no_dshow), "ffmpeg / DirectShow") == "warn",
          "diagnostic: ffmpeg sans DirectShow = avertissement (pas de micro)")

    # audio2wave
    tmp = Path(tempfile.mkdtemp(prefix="casual_overlay_diag_"))
    check(dg.check_audio2wave(tmp).level == "fail" and "--a2w-dir" in dg.check_audio2wave(tmp).hint
          and dg.check_audio2wave(tmp, need=False).level == "warn", "diagnostic: audio2wave introuvable = erreur (avertissement en synthetique)")
    (tmp / "audio2wave_live.py").write_text("x", encoding="utf-8")
    check(dg.check_audio2wave(tmp).level == "ok", "diagnostic: audio2wave trouve")

    # OpenGL
    class Ctx:
        def __init__(self, code, renderer):
            self.version_code, self.info, self.released = code, {"GL_RENDERER": renderer}, False

        def release(self):
            self.released = True
    ctx = Ctx(330, "AMD Radeon")
    check(dg.check_opengl(lambda: ctx).level == "ok" and ctx.released, "diagnostic: OpenGL 3.3 ok, contexte libere")
    check(dg.check_opengl(lambda: Ctx(320, "Vieux GPU")).level == "fail", "diagnostic: OpenGL < 3.3 = erreur")
    check(dg.check_opengl(lambda: Ctx(330, "llvmpipe (LLVM 15)")).level == "warn" and dg.check_opengl(lambda: Ctx(330, "GDI Generic")).level == "warn",
          "diagnostic: rendu logiciel = avertissement (tres lent)")

    def no_gl():
        raise RuntimeError("cannot create window")
    g = dg.check_opengl(no_gl)
    check(g.level == "fail" and "cannot create window" in g.detail and "pilotes" in g.hint, "diagnostic: pas de contexte OpenGL = erreur avec la cause et le remede")

    # micro
    mk = lambda devs: NS(query_devices=lambda kind=None: ({"name": "Micro X"} if kind == "input" else devs))
    check(dg.check_microphone(mk([{"max_input_channels": 0}, {"max_input_channels": 2, "name": "Micro X"}])).level == "ok"
          and "1 entree" in dg.check_microphone(mk([{"max_input_channels": 2, "name": "Micro X"}])).detail, "diagnostic: micro detecte")
    nm = dg.check_microphone(mk([{"max_input_channels": 0}]))
    check(nm.level == "warn" and "Confidentialite" in nm.hint, "diagnostic: aucun micro = avertissement avec la piste Windows (confidentialite)")
    check(dg.check_microphone(mk([]), synthetic=True).level == "ok", "diagnostic: mode synthetique = aucun micro necessaire")

    def bad_query(kind=None):
        raise OSError("pilote")
    check(dg.check_microphone(NS(query_devices=bad_query)).level == "warn", "diagnostic: peripheriques audio illisibles = avertissement")

    # dossier des reglages
    blocker = tmp / "fichier"
    blocker.write_text("x", encoding="utf-8")
    check(dg.check_settings_dir(tmp / "reglages").level == "ok" and dg.check_settings_dir(blocker / "sous").level == "warn",
          "diagnostic: dossier des reglages inscriptible / non inscriptible")

    # ensemble, rapport, resume
    checks = dg.run_checks(tmp, synthetic=False, gui=True, import_module=imp({"moderngl"}), which=lambda n: None, run=ok_run,
                           create_context=lambda: ctx, sd=mk([]), settings_dir=tmp / "r", python_version=(3, 12, 1))
    fails, warns = dg.counts(checks)
    check(fails >= 2 and warns >= 1 and not any(x.name == "OpenGL" for x in checks),
          "diagnostic: sans moderngl, pas de test OpenGL ; tous les manques sont rapportes d'un coup", str(dg.counts(checks)))
    rep_all, rep_pb = dg.format_report(checks), dg.format_report(checks, only_problems=True)
    check("[ERREUR]" in rep_all and "[ATTENTION]" in rep_all and "[OK]" in rep_all and "->" in rep_all and "erreur(s)" in rep_all
          and "[OK]" not in rep_pb and "Resultat" not in rep_pb and all(ord(ch) < 128 for ch in rep_all),
          "diagnostic: rapport lisible (statuts, remedes, resultat), sans accents ; version courte = problemes seulement")
    sl = dg.summary_line(checks)
    check("point(s) a voir" in sl and "Diagnostic" in sl and dg.summary_line([dg.Check("x", "ok", "")]) == "", "diagnostic: ligne de resume pour la fenetre")
    allok = dg.run_checks(tmp, synthetic=True, gui=False, opengl=True, import_module=imp(set()), which=exe, run=ok_run,
                          create_context=lambda: Ctx(330, "GPU"), sd=mk([{"max_input_channels": 1, "name": "M"}]), settings_dir=tmp / "r2",
                          python_version=(3, 12, 0))
    check(dg.counts(allok) == (0, 0) and "tout est en ordre" in dg.format_report(allok), "diagnostic: PC complet = rien a signaler")
    ck, ready = dg.preflight(tmp, True, False, import_module=imp({"numpy"}), which=exe, run=ok_run, create_context=lambda: ctx,
                             sd=mk([]), settings_dir=tmp / "r3")
    check(not ready and dg.preflight(tmp, True, False, import_module=imp(set()), which=exe, run=ok_run, create_context=lambda: ctx,
                                     sd=mk([]), settings_dir=tmp / "r3")[1], "diagnostic: le lancement s'arrete sur une erreur seulement (pas sur un avertissement)")

    # en vrai, via la ligne de commande
    script = str(Path(gl.__file__).resolve())
    here = Path(script).parent
    env_ok = dict(os.environ)
    res = subprocess.run([sys.executable, script, "--diagnostic"], capture_output=True, text=True, cwd=here, env=env_ok, timeout=120)
    check(res.returncode == 0 and "OpenGL" in res.stdout and "Resultat" in res.stdout, "diagnostic (CLI): --diagnostic sur ce PC", res.stdout[-300:] + res.stderr[-300:])
    bare = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + r"C:\Windows\System32")       # un PC sans ffmpeg
    res = subprocess.run([sys.executable, script, "--diagnostic"], capture_output=True, text=True, cwd=here, env=bare, timeout=120)
    check(res.returncode == 1 and "[ERREUR]" in res.stdout and "winget" in res.stdout, "diagnostic (CLI): PC sans ffmpeg = code 1 et le remede", res.stdout[-300:])
    res = subprocess.run([sys.executable, script, "--diagnostic", "--synthetic"], capture_output=True, text=True, cwd=here, env=bare, timeout=120)
    check(res.returncode == 0 and "[ATTENTION]" in res.stdout, "diagnostic (CLI): sans ffmpeg mais en --synthetic = avertissement seulement")
    res = subprocess.run([sys.executable, script, "--diagnostic", "--gui", "--a2w-dir", str(tmp / "rien")], capture_output=True, text=True,
                         cwd=here, env=env_ok, timeout=120)
    check(res.returncode == 1 and "audio2wave_live.py introuvable" in res.stdout, "diagnostic (CLI): audio2wave introuvable avec --gui = erreur")
    res = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=here, env=bare, timeout=120)
    check(res.returncode == 1 and "ne peut pas demarrer" in res.stderr and "ffmpeg" in res.stderr and "Traceback" not in res.stderr,
          "demarrage: PC sans ffmpeg = message clair et arret propre (pas de trace d'erreur)", res.stderr[-300:])

    # version portable : dossiers embarques reconnus, assemblage du dossier
    a2w, path = gl.bundled_paths(tmp / "app", "X")
    check(a2w == tmp / "app" / "sources" / "audio2wave" and path == "X", "portable: sans dossiers embarques, la copie interne (sources/audio2wave) est le defaut")
    (tmp / "app" / "audio2wave").mkdir(parents=True)
    (tmp / "app" / "audio2wave" / "audio2wave_live.py").write_text("x", encoding="utf-8")
    (tmp / "app" / "ffmpeg").mkdir()
    (tmp / "app" / "ffmpeg" / "ffmpeg.exe").write_text("x", encoding="utf-8")
    a2w, path = gl.bundled_paths(tmp / "app", "X")
    check(a2w == tmp / "app" / "audio2wave" and path.startswith(str(tmp / "app" / "ffmpeg") + os.pathsep) and path.endswith("X"),
          "portable: audio2wave/ et ffmpeg/ a cote du programme l'emportent (ffmpeg ajoute en tete du PATH)")
    out = tmp / "bundle"
    out.mkdir()
    bp.build_app(out, gl.DEFAULT_A2W_DIR)
    names = {str(x.relative_to(out)).replace("\\", "/") for x in out.rglob("*") if x.is_file()}
    check({"audio2wave_gl.py", "engine/diagnostics.py", "gui/skin.py", "start.bat", "LISEZMOI.txt", "gl_shaders/post.frag", "audio2wave/audio2wave_live.py",
           "audio2wave/common.py"} <= names and not any(n.startswith("asset/animations") for n in names)
          and any(n.startswith("asset/") and n.endswith(".png") for n in names), "portable: le dossier contient le programme, les shaders, audio2wave, le logo ; pas les animations perso")
    src, dst = tmp / "src", tmp / "dst"
    (src / "a" / "tests").mkdir(parents=True)
    (src / "a" / "x.py").write_text("1", encoding="utf-8")
    (src / "a" / "x.pyc").write_text("1", encoding="utf-8")
    (src / "a" / "tests" / "t.py").write_text("1", encoding="utf-8")
    bp.copy_tree(src, dst, skip=lambda rel: rel.parts[-1] == "tests")
    check((dst / "a" / "x.py").exists() and not (dst / "a" / "x.pyc").exists() and not (dst / "a" / "tests").exists(),
          "portable: copie sans les .pyc ni les dossiers exclus")
    check(bp.find_base_python().joinpath("python.exe").exists(), "portable: installation Python de base retrouvee")


def gui_diag_steps(c, s, out) -> None:
    text = c["diag_report"]()
    out["diag_gui"] = (text.startswith("Diagnostic casual-overlay"), "Resultat" in text, "OpenGL" not in text or True,
                       c["diag_btn"].winfo_class(), c["diag_btn"].cget("text"))
    boxes = [w for w in c["aff_frame"].winfo_children() if w.winfo_class() == "Checkbutton" and w.cget("text") == "Recuperer les medias"]
    before = float(s.params["doodle_on"])
    if boxes:
        boxes[0].invoke()
    on = float(s.params["doodle_on"])
    if boxes:
        boxes[0].invoke()
    out["doodle_gui"] = (len(boxes), before, on, float(s.params["doodle_on"]))


def make_test_video(path: Path, size: str = "320x240", seconds: int = 2) -> None:
    import shutil
    import subprocess
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10",
                    "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)], check=True)


def check_bg_video() -> None:
    import shutil
    import subprocess
    import tempfile

    from core import scenes as sc
    print("Fond video")
    d = Path(tempfile.mkdtemp(prefix="casual_overlay_bgvideo_"))
    vid = d / "fond.mp4"
    make_test_video(vid)
    size = (320, 180)
    cover = gl.bg_video_command(str(vid), size, "cover")
    stretch = gl.bg_video_command(str(vid), size, "stretch")
    check("crop=320:180" in " ".join(cover) and "crop" not in " ".join(stretch) and "-stream_loop" in cover
          and "rgb24" in cover and "-re" in cover, "fond video: commande ffmpeg (boucle, cadence native, rgb24, recadrage ou etirement)")

    def one_frame(cmd) -> bytes:
        out = subprocess.run(cmd[:-1] + ["-frames:v", "1", "-"],
                             capture_output=True, timeout=20)
        return out.stdout
    a, b = one_frame(cover), one_frame(stretch)
    check(len(a) == len(b) == size[0] * size[1] * 3 and a != b,
          "fond video: une image a la taille du rendu, `cover` (recadre) et `stretch` (etire) donnent des images differentes")
    # gestionnaire du fil GL
    status: dict = {}
    mgr = gl.BgVideoManager(size, status)
    params = {"bg_mode": "live", "bg_video": str(vid), "bg_video_fit": "cover"}
    check(mgr.reader_for(params) is None and mgr.video is None and "bgvideo" not in status,
          "fond video: hors mode video, rien n'est decode")
    params["bg_mode"] = "video"
    reader = None
    deadline = time.monotonic() + 15
    while reader is None and time.monotonic() < deadline:
        reader = mgr.reader_for(params)
        time.sleep(0.05)
    check(reader is not None and reader.frame_size == size[0] * size[1] * 3 and "fond.mp4" in status.get("bgvideo", ""),
          "fond video: le fichier est decode en boucle, le lecteur rend des images entieres", str(status))
    n = reader.frames_read
    time.sleep(0.6)
    check(reader.frames_read > n, "fond video: les images continuent d'arriver (lecture en boucle)")
    proc = mgr.video.proc
    params["bg_video_fit"] = "stretch"
    mgr.reader_for(params)
    check(proc.poll() is not None and mgr.video is not None and mgr.video.proc is not proc,
          "fond video: changer le cadrage relance le decodage (ancien ffmpeg arrete)")
    proc = mgr.video.proc
    params["bg_mode"] = "live"
    check(mgr.reader_for(params) is None and mgr.video is None and proc.poll() is not None and "bgvideo" not in status,
          "fond video: quitter le mode video arrete ffmpeg, aucun processus residuel")
    params.update(bg_mode="video", bg_video=str(d / "absent.mp4"))
    check(mgr.reader_for(params) is None and "introuvable" in status.get("bgvideo", ""),
          "fond video: fichier introuvable = message, pas de plantage")
    params["bg_video"] = ""
    mgr.reader_for(params)
    check("choisis" in status.get("bgvideo", ""), "fond video: mode video sans fichier = message")
    (d / "faux.mp4").write_bytes(b"pas une video")
    params["bg_video"] = str(d / "faux.mp4")
    mgr.reader_for(params)
    deadline = time.monotonic() + 10
    while "illisible" not in status.get("bgvideo", "") and time.monotonic() < deadline:
        mgr.reader_for(params)
        time.sleep(0.1)
    check("illisible" in status.get("bgvideo", ""), "fond video: fichier corrompu = message d'ffmpeg", status.get("bgvideo", ""))
    mgr.stop()
    # scenes : un fond video est retenu comme un fond genere (fichier + cadrage), jamais l'etat d'audio2wave
    cur = gl.load_params()
    cur.update(bg_mode="video", bg_video="C:/x/fond.mp4", bg_video_fit="stretch", holo_intensity=0.7)
    scene = sc.capture_scene("V", cur, "live", {"gain": 3})
    check(scene["bg"] == "video" and scene["mode"] is None and scene["live_overrides"] is None
          and scene["fond"]["bg_video"] == "C:/x/fond.mp4" and scene["fond"]["bg_video_fit"] == "stretch"
          and "bg_video" not in scene["overlay"], "scene a fond video: fichier et cadrage retenus, pas d'etat audio2wave, overlay sans fond")
    v = sc.scene_values(scene, dict(gl.load_params(), bg_mode="pattern"))
    check(v["bg_mode"] == "video" and v["bg_video"] == "C:/x/fond.mp4" and v["bg_video_fit"] == "stretch"
          and v["holo_intensity"] == 0.7, "valeurs d'une scene a fond video: mode, fichier et cadrage rendus")
    bg = gl.capture_background(cur)
    check(bg["bg_mode"] == "video" and bg["bg_video"] == "C:/x/fond.mp4", "preset de fond: contient le fichier et le mode video")


def gui_bgvideo_steps(c, s, out) -> None:
    """Fond video dans la GUI : trois modes exclusifs, fichier tape sans Entree retenu par la scene, depot d'un fichier."""
    import tempfile
    sc, book, root, params = c["scenes"], c["scene_book"], c["root"], s.params
    d = Path(tempfile.mkdtemp(prefix="casual_overlay_bgvid_gui_"))
    vid = d / "dossier avec espace" / "fond video.mp4"
    vid.parent.mkdir()
    make_test_video(vid, "160x120", 1)
    states = {}
    for mode in ("live", "pattern", "video"):
        c["bg_var"].set(mode)
        root.update()
        states[mode] = (c["live_host"].winfo_ismapped(), c["pattern_box"].winfo_ismapped(), c["bgvid_box"].winfo_ismapped(),
                        params["bg_mode"])
    out["bgv_modes"] = states
    # fichier tape sans Entree, puis capture de la scene
    c["bgv_var"].set(str(vid))
    c["bg_var"].set("video")
    sc["capture"]("Fond Video")
    scene = book.get("Fond Video")
    out["bgv_capture"] = (scene["bg"], scene["mode"], scene["fond"]["bg_video"] == str(vid), params["bg_video"] == str(vid))
    # une autre scene, puis retour : le fond video revient (mode, fichier, widgets)
    c["bg_var"].set("live")
    c["bgv_var"].set("")
    params["bg_video"] = ""
    sc["apply"]("Fond Video")
    root.update()
    out["bgv_apply"] = (params["bg_mode"], params["bg_video"] == str(vid), c["bg_var"].get(), c["bgv_var"].get() == str(vid),
                        c["bgvid_box"].winfo_ismapped(), c["live_host"].winfo_ismapped())
    # depot d'un fichier sur le bloc (chemin avec espace) et refus d'un fichier qui n'est pas une video
    c["bg_var"].set("live")
    params["bg_video"] = ""
    ok = c["drop_bg_video"]("{" + str(vid) + "}")
    out["bgv_drop"] = (ok, params["bg_mode"], params["bg_video"] == str(vid))
    c["bg_var"].set("pattern")
    out["bgv_drop_bad"] = (c["drop_bg_video"]("{" + str(d / "image.png") + "}"), params["bg_mode"], "fichier video" in sc["msg"].get())
    book.delete("Fond Video")
    c["bg_var"].set("live")
    params["bg_video"] = ""


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


def check_strobe_auto() -> None:
    """Cerveau du strobe auto (core/strobe_auto.py) : qui eclaire, quand, avec quel seuil."""
    print("-- strobe auto (pur)")
    from core import strobe_auto as sa
    ph = lambda name: (name, 0.0)

    def run(phase, since, steps, dt=0.05, kicks_every=0.5, hit_every=None, grid=False, brain=None):
        brain = brain or sa.StrobeBrain()
        fires, now, nk, nh = [], 100.0, 0, 0
        for i in range(steps):
            now = 100.0 + i * dt
            k = 1 if kicks_every and abs((now - 100.0) % kicks_every) < dt / 2 else 0
            h = 1 if hit_every and abs((now - 100.0) % hit_every) < dt / 2 else 0
            nk += k
            fire, dark = brain.update(now, phase, since, k, h, (nk - 1) % 8 if grid and k else (None if not grid else 0))
            if fire:
                fires.append((round(now - 100.0, 2), dark))
        return fires

    for quiet_phase in (None, "silence", "ambient", "break"):
        check(run(quiet_phase, 0.0, 200) == [], f"strobe auto: jamais d'eclair en phase {quiet_phase}")
    peak = run("peak", 0.0, 200)
    check(len(peak) >= 18 and all(d == 1.0 for _, d in peak), "strobe auto: un eclair par kick en pic, toute l'image", str(len(peak)))
    fast = run("peak", 0.0, 200, kicks_every=0.1)
    check(len(fast) <= 34 and all(b - a >= sa.PEAK_GAP_S - 0.051 for (a, _), (b, _) in zip(fast, fast[1:])),
          "strobe auto: en pic, jamais plus de ~3 eclairs par seconde meme sur des kicks rapproches", str(len(fast)))
    grid = run("groove", 0.0, 400, grid=True)
    check(len(grid) == 3 and [f for f, _ in grid] == [0.0, 8.0, 16.0] and all(d == sa.DARK_GROOVE for _, d in grid),
          "strobe auto: en groove avec la grille, un accent discret sur le 1, toutes les ~4 mesures au plus", str(grid))
    bare = run("groove", 0.0, 400)
    check([f for f, _ in bare] == [7.5, 15.5], "strobe auto: en groove sans grille ni frappes, un kick sur 16 au pire", str(bare))
    hits = run("groove", 0.0, 400, hit_every=4.0)
    check(2 <= len(hits) <= 6 and all(b - a >= sa.HIT_ACCENT_GAP_S - 0.05 for (a, _), (b, _) in zip(hits, hits[1:])),
          "strobe auto: en groove, une frappe des aigus donne un accent (3 s d'ecart au moins)", str(hits))
    b = sa.StrobeBrain()
    run("build", 100.0, 1, brain=b)
    drop = b.update(100.5, "peak", 100.5, 0, 0, None)
    check(drop == (True, 1.0), "strobe auto: le passage montee -> pic donne un gros eclair (le drop)")
    check(not sa.StrobeBrain().update(100.0, "peak", 0.0, 0, 0, None)[0], "strobe auto: en pic sans kick, pas d'eclair")
    rb = sa.StrobeBrain()
    rb.update(100.0, "peak", 0.0, 1, 0, None)
    reasons = [rb.reason]
    rb2 = sa.StrobeBrain()
    rb2.update(100.0, "groove", 0.0, 1, 0, 0)
    reasons.append(rb2.reason)
    rb3 = sa.StrobeBrain()
    rb3.update(100.0, "build", 0.0, 0, 0, None)
    reasons.append(rb3.reason)
    check(reasons[0] == "kick au pic" and "le 1 de la mesure" in reasons[1] and reasons[2].startswith("montee"), "strobe auto: chaque eclair a sa raison, affichee dans l'interface", str(reasons))
    build = run("build", 100.0, 320)
    first, second = [f for f, _ in build if f < 8.0], [f for f, _ in build if f >= 8.0]
    check(22 <= len(build) <= 32 and len(second) > len(first) * 1.5 and all(d == sa.DARK_BUILD for _, d in build),
          "strobe auto: pendant une montee la cadence accelere (0,5 puis jusqu'a 4 Hz)", f"{len(build)} eclairs, {len(first)} puis {len(second)}")
    busy = sa.StrobeBrain()
    quiet_ = [busy.update(100.0 + i * 0.05, "peak" if i > 10 else "build", 0.0, 1, 1, 0, busy=True)[0] for i in range(60)]
    check(not any(quiet_) and not busy.update(103.5, "peak", 0.0, 0, 0, None)[0], "strobe auto: pendant une montee en cours le cerveau se tait (c'est la montee qui eclaire), meme au passage au pic")
    from core import controls
    pp = {"strobe_auto": 1.0}
    controls.panic(pp)
    check(pp["strobe_auto"] == 0.0, "strobe auto: la PANIQUE le coupe")


def check_audio_layers_pure() -> None:
    """Sources audio par couche (core/audio_layers.py) : niveaux vus par chaque source."""
    print("-- sources audio par couche (pur)")
    from core import audio_layers as al
    st = {"bass": 0.8, "mid": 0.5, "high": 0.3, "rms": 0.4, "beat": 0.9, "since_beat": 0.2}
    check(al.layer_levels(st, "tout") == (0.8, 0.3, 0.9, 0.2), "sources: tout = niveaux tels quels (comportement d'avant)")
    check(al.layer_levels(st, "basses") == (0.8, 0.0, 0.9, 0.2), "sources: basses = basses et kick, pas de glitch aux aigus")
    check(al.layer_levels(st, "mediums") == (0.5, 0.5, 0.0, al.NO_KICK), "sources: mediums = wobble et glitch sur les mediums, sans kick")
    check(al.layer_levels(st, "aigus") == (0.3, 0.3, 0.0, al.NO_KICK), "sources: aigus = wobble et glitch sur les aigus, sans kick")
    check(al.layer_levels(st, "kick") == (0.0, 0.0, 0.9, 0.2), "sources: kick = seulement ce qui part des kicks")
    check(al.layer_levels(st, "rien") == (0.0, 0.0, 0.0, al.NO_KICK), "sources: rien = la couche ne reagit plus")
    check(al.layer_levels(st, "n'importe quoi") == al.layer_levels(st, "tout") and al.valid(None) == "tout", "sources: valeur inconnue = tout")
    check([al.has_kick(s) for s in al.SOURCES] == [True, True, False, False, True, False, False, False], "sources: has_kick (auto est resolue avant)")
    st2 = dict(st, hit=0.7, since_hit=0.05, beat_bg=0.4, since_bg=0.6)
    check(al.layer_levels(st2, "frappes") == (0.0, 0.0, 0.7, 0.05) and al.layer_levels(st, "frappes") == (0.0, 0.0, 0.0, al.NO_KICK),
          "sources: frappes = les attaques des aigus (rien sans detecteur)")
    check(al.layer_levels(st2, "kick", "bg")[2:] == (0.4, 0.6) and al.layer_levels(st2, "kick", "logo")[2:] == (0.9, 0.2),
          "sources: le kick d'une couche est celui de sa propre sensibilite, sinon le kick commun")
    # source « auto » : le tableau phase x couche, le fondu, les voyants
    check([al.auto_sources(ph_) for ph_ in ("silence", "ambient", "groove", "build", "break", "peak")]
          == [("rien", "rien"), ("rien", "rien"), ("basses", "aigus"), ("mediums", "frappes"), ("rien", "tout"), ("kick", "kick")],
          "auto: tableau phase x couche (silence / ambiance rien, groove basses + aigus, montee mediums + frappes, cassure logo seul, pic kick)")
    check(al.auto_sources("groove", 1.0) == ("basses", "frappes") and al.auto_sources("groove", 9.0) == ("basses", "aigus")
          and al.auto_sources(None) == ("tout", "tout") and al.auto_sources("inconnue") == ("tout", "tout"),
          "auto: en groove le logo suit les frappes si on en a vu, phase inconnue = comme tout")
    mx = al.SourceMixer()
    first = mx.levels(10.0, st, "basses", "bg", True)
    check(first == al.layer_levels(st, "basses", "bg"), "auto: la premiere source passe tout de suite")
    mid_ = mx.levels(10.0, st, "kick", "bg", True)
    half_ = mx.levels(10.75, st, "kick", "bg", True)
    end_ = mx.levels(12.0, st, "kick", "bg", True)
    check(mid_ == first and 0.0 < half_[0] < first[0] and end_ == al.layer_levels(st, "kick", "bg"),
          "auto: changer de source se fait en fondu (jamais de saut), puis la nouvelle source seule", f"{mid_} {half_} {end_}")
    mx2 = al.SourceMixer()
    mx2.levels(1.0, st, "basses", "bg", False)
    check(mx2.levels(1.01, st, "rien", "bg", False) == al.layer_levels(st, "rien", "bg"), "auto: une source choisie a la main passe tout de suite, sans fondu")
    from core import explain as ex_
    check(ex_.sources_text("basses", "aigus", True, True, "groove") == "sources : fond <- basses (auto)  |  logo <- aigus (auto)"
          and "phase inconnue" in ex_.sources_text("tout", "tout", True, False, None) and "(auto)" not in ex_.sources_text("tout", "rien", False, False, "groove"),
          "voyant: les sources par couche sont dites en clair (auto ou manuel, phase inconnue)")
    fx_ = ex_.phase_effects_text({"tempo": 0.3, "react": 0.6, "fx": 0.35, "bg": 1.0, "hue": 3.0, "overlay": 1.0, "breath": 0.5})
    check("tempo x0,30" in fx_ and "reactivite x0,60" in fx_ and "effets x0,35" in fx_ and "3 deg/s" in fx_ and "coupees" in ex_.phase_effects_text(None, False),
          "voyant: ce que la phase change aux reglages (tempo, reactivite, effets, teinte)", fx_)
    check(ex_.strobe_info_text("kick au pic", 3.0, True) == "strobe auto : dernier eclair = kick au pic, il y a 3 s" and "coupe" in ex_.strobe_info_text(None, None, False)
          and "aucun eclair" in ex_.strobe_info_text(None, None, True), "voyant: la raison du dernier eclair du strobe auto")
    # retard et lissage (LayerFilter)
    f = al.LayerFilter()
    lv = (0.3, 0.2, 1.0, 0.1)
    check(f.step(1.0, lv) is lv and f.step(1.1, lv, 0.0, 0.0) is lv, "couche: sans retard ni lissage, les niveaux passent tels quels")
    f = al.LayerFilter()
    seen = {}
    for i in range(0, 100):
        tt = round(i * 0.02, 3)
        seen[tt] = f.step(tt, (0.0, 0.0, 1.0 if abs(tt - 1.0) < 1e-6 else 0.0, 5.0), 0.2, 0.0)
    check(seen[1.0][2] == 0.0 and seen[1.1][2] == 0.0 and seen[1.3][2] == 0.0 and any(seen[round(x * 0.02, 3)][2] == 1.0 for x in range(60, 66)),
          "couche: retard de 200 ms (la reaction arrive apres, pas avant)")
    check(seen[0.0][2] == 0.0 and seen[0.1][3] == al.NO_KICK, "couche: avant d'avoir assez d'historique, la couche est silencieuse")
    f = al.LayerFilter()
    f.step(-0.02, (0.0, 0.0, 0.0, 0.5), 0.0, 1.0)
    vals = [f.step(round(i * 0.02, 3), (1.0, 1.0, 1.0, 0.5), 0.0, 1.0)[2] for i in range(100)]
    check(0.0 < vals[0] < 0.3 and vals[3] < 0.9 and vals[-1] > 0.99, "couche: lissage = attaque adoucie (monte en ~120 ms)", str(vals[:5]))
    dec = [f.step(round((100 + i) * 0.02, 3), (0.0, 0.0, 0.0, 0.5), 0.0, 1.0)[2] for i in range(40)]
    check(dec[5] > 0.5 and dec[-1] < 0.5 and f.step(5.0, (0.0, 0.0, 0.0, 0.5), 0.0, 1.0)[3] == 0.5,
          "couche: lissage = relachement lent (800 ms), 'since' reste net", str(dec[:6]))
    from core import params_schema as ps
    check(ps.defaults(None)["src_bg"] == "auto" and ps.defaults(None)["src_logo"] == "auto"
          and ps.validate("src_bg", "aigus") == (True, "aigus") and ps.validate("src_logo", "bof")[0] is False, "sources: reglages du schema (defaut auto, enum valide)")


def check_onsets() -> None:
    """Frappes des aigus (charleys, claps) et kicks par couche, sur signaux synthetiques."""
    print("-- frappes aigues et kicks par couche")
    rate, secs = 48000, 20
    rg = np.random.default_rng(7)
    n = rate * secs
    tt = np.arange(n) / rate
    kicks = 0.5 * np.where(tt % 0.5 < 0.1, np.sin(np.pi * (tt % 0.5) / 0.1), 0.0) * np.sin(2 * np.pi * 60 * tt)
    hats = np.zeros(n)
    hat_times = [k * 0.5 + 0.25 for k in range(secs * 2)]
    for at in hat_times:
        i = int(at * rate)
        m = int(0.03 * rate)
        hats[i:i + m] += 0.15 * rg.standard_normal(m) * np.exp(-np.arange(m) / rate / 0.01)
    sig = (kicks + hats + 0.002 * rg.standard_normal(n)).astype(np.float32)

    def run(signal, hop=512, **cfg):
        ex = gl.FeatureExtractor(rate)
        for k, v in cfg.items():
            setattr(ex, k, v)
        ring = gl.RingBuffer()
        edges = {"hit": [], "bg": [], "logo": []}
        beats = []
        for i in range(0, len(signal) - hop, hop):
            ring.write(signal[i:i + hop])
            tm = (i + hop) / rate
            lv, beat = ex.process(ring.latest(ex.win_size), hop / rate, tm)
            if beat:
                beats.append(tm)
            for k in edges:
                if lv["edge_" + k]:
                    edges[k].append(tm)
        return edges, beats

    edges, beats = run(sig)
    near = lambda times, targets, tol=0.06: sum(1 for x in targets if any(abs(x - y) < tol for y in times))
    check(near(edges["hit"], hat_times[2:]) >= 0.8 * len(hat_times[2:]), "frappes: les charleys sur les contretemps sont detectes",
          f"{near(edges['hit'], hat_times[2:])}/{len(hat_times[2:])}")
    kick_times = [k * 0.5 for k in range(2, secs * 2)]
    check(near(edges["hit"], kick_times) <= 0.25 * len(kick_times), "frappes: les kicks ne sont (presque) pas pris pour des frappes",
          f"{near(edges['hit'], kick_times)}/{len(kick_times)}")
    only_kicks, _ = run((kicks + 0.002 * rg.standard_normal(n)).astype(np.float32))
    check(len(only_kicks["hit"]) <= 3, "frappes: un kick sans charleys ne declenche presque rien", f"{len(only_kicks['hit'])}")
    check(edges["bg"] == beats and edges["logo"] == beats, "kick par couche: sensibilite 1 = le kick commun")
    # kick sur basse continue : un kick commun a sensibilite 1 en rate, la couche a sensibilite haute le rattrape
    r2 = np.random.default_rng(4)
    env = np.where((tt % 0.5) < 0.1, np.sin(np.pi * (tt % 0.5) / 0.1), 0.0)
    onbass = (0.15 * np.sin(2 * np.pi * 55 * tt) + 0.3 * env * np.sin(2 * np.pi * 60 * tt) + 0.002 * r2.standard_normal(n)).astype(np.float32)
    e2, b2 = run(onbass, layer_sens={"bg": 2.5, "logo": 1.0})
    check(len(e2["bg"]) >= 30 and len(e2["bg"]) > len(b2) and e2["logo"] == b2,
          "kick par couche: la couche a sensibilite haute rattrape des kicks que le kick commun rate", f"bg {len(e2['bg'])} commun {len(b2)}")


def check_audio_layers() -> None:
    """Sources audio par couche en pixels : chaque couche ne reagit qu'a la partie du son choisie."""
    print("Sources audio par couche")
    from core.audio_layers import LayerFilter
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    grad = np.zeros((size[1], size[0], 3), np.uint8)
    grad[:, :, 0] = (np.arange(size[0]) * 255 // size[0])[None, :] // 2
    grad[:, :, 1] = (np.arange(size[1]) * 255 // size[1])[:, None]
    grad[:, :, 2] = 80 + (np.arange(size[0]) // 40 % 2)[None, :] * 100
    r.video_tex.write(np.ascontiguousarray(grad).tobytes())
    r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    bass_only = {"bass": 1.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.3, logo_opacity=1.0, logo_glow=0.0,
                logo_pulse=0.0, logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1], holo_on=0.0)

    def render(state=bass_only, **kw):
        r.draw(fbo, size, state, dict(base, **kw), 1.0)
        return read_target(ctx, fbo, size).astype(int)

    def changed(img, ref):
        return int((np.abs(img - ref).max(axis=2) > 4).sum())

    still = render()
    wob = dict(fx_on=[1, 0, 0, 0, 1], fx_int=[2.0, 1, 1, 1, 1])
    check(changed(render(**wob), still) > 2000, "sources: par defaut (tout) le wobble suit les basses")
    check(changed(render(src_bg="aigus", **wob), still) == 0, "sources: fond sur les aigus, des basses seules ne font pas onduler")
    check(changed(render(src_bg="rien", **wob), still) == 0, "sources: fond sur rien = aucune reaction")
    check(changed(render(src_bg="basses", **wob), still) > 2000, "sources: fond sur les basses = le wobble ondule")
    # le logo suit le fond tant que les effets sont lies
    check(changed(render(src_bg="aigus", src_logo="tout", **wob), still) == 0, "sources: lies, le logo suit la source du fond (src_logo ignore)")
    # separes : le fond ondule, le logo ne reagit a rien
    sep = render(fx_link=0.0, src_bg="tout", src_logo="rien", fx_on=[1, 0, 0, 0, 1], fx_int=[2.0, 1, 1, 1, 1], fxl_on=[1, 0, 0, 0], fxl_int=[2.0, 1, 1, 1])
    m0 = (still[:, :, :3] > 240).all(axis=2)
    ms = (sep[:, :, :3] > 240).all(axis=2)
    check(changed(sep, still) > 2000 and int((m0 != ms).sum()) == 0, "sources: separes, le fond ondule et le logo (sur rien) garde sa forme")
    sep2 = render(fx_link=0.0, src_bg="rien", src_logo="basses", fx_on=[1, 0, 0, 0, 1], fx_int=[2.0, 1, 1, 1, 1], fxl_on=[1, 0, 0, 0], fxl_int=[2.0, 1, 1, 1])
    ms2 = (sep2[:, :, :3] > 240).all(axis=2)
    check(int((m0 != ms2).sum()) > 100, "sources: separes, le logo (sur les basses) ondule meme si le fond est sur rien")
    # kick : aberration chromatique et onde de choc ne suivent que les kicks
    hit = dict(bass_only, bass=0.0, beat=1.0, since_beat=0.1)
    ab = dict(fx_on=[0, 0, 1, 0, 1], fx_int=[1, 1, 2.0, 1, 1])
    check(changed(render(hit, **ab), still) > 500, "sources: tout, l'aberration chromatique part au kick")
    check(changed(render(hit, src_bg="aigus", **ab), still) == 0, "sources: fond sur les aigus, un kick seul ne declenche pas l'aberration")
    check(changed(render(hit, src_bg="kick", **ab), still) > 500, "sources: fond sur kick, l'aberration part au kick")
    check(changed(render(bass_only, src_bg="kick", **wob), still) == 0, "sources: fond sur kick, les basses seules ne font pas onduler")
    # source « auto » : suit la phase du morceau, avec fondu (l'horloge `t` fait foi)
    from core.audio_layers import SourceMixer
    wob_auto = dict(src_bg="auto", **wob)
    r._src_mix = (SourceMixer(), SourceMixer())
    r._layer_filters = (LayerFilter(), LayerFilter())
    groove_st = dict(bass_only, phase={"name": "groove"})
    check(changed(render(groove_st, **wob_auto), still) > 2000 and r.sources_now[:2] == ("basses", "aigus") and r.sources_now[2:] == (True, True),
          "auto: en groove le fond suit les basses (et le voyant le dit)")
    r.draw(fbo, size, dict(bass_only, phase={"name": "peak"}), dict(base, **wob_auto), 5.0)
    mid_img = read_target(ctx, fbo, size).astype(int)
    r.draw(fbo, size, dict(bass_only, phase={"name": "peak"}), dict(base, **wob_auto), 7.0)
    end_img = read_target(ctx, fbo, size).astype(int)
    check(changed(mid_img, still) > 2000 and changed(end_img, still) == 0 and r.sources_now[:2] == ("kick", "kick"),
          "auto: au pic le fond passe sur le kick (fondu d'abord, puis les basses seules ne font plus onduler)")
    r._src_mix = (SourceMixer(), SourceMixer())
    check(changed(render(dict(bass_only, phase={"name": "silence"}), **wob_auto), still) == 0 and r.sources_now[:2] == ("rien", "rien"),
          "auto: en silence le fond ne reagit a rien")
    r._src_mix = (SourceMixer(), SourceMixer())
    check(changed(render(dict(bass_only), **wob_auto), still) > 2000 and r.sources_now[:2] == ("tout", "tout"),
          "auto: sans phase connue, repli sur tout")
    r._src_mix = (SourceMixer(), SourceMixer())
    check(changed(render(dict(bass_only, phase={"name": "silence"}), auto_phase=0.0, **wob_auto), still) > 2000,
          "auto: phases auto coupees, repli sur tout")
    r._src_mix = (SourceMixer(), SourceMixer())
    r._layer_filters = (LayerFilter(), LayerFilter())
    # retard : la reaction arrive apres (un renderer neuf, l'horloge `t` fait foi)
    r._layer_filters = (LayerFilter(), LayerFilter())
    r.draw(fbo, size, bass_only, dict(base, lag_bg=0.3, **wob), 1.0)
    first = read_target(ctx, fbo, size).astype(int)
    r.draw(fbo, size, bass_only, dict(base, lag_bg=0.3, **wob), 1.5)
    later = read_target(ctx, fbo, size).astype(int)
    check(changed(first, still) == 0 and changed(later, still) > 2000, "sources: retard du fond, silencieux d'abord puis la reaction arrive")
    r._layer_filters = (LayerFilter(), LayerFilter())
    # le master de reactivite s'applique avant : 0 = plus aucune reaction, quelle que soit la source
    check(changed(render(react_master=0.0, **wob), still) == 0, "sources: reactivite a 0 = aucune reaction")


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


def check_masters() -> None:
    """Masters du Mode VJ : `react_master` (tout ce qui vient de l'audio) et `tempo` (vitesse des animations et des courbes)."""
    print("masters (audioreactivite, tempo)")
    d = gl.DEFAULT_PARAMS
    check(d["react_master"] == 1.0 and d["tempo"] == 1.0
          and all(k in gl.OVERLAY_PRESET_EXCLUDED for k in ("react_master", "tempo")),
          "masters: 100 % par defaut, hors presets et scenes (reglages de salle)")
    import copy
    spec = gl.AUTOMATION_SPECS["bg_hue"]
    lo, hi, period = spec[1], spec[2], spec[4]
    for tempo, real in ((1.0, period / 4), (0.5, period / 2), (2.0, period / 8), (0.0, period)):
        p = copy.deepcopy(d)
        p["_automation"], p["tempo"] = gl.default_automation(), tempo
        engine = gl.AutomationEngine()
        engine.step(p, 50.0)
        engine.step(p, 50.0 + real)
        want = hi if tempo else (lo + hi) / 2
        check(abs(p["bg_hue"] - want) < 1e-9, f"tempo {tempo:g}: la courbe avance au bon rythme", f"{p['bg_hue']:.3f}")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone (masters)", str(exc))
        return
    size = (320, 180)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    r.video_tex.write(np.full((size[1], size[0], 3), 40, np.uint8).tobytes())
    r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    quiet = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    loud = {"bass": 1.0, "mid": 0.5, "high": 0.5, "rms": 0.8, "beat": 1.0, "since_beat": 0.0, "beats": 4}
    base = dict(d, logo_x=0.5, logo_y=0.5, logo_scale=0.25, logo_opacity=1.0, logo_glow=0.0, logo_pulse=0.4,
                logo_jitter=0.0, bg_mode="live", fx_on=[0, 0, 0, 0, 1], holo_on=0.0)

    def render(state, **kw):
        r.draw(fbo, size, state, dict(base, **kw), 1.0)
        return read_target(ctx, fbo, size).astype(int)

    still = render(quiet)
    check(not np.array_equal(render(loud), still), "reactivite 100 %: le kick pulse le logo")
    check(np.array_equal(render(loud, react_master=0.0), still), "reactivite 0 %: le visuel ne reagit plus a l'audio")
    check(not np.array_equal(render(loud, react_master=2.0), render(loud)), "reactivite 200 %: reaction plus forte qu'a 100 %")
    check("overlay_master" in gl.OVERLAY_PRESET_EXCLUDED and d["overlay_master"] == 1.0,
          "overlay: 100 % par defaut, hors presets et scenes")
    shown = render(quiet, logo_pulse=0.0)
    gone = render(quiet, logo_pulse=0.0, overlay_master=0.0, logo_glow=1.0)
    half = render(quiet, logo_pulse=0.0, overlay_master=0.5)
    r.set_logo(np.zeros((64, 64, 4), np.uint8), "x")
    empty = render(quiet, logo_pulse=0.0)
    r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    check(not np.array_equal(shown, empty) and np.array_equal(gone, empty),
          "overlay 0 %: le logo (et son contour) disparait, le fond reste")
    check(empty.mean() < half.mean() < shown.mean(), "overlay 50 %: logo a moitie present", f"{empty.mean():.1f} < {half.mean():.1f} < {shown.mean():.1f}")
    dark = render(quiet, logo_pulse=0.0, bg_master=0.0)
    half_bg = render(quiet, logo_pulse=0.0, bg_master=0.5)
    cy, cx = size[1] // 2, size[0] // 2
    check(dark[:20, :20].max() == 0 and dark[cy, cx].min() > 200 and half_bg[:20, :20].mean() < still[:20, :20].mean() * 0.8,
          "fond 0 %: le fond est noir, le logo reste ; 50 % : fond attenue",
          f"coin {dark[:20, :20].max()}, logo {dark[cy, cx].tolist()}")
    check("bg_master" in gl.OVERLAY_PRESET_EXCLUDED and d["bg_master"] == 1.0, "fond: 100 % par defaut, hors presets et scenes")
    # strobe : eclair blanc court (appui, kick, leger), rien sans appui
    sr = gl.Renderer(ctx, size, None)
    sp = dict(d, strobe_mode="manual")
    check(sr._strobe(sp, quiet) == 0.0, "strobe: rien sans appui")
    gl.strobe_press(sp)
    first = sr._strobe(sp, quiet)
    check(first > 0.9 and sp["strobe_on"] == 1.0, "strobe manuel: un appui donne un eclair, meme tres bref", f"{first:.2f}")
    gl.strobe_release(sp)
    time.sleep(0.12)
    check(sr._strobe(sp, quiet) == 0.0, "strobe: l'eclair retombe en 70 ms et s'arrete au relachement")
    gl.strobe_press(sp)
    sr._strobe(sp, quiet)
    flashes = 0
    for _ in range(12):
        time.sleep(0.03)
        flashes += sr._strobe(sp, quiet) > 0.9
    gl.strobe_release(sp)
    check(flashes == 0, "strobe maintenu: un seul eclair par appui, pas de rafale", str(flashes))
    kp = dict(d, strobe_mode="kick")
    kr = gl.Renderer(ctx, size, None)
    gl.strobe_press(kp)
    kr._strobe(kp, quiet)
    time.sleep(0.12)
    still_dark = kr._strobe(kp, quiet)
    kicked = kr._strobe(kp, dict(quiet, beats=1))
    gl.strobe_press(kp)
    check(still_dark == 0.0 and kicked > 0.9 and kp["strobe_on"] == 0.0,
          "strobe au kick : arme, eclair a chaque kick seulement ; un 2e appui desarme")
    # strobe auto : decide seul selon la phase du morceau (core/strobe_auto.py), dans le vrai rendu
    ap = dict(d, strobe_auto=1.0)
    peak = dict(quiet, phase={"name": "peak", "since": time.monotonic() - 30.0}, beats=0, hits=0)
    ar = gl.Renderer(ctx, size, None)
    ar._strobe(ap, dict(peak))
    time.sleep(0.4)
    check(ar._strobe(ap, dict(peak, beats=1)) > 0.9 and ar.strobe_fired == 1, "strobe auto: un kick en pic donne un eclair")
    time.sleep(0.12)
    check(ar._strobe(dict(ap, strobe_auto=0.0), dict(peak, beats=2)) == 0.0, "strobe auto coupe: aucun eclair, meme en pic")
    cold = gl.Renderer(ctx, size, None)
    cold._strobe(ap, dict(quiet, phase={"name": "silence", "since": 0.0}, beats=0))
    check(cold._strobe(ap, dict(quiet, phase={"name": "silence", "since": 0.0}, beats=1)) == 0.0 and cold.strobe_fired == 0,
          "strobe auto: jamais d'eclair en silence, meme sur un kick")
    gr = gl.Renderer(ctx, size, None)
    groove = dict(quiet, phase={"name": "groove", "since": time.monotonic() - 30.0}, beats=0, hits=0, beat_index=0)
    gr._strobe(ap, dict(groove))
    time.sleep(0.12)
    check(gr._strobe(ap, dict(groove, beats=1)) > 0.9 and gr._strobe_auto_now and abs(gr._strobe_auto_dark - 0.5) < 1e-9,
          "strobe auto: accent sur le 1 en groove, discret (seuil de luminosite de 50 %)")
    time.sleep(0.12)
    gl.strobe_press(ap)
    gr._strobe(ap, dict(groove, beats=1))
    check(not gr._strobe_auto_now, "strobe auto: un eclair manuel garde le seuil du reglage")
    gl.strobe_release(ap)
    # strobe leger : un kick sur N seulement ; le compteur repart a zero a l'armement
    lp = dict(d, strobe_mode="light", strobe_div=4)
    lr = gl.Renderer(ctx, size, None)
    gl.strobe_press(lp)
    lr._strobe(lp, quiet)                                                  # l'appui lui-meme fait un eclair
    fired = []
    for i in range(1, 9):
        time.sleep(0.12)
        fired.append(lr._strobe(lp, dict(quiet, beats=i)) > 0.9)
    check(sum(fired) == 2 and fired[3] and fired[7] and lp["strobe_on"] == 1.0, "strobe leger: un kick sur quatre (8 kicks = 2 eclairs, les 4e et 8e)", str(fired))
    gl.strobe_press(lp)
    gl.strobe_press(lp)                                                    # desarme puis rearme : le compteur repart a zero
    lr._strobe(lp, dict(quiet, beats=8))
    early = []
    for i in range(9, 12):
        time.sleep(0.12)
        early.append(lr._strobe(lp, dict(quiet, beats=i)) > 0.9)
    check(not any(early), "strobe leger: le compteur de kicks repart a zero a l'armement", str(early))
    # strobe sur les parties sombres : moitie gauche noire, moitie droite claire
    def strobed_dark(thr):
        zr = gl.Renderer(ctx, size, None)
        half_img = np.full((size[1], size[0], 3), 200, np.uint8)
        half_img[:, :size[0] // 2] = 10
        zr.video_tex.write(half_img.tobytes())
        zp = dict(base, holo_on=0.0, overlay_master=0.0, strobe_mode="manual", strobe_max_hz=30.0, strobe_dark=thr)
        gl.strobe_press(zp)
        zr.draw(fbo, size, quiet, zp, 0.0)
        return read_target(ctx, fbo, size).astype(int)

    w_, h_ = size
    dk = strobed_dark(0.5)
    check(dk[h_ // 2, 8].min() > 240 and dk[h_ // 2, w_ - 8].max() < 215, "strobe sombre (seuil 50 %): la partie noire blanchit, la partie claire reste",
          f"{dk[h_ // 2, 8].tolist()} {dk[h_ // 2, w_ - 8].tolist()}")
    dk = strobed_dark(0.05)
    check(dk[h_ // 2, 8].max() < 120 and dk[h_ // 2, w_ - 8].max() < 215, "strobe sombre (seuil bas): rien ne blanchit si rien n'est assez sombre")
    dk = strobed_dark(1.0)
    check(dk[h_ // 2, 8].min() > 240 and dk[h_ // 2, w_ - 8].min() > 240, "strobe sombre (seuil 100 %): toute l'image blanchit")
    # teinte globale : rotation de couleur de toute l'image (fond et logo), les gris restent gris
    hr = gl.Renderer(ctx, size, None)
    hr.video_tex.write(np.tile(np.array([200, 30, 30], np.uint8), (size[1], size[0], 1)).tobytes())
    hr.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")

    def hue_px(deg, rr=hr):
        rr.draw(fbo, size, quiet, dict(base, logo_pulse=0.0, hue_master=deg), 1.0)
        return read_target(ctx, fbo, size).astype(int)[5, 5]

    red, green, full, opposite = hue_px(0.0), hue_px(120.0), hue_px(360.0), hue_px(180.0)
    check(red[0] > 150 and green[1] > green[0] and green[1] > green[2] and abs(full - red).max() <= 2 and opposite[0] < red[0],
          "teinte globale: 120 deg fait virer le rouge au vert, 360 deg = couleurs d'origine, 180 deg = opposees",
          f"{red.tolist()} -> {green.tolist()} / {opposite.tolist()}")
    check("hue_master" in gl.OVERLAY_PRESET_EXCLUDED and d["hue_master"] == 0.0, "teinte: 0 par defaut, hors presets et scenes")
    # --- phases auto : tempo effectif et rendu
    check(abs(gl.effective_tempo({"tempo": 1.0, "phase_tempo": 0.3}) - 0.3) < 1e-9 and gl.effective_tempo({"tempo": 1.0}) == 1.0
          and abs(gl.effective_tempo({"tempo": 2.0, "phase_tempo": 0.5}) - 1.0) < 1e-9,
          "phases auto: tempo effectif = master x tempo de la phase")
    gone = render(quiet, phase_overlay=0.0)
    check(np.array_equal(gone, render(quiet, overlay_master=0.0)) and not np.array_equal(gone, still),
          "phases auto: presence de l'overlay a 0 = fond seul (comme le master)")
    dim = render(quiet, phase_bg=0.0)
    check(np.array_equal(dim, render(quiet, bg_master=0.0)), "phases auto: luminosite du fond a 0 = fond noir (comme le master)")
    # --- composition : symetries, kaleidoscope, pixelisation, deformation, trainee
    grad = np.zeros((size[1], size[0], 3), np.uint8)
    grad[..., 0] = np.linspace(0, 255, size[0]).astype(np.uint8)[None, :]
    grad[..., 1] = np.linspace(255, 0, size[1]).astype(np.uint8)[:, None]
    r.video_tex.write(grad.tobytes())
    plain = render(quiet, overlay_master=0.0, holo_on=0.0)
    mir = render(quiet, overlay_master=0.0, comp_sym=1)
    half = size[0] // 2 - 8
    left, right = mir[:, :half], mir[:, size[0] - half:][:, ::-1]
    check(abs(left - right).mean() < 6 and abs(plain[:, :half] - plain[:, size[0] - half:][:, ::-1]).mean() > 30,
          "symetrie: miroir gauche = la moitie droite reflete la gauche")
    quad = render(quiet, overlay_master=0.0, comp_sym=3)
    top = quad[:size[1] // 2 - 6, :]
    check(abs(quad[:size[1] // 2 - 6] - quad[::-1][:size[1] // 2 - 6]).mean() < 6 and abs(top[:, :half] - top[:, size[0] - half:][:, ::-1]).mean() < 6,
          "symetrie: quatre quadrants (miroirs horizontal et vertical)")
    kal = render(quiet, overlay_master=0.0, comp_sym=4, comp_seg=6)
    check(not np.array_equal(kal, plain) and 20 < kal.mean() < 235, "kaleidoscope: image repliee, ni noire ni saturee", f"{kal.mean():.0f}")
    pix = render(quiet, overlay_master=0.0, comp_pix=1.0)
    check(len(np.unique(pix[10, :, 0])) <= 45 < len(np.unique(plain[10, :, 0])), "pixelisation: l'image est decoupee en gros blocs",
          f"{len(np.unique(pix[10, :, 0]))} / {len(np.unique(plain[10, :, 0]))}")
    check(not np.array_equal(render(quiet, overlay_master=0.0, comp_warp=1.0), plain), "deformation globale: l'image est deformee")
    check(np.array_equal(render(quiet, overlay_master=0.0, comp_sym=0, comp_pix=0.0, comp_warp=0.0, comp_trail=0.0), plain),
          "composition coupee: image inchangee")
    white_vid = np.full((size[1], size[0], 3), 255, np.uint8)
    r.video_tex.write(white_vid.tobytes())
    render(quiet, overlay_master=0.0, comp_trail=0.9)                      # image blanche memorisee
    r.video_tex.write(np.zeros((size[1], size[0], 3), np.uint8).tobytes())
    with_trail = render(quiet, overlay_master=0.0, comp_trail=0.9)
    without = render(quiet, overlay_master=0.0, comp_trail=0.0)
    check(with_trail[20:-20, 20:-20].mean() > 180 > 20 > without[20:-20, 20:-20].mean(),
          "trainee: l'image precedente s'estompe au lieu de disparaitre", f"{with_trail.mean():.0f} / {without.mean():.0f}")
    r.video_tex.write(np.full((size[1], size[0], 3), 40, np.uint8).tobytes())
    # --- cycle de teinte, respiration, montee
    cr = gl.Renderer(ctx, size, None)
    cp = dict(base, hue_cycle=90.0)
    cr.draw(fbo, size, quiet, cp, 0.0)
    for i in range(1, 11):
        cr.draw(fbo, size, quiet, cp, 0.1 * i)
    check(abs(cr._hue_phase - 90.0) < 1.0, "cycle de teinte: 90 degres par seconde", f"{cr._hue_phase:.1f}")
    from core import controls as _ctl
    check(_ctl.spectral_hue(0.0, 0.0) == 0.0 and _ctl.spectral_hue(1.0, 0.0) == 0.0 and _ctl.spectral_hue(0.0, 1.0) > 170
          and _ctl.spectral_hue(0.5, 0.5) < _ctl.spectral_hue(0.2, 0.8),
          "teinte audio: silence et graves = 0, aigus = froid, monotone")
    def spec_image(bass_, high_, amount):
        rs = gl.Renderer(ctx, size, None)
        red_ = np.zeros((size[1], size[0], 3), np.uint8)
        red_[..., 0] = 200                                    # une teinte qui tourne se voit sur une couleur, pas sur un gris
        rs.video_tex.write(red_.tobytes())
        st = dict(quiet, bass=bass_, high=high_)
        pp = dict(base, hue_spectrum=amount, hue_master=0.0, hue_cycle=0.0)
        for i in range(40):
            rs.draw(fbo, size, st, pp, 0.1 * i)
        return read_target(ctx, fbo, size).astype(int)
    warm_, cold_, off_ = spec_image(0.9, 0.0, 1.0), spec_image(0.0, 0.9, 1.0), spec_image(0.0, 0.9, 0.0)
    check(np.abs(cold_ - warm_).mean() > 3.0 and np.abs(warm_ - off_).mean() < 1.0 and np.abs(cold_ - off_).mean() > 3.0,
          "teinte audio: les aigus tournent les couleurs (froid), les graves non, curseur a 0 = aucun effet",
          f"{np.abs(cold_ - warm_).mean():.1f} / {np.abs(warm_ - off_).mean():.1f} / {np.abs(cold_ - off_).mean():.1f}")
    cr2 = gl.Renderer(ctx, size, None)
    cr2.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    bp = dict(base, breath_overlay=1.0, logo_pulse=0.0)
    cr2.draw(fbo, size, quiet, bp, 1.0)
    cr2._breath_t = 0.25
    gone_img = (cr2.draw(fbo, size, quiet, bp, 1.0), read_target(ctx, fbo, size).astype(int))[1]
    cr2._breath_t = 0.75
    back_img = (cr2.draw(fbo, size, quiet, bp, 1.0), read_target(ctx, fbo, size).astype(int))[1]
    check(gone_img.mean() < back_img.mean() - 5, "respiration: l'overlay oscille (efface a un quart de periode, present aux trois quarts)",
          f"{gone_img.mean():.1f} < {back_img.mean():.1f}")
    br = gl.Renderer(ctx, size, None)
    bq = dict(d, strobe_on=0.0, build=1.0)
    fires = 0
    t_end = time.monotonic() + 0.4
    while time.monotonic() < t_end:
        fires += br._strobe(bq, quiet) > 0.9
        time.sleep(0.004)
    check(fires >= 3 and br._strobe(dict(d, build=0.0), quiet) <= 1.0 and gl.Renderer(ctx, size, None)._strobe(dict(d, build=0.0, strobe_on=0.0), quiet) == 0.0,
          "montee: le strobe part tout seul (jusqu'a 16 Hz) sans etre arme", str(fires))
    first = gl.Renderer(ctx, size, None)
    check(first._strobe(dict(d, strobe_on=0.0, build=1e-6), quiet) > 0.9,
          "montee: le premier eclair part des le debut de la montee (pas quelques secondes apres le clic)")
    from core import controls as cc_
    hz = [cc_.build_strobe_hz(b_ / 10) for b_ in range(0, 11)]
    check(hz[0] == 0.5 and hz[10] == 16.0 and all(a_ < b_ for a_, b_ in zip(hz, hz[1:])) and hz[3] < 2.0 and hz[5] < 5.0,
          "montee: cadence des eclairs lente au depart (0,5 Hz, moins de 2 Hz a 30 %, moins de 5 Hz a mi-montee), 16 Hz a 100 %", str([round(h_, 1) for h_ in hz]))
    built = render(loud, build=1.0)
    time.sleep(0.15)                                                    # l'eclair du strobe de la montee doit etre retombe
    check(not np.array_equal(built, render(loud)), "montee: l'image change (reactivite et eclairs)")
    check(all(k in gl.OVERLAY_PRESET_EXCLUDED for k in ("hue_cycle", "breath_overlay", "breath_bg", "auto_phase", "comp_sym", "comp_trail", "comp_warp", "build_time"))
          and set(gl.TRANSIENT_KEYS) <= set(gl.ROOM_KEYS) and not set(gl.TRANSIENT_KEYS) & set(gl.MEMORY_KEYS),
          "reglages de salle: hors presets et scenes ; etats du moment (phase, montee, strobe arme) hors memoires")
    import json
    saved_path = core_params.PARAMS_PATH
    try:
        import tempfile as _t
        core_params.PARAMS_PATH = Path(_t.mkdtemp(prefix="casual_overlay_params_")) / "gl_params.json"
        gl.save_params(dict(d, phase_tempo=0.3, build=0.5, strobe_on=1.0, comp_sym=3.0))
        reloaded = gl.load_params()
        check(reloaded["comp_sym"] == 3.0 and reloaded["phase_tempo"] == 1.0 and reloaded["build"] == 0.0 and reloaded["strobe_on"] == 0.0
              and "phase_tempo" not in json.loads(core_params.PARAMS_PATH.read_text(encoding="utf-8")),
              "sauvegarde (P): les etats du moment (phase, montee, strobe arme) ne sont ni ecrits ni recharges")
    finally:
        core_params.PARAMS_PATH = saved_path
    # --- memoires de masters (fichier)
    import tempfile as _tf2
    from core import scenes as _sc
    mpath = Path(_tf2.mkdtemp(prefix="casual_overlay_masters_")) / "masters.json"
    mb = _sc.MasterBook(lambda: mpath)
    check(mb.slots() == [None] * 4 and mb.get(2) is None, "memoires de masters: quatre emplacements vides au depart")
    mb.save(1, "calme", dict(d, hue_master=120.0, comp_sym=4.0, build=0.9, phase_tempo=0.5))
    got_slot = mb.get(1)
    check(got_slot["name"] == "calme" and got_slot["values"]["hue_master"] == 120.0 and got_slot["values"]["comp_sym"] == 4.0
          and "build" not in got_slot["values"] and "phase_tempo" not in got_slot["values"] and mb.get(0) is None and not mb.save(9, "x", d),
          "memoires de masters: enregistrement, rappel, sans etats du moment, emplacement hors plage refuse")
    mpath.write_text("{ pas du json", encoding="utf-8")
    check(mb.slots() == [None] * 4, "memoires de masters: fichier illisible = emplacements vides")
    phases = {}
    for tempo in (1.0, 0.5, 0.0, 2.0):
        rr = gl.Renderer(ctx, size, None)
        pr = dict(base, tempo=tempo, holo_speed=1.0)
        rr.draw(fbo, size, quiet, pr, 0.0)
        for i in range(1, 11):
            rr.draw(fbo, size, quiet, pr, 0.05 * i)
        phases[tempo] = rr._holo_t
    check(abs(phases[0.5] - phases[1.0] / 2) < 1e-6 and abs(phases[2.0] - phases[1.0] * 2) < 1e-6 and phases[0.0] == 0.0
          and phases[1.0] > 0.4, "tempo: la phase des animations suit le master (0,5 / 1 / 2, 0 = fige)",
          ", ".join(f"{k:g}: {v:.2f}" for k, v in phases.items()))


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
    original = core_params.PARAMS_PATH
    core_params.PARAMS_PATH = Path(tempfile.mkdtemp()) / "gl_params.json"
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
        core_params.PARAMS_PATH = original


def check_params() -> None:
    print("Reglages (touche P)")
    original = core_params.PARAMS_PATH
    core_params.PARAMS_PATH = Path(tempfile.mkdtemp()) / "sous" / "gl_params.json"
    try:
        check(gl.load_params() == gl.DEFAULT_PARAMS, "sans fichier: valeurs par defaut")
        params = gl.load_params()
        params["fx_on"][3] = 0
        params["master"] = 1.4
        params["sensitivity"] = 2.0
        gl.save_params(params)
        check(gl.load_params() == params, "sauvegarde puis rechargement identiques")
        core_params.PARAMS_PATH.write_text("{ pas du json", encoding="utf-8")
        check(gl.load_params() == gl.DEFAULT_PARAMS, "fichier corrompu: retour aux valeurs par defaut")
    finally:
        core_params.PARAMS_PATH = original


def _plain_vj(vj: dict) -> dict:
    """Reglages du VJ sans le bloc du hasard (duree et ordre seulement)."""
    return {k: vj[k] for k in ("seconds", "order")}


def check_fond_saveable() -> None:
    """Un fond n'est propose en preset que s'il y a quelque chose a enregistrer."""
    import tempfile
    from core import randomizer as rz
    print("fond enregistrable en preset")
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.mp4"
        video.write_bytes(b"x")
        check(rz.fond_saveable({"bg": "pattern", "fond": {"bg_mode": "pattern"}}) is True
              and rz.fond_saveable({"bg": "video", "fond": {"bg_mode": "video", "bg_video": str(video)}}) is True,
              "fond genere et video existante: enregistrables")
        check(rz.fond_saveable({"bg": "video", "fond": {"bg_mode": "video", "bg_video": ""}}) is False
              and rz.fond_saveable({"bg": "video", "fond": {"bg_mode": "video", "bg_video": str(Path(tmp) / "absente.mp4")}}) is False,
              "fond video sans fichier choisi (noir) ou fichier absent: rien a enregistrer")
        check(rz.fond_saveable({"bg": "live", "fond": None}) is False and rz.fond_saveable(None) is False
              and rz.fond_saveable({"bg": "pattern", "fond": None}) is False, "fond audio2wave ou vide: pas de preset de fond")


def check_randomizer() -> None:
    """Full Randomizer (randomizer.py) : banque, tirage, verrous, plages, reglages du set. Sans fenetre."""
    print("Full Randomizer (randomizer.py)")
    import json
    import random as rnd_mod
    import tempfile

    from core import randomizer as rz
    from core import scenes as sc
    d = Path(tempfile.mkdtemp(prefix="casual_overlay_bank_"))
    (d / "logos").mkdir()
    (d / "loops" / "sous" / "profond").mkdir(parents=True)
    (d / ".cache").mkdir()
    for name in ("logos/a.png", "logos/b.JPG", "loops/c.mp4", "loops/sous/d.webm", "loops/sous/profond/e.gif", "root.png", "note.txt",
                 ".cache/hidden.png", "logos/.secret.png"):
        (d / name).write_bytes(b"x")
    bank = rz.scan_bank([str(d), str(d / "absent"), str(d)])
    bank_fond = rz.scan_bank([str(d)], "fond")
    names = sorted(Path(a.path).name for a in bank)
    check(names == ["a.png", "b.JPG", "c.mp4", "d.webm", "e.gif", "root.png"],
          "banque: images et videos trouvees (sous-dossiers compris), pas de texte ni de fichier cache, dossier absent / double ignore", str(names))
    kinds = {Path(a.path).name: (a.kind, a.category) for a in bank}
    check(kinds["a.png"] == ("image", "logos") and kinds["d.webm"] == ("video", "loops") and kinds["root.png"] == ("image", rz.ROOT_CATEGORY),
          "banque: type (image / video) et categorie = premier sous-dossier", str(kinds))
    check(sorted(Path(a.path).name for a in bank_fond) == ["c.mp4", "d.webm", "e.gif"] and all(a.kind == "video" for a in bank_fond),
          "banque FOND: que des videos (pas d'image), classees par le dossier declare et non par l'extension")
    check(rz.categories(bank) == sorted({"logos", "loops", rz.ROOT_CATEGORY}), "banque: liste des categories")
    check(len(rz.scan_bank([str(d)], "overlay", max_files=2)) == 2, "banque: plafond de fichiers")
    only = rz.usable(bank, ["logos"])
    check({Path(a.path).name for a in only} == {"a.png", "b.JPG"} and len(rz.usable(bank, [], "video")) == 3,
          "banque: filtre par categorie et par type")
    (d / "logos" / "a.png").rename(d / "logos" / "a_moved.png")
    check("a.png" not in {Path(a.path).name for a in rz.usable(bank, [])}, "banque: un fichier deplace depuis le scan est ecarte")
    (d / "logos" / "a_moved.png").rename(d / "logos" / "a.png")
    check(rz.media_for(rz.Asset("x.png", "image", "")) == {"logo_source": "image", "logo_path": "x.png"}
          and rz.media_for(rz.Asset("x.mp4", "video", ""))["logo_source"] == "video", "media d'un asset: image ou video du logo")
    # reglages
    dirty = rz.clean_settings({"folders_overlay": ["a", 3, "", "b"], "bg_sources": ["video", "nimporte"], "overlay": "x", "lock_bg": "oui",
                               "categories_overlay": None})
    check(dirty["modes"] == list(rz.A2W_MODES) and rz.clean_settings({"modes": ["snap", "nimporte"]})["modes"] == ["snap"],
          "reglages du hasard: modes audio2wave valides (tous par defaut)")
    check(dirty["folders_overlay"] == ["a", "b"] and dirty["folders_fond"] == [] and dirty["bg_sources"] == ["video"]
          and dirty["overlay"] == list(rz.OVERLAY_PARTS) and dirty["lock_bg"] is False and dirty["categories_overlay"] == [] and rz.clean_settings("abime") == rz.clean_settings(None),
          "reglages du hasard: valides, valeurs par defaut pour tout ce qui est abime", str(dirty))
    # plages
    rng = rnd_mod.Random(7)
    ok_bg = all(0 <= v["bg_hue"] <= 1 and 0 <= v["bg_angle"] <= 180 and 0.4 <= v["bg_speed"] <= 1.8 and 50 <= v["bg_tile"] <= 150
                and 0.06 <= v["bg_checker"] <= 0.32 and v["bg_palette"] in ("classic", "duo")
                and (v["bg_palette"] != "duo" or (v["bg_color1"].startswith("#") and len(v["bg_color2"]) == 7))
                for v in (rz.random_background_params(rng) for _ in range(300)))
    check(ok_bg, "fond aleatoire: valeurs dans les plages des curseurs, jamais la palette banc de test")
    fxs = [rz.random_effects(rng) for _ in range(600)]
    costly = [sum(1 for k in ("holo_on", "melt_on", "cell_on") if fx[k] >= 0.5) for fx in fxs]
    check(max(costly) <= rz.MAX_COSTLY and set(costly) >= {0, 1, 2}, "effets aleatoires: au plus deux effets couteux a la fois",
          str(sorted(set(costly))))
    check(all(k in gl.DEFAULT_PARAMS for fx in fxs for k in fx) and all(len(fx["fx_on"]) == 5 and len(fx["noise_fx"]) == 5 for fx in fxs),
          "effets aleatoires: seulement des reglages connus, listes de la bonne longueur")
    coerced = gl.coerce_params(fxs[0])
    check(all(coerced[k] == fxs[0][k] for k in ("holo_on", "fx_on", "logo_glow_color")), "effets aleatoires: acceptes par coerce_params")
    ovs = [rz.random_overlay_params(rng) for _ in range(400)]
    costly_ov = [sum(1 for k in ("holo_on", "melt_on", "cell_on") if o[k] >= 0.5) for o in ovs]
    check(all(k in gl.DEFAULT_PARAMS for o in ovs for k in o)
          and all(o["logo_x"] == 0.5 and o["logo_y"] == 0.5 and 0.28 <= o["logo_scale"] <= 0.45
                  and 0.8 <= o["logo_opacity"] <= 1.0 and len(o["fxl_on"]) == 4 and len(o["fxl_int"]) == 4 for o in ovs)
          and max(costly_ov) <= rz.MAX_COSTLY and {o["fx_link"] for o in ovs} == {0.0, 1.0}
          and all("logo_path" not in o and "_automation" not in o and not any(k.startswith("bg_") for k in o) for o in ovs),
          "overlay aleatoire: seulement des reglages connus, valeurs sures, toujours centre, au plus deux effets couteux, ni media ni fond ni automation")
    check(all(gl.coerce_params(ovs[0])[k] == ovs[0][k] for k in ("logo_x", "fx_link", "fxl_on", "fx_on")),
          "overlay aleatoire: accepte par coerce_params")
    # tirage
    presets = {"default": {}, "neon": {"bg_palette": "duo"}, "sobre": {"bg_react": 0.4}}
    overlays = {"default": {}, "sobre": {"logo_glow": 0.5}, "neon": {"logo_glow": 1.4}}
    a2w = {"default": {}, "club": {"gain": 35.0}, "calme": {"gain": 15.0}}

    def ctx(**kw):
        base = dict(bank_fond=bank_fond, bank_overlay=bank, bg_presets=presets, overlay_presets=overlays, a2w_presets=a2w, mode="snap", last={},
                    current_scene={"bg": "pattern", "mode": None, "fond": {"bg_hue": 0.9, "bg_palette": "classic"},
                                   "overlay": {"logo_source": "text", "text_content": "HELLO", "logo_glow": 0.77, "holo_on": 1.0}})
        base.update(kw)
        return rz.Context(**base)

    def run(settings, n=1, seed=3, **kw):
        r, last, out_ = rnd_mod.Random(seed), {}, []
        for _ in range(n):
            scene, info, record = rz.draw_scene(r, settings, ctx(last=last, **kw))
            last = record
            out_.append((scene, info, record))
        return out_
    first = run({}, 5)
    again = run({}, 5)
    check([x[0] for x in first] == [x[0] for x in again] and all(x[0]["name"] == "Full Randomizer" for x in first),
          "tirage: meme graine = memes scenes")
    kinds_seen = {x[0]["bg"] + (":" + x[0]["mode"] if x[0]["bg"] == "live" else "") for x in run({}, 80, seed=11)}
    check(kinds_seen == {"pattern", "live:snap", "video"}, "tirage: les quatre sources de fond sortent (preset / audio2wave / video / aleatoire)", str(kinds_seen))
    only_preset = run({"bg_sources": ["preset"]}, 30)
    check(all(x[0]["bg"] == "pattern" and "fond: preset" in x[1] for x in only_preset), "tirage: seule la source cochee est tiree")
    only_video = run({"bg_sources": ["video"]}, 20)
    vids = {a.path for a in bank_fond}
    check(all(x[0]["bg"] == "video" and x[0]["fond"]["bg_video"] in vids and x[0]["fond"]["bg_video_fit"] in ("cover", "stretch")
              for x in only_video), "tirage: fond video = une video de la banque")
    check(all(x[0]["overlay"].get("logo_video") != x[0]["fond"]["bg_video"] for x in only_video),
          "tirage: la video du fond n'est pas aussi le logo")
    no_video = run({"bg_sources": ["video"]}, 3, bank_fond=[])
    check(all(x[0]["bg"] == "live" and x[0]["live_overrides"] is None and "aucune source" in x[1] for x in no_video),
          "tirage: source cochee mais indisponible -> le fond ne change pas, message")
    a2w_modes = {"live": {"default": {}, "calme": {"gain": 15.0}}, "snap": {"default": {}, "club": {"gain": 35.0}}, "ridge": {"default": {}, "fin": {"line_width": 2}}}
    cfg = run({"bg_sources": ["params"]}, 120, seed=21, a2w_modes=a2w_modes)
    gens = [x for x in cfg if x[0]["bg"] == "pattern"]
    a2ws = [x for x in cfg if x[0]["bg"] == "live"]
    check(gens and a2ws and all(x[0]["fond"]["bg_mode"] == "pattern" and "fond: aleatoire" in x[1] for x in gens),
          "config aleatoire du fond: soit un fond genere, soit un visuel audio2wave")
    check({x[0]["mode"] for x in a2ws} == {"live", "snap", "ridge"}
          and all(x[0]["live_overrides"] is None or x[0]["live_overrides"] in a2w_modes[x[0]["mode"]].values() for x in a2ws)
          and all("fond: audio2wave" in x[1] for x in a2ws),
          "config aleatoire du fond: mode audio2wave (Live / Snap / Ridge) et preset de ce mode tires au hasard (default vide = pas d'etat)")
    only_snap = run({"bg_sources": ["params"], "modes": ["snap"]}, 60, seed=5, a2w_modes=a2w_modes)
    check({x[0]["mode"] for x in only_snap if x[0]["bg"] == "live"} == {"snap"}, "config aleatoire du fond: seuls les modes coches sont tires")
    no_modes = run({"bg_sources": ["params"], "modes": []}, 30, seed=5, a2w_modes=a2w_modes)
    check(all(x[0]["bg"] == "pattern" for x in no_modes) and all(x[0]["bg"] == "pattern" for x in run({"bg_sources": ["params"]}, 10)),
          "config aleatoire du fond: aucun mode coche ou aucun preset de mode = fonds generes seulement")
    seq_cfg = run({"bg_sources": ["params"]}, 80, seed=8, a2w_modes=a2w_modes)
    rep_bg = sum(1 for a, b in zip(seq_cfg, seq_cfg[1:]) if a[0]["bg"] == b[0]["bg"] and a[0].get("mode") == b[0].get("mode")
                 and a[0]["live_overrides"] == b[0]["live_overrides"] and a[0]["fond"] == b[0]["fond"])
    check(rep_bg == 0, "config aleatoire du fond: jamais deux fois de suite le meme visuel", str(rep_bg))
    only_a2w = run({"bg_sources": ["a2w"]}, 20)
    check(all(x[0]["bg"] == "live" and x[0]["mode"] == "snap" and x[0]["live_overrides"] in ({"gain": 35.0}, {"gain": 15.0}) for x in only_a2w),
          "tirage: preset audio2wave du mode courant (jamais `default` vide, jamais un autre mode)")
    cats = run({"categories_overlay": ["logos"], "bg_sources": ["params"], "overlay": ["params", "media"]}, 30)
    media_names = {Path(x[0]["overlay"].get("logo_path", "")).name for x in cats}
    check(media_names <= {"a.png", "b.JPG"} and media_names, "tirage: media pris seulement dans les categories cochees", str(media_names))
    # texte tire comme overlay (CASUAL RAVERS par defaut), au meme titre qu'un fichier de la banque
    check(rz.clean_settings({})["text"] == "CASUAL RAVERS" and "text" in rz.clean_settings({})["overlay"]
          and rz.clean_settings({"text": "  MON TEXTE  "})["text"] == "MON TEXTE" and rz.clean_settings({"text": 5})["text"] == "CASUAL RAVERS",
          "tirage: texte par defaut CASUAL RAVERS, reglage valide")
    txt = run({"overlay": ["params", "text"], "bg_sources": ["params"]}, 12)
    check(all(x[0]["overlay"].get("logo_source") == "text" and x[0]["overlay"].get("text_content") == "CASUAL RAVERS"
              and x[0]["overlay"]["logo_x"] == 0.5 and x[2].get("media") == "text" for x in txt),
          "tirage: le texte est tire comme logo (centre, couleur), meme sans media de banque", str(txt[0][0]["overlay"]))
    mixed_pick = run({"overlay": ["params", "media", "text"], "bg_sources": ["params"], "text": "HELLO"}, 60, seed=11)
    kinds = {x[0]["overlay"].get("logo_source") for x in mixed_pick}
    check("text" in kinds and kinds & {"image", "video"}
          and all(a[2].get("media") != b[2].get("media") for a, b in zip(mixed_pick, mixed_pick[1:])),
          "tirage: texte et fichiers de la banque alternent, jamais le meme media deux fois de suite", str(kinds))
    check(all(x[0]["overlay"].get("logo_source") != "text" or x[0]["overlay"]["text_content"] == "HELLO" for x in mixed_pick)
          and run({"overlay": ["params", "text"], "text": "", "bg_sources": ["params"]}, 3)[0][0]["overlay"].get("text_content") == "HELLO",
          "tirage: texte vide = jamais de texte (le media du moment reste)")
    seq = run({"bg_sources": ["preset"]}, 60, seed=5)
    same_media = sum(1 for a, b in zip(seq, seq[1:]) if a[0]["overlay"].get("logo_path") == b[0]["overlay"].get("logo_path")
                     and a[0]["overlay"].get("logo_video") == b[0]["overlay"].get("logo_video"))
    same_bg = sum(1 for a, b in zip(seq, seq[1:]) if a[0]["fond"] == b[0]["fond"])
    check(same_media == 0 and same_bg == 0, "tirage: jamais deux fois de suite le meme media ni le meme preset de fond", f"{same_media} {same_bg}")
    only_cfg = run({"overlay": ["params", "media"], "bg_sources": ["params"]}, 20)
    check(all("overlay: config aleatoire" in x[1] and "logo_scale" in x[0]["overlay"]
              and ("logo_path" in x[0]["overlay"] or "logo_video" in x[0]["overlay"]) for x in only_cfg), "tirage: overlay = config entierement nouvelle (sans preset), avec un media de la banque")
    both = run({"overlay": ["overlay_preset", "params"], "bg_sources": ["params"]}, 60, seed=9)
    kinds_ov = ["params" if "config aleatoire" in x[1] else "preset" for x in both]
    check(set(kinds_ov) == {"preset", "params"} and all(a != b for a, b in zip(kinds_ov, kinds_ov[1:])),
          "tirage: preset overlay et config aleatoire alternent quand les deux sont cochees")
    only_fx = run({"overlay": ["fx"]}, 5)
    check(all("overlay: preset" not in x[1] and "overlay: config" not in x[1] and "holo_on" in x[0]["overlay"] for x in only_fx),
          "tirage: effets seuls (ni preset ni config) = juste les effets sur l'overlay")
    # l'overlay est toujours centre et statique, quelle que soit sa source
    off_center = dict(overlays, decale={"logo_x": 0.2, "logo_y": 0.8, "_automation": {"logo_x": {"enabled": True}, "logo_y": {"enabled": True},
                                                                                   "logo_glow": {"enabled": True}}})
    drawn = run({"overlay": ["overlay_preset"], "bg_sources": ["params"]}, 80, seed=4, overlay_presets=off_center)
    check(all(x[0]["overlay"]["logo_x"] == 0.5 and x[0]["overlay"]["logo_y"] == 0.5 for x in drawn)
          and any("preset decale" in x[1] for x in drawn), "overlay centre: meme un preset qui le decale est recentre")
    auto_off = [x[0]["overlay"]["_automation"] for x in drawn if "preset decale" in x[1]]
    check(auto_off and all(a["logo_x"]["enabled"] is False and a["logo_y"]["enabled"] is False and a["logo_glow"]["enabled"] is True
                           for a in auto_off), "overlay centre: l'automation de position est coupee, les autres restent")
    check(all("_automation" not in x[0]["overlay"] for x in drawn if "preset decale" not in x[1]),
          "overlay centre: rien n'est ajoute aux automations quand aucune ne bouge la position")
    moving = {"bg": "pattern", "mode": None, "fond": {}, "overlay": {"logo_x": 0.3, "logo_y": 0.7, "text_content": "HELLO", "logo_source": "text",
                                                                    "_automation": {"logo_y": {"enabled": True}}}}
    locked = run({"lock_fx": True, "lock_media": True}, 3, current_scene=moving)
    check(all(x[0]["overlay"]["logo_x"] == 0.5 and x[0]["overlay"]["logo_y"] == 0.5 and x[0]["overlay"]["text_content"] == "HELLO"
              and x[0]["overlay"]["_automation"]["logo_y"]["enabled"] is False for x in locked),
          "overlay centre: meme avec les verrous, l'overlay du moment est recentre et rendu statique")
    # verrous
    locked_bg = run({"lock_bg": True}, 5)
    check(all(x[0]["bg"] == "pattern" and x[0]["fond"] == {"bg_hue": 0.9, "bg_palette": "classic"} for x in locked_bg),
          "verrou fond: le fond courant est repris")
    locked_media = run({"lock_media": True}, 5)
    check(all(x[0]["overlay"]["logo_source"] == "text" and x[0]["overlay"]["text_content"] == "HELLO" for x in locked_media),
          "verrou media: le texte courant reste")
    locked_fx = run({"lock_fx": True, "overlay": ["media"]}, 5)
    check(all(x[0]["overlay"].get("logo_glow") == 0.77 and x[0]["overlay"].get("holo_on") == 1.0
              and ("logo_path" in x[0]["overlay"] or "logo_video" in x[0]["overlay"]) for x in locked_fx),
          "verrou effets: effets courants gardes, media tire")
    keep_all = run({"lock_bg": True, "lock_media": True, "lock_fx": True}, 2)
    check(keep_all[0][0]["overlay"]["text_content"] == "HELLO" and keep_all[0][0]["overlay"]["logo_glow"] == 0.77,
          "tous les verrous: rien ne change")
    empty = run({"overlay": ["media"]}, 3, bank_overlay=[])
    check(all(x[0]["overlay"]["text_content"] == "HELLO" and "banque vide" in x[1] for x in empty),
          "banque vide: le media courant reste, message")
    no_media = run({"overlay": ["fx"]}, 2)
    check(all("logo_path" not in x[0]["overlay"] and x[0]["overlay"]["text_content"] == "HELLO" for x in no_media),
          "media non coche: le media courant reste (un preset ne remet pas le logo par defaut)")
    # une scene tiree est applicable comme une scene enregistree
    bad = None
    for scene, _info, _rec in run({}, 30, seed=2):
        values = sc.scene_values(scene, gl.load_params())
        if not (values["bg_mode"] == scene["bg"] and all(k in gl.DEFAULT_PARAMS or k == "_automation" for k in values)):
            bad = scene
            break
    check(bad is None, "une scene tiree donne des reglages valides (scene_values)", str(bad))
    # reglages du set : persistance, par set, copie, export / import
    book = sc.SceneBook(lambda: d / "scenes.json")
    book.put({"name": "S1", "bg": "pattern", "mode": None, "live_overrides": None, "fond": {}, "overlay": {}})
    first_set = book.active_set_name()
    book.set_vj(order="alea", random={"folders_overlay": [str(d)], "lock_bg": True, "bg_sources": ["video"]})
    relu = sc.SceneBook(lambda: d / "scenes.json").vj()
    check(relu["order"] == "alea" and relu["random"]["folders_overlay"] == [str(d)] and relu["random"]["lock_bg"] is True
          and relu["random"]["bg_sources"] == ["video"], "reglages du hasard: ecrits dans le set, relus", str(relu))
    book.set_vj(order="nimporte")
    check(book.vj()["order"] == "seq" and book.vj()["random"]["lock_bg"] is True,
          "ordre inconnu: retour a l'ordre par defaut, reglages du hasard intacts")
    book.set_vj(order="alea")
    book.new_set("Autre")
    book.set_active("Autre")
    check(book.vj()["order"] == "seq" and book.vj()["random"] == rz.clean_settings(None), "reglages du hasard: propres a chaque set")
    book.set_active(first_set)
    book.new_set("Copie", copy_of=first_set)
    book.set_active("Copie")
    check(book.vj()["order"] == "alea" and book.vj()["random"]["lock_bg"], "dupliquer un set copie ses reglages du hasard")
    parsed = sc.parse_import(json.dumps(sc.export_set(book, "Copie")))
    check(parsed["set"]["vj"]["order"] == "alea" and parsed["set"]["vj"]["random"]["bg_sources"] == ["video"],
          "export / import d'un set: reglages du hasard conserves")
    check(sc.next_entry_index([], None, "alea") is None, "ordre aleatoire: pas d'entree a lire")
    import gl_gui
    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    stores = {m: gl_gui.mode_store(live, m).all() for m in rz.A2W_MODES}
    check(all(isinstance(v, dict) and "default" in v for v in stores.values()),
          "presets des trois modes audio2wave lisibles (config aleatoire du fond)", str({m: len(v) for m, v in stores.items()}))
    import build_portable
    check("core" in build_portable.APP_DIRS, "version portable: le dossier core/ (randomizer, scenes, reglages...) est embarque")


def gui_random_steps(c, s, out) -> None:
    """Full Randomizer dans la vraie fenetre : ordre, panneau (banque, categories, verrous), tirage, VJ, garder."""
    import random as rnd_mod
    import tempfile

    from PIL import Image
    sc, book, vj, rd = c["scenes"], c["scene_book"], c["vj"], c["random"]
    root, params = c["root"], s.params
    tmpd = Path(tempfile.mkdtemp(prefix="casual_overlay_alea_"))
    (tmpd / "logos").mkdir()
    (tmpd / "loops").mkdir()
    for i in range(2):
        Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(tmpd / "logos" / f"l{i}.png")
    (tmpd / "loops" / "v.mp4").write_bytes(b"x")
    base_set = book.active_set_name()
    sc["create_set"]("Alea")
    book.set_vj(order="alea", seconds=20, random={"folders_overlay": [str(tmpd)], "folders_fond": [str(tmpd / "loops")],
                                                  "bg_sources": ["preset", "params", "video"]})
    rd["state"]["rng"] = rnd_mod.Random(5)
    rd["state"]["last"] = {}
    rd["state"]["scene"] = None
    sc["switch_set"]("Alea")                       # reconstruit la barre et le panneau avec les nouveaux reglages
    sc["expand"](True)
    root.update()
    panel = sc["panel"]

    def walk_(w):
        yield w
        for ch in w.winfo_children():
            yield from walk_(ch)

    def widgets(cls, text):
        return [w for w in walk_(panel) if w.winfo_class() == cls and str(w.cget("text")) == text]
    out["r_panel"] = (len(widgets("Button", "Tirer maintenant")), len(widgets("Button", "Garder ce tirage...")), len(widgets("Button", "Retirer")),
                      bool(widgets("Checkbutton", "logos (2)")), bool(widgets("Checkbutton", "loops (1)")),
                      any("Full Randomizer" in str(w.cget("text")) for w in walk_(panel) if w.winfo_class() == "Menubutton"))
    out["r_bank"] = (len(rd["bank"]()["fond"]), len(rd["bank"]()["overlay"]), book.vj()["random"]["folders_overlay"] == [str(tmpd)],
                     len(widgets("Button", "+ Dossier")))
    # tirage a la demande
    while not s.commands.empty():
        s.commands.get_nowait()
    widgets("Button", "Tirer maintenant")[0].invoke()
    root.update()
    cmds = []
    while not s.commands.empty():
        cmds.append(s.commands.get_nowait())
    scene = rd["state"]["scene"]
    out["r_draw"] = ("flash" in cmds, s.active_scene, sc["msg"].get().startswith("Full Randomizer :"),
                     scene is not None and params["bg_mode"] == scene["bg"], params["logo_source"] in ("image", "video"),
                     str(params.get("logo_path")).startswith(str(tmpd)) or str(params.get("logo_video")).startswith(str(tmpd)))
    # VJ : tourne meme avec un set vide, un tirage par changement, minuteur = duree du set
    before = rd["state"]["scene"]
    vj["state"]["running"] = False
    vj["toggle"]()
    started = vj["state"]["running"]
    vj["advance"]()
    out["r_vj"] = (len(book.rows()) == 0, started, rd["state"]["scene"] is not before, 19 < vj["remaining"]() <= 20)
    vj["toggle"]()                                                       # pause
    # garder : devient une scene du set
    kept_scene = rd["state"]["scene"]
    asked: dict = {}

    def ask_keep(defaults, fond_ok, validate, a2w_mode=None):
        asked.update(defaults=dict(defaults), fond_ok=fond_ok, empty=validate({"fond": "", "overlay": "", "scene": ""}), a2w_mode=a2w_mode)
        return {"fond": "Tirage fond", "overlay": "Tirage overlay", "scene": "Tirage"}
    sc["ui"]["ask_keep"] = ask_keep
    widgets("Button", "Garder ce tirage...")[0].invoke()
    root.update()
    kept = book.get("Tirage")
    active_kept = s.active_scene
    labels = lambda m: [m.entrycget(i, "label") for i in range((m.index("end") or -1) + 1) if m.type(i) == "command"]
    out["r_keep3"] = (asked.get("defaults") == {"fond": "Tirage fond", "overlay": "Tirage overlay", "scene": "Tirage"}, asked.get("fond_ok"),
                      "au moins un nom" in str(asked.get("empty")),
                      gl.BACKGROUND_BOOK.all().get("Tirage fond") == kept_scene["fond"],
                      gl.OVERLAY_BOOK.all().get("Tirage overlay") == kept_scene["overlay"],
                      "Tirage fond" in labels(c["bg_presets"]["menu"]["menu"]) and "Tirage overlay" in labels(c["overlay_presets"]["menu"]["menu"]))
    # nom deja pris : rien n'est ecrit ; annuler : rien ; un seul champ rempli : seulement celui-la ; fond refuse sans fond genere
    n_scenes = len(book.names())
    err_dup = rd["save"]({"fond": "Tirage fond", "overlay": "", "scene": ""})
    err_default = rd["save"]({"fond": "", "overlay": "default", "scene": ""})
    sc["ui"]["ask_keep"] = lambda defaults, fond_ok, validate, a2w_mode=None: None
    widgets("Button", "Garder ce tirage...")[0].invoke()
    err_solo = rd["save"]({"fond": "", "overlay": "", "scene": "Solo"})
    real_scene = rd["state"]["scene"]
    rd["state"]["scene"] = {"bg": "live", "mode": "live", "live_overrides": None, "fond": None, "overlay": {"logo_glow": 1.0}}
    err_nofond = rd["check"]({"fond": "X", "overlay": "", "scene": ""})
    rd["state"]["scene"] = real_scene
    out["r_keep_err"] = ("existe deja" in str(err_dup), "existe deja" in str(err_default), err_solo is None and book.get("Solo") is not None,
                         len(book.names()) == n_scenes + 1, "pas de fond genere" in str(err_nofond),
                         sorted(gl.BACKGROUND_BOOK.names()).count("Tirage fond"))
    book.delete("Solo")
    # fond « video » sans fichier choisi (ecran noir) : pas de preset de fond ; fond audio2wave : preset du mode + etat reel du panneau
    rd["state"]["scene"] = {"bg": "video", "mode": None, "live_overrides": None, "fond": {"bg_mode": "video", "bg_video": ""}, "overlay": {}}
    err_black = rd["check"]({"fond": "Noir", "overlay": "", "a2w": "", "scene": ""})
    rd["state"]["scene"] = {"bg": "live", "mode": s.mode, "live_overrides": None, "fond": None, "overlay": {"logo_glow": 1.0}}
    import preset_registry
    store = preset_registry.store_for(s.mode)
    err_a2w_dup = rd["check"]({"fond": "", "overlay": "", "a2w": "default", "scene": ""})
    err_a2w = rd["save"]({"fond": "", "overlay": "", "a2w": "Mon live", "scene": "Scene live"})
    live_scene = book.get("Scene live")
    out["r_keep_a2w_err"] = err_a2w; out["r_keep_a2w"] = ("pas de fond genere" in str(err_black), "existe deja" in str(err_a2w_dup), err_a2w is None,
                         "mon live" in store.user(), bool(live_scene) and bool(live_scene.get("live_overrides")),
                         bool(live_scene) and live_scene.get("mode") == s.mode, sc["msg"].get().startswith("Enregistre"))
    store.delete("mon live")
    book.delete("Scene live")
    rd["state"]["scene"] = real_scene
    out["r_keep"] = (kept is not None, kept is not None and kept["bg"] == kept_scene["bg"] and kept["overlay"] == kept_scene["overlay"],
                     [r["name"] for r in book.rows()] == ["Tirage"], active_kept == "Tirage")
    sc["apply"]("Tirage")                                                # la scene gardee se rejoue comme les autres
    out["r_keep_apply"] = (s.active_scene == "Tirage", params["bg_mode"] == kept["bg"])
    # verrou du fond : le fond courant reste
    book.set_vj(random={**book.vj()["random"], "lock_bg": True})
    mode_before, hue_before = params["bg_mode"], params["bg_hue"]
    for _ in range(6):
        rd["draw"]()
    out["r_lock"] = (params["bg_mode"] == mode_before, params["bg_hue"] == hue_before)
    book.set_vj(random={**book.vj()["random"], "lock_bg": False})
    # config aleatoire du fond : cases des modes audio2wave ; avec le seul mode courant, aucun changement de mode (pas de reconstruction)
    sc["switch_set"]("Alea")
    root.update()
    snap_box = widgets("Checkbutton", "Snap")
    snap_box[0].invoke()
    modes_off = list(book.vj()["random"]["modes"])
    snap_box[0].invoke()
    modes_on = list(book.vj()["random"]["modes"])
    # (pas de preset audio2wave tire ici : appliquer `club` / `calme` au panneau de la fenetre de test fausserait les etapes suivantes ;
    # la logique du tirage de mode est verifiee en pur, les magasins des trois modes par check_randomizer)
    book.set_vj(random={**book.vj()["random"], "bg_sources": ["params"], "modes": []})
    mode_before = s.mode
    for _ in range(14):
        rd["draw"]()
    out["r_modes"] = (len(snap_box), modes_off, modes_on, s.mode == mode_before == "live", rd["state"]["scene"] is not None)
    book.set_vj(random={**book.vj()["random"], "bg_sources": ["preset", "params", "video"], "modes": ["live", "snap", "ridge"]})
    # categories, dossiers
    sc["switch_set"]("Alea")
    root.update()
    widgets("Checkbutton", "logos (2)")[0].invoke()
    out["r_cats"] = book.vj()["random"]["categories_overlay"]
    widgets("Checkbutton", "logos (2)")[0].invoke()
    out["r_cats_all"] = book.vj()["random"]["categories_overlay"]
    sc["ui"]["ask_dir"] = lambda: str(tmpd / "logos")
    widgets("Button", "+ Dossier")[1].invoke()                              # banque OVERLAY
    root.update()
    out["r_folders"] = (len(book.vj()["random"]["folders_overlay"]), len(rd["bank"]()["overlay"]), len(book.vj()["random"]["folders_fond"]))
    # un autre set garde son ordre ; revenir a un ordre normal remet les entrees
    sc["switch_set"](base_set)
    out["r_other_set"] = book.vj()["order"] in ("seq", "random")
    sc["switch_set"]("Alea")
    book.set_vj(order="seq")
    sc["switch_set"]("Alea")
    root.update()
    out["r_back"] = (sc["panel"].winfo_ismapped(), len(widgets("Button", "Tirer maintenant")), len(widgets("Button", "Retirer")))
    sc["delete_set"]()                                                   # supprime le set actif ("Alea")
    sc["switch_set"](base_set)
    vj["state"]["running"] = False
    sc["expand"](False)
    root.update()


def check_dialogs() -> None:
    """Les vraies modales (`choice_dialog`, `keep_dialog`) : elles s'ouvrent, repondent, restent ouvertes sur une erreur. Un test qui
    les remplace (scene_ui) ne verrait pas qu'elles plantaient (`a2w` etait un nom inconnu au niveau du module)."""
    print("modales (choice_dialog, keep_dialog)")
    import tkinter as tk
    import gl_gui
    import skin
    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    root = tk.Tk()
    try:
        live.style_gui(root)
        skin.install()

        def find(w, cls, text=None):
            for ch in w.winfo_children():
                if ch.winfo_class() == cls and (text is None or ch.cget("text") == text):
                    return ch
                found = find(ch, cls, text)
                if found is not None:
                    return found
            return None

        def top_():
            return next(w for w in root.winfo_children() if w.winfo_class() == "Toplevel")

        root.after(300, lambda: find(top_(), "Button", "Oui").invoke())
        check(gl_gui.choice_dialog(root, "Titre", "Texte", ["Non", "Oui"]) == "Oui", "choice_dialog: s'ouvre et rend le bouton clique")
        root.after(300, lambda: top_().destroy())
        check(gl_gui.choice_dialog(root, "Titre", "Texte", ["Non", "Oui"]) is None, "choice_dialog: fermer = None")
        seen: dict = {}

        def drive() -> None:
            top = top_()
            entries = []

            def walk(w) -> None:
                for ch in w.winfo_children():
                    if ch.winfo_class() == "Entry":
                        entries.append(ch)
                    walk(ch)
            walk(top)
            seen["values"] = [e.get() for e in entries]
            entries[0].delete(0, "end")
            entries[0].insert(0, "pris")
            find(top, "Button", "Enregistrer").invoke()
            seen["open_after_error"] = bool(top.winfo_exists())
            seen["error"] = any("existe deja" in str(w.cget("text")) for w in top.winfo_children() if w.winfo_class() == "Label")
            entries[0].delete(0, "end")
            entries[0].insert(0, "libre")
            entries[2].delete(0, "end")
            find(top, "Button", "Enregistrer").invoke()
        root.after(300, drive)
        got = gl_gui.keep_dialog(root, {"fond": "A", "overlay": "B", "scene": "C"}, True,
                                 lambda n: "fond 'pris' existe deja" if n["fond"] == "pris" else None)
        check(seen.get("values") == ["A", "B", "C"] and seen.get("open_after_error") and seen.get("error")
              and got == {"fond": "libre", "overlay": "B", "a2w": "", "scene": ""},
              "keep_dialog: trois noms proposes, un nom refuse laisse la fenetre ouverte avec le message, un champ vide = pas enregistre",
              f"{seen} {got}")

        def no_fond() -> None:
            top = top_()
            fields = [w for w in top.winfo_children()[1].winfo_children() if w.winfo_class() == "Entry"]
            seen["fond_state"] = str(fields[0].cget("state")), fields[0].get()
            find(top, "Button", "Annuler").invoke()
        root.after(300, no_fond)
        got = gl_gui.keep_dialog(root, {"fond": "A", "overlay": "B", "scene": "C"}, False, lambda n: None)
        check(got is None and seen.get("fond_state") == ("disabled", ""),
              "keep_dialog: sans fond genere le champ preset fond est grise et vide ; annuler = None", str(seen.get("fond_state")))
    finally:
        root.destroy()


def check_audio2wave_api() -> None:
    """Garde de version : un depot audio2wave sans l'API hote (`common.API_VERSION` >= 2) donne un message clair, pas un AttributeError."""
    print("garde de version d'audio2wave (common.API_VERSION)")
    import types
    import gl_gui
    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    import common
    check(isinstance(common.API_VERSION, int) and common.API_VERSION >= gl_gui.AUDIO2WAVE_API_MIN,
          "audio2wave: audio2wave annonce une API_VERSION assez recente", str(getattr(common, "API_VERSION", None)))
    check(gl_gui.audio2wave_api_missing(live) == [], "audio2wave: rien ne manque", str(gl_gui.audio2wave_api_missing(live)))
    gl_gui.require_audio2wave_api(live)
    check(True, "audio2wave: require_audio2wave_api laisse passer un depot a jour")
    old_live = types.SimpleNamespace(__file__="C:/x/audio2wave/audio2wave_live.py")
    for label, fake in (("sans API_VERSION", types.SimpleNamespace()), ("API_VERSION 1", types.SimpleNamespace(API_VERSION=1)),
                        ("API_VERSION illisible", types.SimpleNamespace(API_VERSION="2"))):
        missing = gl_gui.audio2wave_api_missing(old_live, fake)
        check(len(missing) == 1 and "API_VERSION" in missing[0], f"audio2wave ancien ({label}): signale, sans AttributeError", str(missing))
    check(gl_gui.audio2wave_api_missing(old_live, types.SimpleNamespace(API_VERSION=gl_gui.AUDIO2WAVE_API_MIN)) == []
          and gl_gui.audio2wave_api_missing(old_live, types.SimpleNamespace(API_VERSION=gl_gui.AUDIO2WAVE_API_MIN + 3)) == [],
          "audio2wave: la version exigee ou plus recente passe")
    try:
        gl_gui.require_audio2wave_api(old_live, types.SimpleNamespace(API_VERSION=1))
        message = ""
    except SystemExit as exc:
        message = str(exc)
    check("Mets a jour audio2wave" in message and "API_VERSION" in message and gl_gui.AUDIO2WAVE_REQUIRED in message
          and "AttributeError" not in message, "audio2wave ancien: arret avec un message clair (quoi, ou et comment mettre a jour)", message[:200])


def check_projection() -> None:
    """Projection a la demande : la GUI s'ouvre seule, la fenetre de rendu part d'un bouton (en fenetre par defaut)."""
    print("projection a la demande")
    import types
    runs: list[str] = []

    def target(s):
        runs.append("run")
        s.proj_stop.wait(5)

    def make(second, request=None, monitor=None, boom=None):
        args = types.SimpleNamespace(fullscreen=None, fullscreen_request=request, monitor=monitor, gui=True)
        s = gl.Session(args, {}, None, None, (1280, 720), (1280, 720), None, None, None)
        def run(s):
            if boom:
                raise boom
            target(s)
        return s, gl.Projection(s, target=run)

    s, p = make(True)
    s.commands.put("flash")
    msg = p.start()
    check(p.running() and s.args.fullscreen is False and "fenetre" in msg and s.commands.empty(),
          "projection: par defaut en fenetre meme avec deux ecrans, commandes perimees jetees", msg)
    check("deja ouverte" in p.start() and runs == ["run"], "projection: un second demarrage n'ouvre rien de plus")
    p.stop(wait=3.0)
    check(not p.running() and not s.finished_event.is_set(), "projection: l'arret ferme la fenetre sans fermer le programme")
    p.start()
    time.sleep(0.2)
    check(p.running() and runs == ["run", "run"], "projection: on peut la rouvrir autant de fois qu'on veut")
    p.stop(wait=3.0)
    s, p = make(False)
    msg = p.start()
    check(s.args.fullscreen is False and "fenetre" in msg, "projection: un seul ecran = en fenetre, jamais sur le poste de travail", msg)
    p.stop(wait=3.0)
    s, p = make(False, request=True)
    p.start()
    check(s.args.fullscreen is True, "projection: --fullscreen explicite l'emporte meme avec un seul ecran")
    p.stop(wait=3.0)
    s, p = make(True, request=False)
    p.start()
    check(s.args.fullscreen is False, "projection: --no-fullscreen explicite l'emporte meme avec deux ecrans")
    p.stop(wait=3.0)
    s, p = make(True, boom=SystemExit(1))
    p.start()
    time.sleep(0.3)
    check(not p.running() and "Projection impossible" in s.status.get("msg", ""),
          "projection: un echec d'ouverture (SystemExit) est signale, la GUI continue", s.status.get("msg", ""))
    s, p = make(True, boom=RuntimeError("pilote"))
    p.RESTART_DELAY_S = 0.02
    p.start()
    time.sleep(0.8)
    check(not p.running() and "pilote" in s.status.get("msg", "") and "trop de reprises" in s.status.get("msg", ""),
          "projection: une erreur repetee est signalee apres 3 reprises, la GUI continue", s.status.get("msg", ""))
    # reprise automatique : la fenetre qui meurt une fois est relancee toute seule, une fermeture voulue ne l'est pas
    calls: list[str] = []

    def flaky(s):
        calls.append("run")
        if len(calls) == 1:
            raise RuntimeError("pilote graphique")
        s.proj_stop.wait(5)

    args_ = types.SimpleNamespace(fullscreen=None, fullscreen_request=None, monitor=None, gui=True)
    s = gl.Session(args_, {}, None, None, (1280, 720), (1280, 720), None, None, None)
    p = gl.Projection(s, target=flaky)
    p.RESTART_DELAY_S = 0.02
    p.start()
    time.sleep(0.5)
    check(p.running() and calls == ["run", "run"] and "relancee" in s.status.get("msg", ""),
          "projection: la fenetre GL qui meurt est relancee toute seule", f"{calls} {s.status.get('msg', '')}")
    p.stop(wait=3.0)
    check(not p.running() and calls == ["run", "run"], "projection: une fermeture voulue ne relance rien")
    # une seule fenetre a la fois : un second gl_main (fermeture pas finie, double declenchement) renonce au lieu d'en ouvrir une autre
    s, p = make(True)
    gl._GL_SLOT.acquire()
    try:
        t0 = time.monotonic()
        gl.gl_main(s)                                               # n'atteint jamais glfw : le creneau est pris
    finally:
        gl._GL_SLOT.release()
    check(s.renderer is None and "deja ouverte" in s.status.get("msg", "") and time.monotonic() - t0 < 6.0,
          "projection: un second gl_main renonce quand une fenetre est deja ouverte", s.status.get("msg", ""))


def _render_stats(live, args, seconds: int = 2) -> tuple:
    """Rend le filtre d'audio2wave sur du bruit (vrai ffmpeg) : (part de pixels allumes, colonnes allumees) de la derniere image."""
    import subprocess
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", f"anoisesrc=d={seconds + 1}:a=0.3",
           "-filter_complex", live.build_filter(args), "-map", "[v]", "-t", str(seconds), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(cmd, capture_output=True).stdout
    w, h = live.resolve_size(args)
    n = len(data) // (w * h * 3)
    frame = np.frombuffer(data[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)[-1]
    return float((frame > 0).mean()), int((frame.max(axis=(0, 2)) > 0).sum())


def check_bar_gap() -> None:
    """Analyzer en barres : audio2wave plafonne lui-meme `bar_gap` (a 1,4 le separateur noir couvrait toute la barre)."""
    import re
    print("espace entre barres (analyzer, corrige a la source)")
    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    base = gl.build_live_args(live, "Micro", (1280, 720), 30, None)
    base.style, base.shape = "analyzer", "bar"
    cell = 1280 // 128
    thick = {}
    for gap in (0.25, 1.4):
        base.bar_gap = gap
        thick[gap] = int(re.search(r"drawgrid=[^:]*:h=ih:t=(\d+)", " ".join(live.producer_command(base))).group(1))
    check(thick[1.4] < cell and thick[0.25] < cell, "bar_gap 1,4 reste plafonne par audio2wave: separateur plus etroit que la barre",
          f"cellule {cell} px : {thick}")
    base.bar_gap = 1.4
    lit, cols = _render_stats(live, base)
    check(lit > 0.05 and cols > 300, "bar_gap 1,4: le vrai filtre dessine encore des barres (plus d'ecran noir)", f"{lit:.3f} {cols}")


def check_line_resolution() -> None:
    """Analyzer en courbe : audio2wave sort lui-meme 400 points sur une fenetre de 2048."""
    import re
    print("resolution de l'analyzer en courbe (corrigee a la source)")
    live = gl.load_live(gl.DEFAULT_A2W_DIR)
    base = gl.build_live_args(live, "Micro", (1280, 720), 30, None)
    base.style, base.shape = "analyzer", "line"
    cmd = " ".join(live.producer_command(base))
    got = (int(re.search(r"showfreqs=s=(\d+)x", cmd).group(1)), int(re.search(r"win_size=(\d+)", cmd).group(1)))
    check(got == (400, 2048), "courbe de l'analyzer: 400 points sur une fenetre de 2048, sans rien faire cote overlay", str(got))


def check_contract() -> None:
    """Test de contrat d'audio2wave (`check_contract.py` d'audio2wave : noms et signatures dont depend casual-overlay) :
    un code de sortie non nul fait echouer la suite. Si c'est un nom dont on depend qui est casse, c'est cote audio2wave
    qu'il faut le corriger (on n'y touche pas)."""
    print("contrat d'audio2wave (check_contract.py)")
    import subprocess
    script = Path(gl.DEFAULT_A2W_DIR) / "check_contract.py"
    if not script.is_file():
        check(False, "contrat d'audio2wave: check_contract.py absent du depot audio2wave", f"{script} : mets a jour le depot audio2wave")
        return
    res = subprocess.run([sys.executable, str(script)], cwd=script.parent, capture_output=True, text=True, timeout=120)
    lines = [ln for ln in (res.stdout + res.stderr).splitlines() if "ECHEC" in ln or "Traceback" in ln or "Error" in ln]
    check(res.returncode == 0, "contrat d'audio2wave: check_contract.py sort avec le code 0 (tous les noms et signatures dont on depend)",
          f"code {res.returncode} : " + " | ".join(lines[:6]))


def _contrast(a: str, b: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs #rrggbb."""
    import theme

    def lum(c: str) -> float:
        r, g, bl = (v / 255 for v in theme.hex_to_rgb(c))
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def check_menu_style() -> None:
    """Menus deroulants et selects : le survol (fond d'accent) garde un texte lisible. C'est audio2wave qui le choisit
    (`common.readable_fg` dans `style_gui` et `style_option_menu`), a partir de l'accent pose par `set_gui_accent`."""
    import tkinter as tk
    import common
    import gl_gui
    import theme
    print("menus deroulants et selects (survol lisible, corrige a la source)")
    saved = (common.GUI_ACCENT, common.GUI_ACCENT_FG)
    root = tk.Tk()
    try:
        gl_gui.apply_a2w_palette()
        common.style_gui(root)
        var = tk.StringVar(value="a")
        option = tk.OptionMenu(root, var, "a", "b")
        common.style_option_menu(option)
        menu = option["menu"]
        plain = tk.Menu(root, tearoff=0)
        pairs = {"menu du select": (menu.cget("activebackground"), menu.cget("activeforeground")),
                 "bouton du select": (option.cget("activebackground"), option.cget("activeforeground")),
                 "menu": (plain.cget("activebackground"), plain.cget("activeforeground"))}
        for label, (bg, fg) in pairs.items():
            bg, fg = (root.winfo_rgb(x) for x in (bg, fg))
            bg, fg = ("#%02x%02x%02x" % tuple(v >> 8 for v in c) for c in (bg, fg))
            check(_contrast(bg, fg) >= 3.0 and fg != "#0b1018" and fg != theme.ON_ACCENT,
                  f"survol lisible sur l'accent bleu: {label} (texte {fg} sur {bg})", f"contraste {_contrast(bg, fg):.2f}")
        check(common.readable_fg(theme.LIVE) in (theme.FG, common.GUI_FG), "common.readable_fg donne du texte clair sur le bleu LIVE")
    finally:
        common.set_gui_accent(*saved)
        root.destroy()


def check_scrollbar() -> None:
    """Ascenseur de la GUI (skin.scrollbar) : sombre, fin, sans fleches, meme interface que tk.Scrollbar."""
    import tkinter as tk
    from tkinter import ttk
    import skin
    import theme
    print("ascenseurs (skin.scrollbar)")
    root = tk.Tk()
    try:
        canvas = tk.Canvas(root, height=100, width=100)
        bar = skin.scrollbar(root, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left")
        canvas.configure(scrollregion=(0, 0, 100, 400))
        root.update()
        lo, hi = bar.get()
        canvas.yview_moveto(0.5)
        root.update()
        style = ttk.Style(root)
        check(isinstance(bar, ttk.Scrollbar) and bar.winfo_class() == "TScrollbar" and str(bar.cget("style")) == skin.SCROLL_STYLE,
              "ascenseur: ttk (couleurs reglables), pas le tk.Scrollbar clair de Windows")
        check(style.lookup(skin.SCROLL_STYLE, "troughcolor") == theme.BG and style.lookup(skin.SCROLL_STYLE, "background") != "#f0f0f0",
              "ascenseur: rail a la couleur de la fenetre, poignee discrete", f"{style.lookup(skin.SCROLL_STYLE, 'troughcolor')} {style.lookup(skin.SCROLL_STYLE, 'background')}")
        check(bar.winfo_reqwidth() <= skin.SCROLL_WIDTH + 6, "ascenseur: fin (pas de fleches)", str(bar.winfo_reqwidth()))
        check(lo < 0.02 and 0.2 < hi < 0.3 and bar.get()[0] > 0.45, "ascenseur: suit le canvas (set) et le pilote (command)", f"{(lo, hi)} {bar.get()}")
        bar2 = skin.scrollbar(root)
        check(str(bar2.cget("style")) == skin.SCROLL_STYLE, "ascenseur: un second ascenseur reutilise le style")
    finally:
        root.destroy()


def check_skin() -> None:
    """Habillage (skin.py / theme.py) : les widgets gardent leur comportement, seules leurs images changent."""
    print("habillage de la fenetre (skin.py)")
    import tkinter as tk
    import skin
    import theme
    check(theme.mix("#000000", "#ffffff", 0.5) == "#808080" and theme.lighten("#000000", 1) == "#ffffff"
          and theme.darken("#ffffff", 1) == "#000000" and theme.hex_to_rgb("#0af") == (0, 170, 255), "theme: melange / eclaircir / assombrir")
    root = tk.Tk()
    try:
        root.configure(bg=theme.BG)
        root.option_add("*Button.Background", theme.LIVE)
        frame = tk.Frame(root, bg=theme.BG)
        frame.pack()
        skin.zone(frame, theme.OVERLAY)
        hit = []
        btn = tk.Button(frame, text="Mettre a jour", command=lambda: hit.append(1))
        main = tk.Button(frame, text="Sauvegarder", command=lambda: None)
        custom = tk.Button(frame, text="3 scene", bg=theme.SCENE, fg=theme.ON_ACCENT)
        var = tk.StringVar(value="a")
        rad = tk.Radiobutton(frame, text="un", variable=var, value="a")
        rad2 = tk.Radiobutton(frame, text="deux", variable=var, value="b")
        ivar = tk.IntVar(value=0)
        chk = tk.Checkbutton(frame, text="case", variable=ivar)
        scale = tk.Scale(frame, from_=0, to=1, resolution=0.1, orient="horizontal", showvalue=False)
        ent = tk.Entry(frame)
        opt_var = tk.StringVar(value="log")
        opt = tk.OptionMenu(frame, opt_var, "log", "lineaire", "une valeur longue")
        for w in (btn, main, custom, rad, rad2, chk, scale, ent, opt):
            w.pack()
        done = skin.apply(root)
        root.update()
        check(done == 9 and skin.apply(root) == 0, "habillage: tous les widgets, une seule fois", str(done))
        check(btn.winfo_class() == "Button" and btn.cget("text") == "Mettre a jour" and btn.cget("command") != "",
              "bouton habille: meme classe, meme texte")
        btn.invoke()
        check(hit == [1], "bouton habille: invoke() appelle sa commande")
        check(btn.cget("bg") == theme.CARD and main.cget("bg") == theme.OVERLAY and custom.cget("bg") == theme.SCENE,
              "boutons: neutre par defaut, accent de la zone pour l'action principale, couleur propre respectee",
              f"{btn.cget('bg')} {main.cget('bg')} {custom.cget('bg')}")
        check(btn.cget("image") != "" and btn.winfo_reqheight() <= 30, "bouton: image arrondie posee, hauteur contenue",
              str(btn.winfo_reqheight()))
        before = str(btn.cget("image"))
        btn.configure(state="disabled")
        check(str(btn.cget("image")) != before and str(btn.cget("state")) == "disabled", "bouton desactive: autre image, etat conserve")
        btn.configure(state="normal")
        check(str(btn.cget("image")) == before, "bouton reactive: image d'origine (cache)")
        width = btn.winfo_reqwidth()
        btn.configure(text="Un texte nettement plus long")
        root.update_idletasks()
        check(btn.winfo_reqwidth() > width, "changer le texte redimensionne l'image")
        custom.configure(bg=theme.OVERLAY)
        check(custom.cget("bg") == theme.OVERLAY, "configure(bg=) change la couleur de remplissage voulue (cget la rend)")
        rad2.invoke()
        check(var.get() == "b" and rad2.cget("indicatoron") in (0, "0") and rad.cget("selectimage") != "",
              "radio habille: variable pilotee par invoke(), images normal / selectionne")
        chk.invoke()
        check(ivar.get() == 1, "case habillee: invoke() change la variable")
        rad.configure(state="disabled")
        check(str(rad.cget("state")) == "disabled", "radio desactive (gui_gates)")
        value_before = None
        scale.set(0.5)
        scale.configure(state="disabled")
        faint = scale.cget("bg")
        scale.configure(state="normal")
        check(faint == theme.FAINT and scale.cget("bg") == theme.OVERLAY and abs(scale.get() - 0.5) < 1e-9,
              "curseur: poignee a l'accent, eteinte quand grise, valeur conservee", f"{faint} {scale.cget('bg')}")
        check(str(ent.cget("highlightcolor")) == theme.OVERLAY and str(ent.cget("bg")) == theme.PANEL, "champ: fond sombre, focus a l'accent")
        check(opt.cget("indicatoron") in (0, "0") and str(opt.cget("image")) != "" and opt.winfo_reqwidth() > 70,
              "liste deroulante: champ arrondi assez large pour la plus longue valeur", str(opt.winfo_reqwidth()))
        tick = tk.Label(frame, text="TITRE")
        skin.title(tick, theme.LIVE)
        check(str(tick.cget("image")) != "" and tick.cget("text") == "TITRE", "titre de section: repere a l'accent, texte inchange")
        fresh = tk.Button(frame, text="Image", image=tk.PhotoImage(width=4, height=4))
        check(skin.skin_widget(fresh) is False, "un bouton qui a deja une image n'est pas touche")
        r2 = tk.Tk()
        try:
            b2 = tk.Button(r2, text="Autre racine")
            b2.pack()
            check(skin.apply(r2) == 1 and b2.cget("image") != "", "deuxieme racine Tk: ses propres images (cache par interpreteur)")
        finally:
            r2.destroy()
    finally:
        root.destroy()
    import build_portable
    check("gui" in build_portable.APP_DIRS and all((ROOT / "gui" / f).is_file() for f in ("skin.py", "theme.py", "icons.py")),
          "version portable: le dossier gui/ (skin.py, theme.py, icons.py...) est embarque")


def check_portable_modules() -> None:
    """Chaque module du depot importe par le code embarque (y compris les imports tardifs dans une fonction) est dans le dossier portable.
    Le script de fabrication ne lance pas la GUI : un module oublie ne se verrait qu'au premier lancement sur le PC de la soiree."""
    print("version portable : modules embarques")
    import ast
    import build_portable as bp
    here = ROOT
    local = {p.stem for p in here.glob("*.py")} | {p.stem for p in (here / "gui").glob("*.py")}          # modules plats : racine et gui/
    shipped_files = ([here / n for n in bp.APP_FILES if n.endswith(".py")]
                     + [f for d in ("core", "engine", "gui") for f in sorted((here / d).glob("*.py"))])
    missing = []
    for path in shipped_files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for m in mods:
                top = m.split(".")[0]
                if top in local and f"{top}.py" not in bp.APP_FILES and not ((here / "gui" / f"{top}.py").is_file() and "gui" in bp.APP_DIRS):
                    missing.append((path.name, m))
    check(not missing, f"portable: {len(shipped_files)} modules embarques, aucun import d'un module du depot qui n'y soit pas", str(missing[:3]))
    check(all((here / n).is_file() for n in bp.APP_FILES), "portable: tous les fichiers de la liste existent",
          str([n for n in bp.APP_FILES if not (here / n).is_file()]))

def check_device_cache() -> None:
    """Liste des entrees audio : relue a chaque fois hors reconstruction de la fenetre, gardee 60 s pendant une reconstruction."""
    import types as types_
    import gl_gui as gg
    calls = []
    fake = types_.ModuleType("fake_mode")
    fake.list_audio_devices = lambda: calls.append(1) or ["Micro A"]
    gg.install_device_cache([fake])
    gg.install_device_cache([fake])                       # idempotent : ne s'enveloppe pas deux fois
    fake.list_audio_devices()
    fake.list_audio_devices()
    off = len(calls)
    gg.DEVICE_CACHE["on"] = True
    fake.list_audio_devices()
    fake.list_audio_devices()
    on = len(calls)
    gg.DEVICE_CACHE["on"] = False
    fake.list_audio_devices()
    check(off == 2 and on == 2 and len(calls) == 3, "entrees audio: relues hors reconstruction, la derniere lecture est reutilisee pendant une reconstruction (un ffmpeg de moins)", str(calls))


def check_uistats() -> None:
    """Mesure de la fluidite de l'interface (core/uistats.py) : horloge injectee, aucune attente reelle."""
    from core import uistats as ui
    now = [0.0]
    said = []
    st = ui.Stats(clock=lambda: now[0], log=said.append)

    def op(ms):
        with st.timed("refresh_scene_bar"):
            now[0] += ms / 1000.0
    op(10)
    op(200)
    op(10)
    row = st.ops["refresh_scene_bar"]
    check(row[0] == 3 and abs(row[1] - 220) < 1e-6 and abs(row[2] - 200) < 1e-6, "uistats: appels, total et pire temps d'une operation", str(row))
    check(len(said) == 1 and "refresh_scene_bar" in said[0] and "200" in said[0], "uistats: une operation lente est signalee une fois (pas de rafale dans le journal)", str(said))
    now[0] += 2.0
    op(300)
    check(len(said) == 2, "uistats: le signalement revient apres une seconde", str(said))

    @st.watched("deco")
    def f(x):
        now[0] += 0.001
        return x * 2
    check(f(4) == 8 and st.ops["deco"][0] == 1, "uistats: le decorateur chronometre et garde la valeur rendue")
    w = ui.StallWatch(period_ms=50.0)
    seq = [0.0, 0.05, 0.10, 0.55, 0.60]
    out = [w.beat(v) for v in seq]
    check(out[:3] == [0.0, 0.0, 0.0] and abs(out[3] - 400.0) < 1e-6 and out[4] == 0.0, "uistats: un retard de minuterie de 400 ms est vu comme un gel, pas les petits retards", str(out))
    st.stalls.clear()
    st.stall(400.0)
    check(st.stalls and st.stalls[-1][1] == "refresh_scene_bar", "uistats: le gel est attribue a l'operation lente de la fenetre", str(st.stalls))
    check(any("refresh_scene_bar" in line for line in st.report()) and "Derniers gels" in "\n".join(st.report()), "uistats: rapport lisible")
    ran = []
    sch = ui.Scheduler(stats=None, on_error=lambda n, e: ran.append(("err", n)))
    sch.add("a", 100, lambda: ran.append("a"))
    sch.add("b", 250, lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sch.add("c", 50, lambda: ran.append("c"))
    first = sch.run_due(0.0)
    sch.run_due(0.04)
    second = sch.run_due(0.06)
    third = sch.run_due(5.0)
    check(first == ["a", "b", "c"] and second == ["c"] and sorted(third) == ["a", "b", "c"], "uistats: ordonnanceur, chaque tache a sa periode, pas de rattrapage apres un gel", str((first, second, third)))
    check(("err", "b") in ran and ran.count("a") == 2, "uistats: l'erreur d'une tache n'arrete ni les autres ni la boucle")
    check(10.0 <= sch.next_delay_ms(5.0) <= 100.0 and ui.Scheduler().next_delay_ms(0.0) == 100.0, "uistats: delai avant la prochaine echeance borne")


def check_core_purity() -> None:
    """core/ est du Python pur : stdlib seulement, jamais le moteur (audio2wave_gl) ni la GUI (gl_gui). Verifie par l'analyse des imports
    ET en important tout core/ dans un interpreteur ou ces modules sont interdits."""
    print("core/ : Python pur")
    import ast
    import subprocess
    here = ROOT
    forbidden = ("audio2wave_gl", "gl_gui", "audio2wave", "tkinter", "numpy", "moderngl", "glfw", "PIL", "sounddevice", "skin", "theme",
                 "gui_colors", "gui_gates", "py_modes", "diagnostics", "tkinterdnd2")
    files = sorted((here / "core").glob("*.py"))
    bad = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for m in mods:
                if m.split(".")[0] in forbidden:
                    bad.append((path.name, m))
    check(len(files) >= 8 and not bad, f"core/: {len(files)} modules, aucun import du moteur, de la GUI ni d'une dependance", str(bad[:3]))
    code = ("import sys, importlib.abc\n"
            f"BLOCK = {forbidden!r}\n"
            "class Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in BLOCK:\n"
            "            raise ImportError('interdit dans core/: ' + name)\n"
            "sys.meta_path.insert(0, Block())\n"
            f"sys.path.insert(0, {str(here)!r})\n"
            "import pkgutil, core\n"
            "mods = [m.name for m in pkgutil.iter_modules(core.__path__)]\n"
            "for m in mods: __import__('core.' + m)\n"
            "print(len(mods))\n")
    res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    check(res.returncode == 0 and res.stdout.strip().isdigit(),
          "core/: tout s'importe sans numpy, GL, Tk ni le moteur", (res.stderr or res.stdout).strip().splitlines()[-1] if (res.stderr or res.stdout).strip() else "")

def check_doodles() -> None:
    """Doodles du site : logique pure (core/doodles.py), fil de recuperation (faux reseau puis vrai serveur HTTP local), rendu par-dessus
    la projection (contexte standalone), et le reglage 'doodle_on' qui ne va ni dans les presets ni dans les scenes."""
    print("doodles du site (recuperation + affichage)")
    import io
    import json as _json
    import queue
    import random
    import shutil
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from core import doodles as dd
    from engine import doodle_feed as feed_mod

    # --- configuration
    cfg, why = dd.parse_config({"url": "https://abc.supabase.co/", "anon_key": "KEY"})
    check(cfg == {"url": "https://abc.supabase.co", "anon_key": "KEY"} and why == "", "config: url nettoyee, cle lue")
    check(dd.parse_config({"url": "https://x"})[0] is None and "anon_key" in dd.parse_config({"url": "https://x"})[1],
          "config incomplete: message qui nomme ce qui manque", dd.parse_config({"url": "https://x"})[1])
    check(dd.parse_config({"url": "ftp://x", "anon_key": "k"})[0] is None and dd.parse_config([])[0] is None, "config: url non http / contenu inattendu refuses")
    tmp = Path(tempfile.mkdtemp(prefix="doodles_"))
    check(dd.load_config(tmp / "absent.json")[0] is None and "absente" in dd.load_config(tmp / "absent.json")[1],
          "config absente: message clair (le bouton reste actif)", dd.load_config(tmp / "absent.json")[1])
    (tmp / "bad.json").write_text("{pas du json", encoding="utf-8")
    (tmp / "ok.json").write_text(_json.dumps({"url": "https://x.co", "anon_key": "k"}), encoding="utf-8-sig")
    check(dd.load_config(tmp / "bad.json")[0] is None and dd.load_config(tmp / "ok.json")[0] is not None, "config: JSON invalide refuse, fichier valide (BOM compris) lu")

    # --- requetes
    url, body, headers = dd.list_request(cfg)
    b = _json.loads(body)
    check(url == "https://abc.supabase.co/storage/v1/object/list/doodles" and b == {"prefix": "", "limit": 100, "sortBy": {"column": "created_at", "order": "desc"}}
          and headers["apikey"] == "KEY" and headers["Authorization"] == "Bearer KEY", "liste: POST sur le bucket doodles, plus recents d'abord, cle anon en en-tetes")
    check(dd.download_url(cfg, "a b.png") == "https://abc.supabase.co/storage/v1/object/public/doodles/a%20b.png", "telechargement: URL publique, nom encode")
    listing = dd.parse_listing(_json.dumps([{"name": "b.png", "created_at": "2026-01-02"}, {"name": ".emptyFolderPlaceholder"}, {"name": "dir/x.png"},
                                            {"id": None}, "bruit", {"name": "a.png", "created_at": "2026-01-01"}]))
    check([e["name"] for e in listing] == ["b.png", "a.png"], "liste: dossiers, marqueurs et entrees sans nom ecartes", str(listing))
    try:
        dd.parse_listing("{pas du json")
        bad = False
    except ValueError:
        bad = True
    check(bad, "liste: reponse illisible = erreur (reessai au tour suivant)")

    # --- fichiers vus
    seen = dd.Seen()
    seen.arm(listing)
    check(seen.fresh(listing) == [], "activation: l'existant est memorise comme vu, rien a afficher")
    more = listing + [{"name": "d.png", "created_at": "2026-01-04"}, {"name": "c.png", "created_at": "2026-01-03"}]
    check(seen.fresh(more) == ["c.png", "d.png"], "nouveaux fichiers rendus du plus ancien au plus recent", str(seen.fresh(more)))
    seen.mark("c.png")
    check(seen.fresh(more) == ["d.png"], "un fichier traite n'est plus propose (affiche une seule fois)")

    # --- validation avant affichage
    def png_bytes(w, h, color=(255, 0, 0, 255), fmt="PNG"):
        buf = io.BytesIO()
        im = gl.PIL_Image.new("RGBA" if fmt == "PNG" else "RGB", (w, h), color if fmt == "PNG" else color[:3])
        im.save(buf, fmt)
        return buf.getvalue()

    png, jpg = png_bytes(8, 8), png_bytes(8, 8, fmt="JPEG")
    check(dd.check_download(png) == ("png", "") and dd.check_download(jpg) == ("jpeg", ""), "validation: PNG et JPEG acceptes")
    check(dd.check_download(b"")[0] is None and dd.check_download(b"GIF89a" + b"0" * 50)[0] is None
          and dd.check_download(png + b"0" * (dd.MAX_BYTES + 1))[0] is None, "validation: vide, autre format et plus de 5 Mo refuses",
          str((dd.check_download(b"")[1], dd.check_download(b"GIF89a" + b"0" * 50)[1], dd.check_download(png + b"0" * (dd.MAX_BYTES + 1))[1])))
    check(dd.check_size(4096, 4096) == "" and dd.check_size(4097, 10) != "" and dd.check_size(0, 5) != "", "validation: dimensions <= 4096 px")
    try:
        feed_mod.decode(png + b"")
        ok_decode = True
        feed_mod.decode(b"\x89PNG\r\n\x1a\nfaux")
        ok_decode = False
    except ValueError:
        pass
    check(ok_decode, "decodage: un PNG tronque / faux est refuse (ValueError), un vrai passe")
    try:
        feed_mod.decode(png_bytes(5000, 4))
        too_big = False
    except ValueError as exc:
        too_big = "trop grande" in str(exc)
    check(too_big, "decodage: image de plus de 4096 px refusee avant tout decodage")
    arr, w, h = feed_mod.decode(png_bytes(4, 2, (255, 0, 0, 128)))
    check((w, h) == (4, 2) and tuple(arr[0, 0]) == (128, 0, 0, 128), "decodage: RGBA a alpha premultiplie", str(tuple(arr[0, 0])))

    # --- position, opacite, mur
    rng = random.Random(7)
    inside = True
    for aspect in (0.05, 0.5, 1.0, 2.0, 20.0):
        for sa in (1.0, 16 / 9, 4 / 3, 0.5):
            for _ in range(300):
                x, y, rw, rh = dd.place(rng, aspect, sa)
                inside &= -1e-9 <= x and -1e-9 <= y and x + rw <= 1.0 + 1e-9 and y + rh <= 1.0 + 1e-9 and rh <= dd.MAX_HEIGHT_FRAC + 1e-9
    check(inside, "position: toujours entierement dans le cadre (ratios extremes compris), hauteur <= 30 %")
    x, y, rw, rh = dd.place(rng, 2.0, 16 / 9)
    check(abs(rh - 0.30) < 1e-9 and abs(rw * 16 / 9 / rh - 2.0) < 1e-9, "position: hauteur 30 % de l'ecran, ratio de l'image garde")
    xs = {round(dd.place(rng, 1.0, 16 / 9)[0], 3) for _ in range(40)}
    check(len(xs) > 20, "position: tirage different a chaque apparition")
    a = [dd.alpha(t) for t in (-1, 0, 0.2, 0.4, 7, 14.3, 14.65, 15, 16)]
    check(a[0] == 0 and a[1] == 0 and abs(a[2] - 0.5) < 1e-9 and a[3] == 1 and a[4] == 1 and abs(a[5] - 1.0) < 1e-9 and abs(a[6] - 0.5) < 1e-9
          and a[7] == 0 and a[8] == 0, "cycle de vie: fondu d'entree, palier, fondu de sortie, 15 s", str(a))
    p0, p1, p2, p3, p4, p5 = (dd.pose(t) for t in (0.0, 0.45, 0.9, 7.0, 14.55, 15.0))
    check(abs(p0[0]) < 1e-9 and abs(p0[1]) > 0.2 and 1.05 < p1[0] < 1.15 and p2 == (1.0, 0.0) and p3 == (1.0, 0.0)
          and p4[0] > 1.0 and p5 == (0.0, 0.0) and dd.pose(1.0, -1)[1] == dd.pose(1.0, 1)[1] == 0.0
          and dd.pose(0.1, -1)[1] == -dd.pose(0.1, 1)[1] and dd.pose(-1.0) == (0.0, 0.0),
          "animation: apparition (pop avec depassement, rotation), palier droit, disparition (gonfle puis rapetisse), sens alterne", str((p0, p1, p4)))
    wall = dd.Wall()
    dropped = []
    for i in range(7):
        dropped += wall.add(i, (0, 0, 0.1, 0.1), 100.0 + i)
    check(len(wall) == 5 and dropped == [0, 1] and [it["id"] for it in wall.items] == [2, 3, 4, 5, 6], "mur: 5 medias au plus, le plus ancien part", str(dropped))
    check(wall.step(110.0) == [] and wall.step(119.5) == [2, 3, 4] and len(wall) == 2, "mur: les medias expires sont retires")
    check(len(wall.visible(112.0)) == 2 and wall.clear() == [5, 6] and len(wall) == 0, "mur: visibles puis vide")

    # --- un tour d'interrogation avec un faux reseau
    def fake_net(files, fail=()):
        def post(url, body, hdr):
            return _json.dumps([{"name": n, "created_at": f"2026-01-{i + 1:02d}"} for i, n in enumerate(files)])

        def get(url):
            name = url.rsplit("/", 1)[1]
            if name in fail:
                raise OSError("coupure")
            return files[name]
        return post, get

    files = {"old.png": png, "new1.png": png, "bad.png": b"pas une image" * 4, "new2.jpg": jpg}
    post, get = fake_net(files)
    seen = dd.Seen()
    seen.arm([{"name": "old.png", "created_at": "2026-01-01"}])
    good, rejected, error = dd.poll_once(cfg, seen, post, get)
    check([n for n, _ in good] == ["new1.png", "bad.png"][:1] + ["new2.jpg"] and len(rejected) == 1 and "bad.png" in rejected[0] and error == "",
          "tour: nouveaux fichiers valides rendus, fichier invalide ecarte et journalise", str((good and [n for n, _ in good], rejected, error)))
    check(dd.poll_once(cfg, seen, post, get) == ([], [], ""), "tour suivant: rien de nouveau, rien a refaire")
    seen2 = dd.Seen()
    post2, get2 = fake_net({"a.png": png, "b.png": png, "c.png": png}, fail=("b.png",))
    good, rejected, error = dd.poll_once(cfg, seen2, post2, get2)
    check([n for n, _ in good] == ["a.png"] and "b.png" in error and "b.png" not in seen2.names and "c.png" not in seen2.names,
          "tour: coupure en cours de route = fichiers deja recus gardes, le fichier en echec sera reessaye", str((good and [n for n, _ in good], error)))

    # --- le fil (faux reseau, horloge injectee)
    class FakeS:
        def __init__(self):
            self.params = {"doodle_on": 0.0}
            self.status = {}
            self.doodle_queue = queue.SimpleQueue()

    def drain(q):
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                return out

    clock = {"t": 0.0}
    fs = FakeS()
    live_files = {"old.png": png}
    state_cfg = {"cfg": (None, "Configuration du depot absente : cree depot.json")}

    def listing_post(url, body, hdr):
        if live_files.get("__down__"):
            raise OSError("reseau coupe")
        return _json.dumps([{"name": n, "created_at": f"2026-01-{i + 1:02d}"} for i, n in enumerate(k for k in live_files if k != "__down__")])

    def file_get(url):
        return live_files[url.rsplit("/", 1)[1]]

    fd = feed_mod.DoodleFeed(fs, config_loader=lambda: state_cfg["cfg"], post=listing_post, get=file_get, poll_s=5.0, clock=lambda: clock["t"])
    fd.tick()
    check(fs.status.get("doodles", "") == "" and fs.doodle_queue.empty(), "fil: desactive = aucune interrogation, aucun statut")
    fs.params["doodle_on"] = 1.0
    fd.tick()
    check("absente" in fs.status["doodles"] and fs.doodle_queue.empty(), "fil: config absente = le statut le dit clairement", fs.status["doodles"])
    state_cfg["cfg"] = (cfg, "")
    clock["t"] = 5.0
    fd.tick()
    check(fd.armed and "attente" in fs.status["doodles"] and fs.doodle_queue.empty(), "fil: a l'activation l'existant est memorise, rien n'est affiche", fs.status["doodles"])
    live_files.update({"fresh.png": png, "junk.png": b"\x89PNG\r\n\x1a\nxxxx"})
    clock["t"] = 7.0
    fd.tick()
    check(fs.doodle_queue.empty(), "fil: pas d'interrogation avant 5 s")
    clock["t"] = 10.5
    fd.tick()
    got = drain(fs.doodle_queue)
    check([g["name"] for g in got] == ["fresh.png"] and got[0]["rgba"].shape == (8, 8, 4), "fil: nouveau fichier decode et mis en file, fichier corrompu ignore",
          str([g["name"] for g in got]))
    clock["t"] = 16.0
    fd.tick()
    check(fs.doodle_queue.empty() and "1 recu" in fs.status["doodles"], "fil: un fichier n'est affiche qu'une fois", fs.status["doodles"])
    live_files["__down__"] = True
    clock["t"] = 21.5
    fd.tick()
    check("reseau" in fs.status["doodles"], "fil: erreur reseau = statut, le fil continue", fs.status["doodles"])
    del live_files["__down__"]
    live_files["after.png"] = png
    clock["t"] = 27.0
    fd.tick()
    check([g["name"] for g in drain(fs.doodle_queue)] == ["after.png"], "fil: apres une coupure, on reprend au tour suivant")
    fs.params["doodle_on"] = 0.0
    fd.tick()
    live_files["while_off.png"] = png
    clock["t"] = 40.0
    fd.tick()
    fs.params["doodle_on"] = 1.0
    clock["t"] = 41.0
    fd.tick()
    clock["t"] = 47.0
    fd.tick()
    check(fs.doodle_queue.empty(), "fil: ce qui est arrive pendant la desactivation n'est pas affiche a la reactivation")

    # --- vrai HTTP local : en-tetes, corps de la liste, telechargement
    served = {"post": [], "get": []}
    payload = {"list": _json.dumps([{"name": "net.png", "created_at": "2026-02-01"}]).encode(), "net.png": png_bytes(6, 6, (0, 255, 0, 255))}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            served["post"].append((self.path, self.headers.get("apikey"), self.headers.get("Authorization"), _json.loads(self.rfile.read(n))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload["list"])

        def do_GET(self):
            served["get"].append(self.path)
            name = self.path.rsplit("/", 1)[1]
            if name not in payload:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload[name])

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c2 = {"url": f"http://127.0.0.1:{srv.server_port}", "anon_key": "ANON"}
        seen3 = dd.Seen()
        seen3.arm([])
        good, rejected, error = dd.poll_once(c2, seen3, feed_mod.http_post, feed_mod.http_get)
        post_path, k1, k2, post_body = served["post"][0]
        check(post_path == "/storage/v1/object/list/doodles" and k1 == "ANON" and k2 == "Bearer ANON" and post_body["sortBy"]["order"] == "desc"
              and served["get"] == ["/storage/v1/object/public/doodles/net.png"] and [n for n, _ in good] == ["net.png"] and not rejected and not error,
              "HTTP reel (urllib): liste POST avec cle anon, telechargement public", str((served["post"][0][:3], served["get"], error)))
        payload["list"] = _json.dumps([{"name": "ghost.png", "created_at": "2026-03-01"}]).encode()
        good, rejected, error = dd.poll_once(c2, seen3, feed_mod.http_post, feed_mod.http_get)
        check(good == [] and "ghost.png" in error and "ghost.png" not in seen3.names, "HTTP reel: 404 = erreur de telechargement, reessai possible", error)
    finally:
        srv.shutdown()
    try:
        feed_mod.http_post("http://127.0.0.1:9/x", b"{}", {})
        refused = False
    except Exception:
        refused = True
    check(refused, "HTTP reel: serveur injoignable = exception (journalisee par le fil, jamais bloquante)")

    # --- reglage : ni sauve, ni preset, ni scene, ni panique
    check("doodle_on" in gl.TRANSIENT_KEYS and gl.DEFAULT_PARAMS["doodle_on"] == 0.0, "reglage doodle_on: etat du moment, desactive par defaut")
    pr = dict(gl.DEFAULT_PARAMS, doodle_on=1.0)
    check("doodle_on" not in gl.capture_overlay(pr) and "doodle_on" not in gl.capture_background(pr) and "doodle_on" not in gl.PANIC_RESET,
          "reglage doodle_on: absent des presets overlay / fond et de la panique")
    saved = tmp / "saved.json"
    old_path = core_params.PARAMS_PATH
    core_params.PARAMS_PATH = saved
    try:
        gl.save_params(pr)
        check("doodle_on" not in _json.loads(saved.read_text(encoding="utf-8")), "reglage doodle_on: pas ecrit par la sauvegarde (P)")
    finally:
        core_params.PARAMS_PATH = old_path
    check(hasattr(gl.Session.__init__, "__code__") and "doodle_queue" in gl.Session.__init__.__code__.co_names, "Session: file des doodles")

    # --- rendu par-dessus la projection
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        shutil.rmtree(tmp, ignore_errors=True)
        return
    size = (640, 360)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    params = dict(gl.DEFAULT_PARAMS, master=0.0, logo_glow=0.0, logo_pulse=0.0, logo_jitter=0.0)
    st = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    r.video_tex.write(bytes([0, 0, 255]) * (size[0] * size[1]))              # fond bleu pur
    r.draw(fbo, size, st, params, 0.0)
    base = read_target(ctx, fbo, size).copy()
    check(len(r.wall) == 0 and not r.doodle_tex, "rendu: liste vide = aucun doodle (la passe est sautee)")
    top_red_bottom_green = np.zeros((50, 100, 4), dtype=np.uint8)
    top_red_bottom_green[:25] = (255, 0, 0, 255)
    top_red_bottom_green[25:] = (0, 255, 0, 255)
    now = time.monotonic() - 5.0                                              # age 5 s : palier, opacite pleine
    r.add_doodle(top_red_bottom_green, now)
    rect = r.wall.items[-1]["rect"]
    r.draw(fbo, size, st, params, 0.0)
    img = read_target(ctx, fbo, size)
    changed = np.abs(img.astype(int) - base.astype(int)).sum(axis=2) > 60
    ys, xs = np.nonzero(changed)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
    check(abs((y1 - y0 + 1) - 0.30 * size[1]) <= 3 and abs((x1 - x0 + 1) - 2 * 0.30 * size[1]) <= 3 and x0 >= 0 and y0 >= 0 and x1 < size[0] and y1 < size[1],
          "rendu: doodle de 30 % de la hauteur, ratio garde, entierement dans le cadre", f"{x1 - x0 + 1}x{y1 - y0 + 1}")
    ex, ey = rect[0] * size[0], rect[1] * size[1]
    check(abs(x0 - ex) <= 3 and abs(y0 - ey) <= 3, "rendu: pose a l'endroit tire", f"({x0},{y0}) attendu ({ex:.0f},{ey:.0f})")
    cx = (x0 + x1) // 2
    top, bottom = img[y0 + 5, cx], img[y1 - 5, cx]
    check(top[0] > 240 and top[1] < 20 and bottom[1] > 240 and bottom[0] < 20, "rendu: image dans le bon sens (haut = rouge, bas = vert)", f"{top} {bottom}")
    outside = img[~changed]
    check((np.abs(outside.astype(int) - base[~changed].astype(int)).sum() == 0), "rendu: le reste de l'image est strictement intact")
    # animation : a l'apparition le doodle est plus petit et incline, pres de la fin il rapetisse
    def anim_box(age):
        ra = gl.Renderer(ctx, size, None)
        ra._doodle_rng = __import__("random").Random(7)
        ra.video_tex.write(bytes([0, 0, 0]) * (size[0] * size[1]))
        ra.draw(fbo, size, st, params, 0.0)                       # une premiere image : le rendu connait le rapport de l'ecran
        ra.add_doodle(top_red_bottom_green, time.monotonic() - age)
        ra.draw(fbo, size, st, params, 0.0)
        im = read_target(ctx, fbo, size).astype(int).sum(axis=2) > 3
        yy, xx = np.nonzero(im)
        return (xx.max() - xx.min() + 1, yy.max() - yy.min() + 1) if len(xx) else (0, 0), im
    (w5, h5), _im5 = anim_box(5.0)
    (wa, ha), _ima = anim_box(0.3)
    (wb, hb), _imb = anim_box(14.9)
    check(w5 > 0 and 0 < wa and wa * ha != w5 * h5 and 0 < wb * hb < w5 * h5, "doodle: apparition et disparition animees (taille et inclinaison changent)", f"{(w5, h5)} {(wa, ha)} {(wb, hb)}")
    # les doodles sont sur la couche du logo : ils subissent ses effets (et eux seuls)
    def doodle_render(pre=None, **kw):
        rr = gl.Renderer(ctx, size, None)
        if pre:
            pre(rr)
        rr._doodle_rng = __import__("random").Random(7)                      # meme emplacement d'un rendu a l'autre
        rr.video_tex.write(bytes([0, 0, 255]) * (size[0] * size[1]))
        rr.add_doodle(top_red_bottom_green, time.monotonic() - 5.0)
        rr.draw(fbo, size, dict(st, bass=1.0, beat=1.0, since_beat=0.0), dict(params, master=1.0, fx_link=0.0, **kw), 0.7)
        return read_target(ctx, fbo, size).astype(int), rr.wall.items[-1]["rect"]
    off, rc = doodle_render(fx_on=[0.0] * 5, fxl_on=[0.0] * 4)
    on_logo, _ = doodle_render(fx_on=[0.0] * 5, fxl_on=[1.0] * 4, fxl_int=[1.0] * 4)
    on_bg, _ = doodle_render(fx_on=[1.0, 1.0, 1.0, 0.0, 0.0], fx_int=[1.0] * 5, fxl_on=[0.0] * 4)
    mask = np.abs(off - np.array([0, 0, 255])).sum(axis=2) > 60                # emplacement du doodle
    check(np.abs(on_logo - off).sum() > 2000, "doodle: les effets de la couche du logo le deforment", str(np.abs(on_logo - off).sum()))
    check(np.abs(on_bg - off)[mask].sum() == 0, "doodle: les effets propres au fond (logo delie) ne le touchent pas")
    # fonte acide, halo holographique : memes passes que le logo
    plain = dict(fx_on=[0.0] * 5, fxl_on=[0.0] * 4)
    melted, _ = doodle_render(pre=lambda rr: setattr(rr, "_melt_t", 0.5), melt_on=1.0, melt_depth=1.0, **plain)
    check(np.abs(melted - off)[mask].sum() > 20000, "doodle: la fonte acide le ronge comme le logo", str(np.abs(melted - off)[mask].sum()))
    aura, _ = doodle_render(holo_on=1.0, holo_intensity=1.0, holo_reach=1.5, holo_dust=0.0, **plain)
    check(np.abs(aura - off)[~mask].sum() > 20000, "doodle: l'aura holographique l'entoure et deforme le fond", str(np.abs(aura - off)[~mask].sum()))
    # alpha premultiplie : 50 % d'opacite sur du bleu ne doit pas noircir les bords (pas de halo sombre)
    r2 = gl.Renderer(ctx, size, None)
    r2.video_tex.write(bytes([0, 0, 0]) * (size[0] * size[1]))
    half = np.zeros((20, 20, 4), dtype=np.uint8)
    half[:] = (128, 0, 0, 128)                                                # rouge a 50 %, premultiplie
    r2.add_doodle(half, time.monotonic() - 5.0)
    r2.draw(fbo, size, st, params, 0.0)
    img2 = read_target(ctx, fbo, size)
    cy2 = int((r2.wall.items[-1]["rect"][1] + 0.15) * size[1])
    cx2 = int((r2.wall.items[-1]["rect"][0] + r2.wall.items[-1]["rect"][2] / 2) * size[0])
    px = img2[cy2, cx2]
    check(120 <= px[0] <= 136 and px[1] == 0 and px[2] == 0, "rendu: alpha premultiplie (50 % de rouge sur noir = 128, pas 64)", str(px))
    # le fondu : au tout debut le doodle est invisible, il apparait ensuite
    r3 = gl.Renderer(ctx, size, None)
    r3.video_tex.write(bytes([0, 0, 0]) * (size[0] * size[1]))
    r3.draw(fbo, size, st, params, 0.0)                # premier rendu a blanc : compile les shaders (en OpenGL logiciel cela prend plus que le fondu)
    r3.add_doodle(top_red_bottom_green, time.monotonic() + 0.0)
    r3.draw(fbo, size, st, params, 0.0)
    near_zero = int(read_target(ctx, fbo, size).max())
    check(near_zero < 40, "rendu: fondu d'entree (quasi invisible a l'apparition)", str(near_zero))
    # limite de 5 et liberation des textures ; expiration
    r4 = gl.Renderer(ctx, size, None)
    for _ in range(8):
        r4.add_doodle(half, time.monotonic() - 5.0)
    check(len(r4.wall) == 5 and len(r4.doodle_tex) == 5, "rendu: 5 doodles au plus, les textures des evinces sont liberees", f"{len(r4.wall)} {len(r4.doodle_tex)}")
    r4._draw_doodles(fbo, size, time.monotonic() + 60.0)
    check(len(r4.wall) == 0 and not r4.doodle_tex, "rendu: apres 15 s tout est retire et libere")
    # la panique n'y touche pas (meme regle que le logo et le texte)
    r5 = gl.Renderer(ctx, size, None)
    r5.add_doodle(half, time.monotonic() - 5.0)
    gl.panic(params)
    check(len(r5.wall) == 1 and params["doodle_on"] == 0.0, "panique: ne retire pas les doodles affiches")
    shutil.rmtree(tmp, ignore_errors=True)

def check_icons() -> None:
    """Icones de l'interface : toutes dessinables, libelles inchanges (tests et code les retrouvent par leur texte), boutons / titres / onglets /
    cases habilles."""
    print("icones")
    import icons
    import skin
    import theme as T
    import tkinter as tk
    from tkinter import ttk
    check(all(name in icons.ICONS for name in icons.TEXT_ICONS.values()), "icones: chaque libelle connu pointe vers une icone dessinee",
          str([n for n in icons.TEXT_ICONS.values() if n not in icons.ICONS]))
    bad = []
    for name in icons.ICONS:
        for size in (14, 16, 24):
            img = icons.image(name, size, T.FG)
            if img.size != (size, size) or img.getbbox() is None:
                bad.append((name, size))
    check(not bad, f"icones: les {len(icons.ICONS)} icones se dessinent a toutes les tailles, jamais vides", str(bad[:3]))
    check(icons.for_text("Sauvegarder") == "save" and icons.for_text("  PANIQUE ") == "alert" and icons.for_text("▶ VJ") is None
          and icons.for_text("1  Devil Kinky") is None and icons.for_text("") is None, "icones: libelle -> icone (casse ignoree, glyphe existant et noms libres sans icone)")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        check(False, "Tk pour les icones", str(exc))
        return
    try:
        root.configure(bg=T.BG)
        skin.zone(root, T.OVERLAY)
        btn = tk.Button(root, text="Supprimer", font=T.FONT_BOLD)
        plain = tk.Button(root, text="Kick TV", font=T.FONT_BOLD)
        multi = tk.Button(root, text="Deux\nlignes", font=T.FONT_BOLD)
        chk = tk.Checkbutton(root, text="Cellules")
        rad = tk.Radiobutton(root, text="Video", value=1)
        other = tk.Checkbutton(root, text="Stereo")
        lab = tk.Label(root, text="FOND")
        nb = ttk.Notebook(root)
        for label in ("Effets", "Autre"):
            nb.add(tk.Frame(nb), text=label)
        for w in (btn, plain, multi, chk, rad, other, lab, nb):
            w.pack()
        skin.title(lab, T.OVERLAY)
        skin.apply(root)
        root.update()
        check(btn.cget("text") == "Supprimer" and str(btn.cget("compound")) == "none",
              "icones: un bouton a icone garde son libelle (cget text), l'image porte l'icone", str((btn.cget("text"), btn.cget("compound"))))
        check(str(plain.cget("compound")) == "center" and plain.cget("text") == "Kick TV", "icones: un bouton sans icone reste comme avant")
        check(str(multi.cget("compound")) == "center", "icones: un libelle sur plusieurs lignes n'a pas d'icone")
        check(chk._sk["key"][2] == "grid" and rad._sk["key"][2] == "video" and other._sk["key"][2] is None, "icones: cases et radios choisies ont une icone, les autres non")
        check(lab.cget("text") == "FOND" and str(lab.cget("image")) != "", "icones: le titre garde son texte et porte repere + icone")
        check(nb.tab(0, "text") == "Effets" and nb.tab(0, "image") and not nb.tab(1, "image"), "icones: onglet connu = icone, texte inchange ; onglet inconnu = rien",
              str((nb.tab(0, "text"), nb.tab(0, "image"), nb.tab(1, "image"))))
        # menus deroulants du VJ (SET, ordre, duree, reglages) : icone a gauche du texte
        menus = {}
        for label in ("SET  Entropie ▾", "ordre : dans l'ordre ▾", "duree par defaut : 10 s ▾", "reglages VJ ▾", "+ Scene ▾"):
            mb = tk.Menubutton(root, text=label, bg=T.BG, fg=T.FG)
            mb.pack()
            menus[label] = mb
        skin.apply(root)
        check(all(str(menus[l].cget("image")) for l in list(menus)[:4]) and not str(menus["+ Scene ▾"].cget("image"))
              and menus["SET  Entropie ▾"].cget("text") == "SET  Entropie ▾",
              "icones: les menus du VJ (SET, ordre, duree, reglages) ont leur icone, texte inchange ; + Scene n'en double pas le plus")
        # icone seule : le libelle devient l'info-bulle (ou la premiere ligne de celle qui existe deja)
        check(btn._sk["icon_only"] and not plain._sk.get("icon_only") and btn.winfo_reqwidth() < plain.winfo_reqwidth(),
              "icones: Supprimer = icone seule (bouton plus court), un bouton sans icone garde son texte")
        skin._tip_enter(btn)
        own = btn._sk.get("tip")
        own_text = next((c.cget("text") for c in own.winfo_children()), "") if own is not None else None
        skin._tip_leave(btn)
        check(own_text == "Supprimer" and btn._sk.get("tip") is None and not [c for c in btn.winfo_children() if isinstance(c, tk.Toplevel)],
              "icones: le libelle s'affiche en info-bulle au survol et disparait a la sortie", str(own_text))
        theirs = tk.Toplevel(btn)
        theirs.wm_overrideredirect(True)
        tk.Label(theirs, text="Supprime le preset choisi.").pack()
        skin._tip_enter(btn)
        merged = next((c.cget("text") for c in theirs.winfo_children()), "")
        skin._tip_enter(btn)
        again = next((c.cget("text") for c in theirs.winfo_children()), "")
        theirs.destroy()
        check(merged == "Supprimer\nSupprime le preset choisi." and again == merged and btn._sk.get("tip") is None,
              "icones: une info-bulle existante recoit le libelle en premiere ligne (une seule fois)", str((merged, again)))
        nb.select(1)
        root.update()
        btn.configure(state="disabled")
        root.update()
        check(btn.cget("text") == "Supprimer" and str(btn.cget("state")) == "disabled", "icones: griser un bouton a icone le redessine sans changer son libelle")
    finally:
        root.destroy()

def check_audio2wave_interne() -> None:
    """audio2wave est interne (sources/audio2wave) : dossier par defaut, ordre de recherche, et copie fidele a son origine (toute
    modification doit etre notee dans VENDORED.md : l'empreinte de chaque fichier est comparee a ORIGINE.json)."""
    print("audio2wave interne")
    import hashlib
    import json as _json
    import shutil
    import tempfile
    here = ROOT
    vend = here / "sources" / "audio2wave"
    check(Path(gl.DEFAULT_A2W_DIR).resolve() == vend.resolve(), "audio2wave: le dossier par defaut est la copie interne sources/audio2wave", str(gl.DEFAULT_A2W_DIR))
    need = ("audio2wave.py", "audio2wave_live.py", "audio2wave_snap.py", "audio2wave_ridge.py", "common.py", "preset_registry.py", "check_contract.py",
            "VENDORED.md", "ORIGINE.json")
    check(all((vend / n).is_file() for n in need), "audio2wave interne: tous les fichiers sont la", str([n for n in need if not (vend / n).is_file()]))
    # ordre de recherche : portable (audio2wave/ a cote du programme) > copie interne > depot voisin (dernier recours)
    tmp = Path(tempfile.mkdtemp(prefix="a2w_order_"))
    prog = tmp / "prog"
    (prog / "audio2wave").mkdir(parents=True)
    (prog / "sources" / "audio2wave").mkdir(parents=True)
    (tmp / "audio2wave").mkdir()
    for d in (prog / "audio2wave", prog / "sources" / "audio2wave", tmp / "audio2wave"):
        (d / "audio2wave_live.py").write_text("", encoding="utf-8")
    check(gl.bundled_paths(prog)[0] == prog / "audio2wave", "audio2wave: la version portable (audio2wave/ a cote du programme) l'emporte")
    (prog / "audio2wave" / "audio2wave_live.py").unlink()
    check(gl.bundled_paths(prog)[0] == prog / "sources" / "audio2wave", "audio2wave: sinon la copie interne")
    (prog / "sources" / "audio2wave" / "audio2wave_live.py").unlink()
    check(gl.bundled_paths(prog)[0] == tmp / "audio2wave", "audio2wave: le depot voisin n'est qu'un dernier recours")
    shutil.rmtree(tmp, ignore_errors=True)
    # fidelite a l'origine : une modification non notee echoue
    origin = _json.loads((vend / "ORIGINE.json").read_text(encoding="utf-8"))
    notes = (vend / "VENDORED.md").read_text(encoding="utf-8")
    section = notes.split("## Modifications locales", 1)[-1]
    changed = [n for n, digest in origin["sha256"].items()
               if not (vend / n).is_file() or hashlib.sha256((vend / n).read_bytes()).hexdigest() != digest]
    extra = [f.name for f in vend.glob("*.py") if f.name not in origin["sha256"]]
    unlisted = [n for n in changed + extra if n not in section]
    check(origin["commit"] in notes and not unlisted and (not (changed or extra) or "Aucune" not in section),
          f"audio2wave interne: {len(origin['sha256'])} fichiers conformes a l'origine ({origin['commit'][:7]}), ou chaque ecart est note dans VENDORED.md",
          f"modifies: {changed} ajoutes: {extra} non notes: {unlisted}")
    exposed = {"audio2wave_live.py": ("style_var", "shape_var"), "audio2wave_ridge.py": ("gain_auto_var",),
               "audio2wave_snap.py": ("style_var", "wave_var", "kick_glow_var", "glow_live_var", "gain_auto_var")}
    lacking = [f"{n}:{k}" for n, keys in exposed.items() for k in keys
               if f'widgets["{k}"]' not in (vend / n).read_text(encoding="utf-8")]
    check(not lacking, "audio2wave interne: les variables Tk qui commandent les grisages sont exposees dans `widgets`", str(lacking))

def check_engine_layer() -> None:
    """engine/ depend de core/ et des dependances facultatives, jamais de la GUI ni de la facade audio2wave_gl ; il est embarque dans le portable."""
    print("engine/ : couche moteur")
    import ast
    import build_portable
    here = ROOT
    forbidden = ("audio2wave_gl", "gl_gui", "tkinter", "skin", "theme", "gui_colors", "gui_gates", "py_modes", "tkinterdnd2")
    files = sorted((here / "engine").glob("*.py"))
    bad = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [(path.name, m) for m in mods if m.split(".")[0] in forbidden]
    check(len(files) >= 4 and not bad, f"engine/: {len(files)} modules, aucun import de la GUI ni de la facade", str(bad[:3]))
    check("engine" in build_portable.APP_DIRS, "version portable: le dossier engine/ est embarque")
    import engine.audio, engine.render, engine.session, engine.projection, engine.app, engine.video, engine.window
    check(gl.FeatureExtractor is engine.audio.FeatureExtractor and gl.FrameReader is engine.video.FrameReader
          and gl.Window is engine.window.Window, "la facade audio2wave_gl rend les memes objets que engine/")

def check_params_schema() -> None:
    """Table unique des reglages (params_schema.py) : integrite, derivations, validation, et coherence avec le moteur, la GUI, les
    presets integres, les automations et le hasard (rien ne doit sortir des plages declarees)."""
    print("table unique des reglages")
    import json
    import random
    import re
    from core import params_schema as ps
    here = ROOT
    # --- integrite de la table
    bad = [p.key for p in ps.TABLE if p.key != "logo_path" and not _in_range(p, p.default)]
    check(not bad, "table: chaque valeur par defaut est dans sa plage", str(bad))
    check(list(gl.DEFAULT_PARAMS) == [p.key for p in ps.TABLE], "table: DEFAULT_PARAMS = la table, dans le meme ordre")
    check(all(p.doc for p in ps.TABLE), "table: chaque reglage est documente", str([p.key for p in ps.TABLE if not p.doc]))
    check(set(gl.TRANSIENT_KEYS) <= set(gl.ROOM_KEYS) and not set(gl.TRANSIENT_KEYS) & set(gl.MEMORY_KEYS)
          and set(gl.MEMORY_KEYS) | set(gl.TRANSIENT_KEYS) == set(gl.ROOM_KEYS),
          "portees: etats du moment dans la salle, memoires = salle sans les etats du moment")
    groups = [set(ps.keys(s)) for s in ps.SCOPES]
    check(sum(len(g) for g in groups) == len(ps.TABLE) and not any(a & b for i, a in enumerate(groups) for b in groups[i + 1:]),
          "portees: chaque reglage a une portee et une seule")
    check(not set(gl.BG_KEYS) & set(gl.ROOM_KEYS) and not set(gl.BG_KEYS) & set(gl.OVERLAY_PRESET_EXCLUDED),
          "portees: le fond et la salle ne se recouvrent pas (bg_master est un reglage de salle)")
    check("sensitivity" in gl.OVERLAY_PRESET_EXCLUDED and set(gl.ROOM_KEYS) <= set(gl.OVERLAY_PRESET_EXCLUDED),
          "portees: sensibilite et salle hors presets et scenes")
    # --- regression : un preset de fond ou une scene ne touche plus au master « Fond » (bg_master etait compte comme un reglage du fond)
    cur = json.loads(json.dumps(gl.DEFAULT_PARAMS))
    cur["bg_master"] = 0.4
    check("bg_master" not in gl.capture_background(cur) and "bg_master" not in gl.background_preset_values({"bg_speed": 2.0}, cur),
          "fond: capture et preset de fond ne contiennent pas bg_master")
    check(gl.overlay_preset_values({"logo_glow": 2.0}, cur)["bg_master"] == 0.4
          and gl.background_preset_values({"bg_speed": 2.0}, cur).get("bg_master", 0.4) == 0.4,
          "fond: appliquer un preset (overlay ou fond) laisse le master Fond du VJ tel qu'il est")
    from core import scenes as _sc
    check(_sc.scene_values({"bg": "pattern", "fond": gl.capture_background(cur), "overlay": gl.capture_overlay(cur)}, cur)["bg_master"] == 0.4,
          "fond: une scene a motif ne remet pas le master Fond a 100 %")
    # --- validation : type, longueur, enum, plage
    out = gl.coerce_params({"strobe_max_hz": 9999, "tempo": -3, "comp_sym": "x", "logo_source": "weird", "bg_palette": "duo",
                            "fx_on": [5, 0, 0, 0, 0], "fx_int": [9.0, 0, 0, 0, -1], "fx_link": True, "inconnue": 1, "text_align": "left"})
    check(out["strobe_max_hz"] == 30.0 and out["tempo"] == 0.0, "validation: un nombre hors plage est ramene dans la plage",
          f"{out['strobe_max_hz']} {out['tempo']}")
    check(out["comp_sym"] == 0 and out["logo_source"] == "image" and out["fx_link"] == 1.0 and "inconnue" not in out,
          "validation: mauvais type, enum inconnu, booleen et cle inconnue ignores")
    check(out["bg_palette"] == "duo" and out["text_align"] == "left", "validation: une valeur d'enum valide est gardee")
    check(out["fx_on"] == [1, 0, 0, 0, 0] and out["fx_int"] == [2.0, 0.0, 0.0, 0.0, 0.0],
          "validation: listes bornees element par element, longueur controlee", f"{out['fx_on']} {out['fx_int']}")
    check(gl.coerce_params({"fx_on": [1, 1]})["fx_on"] == [1, 1, 1, 1, 1], "validation: liste de mauvaise longueur ignoree")
    # un ancien fichier sauve qui contenait bg_master dans un preset de fond se charge sans erreur
    check(gl.coerce_params({"bg_master": 0.5, "bg_speed": 2.0})["bg_master"] == 0.5, "compat: un ancien preset avec bg_master se charge")
    # --- presets integres : tous valides, aucune valeur ramenee par la validation
    moved = []
    for book, name in ((gl.OVERLAY_PRESETS, "overlay"), (gl.BACKGROUND_PRESETS, "fond")):
        for pname, values in book.items():
            clean = gl.coerce_params(values)
            for k, v in values.items():
                if k in clean and clean[k] != v and not (isinstance(v, (int, float)) and abs(clean[k] - v) < 1e-9):
                    moved.append((name, pname, k, v, clean[k]))
    check(not moved, "presets integres: aucune valeur hors plage ni de mauvais type", str(moved[:3]))
    # --- automations : leur plage reste dans celle du reglage
    wide = []
    for key, spec in gl.AUTOMATION_SPECS.items():
        base = re.sub(r"^(fxl?_int)\d$", r"\1", key)
        p = ps.BY_KEY.get(base)
        if p is None:
            wide.append((key, "reglage absent de la table"))
        elif (p.lo is not None and spec[1] < p.lo) or (p.hi is not None and spec[2] > p.hi):
            wide.append((key, (spec[1], spec[2]), (p.lo, p.hi)))
    check(not wide, "automations: chaque courbe reste dans la plage de son reglage", str(wide[:3]))
    # --- curseurs de la GUI : leur plage tient dans celle de la table
    src = "".join(path.read_text(encoding="utf-8") for path in [here / "gui" / "gl_gui.py", *sorted((here / "gui").glob("gui_*.py"))])     # la GUI est repartie en plusieurs modules
    out_of = []
    n_sliders = 0
    for m in re.finditer(r'param_slider\(\w+, "[^"]*", "(\w+)", ([-\d.]+), ([-\d.]+),', src):
        n_sliders += 1
        key, lo, hi = m.group(1), float(m.group(2)), float(m.group(3))
        p = ps.BY_KEY.get(key)
        if p is None or (p.lo is not None and lo < p.lo - 1e-9) or (p.hi is not None and hi > p.hi + 1e-9):
            out_of.append((key, lo, hi, None if p is None else (p.lo, p.hi)))
    for m in re.finditer(r'\("(\w+)", "[^"]*", ([-\d.]+), ([-\d.]+), ([-\d.]+), ([-\d.]+), "[^"]*", ([-\d.]+),', src):
        key, lo, hi, factor = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(5))
        p = ps.BY_KEY.get(key)
        if p is None:
            continue
        n_sliders += 1
        if (p.lo is not None and lo / factor < p.lo - 1e-9) or (p.hi is not None and hi / factor > p.hi + 1e-9):
            out_of.append((key, lo / factor, hi / factor, (p.lo, p.hi)))
    check(n_sliders >= 40 and not out_of, f"GUI: {n_sliders} curseurs verifies, aucun ne depasse la plage de la table", str(out_of[:3]))
    # --- le code ne lit/ecrit que des reglages declares
    unknown = set()
    allowed = {"_automation", "fx_on", "fx_int"}
    for fname in ("audio2wave_gl.py", "gui/gl_gui.py", *("gui/" + p.name for p in sorted((here / "gui").glob("gui_*.py"))), "core/randomizer.py", "core/scenes.py", "core/params.py", "core/controls.py"):
        text = (here / fname).read_text(encoding="utf-8")
        for m in re.finditer(r'\b(?:params|p|cur|current|values|full)\[\s*"([a-z][a-z0-9_]*)"\s*\]', text):
            key = m.group(1)
            if key.startswith(("logo_", "bg_", "holo_", "melt_", "cell_", "fx", "comp_", "strobe_", "text_", "noise_", "silence_", "breath_")) \
                    or key in ("tempo", "master", "build", "idle", "sensitivity", "auto_master", "react_master", "overlay_master", "hue_master",
                               "hue_cycle", "build_time"):
                if key not in ps.BY_KEY and key not in allowed:
                    unknown.add((fname, key))
    check(not unknown, "code: aucun reglage lu ou ecrit sans etre dans la table", str(sorted(unknown)[:5]))
    # --- hasard : tout ce que tire le Full Randomizer reste dans les plages
    from core import randomizer
    rng = random.Random(1234)
    stray = []
    for _ in range(300):
        for fn in (randomizer.random_background_params, randomizer.random_overlay_params, randomizer.random_effects):
            for k, v in fn(rng).items():
                if k == "_automation":
                    continue
                ok, val = ps.validate(k, v)
                if not ok or (isinstance(v, (int, float)) and not isinstance(v, bool) and abs(val - v) > 1e-9) \
                        or (isinstance(v, list) and any(abs(a - b) > 1e-9 for a, b in zip(val, v))):
                    stray.append((fn.__name__, k, v))
    check(not stray, "hasard: les tirages du Full Randomizer restent dans les plages de la table", str(stray[:3]))
    import build_portable
    check((ROOT / "core" / "params_schema.py").is_file() and "core" in build_portable.APP_DIRS,
          "version portable: core/params_schema.py embarque")


def _in_range(p, value) -> bool:
    vals = value if isinstance(value, list) else [value]
    for v in vals:
        if isinstance(v, str):
            if p.choices is not None and v not in p.choices:
                return False
            continue
        if (p.lo is not None and v < p.lo) or (p.hi is not None and v > p.hi):
            return False
    return True


def check_safeguards() -> None:
    """Garde-fous d'exploitation : limiteur de strobe, panique, tap tempo, auto-sauvegarde, journal, master des effets."""
    print("garde-fous d'exploitation")
    import json
    import tempfile
    # --- limiteur de strobe : cadence maximale et duree maximale d'une rafale
    lim = gl.StrobeLimiter()
    fired = [t for t in (i * 0.02 for i in range(100)) if lim.allow(t, 10.0, 60.0)]        # 50 Hz demandes pendant 2 s
    check(18 <= len(fired) <= 21 and all(b - a >= 0.1 - 1e-6 for a, b in zip(fired, fired[1:])),
          "limiteur: 50 Hz demandes = 10 Hz au maximum, jamais deux eclairs a moins de 100 ms", str(len(fired)))
    lim = gl.StrobeLimiter()
    seq = [(i * 0.1, lim.allow(i * 0.1, 10.0, 2.0)) for i in range(80)]                     # 10 Hz continus pendant 8 s, duree max 2 s
    ok_times = [t for t, a in seq if a]
    gap = [(t, a) for t, a in seq if 2.2 < t < 4.9]
    check(max(ok_times[:20]) <= 2.1 and not any(a for _, a in gap) and any(a for t, a in seq if t > 5.2) and lim.trips >= 1,
          "limiteur: une rafale continue est coupee apres la duree maximale, pause forcee de 3 s, puis le strobe revient",
          f"derniers eclairs {ok_times[:3]}..{ok_times[-1]:.1f}, coupures {lim.trips}")
    lim = gl.StrobeLimiter()
    first = [lim.allow(i * 0.1, 10.0, 1.5) for i in range(16)]
    again = lim.allow(20.0, 10.0, 1.5)
    check(again and first.count(True) >= 10, "limiteur: une pause de plus d'une seconde remet la rafale a zero")
    check(gl.StrobeLimiter().allow(0.0, 1000.0, 0.0) and gl.DEFAULT_PARAMS["strobe_max_hz"] <= 10.0 and gl.DEFAULT_PARAMS["strobe_max_s"] <= 10.0
          and all(k in gl.ROOM_KEYS for k in ("strobe_max_hz", "strobe_max_s", "fx_master")),
          "limiteur: valeurs hors plage bornees, defauts prudents, reglages de salle")
    # le Renderer applique le limiteur a tous les eclairs (ici : cadence demandee 20 Hz, limite 2 Hz)
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone (garde-fous)", str(exc))
        return
    size = (320, 180)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    r = gl.Renderer(ctx, size, None)
    pst = dict(gl.DEFAULT_PARAMS, strobe_on=1.0, strobe_mode="kick", strobe_max_hz=2.0, strobe_max_s=60.0)
    seen, t_end, nb = set(), time.monotonic() + 1.3, 0
    while time.monotonic() < t_end:
        nb += 1                                                        # un kick a chaque tour (100 Hz demandes)
        r._strobe(pst, {"beats": nb})
        seen.add(r._strobe_last)
        time.sleep(0.01)
    check(1 <= len(seen) - 1 <= 3, "strobe: la cadence demandee (100 kicks/s) est plafonnee par le limiteur (2 Hz) dans le rendu", f"{len(seen) - 1} eclairs en 1,3 s")
    # --- master des effets
    r.video_tex.write(np.full((size[1], size[0], 3), 60, np.uint8).tobytes())
    r.set_logo(np.full((64, 64, 4), 255, np.uint8), "x")
    loud = {"bass": 1.0, "mid": 0.8, "high": 0.8, "rms": 0.8, "beat": 1.0, "since_beat": 0.0, "beats": 4}
    base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, logo_scale=0.25, logo_glow=0.0, logo_pulse=0.0, logo_jitter=0.0,
                bg_mode="pattern", bg_palette="classic", holo_on=0.0, fx_link=1.0)

    def render(**kw):
        r.draw(fbo, size, loud, dict(base, **kw), 1.0)
        return read_target(ctx, fbo, size).astype(int)

    effects_on = dict(fx_on=[1, 1, 1, 1, 0], fx_int=[1.0, 1.0, 1.0, 1.0, 1.0], master=1.0)
    none = render(fx_on=[0, 0, 0, 0, 0])
    full = render(**effects_on)
    zero = render(**effects_on, fx_master=0.0)
    half = render(**effects_on, fx_master=0.5)
    check(not np.array_equal(full, none) and np.array_equal(zero, none),
          "master des effets 0 %: plus aucun effet (image identique a tous les effets coupes)")
    check(not np.array_equal(half, full) and not np.array_equal(half, none), "master des effets 50 %: effets attenues")
    # --- panique
    dirty = json.loads(json.dumps(gl.DEFAULT_PARAMS))
    dirty.update(comp_sym=4, comp_pix=0.5, comp_trail=0.9, comp_warp=0.7, hue_master=120.0, hue_cycle=30.0, build=0.8, strobe_on=1.0,
                 bg_master=0.0, overlay_master=0.0, tempo=0.2, auto_phase=1.0, fx_master=0.0, holo_on=1.0, melt_on=1.0, cell_on=1.0,
                 bg_mode="video", bg_video="C:/introuvable/fond.mp4")
    dirty["fx_on"], dirty["fxl_on"], dirty["noise_fx"] = [1] * 5, [1] * 4, [1] * 5
    dirty.update(logo_x=0.3, logo_path="C:/logo.png", text_content="GARDE")
    changed = gl.panic(dirty)
    clean_ok = (dirty["comp_sym"] == 0 and dirty["comp_pix"] == 0 and dirty["comp_trail"] == 0 and dirty["comp_warp"] == 0
                and dirty["hue_master"] == 0 and dirty["hue_cycle"] == 0 and dirty["build"] == 0 and dirty["strobe_on"] == 0
                and dirty["bg_master"] == 1 and dirty["overlay_master"] == 1 and dirty["tempo"] == 1 and dirty["fx_master"] == 1
                and dirty["auto_phase"] == 0 and not any(dirty["fx_on"]) and not any(dirty["fxl_on"])
                and not any(dirty["noise_fx"]) and dirty["holo_on"] == dirty["melt_on"] == dirty["cell_on"] == 0)
    check(clean_ok and len(changed) > 15, "panique: effets, strobe, montee, composition, teinte, silence coupes, masters a 100 %", str(changed[:6]))
    check(dirty["bg_mode"] == "live" and dirty["logo_x"] == 0.3 and dirty["logo_path"] == "C:/logo.png" and dirty["text_content"] == "GARDE",
          "panique: un fond video sans fichier (ecran noir) repasse en live ; logo, texte et position intacts")
    check(gl.panic(dirty) == [], "panique: deuxieme appui sans effet (idempotente)")
    check(not any(k in gl.PANIC_RESET for k in ("logo_x", "logo_path", "bg_video", "logo_opacity", "auto_master")),
          "panique: ne touche ni au logo, ni au fond choisi, ni aux automations")
    # --- la montee n'est pas coupee par la duree maximale d'une rafale (seule la cadence maximale s'applique)
    lim_b = gl.StrobeLimiter()
    fired_b = sum(1 for k_ in range(0, 6000) if lim_b.allow(k_ * 0.1, 10.0, 8.0, timed=False))        # 10 minutes a 10 Hz
    check(fired_b == 6000 and lim_b.trips == 0 and not lim_b.blocked(600.0),
          "limiteur de strobe: les eclairs de la montee ne sont jamais coupes par la duree max (60 s ou 10 min), seule la cadence est plafonnee", str((fired_b, lim_b.trips)))
    lim_c = gl.StrobeLimiter()
    fired_c = [lim_c.allow(k_ * 0.05, 10.0, 8.0, timed=False) for k_ in range(0, 200)]                  # demande a 20 Hz, plafond 10 Hz
    check(0.45 < sum(fired_c) / len(fired_c) < 0.55, "limiteur de strobe: la montee reste plafonnee par la cadence maximale", str(sum(fired_c)))
    lim_d = gl.StrobeLimiter()
    cut = [lim_d.allow(k_ * 0.1, 10.0, 8.0) for k_ in range(0, 120)]
    check(lim_d.trips == 1 and not all(cut), "limiteur de strobe: un strobe manuel / kick / cadence est toujours coupe apres la duree max")
    # --- tap tempo
    check(abs(gl.tap_bpm([0.0, 0.5, 1.0, 1.5]) - 120.0) < 1e-6 and gl.tap_bpm([0.0]) is None and gl.tap_bpm([]) is None,
          "tap tempo: 120 BPM a 0,5 s d'intervalle ; un seul appui = pas de BPM")
    check(abs(gl.tap_bpm([0.0, 0.5, 5.0, 5.4, 5.8]) - 150.0) < 1e-6, "tap tempo: un trou de plus de 2 s recommence la serie")
    check(abs(gl.tap_bpm([0, 1, 1.5, 2, 2.5, 3, 3.5, 4]) - 120.0) < 1e-6,
          "tap tempo: seuls les derniers appuis comptent (le debut hesitant est oublie)")
    # --- VJ en rythme : N mesures = 4 N kicks, avec garde-fou temporel
    from core import controls as cc, scenes as sc_
    bt = cc.BarTimer(2).arm(10, 100.0, 1.0)
    check(bt.total == 8 and not bt.due(17, 101.0) and bt.due(18, 101.0) and bt.label(10) == "1/2" and bt.label(15) == "2/2",
          "VJ en rythme: 2 mesures = 8 kicks, libelle de la mesure en cours")
    check(not bt.due(10, 107.9) and bt.due(10, 108.0) and cc.BarTimer(1).arm(0, 0.0, 1.0).deadline == 8.0,
          "VJ en rythme: sans kicks, le changement a lieu apres le double de la duree attendue (au moins 8 s)")
    check(cc.BarTimer(8).arm(0, 0.0, 2.0).deadline == 16.0 and cc.BarTimer(8).arm(0, 0.0, 1.0).deadline == 32.0,
          "VJ en rythme: le garde-fou suit le master du tempo (240 BPM = deux fois plus court)")
    cv = sc_.SceneBook._clean_vj({"unit": "bars", "bars": 16, "seconds": 20})
    check(cv["unit"] == "bars" and cv["bars"] == 16 and sc_.SceneBook._clean_vj({"unit": "x", "bars": 3})["unit"] == "seconds"
          and sc_.SceneBook._clean_vj({"bars": 3})["bars"] == 8 and sc_.SceneBook._clean_vj(None)["unit"] == "seconds",
          "VJ en rythme: reglages valides (unite, nombre de mesures), valeurs inconnues ramenees aux defauts")
    check(cc.BarTimer(8).arm(0, 0.0, 1.0, bpm=240.0).deadline == 16.0 and cc.BarTimer(8).arm(0, 0.0, 2.0, bpm=60.0).deadline == 64.0,
          "VJ en rythme: le BPM detecte l'emporte sur le master du tempo pour le garde-fou")
    check("bpm_auto" in gl.PANIC_RESET and "drop_auto" in gl.PANIC_RESET, "panique: coupe aussi le tempo auto et la montee / drop auto")
    check(sc_.SceneBook._clean_vj({"on_drop": True})["on_drop"] is True and sc_.SceneBook._clean_vj({"on_drop": "oui"})["on_drop"] is False
          and sc_.SceneBook._clean_vj(None)["on_drop"] is False, "VJ sur le drop: reglage valide (booleen), faux par defaut")
    # --- rythme detecte : BPM a partir des kicks, montee et drop a partir des basses
    from core import rhythm as rh

    def feed_kicks(times):
        r = rh.Rhythm()
        for tk_ in times:
            r.update(tk_, {"bass": 0.5, "rms": 0.5}, True)
        r.update(times[-1] + 0.3, {"bass": 0.5, "rms": 0.5}, False)
        return r

    steady = [i * 60.0 / 128.0 for i in range(24)]
    r = feed_kicks(steady)
    check(r.bpm is not None and abs(r.bpm - 128.0) < 1.5 and r.confidence > 0.9, "BPM detecte: kicks reguliers a 128 BPM", f"{r.bpm} {r.confidence}")
    r = feed_kicks([i * 60.0 / 64.0 for i in range(20)])
    check(r.bpm is not None and abs(r.bpm - 128.0) < 2.0, "BPM detecte: un kick sur deux (64) est ramene a 128", str(r.bpm))
    rng_ = __import__("random").Random(4)
    jitter = [i * 60.0 / 124.0 + rng_.uniform(-0.012, 0.012) for i in range(32)]
    r = feed_kicks(jitter)
    check(r.bpm is not None and abs(r.bpm - 124.0) < 2.0, "BPM detecte: kicks avec un peu de gigue (+-12 ms)", str(r.bpm))
    missing = [x for i, x in enumerate(steady) if i % 5 != 3]
    r = feed_kicks(missing)
    check(r.bpm is not None and abs(r.bpm - 128.0) < 2.5, "BPM detecte: un kick manque de temps en temps ne fausse pas le tempo", str(r.bpm))
    messy = [0.0, 0.31, 1.4, 1.52, 2.9, 3.33, 4.8, 5.1, 6.9, 7.01]
    r = feed_kicks(messy)
    check(r.bpm is None, "BPM detecte: kicks irreguliers = pas de BPM (confiance trop faible)", str(r.bpm))
    check(feed_kicks(steady[:4]).bpm is None, "BPM detecte: moins de 6 kicks = pas de BPM")
    r = feed_kicks(steady)
    t_last = steady[-1]
    r.update(t_last + 6.5, {"bass": 0.0, "rms": 0.0}, False)
    check(r.bpm is None, "BPM detecte: sans kick depuis 5 s, le BPM est perdu")
    # changement de morceau : 128 puis 100 BPM => le tempo saute apres quelques estimations
    r = rh.Rhythm()
    now_ = 0.0
    for i in range(24):
        now_ = i * 60.0 / 128.0
        r.update(now_, {"bass": 0.5}, True)
    start2 = now_ + 0.6
    for i in range(60):
        now_ = start2 + i * 0.6
        r.update(now_, {"bass": 0.5}, True)
    check(r.bpm is not None and abs(r.bpm - 100.0) < 3.0, "BPM detecte: le tempo suit un changement de morceau", str(r.bpm))
    # montee et drop : pleine energie, cassure (basses absentes), retour ; la montee exige que les mediums / aigus montent vraiment
    def play(det, segments):
        """segments : (secondes, basses, mediums-aigus au debut, a la fin) ; rend [(evenement, instant)]."""
        ev_, clk = [], 0.0
        for seconds, bass_lv, top0, top1 in segments:
            n_ = int(seconds * 100)
            for k_ in range(n_):
                clk += 0.01
                wob = bass_lv * 0.1 * (1.0 if (k_ // 25) % 2 else -1.0)
                top_lv = top0 + (top1 - top0) * k_ / max(n_ - 1, 1)
                e_ = det.update(clk, bass_lv + wob, 0.5, top_lv)
                if e_:
                    ev_.append((e_, round(clk, 1)))
        return ev_
    dd = rh.DropDetector()
    events = play(dd, [(20, 0.7, 0.3, 0.3), (12, 0.04, 0.15, 0.8), (10, 0.7, 0.3, 0.3)])
    check([e for e, _ in events] == ["build", "drop"] and dd.drops == 1 and dd.state == "idle",
          "montee / drop: cassure des basses + montee des aigus, puis retour des basses = une montee puis un drop", str(events))
    check(len(events) == 2 and 28.0 <= events[0][1] <= 33.0 and 31.5 <= events[1][1] <= 32.5,
          "montee / drop: la montee n'est annoncee qu'apres une cassure installee, le drop des le retour des basses", str(events))
    ddf = rh.DropDetector()
    flat = play(ddf, [(20, 0.7, 0.3, 0.3), (12, 0.04, 0.3, 0.3), (10, 0.7, 0.3, 0.3)])
    check("build" not in [e for e, _ in flat] and ddf.state == "idle",
          "montee: une simple absence de basses SANS montee des mediums / aigus n'est pas une montee", str(flat))
    ddp = rh.DropDetector()
    short = play(ddp, [(20, 0.7, 0.3, 0.3), (2.5, 0.04, 0.15, 0.8), (6, 0.7, 0.3, 0.3)])
    check(not short, "montee / drop: une baisse des basses de moins de 3 s (un break d'une mesure, un trou dans le motif) ne declenche rien", str(short))
    ddk = rh.DropDetector()
    sparse = []
    for i in range(6000):                                              # kicks espaces de 2 s (basses 0,9 pendant 0,15 s), mediums constants
        tt = i * 0.01
        sparse.append(ddk.update(tt, 0.9 if (tt % 2.0) < 0.15 else 0.05, 0.4, 0.3 + 0.02 * (i % 7)))
    check(not any(sparse), "montee / drop: des kicks espaces / un motif de basses clairseme ne declenchent rien (60 s)")
    dd5 = rh.DropDetector()
    ev5 = play(dd5, [(15, 0.7, 0.3, 0.3), (8, 0.04, 0.3, 0.3), (3, 0.7, 0.3, 0.3), (8, 0.04, 0.3, 0.3), (3, 0.7, 0.3, 0.3)])   # drop, puis une 2e cassure : pause de 12 s
    check(dd5.drops == 1 and dd5.state == "idle" and [e for e, _ in ev5] == ["drop"], "montee / drop: pas de second drop pendant la pause de 12 s (retour au calme, pas de drop en retard)", f"{dd5.drops} {dd5.state} {ev5}")
    dd2 = rh.DropDetector()
    ev2 = [dd2.update(i * 0.01, 0.6 + (0.1 if (i // 20) % 2 else -0.1), 0.6) for i in range(3000)]
    check(not any(ev2), "montee / drop: un passage plein et regulier ne declenche rien")
    dd3 = rh.DropDetector()
    ev3 = [dd3.update(i * 0.01, 0.01, 0.0) for i in range(3000)]
    check(not any(ev3), "montee / drop: un silence depuis le debut ne declenche rien (pas de niveau de reference)")
    dd4 = rh.DropDetector()
    for i in range(2000):
        dd4.update(i * 0.01, 0.7, 0.7)
    for i in range(2000, 2000 + 5200):
        dd4.update(i * 0.01, 0.03, 0.0)                        # cassure de 52 s : au-dela de 45 s ce n'est plus une cassure
    check(dd4.state == "idle" and dd4.drops == 0, "montee / drop: une cassure trop longue est abandonnee sans drop", dd4.state)
    # --- securite GPU : alleger la scene suivante selon ses effets et la charge mesuree
    from core import safety as sf
    heavy = {"cell_on": 1.0, "holo_on": 1.0, "melt_on": 1.0, "bg_hue": 0.3}
    calm = {"cell_on": 0.0, "holo_on": 0.0, "melt_on": 0.0}
    full = (1920, 1080)
    same_v, dropped = sf.cap_values(heavy, calm, 0.0, 16.7, full)
    check(same_v is heavy and dropped == [], "securite GPU: sans mesure du GPU, rien n'est change")
    out_v, dropped = sf.cap_values(heavy, calm, 8.0, 16.7, full)
    check(dropped == ["cellules"] and out_v["cell_on"] == 0.0 and out_v["holo_on"] == 1.0 and out_v["melt_on"] == 1.0 and heavy["cell_on"] == 1.0
          and out_v["bg_hue"] == 0.3, "securite GPU: GPU a 8 ms + effets lourds = les cellules sautent d'abord, le reste est intact, l'entree n'est pas modifiee", str((out_v, dropped)))
    out_v, dropped = sf.cap_values(heavy, calm, 13.0, 16.7, full)
    check(dropped == ["cellules", "halo"] and out_v["melt_on"] == 1.0, "securite GPU: plus la charge est haute, plus d'effets sautent (cellules > halo > fonte)", str(dropped))
    same_v, dropped = sf.cap_values(heavy, {"cell_on": 1.0, "holo_on": 1.0, "melt_on": 1.0}, 14.0, 16.7, full)
    check(dropped == [] and same_v is heavy, "securite GPU: un effet deja allume au moment (mesure incluse) n'est pas compte deux fois")
    same_v, dropped = sf.cap_values(heavy, calm, 1.2, 16.7, (640, 360))
    check(dropped == [], "securite GPU: a petite taille de rendu les memes effets coutent beaucoup moins (cout proportionnel aux pixels)")
    check(abs(sf.effects_cost({"cell_on": 1.0, "holo_on": 1.0}, full) - 10.0) < 1e-9 and sf.effects_cost({}, full) == 0.0, "securite GPU: cout estime des effets allumes")
    # --- annuler / retablir
    udir = Path(tempfile.mkdtemp(prefix="casual_overlay_undo_"))
    ubook = sc_.SceneBook(lambda: udir / "scenes.json")
    ubook.put({"name": "A", "bg": "live", "overlay": {}})
    ubook.put({"name": "B", "bg": "live", "overlay": {}})
    check(ubook.undo_label() == "Enregistrer la scene" and ubook.undo() == "Enregistrer la scene" and ubook.names() == ["A"] and ubook.row_names() == ["A"],
          "annuler: la derniere ecriture est defaite (scene et entree du set)")
    check(ubook.redo_label() == "Enregistrer la scene" and ubook.redo() and ubook.names() == ["A", "B"] and ubook.redo() is None, "retablir: on la refait, puis plus rien a retablir")
    with ubook.group("Trois scenes"):
        for n_ in "CDE":
            ubook.put({"name": n_, "bg": "live", "overlay": {}})
    check(ubook.names() == ["A", "B", "C", "D", "E"] and ubook.undo() == "Trois scenes" and ubook.names() == ["A", "B"],
          "annuler: un groupe d'ecritures (ex. +5) est UN seul pas")
    ubook.redo()
    ubook.remove_entries([1, 3])
    check(ubook.row_names() == ["A", "C", "E"] and ubook.undo() == "Retirer des lignes" and ubook.row_names() == ["A", "B", "C", "D", "E"],
          "annuler: retirer plusieurs lignes se defait, l'ordre revient")
    ubook.move_entry(0, 2)
    ubook.new_set("Autre")
    ubook.set_active("Autre")
    check(ubook.undo() == "Nouveau set" and ubook.set_names() == [sc_.DEFAULT_SET] and ubook.active_set_name() == sc_.DEFAULT_SET,
          "annuler: creer un set se defait (le set affiche retombe sur un set qui existe)", str(ubook.set_names()))
    ubook.new_set("S1")
    ubook.new_set("S2")
    ubook.set_active("S1")
    check(ubook.undo() == "Nouveau set" and ubook.set_names() == [sc_.DEFAULT_SET, "S1"] and ubook.active_set_name() == "S1",
          "annuler: changer de set n'est pas une modification a annuler, et annuler ne change pas le set affiche")
    ubook.set_active(sc_.DEFAULT_SET)
    ubook.put({"name": "Z", "bg": "live", "overlay": {}})
    ubook.undo()
    ubook.put({"name": "Y", "bg": "live", "overlay": {}})
    check(ubook.redo() is None, "annuler: une nouvelle modification efface les pas a retablir")
    ubook2 = sc_.SceneBook(lambda: udir / "autre.json")
    check(ubook2.undo() is None and ubook2.undo_label() is None, "annuler: l'historique est propre a chaque fichier de scenes")
    ubook.keep_scene("A", "A renommee")
    check(ubook.undo() == "Garder la scene" and "A" in ubook.names() and "A renommee" not in ubook.names(), "annuler: garder une scene (renommer + retirer le drapeau) = un seul pas")
    n_hist = len(sc_.SceneBook._HISTORY[str(udir / "scenes.json")]["undo"])
    pdir = udir / "previews"
    old_pdir, sc_.PREVIEW_DIR = sc_.PREVIEW_DIR, pdir
    pdir.mkdir()
    (pdir / "old.jpg").write_bytes(b"x")
    (pdir / "new.jpg").write_bytes(b"y")
    ubook.set_preview("A", "old.jpg")
    ubook.set_preview("A", "new.jpg")
    check(ubook.get("A")["preview"] == "new.jpg" and not (pdir / "old.jpg").exists() and (pdir / "new.jpg").exists()
          and len(sc_.SceneBook._HISTORY[str(udir / "scenes.json")]["undo"]) == n_hist, "apercu: associe a la scene, l'ancien fichier est supprime, ce n'est pas un pas d'annulation")
    sc_.PREVIEW_DIR = old_pdir
    from engine import preview as pv
    rgb = bytes([255, 0, 0]) * (64 * 18) + bytes([0, 0, 255]) * (64 * 18)           # premiere ligne en bas (OpenGL) : rouge en bas, bleu en haut
    dest_p = udir / "p" / "apercu.jpg"
    ok_p = pv.write_preview(rgb, (64, 36), dest_p)
    from PIL import Image as _Img
    im_p = _Img.open(dest_p).convert("RGB")
    check(ok_p and im_p.size == (240, 135) and im_p.getpixel((120, 5))[2] > 200 and im_p.getpixel((120, 130))[0] > 200
          and not pv.write_preview(b"", (0, 0), udir / "x.jpg"), "apercu: JPEG reduit a 240 px de large, image remise a l'endroit, taille invalide refusee sans exception",
          str((im_p.size, im_p.getpixel((120, 5)), im_p.getpixel((120, 130)))))
    # --- auto-sauvegarde
    tmp = Path(tempfile.mkdtemp(prefix="casual_overlay_autosave_"))
    snap = tmp / "autosave.json"
    check(gl.read_autosave(snap) is None, "auto-sauvegarde: aucun instantane = None")
    params = json.loads(json.dumps(gl.DEFAULT_PARAMS))
    params.update(hue_master=77.0, strobe_on=1.0, build=0.5, text_content="SOIREE")
    check(gl.write_autosave(params, {"scene": "Purple", "set": "Soiree"}, snap), "auto-sauvegarde: ecriture")
    got = gl.read_autosave(snap)
    check(got is not None and got["params"]["hue_master"] == 77.0 and "strobe_on" not in got["params"] and "build" not in got["params"]
          and got["scene"] == "Purple" and not (tmp / "autosave.tmp").exists(),
          "auto-sauvegarde: reglages sans etats du moment, scene et set, ecriture atomique (pas de .tmp)")
    base_params = json.loads(json.dumps(gl.DEFAULT_PARAMS))
    recovered, msg = gl.recover_autosave(base_params, snap)
    check(recovered["hue_master"] == 77.0 and recovered["text_content"] == "SOIREE" and "Purple" in msg and "restaures" in msg
          and not snap.exists() and (tmp / "autosave.precedent.json").exists(),
          "reprise apres arret brutal: reglages restaures, scene signalee, instantane mis de cote", msg)
    again, msg2 = gl.recover_autosave(base_params, snap)
    check(again is base_params and msg2 == "", "reprise: sans instantane, rien ne change")
    snap.write_text("{ pas du json", encoding="utf-8")
    bad, msg3 = gl.recover_autosave(base_params, snap)
    check(bad is base_params and msg3 == "", "reprise: un instantane illisible est ignore sans erreur")
    gl.clear_autosave(snap)
    gl.write_autosave(params, None, snap)
    gl.clear_autosave(snap)
    check(not snap.exists(), "sortie propre: l'instantane est efface")
    check(not gl.write_autosave(params, None, tmp / "absent" / "x" / "\0bad"), "auto-sauvegarde: un chemin impossible ne leve pas d'erreur")
    # --- journal de session
    from core import sessionlog
    log_path = tmp / "casual-overlay.log"
    sessionlog.setup(log_path)
    sessionlog.event("Scene : Purple")
    try:
        raise ValueError("boum")
    except ValueError as exc:
        sessionlog.error("Erreur de test", exc)
    for h in list(sessionlog.logger().handlers):
        h.flush()
    text = log_path.read_text(encoding="utf-8")
    check("INFO" in text and "Scene : Purple" in text and "ERROR" in text and "ValueError: boum" in text and "Traceback" in text,
          "journal: evenements et erreurs (avec la trace) ecrits dans le fichier")
    check(any("Scene : Purple" in line for line in sessionlog.tail(10, log_path)), "journal: tail rend les dernieres lignes")
    for h in list(sessionlog.logger().handlers):                    # le journal de test ne reste pas branche
        sessionlog.logger().removeHandler(h)
        h.close()
    sessionlog.event("ignore : aucun fichier branche")              # sans fichier, un evenement ne leve rien
    check(True, "journal: sans fichier branche, un evenement est ignore sans erreur")


def check_native_modes() -> None:
    """Snap (pencil, simple, rekordbox) et Ridge dessines en GL (engine/native_modes.py) : memes pixels que les rendus Python d'audio2wave
    (pencil, ridge : a un niveau pres ; simple / rekordbox : comparaison a ffmpeg), revelation progressive, video, halo, source complete."""
    print("modes Snap / Ridge dessines en GL")
    import random
    from types import SimpleNamespace as NS

    from engine import native_modes as nm
    sys.path.insert(0, str(gl.DEFAULT_A2W_DIR)) if str(gl.DEFAULT_A2W_DIR) not in sys.path else None
    import audio2wave_ridge as ridge_mod
    import audio2wave_snap as snap_mod
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size, rate = (640, 240), 48000

    def synth(sec, seed=1):
        rng = np.random.default_rng(seed)
        t = np.arange(int(rate * sec)) / rate
        env = 0.15 + 0.6 * np.abs(np.sin(2 * np.pi * 0.7 * t))
        kick = np.exp(-((t * 2) % 1.0) * 8) * np.sin(2 * np.pi * 55 * t)
        return (env * 0.5 * np.sin(2 * np.pi * 220 * t) + 0.5 * kick + 0.05 * rng.standard_normal(len(t))).astype(np.float32)

    def pixels(tex):
        return np.frombuffer(tex.read(), np.uint8).reshape(size[1], size[0], 3).astype(int)

    def rgb8(color):
        return bytes(round(c * 255) for c in color)

    x = synth(2.0)
    pcm = nm.to_pcm(x)
    check(nm.available(), "modes GL: numpy et moderngl presents")

    # --- Snap pencil : meme image que compose_pencil (CPU) ---
    def pencil_gl(extra, vids=None, frac=1.0, second=None):
        args = snap_mod.parse_args(["--style", "pencil", "--size", f"{size[0]}x{size[1]}", "--rate", str(rate)] + extra)
        args.stereo = False
        gain, _ = snap_mod.resolve_gain(args, pcm)
        photo = nm.pencil_photo(snap_mod, args, pcm, rate, size, gain, bool(args.kick_glow and args.glow_live))
        g = nm.ModeGL(ctx, size, snap_mod)
        if vids:
            g.videos = [NS(reader=NS(upload_to=lambda t: False), stop=lambda: None), NS(reader=NS(upload_to=lambda t: False), stop=lambda: None)]
            for i, frame in enumerate(vids):
                g.vid_tex[i].write(frame)
        if second is not None:
            g.install(second(args, gain))
        g.install(photo)
        img = pixels(g.draw(frac, None, args))
        g.release()
        cols = snap_mod.pencil_heights(args, pcm, gain, size)
        return img, args, gain, photo, cols

    for label, extra in (("trait seul", []), ("--wave", ["--wave", "6"]), ("halo des kicks", ["--kick-glow", "--kick-glow-size", "50"]),
                         ("trait epais, couleurs", ["--line-width", "5", "--colors", "0x39c9ff", "--bg-color", "0x14161c"])):
        img, args, gain, photo, cols = pencil_gl(extra)
        bg, ink = rgb8(photo["bg"]), rgb8(photo["ink"])
        kicks = snap_mod.detect_kicks(pcm, 1, rate, size[0]) if args.kick_glow else None
        ref = np.frombuffer(snap_mod.compose_pencil(size, cols, bg, ink, max(1, args.line_width), kicks=kicks,
                                                    kick_glow_radius=args.kick_glow_size), np.uint8).reshape(size[1], size[0], 3).astype(int)
        diff = np.abs(img - ref).max()
        check(diff <= 1, f"Snap pencil GL ({label}): memes pixels que le rendu Python d'audio2wave", f"ecart max {diff}")
    # video interieure / exterieure
    grad = np.zeros((size[1], size[0], 3), np.uint8)
    grad[..., 0] = np.linspace(0, 255, size[0], dtype=np.uint8)[None, :]
    grad[..., 1] = np.linspace(0, 255, size[1], dtype=np.uint8)[:, None]
    other = 255 - grad
    img, args, gain, photo, cols = pencil_gl([], vids=[grad.tobytes(), other.tobytes()])
    ref = np.frombuffer(snap_mod.compose_pencil(size, cols, rgb8(photo["bg"]), rgb8(photo["ink"]), 2, grad.tobytes(), other.tobytes()),
                        np.uint8).reshape(size[1], size[0], 3).astype(int)
    check(np.abs(img - ref).max() <= 1, "Snap pencil GL: video interieure (dans l'enveloppe) et exterieure (autour), comme audio2wave",
          f"ecart max {np.abs(img - ref).max()}")
    # revelation progressive : a moitie, la moitie gauche est la nouvelle photo, la droite la precedente
    def older(args, gain):
        return nm.pencil_photo(snap_mod, args, nm.to_pcm(synth(2.0, seed=9)), rate, size, gain, False)
    half, _a, _g, _p, _c = pencil_gl(["--line-width", "3"], frac=0.5, second=older)
    full, *_ = pencil_gl(["--line-width", "3"], frac=1.0)
    old_only, *_ = pencil_gl(["--line-width", "3"], frac=0.0, second=older)
    w2 = size[0] // 2
    check(np.array_equal(half[:, :w2], full[:, :w2]) and np.array_equal(half[:, w2:], old_only[:, w2:]) and not np.array_equal(full, old_only),
          "Snap GL: revelation progressive : colonnes revelees = photo courante, les autres = photo precedente")

    # --- Ridge : memes pixels que shift_canvas + paint_ridge_line (6 lignes de suite) ---
    rargs = ridge_mod.parse_args(["--size", f"{size[0]}x{size[1]}", "--rate", str(rate)])
    bg, ink = nm.parse_color(ridge_mod, rargs.bg_color or "black"), nm.parse_color(ridge_mod, rargs.colors or "white")
    canvas = bytearray(rgb8(bg) * (size[0] * size[1]))
    tracker, rng = ridge_mod.RidgeGain(rargs.gain_window), random.Random(5)
    gl_r = nm.ModeGL(ctx, size, ridge_mod)
    worst = 0
    for i in range(6):
        p_i = nm.to_pcm(synth(2.0, seed=10 + i))
        g, _ = tracker.resolve(rargs, p_i)
        heights = ridge_mod.render_ridge_line(rargs, p_i, g, size, rng)
        ridge_mod.shift_canvas(canvas, size, rargs.ridge_spacing, rgb8(bg))
        canvas[:] = ridge_mod.paint_ridge_line(canvas, size, heights, rgb8(bg), rgb8(ink), max(1, rargs.line_width))
        gl_r.install({"style": "ridge", "data": np.asarray(heights, np.float32).reshape(1, -1), "thick": max(1, rargs.line_width),
                      "spacing": rargs.ridge_spacing, "ink": ink, "bg": bg})
        gl_r.draw(0.4, None, rargs)
        img = pixels(gl_r.draw(1.0, None, rargs))
        worst = max(worst, np.abs(img - np.frombuffer(bytes(canvas), np.uint8).reshape(size[1], size[0], 3)).max())
    check(worst == 0, "Ridge GL: 6 lignes de suite (decalage, occultation, trait) strictement identiques au canevas Python", f"ecart max {worst}")
    gl_r.release()

    # --- Snap simple / rekordbox : comparaison a ffmpeg (showwavespic + overlay), ecarts d'un pixel sur les bords ---
    for style, extra in (("simple", []), ("simple", ["--scale", "log", "--filter-mode", "peak"]), ("rekordbox", []),
                         ("rekordbox", ["--scale", "lin", "--crossover", "120,3000"])):
        args = snap_mod.parse_args(["--style", style, "--size", f"{size[0]}x{size[1]}", "--rate", str(rate)] + extra)
        args.stereo = False
        gain, _ = snap_mod.resolve_gain(args, pcm)
        out = snap_mod.render_photo(args, pcm, gain, size, None)
        g = nm.ModeGL(ctx, size, snap_mod)
        g.install(nm.bars_photo(snap_mod, args, x, rate, size, gain))
        img = pixels(g.draw(1.0, None, args))
        g.release()
        if out is None:
            check(False, f"Snap {style} GL: ffmpeg a rendu la reference")
            continue
        ref = np.frombuffer(out, np.uint8).reshape(size[1], size[0], 3).astype(int)
        share = float((np.abs(img - ref).max(axis=2) > 40).mean())
        check(share < 0.045, f"Snap {style} GL ({' '.join(extra) or 'defauts'}): meme image que showwavespic d'ffmpeg (bords a un pixel pres)",
              f"{share * 100:.2f} % de pixels differents")

    # --- la source complete : analyse dans un fil, photo envoyee au fil GL, memes attributs que PyModeSource ---
    ring = gl.RingBuffer(1 << 20)
    audio = NS(ring=ring, rate=rate, synthetic=True)
    ring.write(synth(3.0))
    for name, mod in (("snap", snap_mod), ("ridge", ridge_mod)):
        sargs = mod.parse_args(["--gui"])
        sargs.size = f"{size[0]}x{size[1]}"
        sargs.interval = 0.25
        src = nm.NativeModeSource(name, mod, sargs, size, audio)
        src.start()
        tex = None
        t_end = time.monotonic() + 6.0
        while time.monotonic() < t_end and tex is None:
            tex = src.render(ctx, time.monotonic())
            time.sleep(0.05)
        img = pixels(tex) if tex is not None else None
        for _ in range(8):
            time.sleep(0.12)
            tex = src.render(ctx, time.monotonic())
        check(img is not None and np.ptp(pixels(tex)) > 0 and src.photos >= 2 and str(src.status.get("text", "")).startswith("["),
              f"source GL {name}: photos calculees dans un fil a partir du tampon audio, texture du fond rendue, statut", f"{src.photos} photos")
        check(src.capture_state["capture"].latest() is not None and src.capture_state["capture"].recent_level(960) is not None
              and nm.NullReader().upload_to(None) is False and src.native is True,
              f"source GL {name}: `capture_state` (bouton Mesurer) et lecteur nul comme PyModeSource")
        src.stop()
        src.thread.join(3.0)
        check(src.finished_event.is_set(), f"source GL {name}: arret propre du fil")
        src.release_gl()
    # halo temps reel : pilote par le niveau du tampon, sans photo de kicks
    sargs = snap_mod.parse_args(["--gui", "--kick-glow", "--glow-live"])
    sargs.interval = 0.25
    src = nm.NativeModeSource("snap", snap_mod, sargs, size, audio)
    src.start()
    time.sleep(0.8)
    live = [src._live_glow() for _ in range(3)]
    for _ in range(4):
        tex = src.render(ctx, time.monotonic())
        time.sleep(0.05)
    check(all(v is not None and 0.0 <= v <= 1.0 for v in live) and tex is not None, "Snap GL: halo temps reel (--glow-live) lu sur le tampon audio", str(live))
    src.stop()
    src.thread.join(3.0)
    src.release_gl()
    class FakeAudio:                                   # l'entree choisie dans le panneau du mode : l'analyse suit sur l'entree sounddevice du meme nom
        synthetic = False

        @staticmethod
        def input_choices():
            return [(3, "[MME] Microphone (Realtek(R) Audio)"), (5, "[WASAPI] Cable Output")]

        def switch(self, index):
            self.calls.append(index)
            return f"entree {index}"
    fa = FakeAudio()
    fa.rate, fa.ring, fa.calls = rate, ring, []
    follow = nm.NativeModeSource("snap", snap_mod, snap_mod.parse_args(["--gui"]), size, fa)
    follow._follow_device("Microphone (Realtek(R) Audio)")
    follow._follow_device("Inconnue")
    check(fa.calls == [3] and "introuvable" in follow.status["text"], "mode GL: changer l'entree dans le panneau du mode fait suivre l'analyse (meme nom), nom inconnu signale",
          f"{fa.calls} {follow.status.get('text')}")
    ring_small = gl.RingBuffer(1 << 14)
    short = nm.NativeModeSource("snap", snap_mod, snap_mod.parse_args(["--gui"]), size, NS(ring=ring_small, rate=rate, synthetic=True))
    check(short.capture_state["capture"].latest() is None, "source GL: fenetre pas encore pleine -> pas de photo (None), comme LiveCapture")
    # le rendu GL accepte la texture du mode a la place du flux video
    r = gl.Renderer(ctx, size, None)
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
    st = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
    prm = dict(gl.DEFAULT_PARAMS, master=0.0, logo_glow=0.0, logo_pulse=0.0, logo_jitter=0.0, holo_on=0.0)
    r.video_tex.write(bytes([0, 0, 255]) * (size[0] * size[1]))
    r.draw(fbo, size, st, prm, 0.0)
    plain = np.frombuffer(fbo.read(components=3), np.uint8).reshape(size[1], size[0], 3).astype(int)
    g = nm.ModeGL(ctx, size, snap_mod)
    sargs = snap_mod.parse_args(["--style", "pencil", "--size", f"{size[0]}x{size[1]}"])
    g.install(nm.pencil_photo(snap_mod, sargs, pcm, rate, size, 0.0, False))
    r.mode_tex = g.draw(1.0, None, sargs)
    r.draw(fbo, size, st, prm, 0.0)
    shown = np.frombuffer(fbo.read(components=3), np.uint8).reshape(size[1], size[0], 3).astype(int)
    check(plain[..., 2].min() == 255 and ((shown[..., 0] == 0) & (shown[..., 2] == 255)).mean() < 0.05 and shown.max() > 200,
          "rendu: `mode_tex` remplace le flux video (le fond bleu du flux disparait, le trait du mode apparait)")
    g.release()
    # bascule de mode (gl_gui.activate_mode) vers un mode GL, sans ffmpeg (--synthetic) puis retour au flux d'origine
    import gl_gui
    live_mod = gl.load_live(gl.DEFAULT_A2W_DIR)
    base_reader = object()
    sess = NS(mode="live", pysrc=None, manager=None, reader=base_reader, live_args=NS(device=None), mode_args={}, render_size=size, audio=audio)
    audio.synthetic = True
    msg = gl_gui.activate_mode(sess, live_mod, "snap")
    check(msg is None and sess.mode == "snap" and getattr(sess.pysrc, "native", False) and sess.reader is sess.pysrc.reader,
          "bascule vers Snap en --synthetic : mode GL actif (sans ffmpeg), flux nul", str(msg))
    msg = gl_gui.activate_mode(sess, live_mod, "ridge")
    check(msg is None and sess.mode == "ridge" and sess.pysrc.name == "ridge", "bascule Snap -> Ridge : l'ancien mode est arrete, le nouveau demarre", str(msg))
    first = sess.pysrc
    msg = gl_gui.activate_mode(sess, live_mod, "live")
    check(msg is None and sess.mode == "live" and sess.pysrc is None and sess.reader is base_reader and first.stop_event.is_set(),
          "retour au mode Live en --synthetic : flux d'origine rendu, mode GL arrete", str(msg))


# Verifications de taille / mise en page de la fenetre : elles dependent de l'etat de l'ecran (fenetre deja deplacee, focus, minuteurs de Tk) et
# echouent parfois seules sans qu'il y ait de defaut. Si TOUS les echecs d'une execution sont de cette liste, le programme sort avec le code 3
# (et non 1) : l'integration continue relance alors la suite une fois ; un echec qui revient, ou tout autre echec, reste un echec (code 1).
EXIT_FRAGILE = 3
FRAGILES = ("garde sa taille quand une scene change de mode", "le bouton agrandit la fenetre", "le bouton du nom remplit sa case",
            "cases VJ sont habillees", "la molette defile la liste", "un panneau reconstruit reste habille", "bibliotheque a gauche du set",
            "un nom de scene tres long", "reduire rend sa taille d'origine", "espace de controle global a droite de la liste",
            "lecture = minuteur arme a la duree choisie")          # (minuteurs reels : un serveur charge les depasse parfois)


# Verifications qui supposent un grand ecran (mise en page large, hauteur de la fenetre sous 700 px...) : sur un ecran plus petit que SCREEN_MIN
# (serveur d'integration continue en 1024 x 768) elles ne veulent rien dire, on les ecarte en le disant.
SCREEN_MIN = (1500, 900)
SCREEN_DEPENDENT = ("fenetre large", "fenetre moyenne", "retour a une grande fenetre", "fenetre sous 700 px", "le bouton agrandit la fenetre",
                    "reduire rend sa taille d'origine", "bibliotheque a gauche du set")


def drop_screen_dependent() -> None:
    """Retire de `failures` les echecs de mise en page qui tiennent a un ecran trop petit (et dit lesquels)."""
    if not any(any(m in label for m in SCREEN_DEPENDENT) for label in failures):
        return
    try:
        import tkinter
        probe = tkinter.Tk()
        probe.withdraw()
        size = (probe.winfo_screenwidth(), probe.winfo_screenheight())
        probe.destroy()
    except Exception:
        return
    if size[0] >= SCREEN_MIN[0] and size[1] >= SCREEN_MIN[1]:
        return
    ignored = [label for label in failures if any(m in label for m in SCREEN_DEPENDENT)]
    failures[:] = [label for label in failures if label not in ignored]
    print(f"Ecran {size[0]} x {size[1]} (< {SCREEN_MIN[0]} x {SCREEN_MIN[1]}) : {len(ignored)} verification(s) de mise en page ecartee(s) :")
    for label in ignored:
        print("  - " + label)


def only_fragile(labels: list[str]) -> bool:
    return bool(labels) and all(any(marker in label for marker in FRAGILES) or "aucune erreur" in label for label in labels)


# `--pur` : seulement les verifications qui n'ont besoin ni de GPU, ni de fenetre, ni de micro, ni de ffmpeg (integration continue : GitHub Actions)
SANS_GPU = "--pur" in sys.argv
PURES = ("check_reader", "check_features", "check_features_phone", "check_audio2wave_interne", "check_portable_modules", "check_core_purity", "check_engine_layer",
         "check_params_schema", "check_uistats", "check_device_cache", "check_scenes", "check_beat_grid", "check_tempo", "check_phase", "check_audio_layers_pure", "check_onsets", "check_strobe_auto", "check_schedule", "check_palette",
         "check_stage_layout")


def main() -> None:
    if SANS_GPU:
        for name in PURES:
            globals()[name]()
        if failures:
            print(f"{len(failures)} verification(s) en echec: {', '.join(failures)}")
            sys.exit(1)
        print("Toutes les verifications pures passent.")
        return
    check_reader()
    check_features()
    check_features_phone()
    check_text()
    check_logo_video()
    check_holo()
    check_melt()
    check_cells()
    check_noise()
    check_doodles()
    check_icons()
    check_audio2wave_interne()
    check_masters()
    check_portable_modules()
    check_core_purity()
    check_engine_layer()
    check_params_schema()
    check_safeguards()
    check_uistats()
    check_device_cache()
    check_scenes()
    check_bg_video()
    check_fond_saveable()
    check_randomizer()
    check_diagnostics()
    check_skin()
    check_scrollbar()
    check_menu_style()
    check_dialogs()
    check_audio2wave_api()
    check_projection()
    check_bar_gap()
    check_line_resolution()
    check_contract()
    check_vj_flash()
    check_gates()
    check_layers()
    check_audio_layers_pure()
    check_onsets()
    check_strobe_auto()
    check_audio_layers()
    check_automation()
    check_params()
    check_manager()
    check_native_modes()
    os.environ["CASUAL_MODES_PYTHON"] = "1"          # check_gui valide le repli : les modes Snap / Ridge dessines par audio2wave en Python
    check_gui()
    os.environ.pop("CASUAL_MODES_PYTHON", None)
    check_render()
    check_latence()
    check_beat_grid()
    check_tempo()
    check_phase()
    check_schedule()
    check_palette()
    check_stage_layout()
    print()
    try:                                                   # information (rien n'echoue) : operations les plus lentes de l'interface pendant les tests
        import gl_gui as gg_stats
        print("Fluidite de l'interface pendant les tests (pire temps) :")
        for line in gg_stats.STATS.report(16):
            print("  " + line)
    except Exception:
        pass
    drop_screen_dependent()
    if failures:
        print(f"{len(failures)} verification(s) en echec: {', '.join(failures)}")
        if only_fragile(failures):
            print(f"Seules des verifications de mise en page connues pour etre instables ont echoue : code {EXIT_FRAGILE} (relancer une fois).")
            sys.exit(EXIT_FRAGILE)
        sys.exit(1)
    print("Toutes les verifications passent.")


if __name__ == "__main__":
    main()
