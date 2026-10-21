"""Telecommande telephone : pont entre le serveur (`engine/remote.py`, fil HTTP) et la fenetre de reglages (fil Tk).

Module de la fenetre de reglages (comme les `gui_*.py`) : `install(c)` recoit les fonctions de la fenetre par le contexte `c`.

  * le fil HTTP pose les commandes VALIDEES (`core.remote_auth.validate_command`) dans `s.remote_requests` ; ce module les execute **dans le fil Tk**
    (toutes les 100 ms, planificateur de la fenetre) avec les memes gestes que les touches et les boutons : rien de la logique VJ n'est reecrit ici ;
  * il publie l'etat (`core.remote_state.build_state`) 5 fois par seconde au plus, seulement quand la telecommande est active ;
  * il possede la case « Telecommande telephone » (onglet Affichage, desactivee a chaque lancement), la fenetre du QR code / du code de secours et le
    bouton Deconnecter.
Pas de PANIQUE ni de tap tempo ici (decision de l'utilisateur) ; le strobe maintenu se relache tout seul si le telephone disparait.
"""
from __future__ import annotations

import os
import queue
import tempfile
import time
import tkinter as tk
from pathlib import Path

import audio2wave_gl as gl
from core import qrcode, remote_auth, remote_state, sessionlog
from engine import remote as rem

PAGE_DIR = Path(__file__).resolve().parent / "remote"
POLL_MS = 100
PUBLISH_EVERY_S = 0.2
STROBE_KEEPALIVE_S = 1.5          # le telephone renvoie « appui » toutes les 0,5 s : sans nouvelles, le strobe se relache
PREVIEW_EVERY_S = 5.0             # au plus une capture toutes les 5 s
PREVIEW_WIDTH = 480
GPU_LIMIT = 0.8                   # pas de capture si le GPU depasse 80 % du budget d'une image


def install(c):
    from gl_gui import SCENE_COLOR
    a2w = c.a2w
    s = c.s
    params = c.params
    root = c.root
    scene_book = c.scene_book
    scene_msg = c.scene_msg
    Tooltip = c.Tooltip
    box = c.remote_box
    st: dict = {"access": remote_auth.Access(), "server": None, "snapshot": {"v": remote_state.VERSION}, "published": 0.0, "strobe_held": False,
                "strobe_until": 0.0, "preview_on": False, "preview_at": 0.0, "preview_note": "", "dialog": None, "msg": "", "last_label": None}
    preview_final = Path(tempfile.gettempdir()) / f"casual_remote_preview_{os.getpid()}.jpg"
    preview_new = preview_final.with_name(preview_final.stem + ".new.jpg")
    if getattr(s, "remote_requests", None) is None:
        s.remote_requests = queue.SimpleQueue()
    requests = s.remote_requests

    def running() -> bool:
        server = st["server"]
        return server is not None and server.running()

    # ---- etat publie
    def snapshot() -> dict:
        rows = scene_book.rows()
        active = c.current_index(rows)
        try:
            voyants = c.vj_voyants()
        except Exception:
            voyants = {}
        masters = {k: params.get(k, 0.0) for k in remote_auth.MASTER_KEYS}
        autos = {k: float(params.get(k, 0.0)) >= 0.5 for k in remote_auth.AUTO_KEYS}
        proj = c.projection
        return remote_state.build_state(
            set_name=scene_book.active_set_name(), scenes=[(r["index"], r["name"]) for r in rows], active=active,
            vj_running=bool(c.vj["running"]), vj_remaining=c.vj_remaining_text() if c.vj["running"] else "", locked=bool(getattr(s, "scene_lock", False)),
            phase=s.status.get("phase_info"), bpm=c.detected_bpm(), voyants=voyants, masters=masters, autos=autos,
            text=params.get("text_content", ""), projection_open=bool(proj is not None and proj.running()), msg=scene_msg.get(),
            build=params.get("build", 0.0), preview_on=st["preview_on"], preview_note=st["preview_note"])

    # ---- commandes (fil HTTP -> file) puis execution (fil Tk)
    def command_in(cmd: dict):
        """Appelee par le fil HTTP : refus immediat quand la scene est verrouillee (le telephone le voit tout de suite), sinon mise en file."""
        locked = bool(getattr(s, "scene_lock", False))
        if locked and (cmd["cmd"] == "scene" or (cmd["cmd"] == "vj" and cmd["action"] in ("next", "back"))):
            return "Scene verrouillee : deverrouille d'abord (bouton Verrou)"
        requests.put(cmd)
        return None

    def release_strobe() -> None:
        if st["strobe_held"]:
            gl.strobe_release(params)
            st["strobe_held"] = False

    def execute(cmd: dict) -> None:
        name = cmd["cmd"]
        if name == "scene":
            rows = scene_book.rows()
            if 0 <= cmd["index"] < len(rows):
                c.apply_entry(cmd["index"])
            else:
                scene_msg.set(f"Telecommande : pas de scene au rang {cmd['index'] + 1}")
        elif name == "vj":
            {"toggle": c.vj_toggle, "next": c.vj_advance, "back": c.back_scene}[cmd["action"]]()
        elif name == "lock":
            c.toggle_scene_lock()
        elif name == "strobe":
            if cmd["down"]:
                if not st["strobe_held"]:
                    gl.strobe_press(params)
                    st["strobe_held"] = True
                st["strobe_until"] = time.monotonic() + STROBE_KEEPALIVE_S
            else:
                release_strobe()
        elif name == "build":
            if cmd["action"] == "start":
                if float(params.get("build", 0.0)) <= 0.0:
                    c.start_build()
            else:
                c.do_drop()
        elif name == "master":
            params[cmd["key"]] = cmd["value"]
            c.sync_master_panel()
        elif name == "memory":
            slot = c.master_book.get(cmd["slot"] - 1)
            if slot is None:
                scene_msg.set(f"Telecommande : memoire {cmd['slot']} vide")
            else:
                params.update(slot["values"])
                params["build"] = 0.0
                c.sync_master_panel()
                scene_msg.set(f"Memoire {cmd['slot']} '{slot['name']}' rappelee (telecommande)")
        elif name == "one":
            c.do_one()
        elif name == "text":
            params["text_content"] = cmd["text"]
            params["logo_source"] = "text"
            c.sync_widgets()
            scene_msg.set("Texte du logo change (telecommande)")
        elif name == "auto":
            params[cmd["key"]] = 1.0 if cmd["value"] else 0.0
            c.sync_master_panel()
        elif name == "preview":
            st["preview_on"] = cmd["on"]
            st["preview_at"] = 0.0
            st["preview_note"] = ""
            if not cmd["on"]:
                drop_preview_files()

    def drop_preview_files() -> None:
        for p in (preview_final, preview_new):
            try:
                p.unlink()
            except OSError:
                pass

    def preview_bytes():
        if not st["preview_on"]:
            return None
        try:
            data = preview_final.read_bytes()
        except OSError:
            return None
        return data if data[-2:] == b"\xff\xd9" else None

    def preview_step(now: float) -> None:
        """Une capture toutes les 5 s au plus, jamais si la projection est fermee ni si le GPU est charge ; la capture est faite par le fil GL."""
        if not st["preview_on"]:
            return
        try:
            if preview_new.exists():
                os.replace(preview_new, preview_final)                   # le fil GL a fini d'ecrire : publication atomique
        except OSError:
            pass
        proj = c.projection
        if proj is None or not proj.running():
            st["preview_note"] = "projection fermee : apercu en pause"
            return
        gpu, budget = s.status.get("gpu_ms"), s.status.get("gpu_budget_ms")
        if gpu and budget and float(gpu) > GPU_LIMIT * float(budget):
            st["preview_note"] = "GPU charge : apercu en pause"
            return
        st["preview_note"] = ""
        if now - st["preview_at"] >= PREVIEW_EVERY_S and not preview_new.exists():
            st["preview_at"] = now
            s.preview_requests.put((str(preview_new), PREVIEW_WIDTH))

    # ---- boucle (fil Tk, 100 ms)
    def poll() -> None:
        now = time.monotonic()
        if st["strobe_held"] and (now > st["strobe_until"] or not running()):
            release_strobe()                                              # le telephone a disparu : plus d'eclair bloque
        if not running():
            return
        for _ in range(50):
            try:
                cmd = requests.get_nowait()
            except queue.Empty:
                break
            try:
                execute(cmd)
            except Exception as exc:
                sessionlog.error(f"Telecommande : commande {cmd.get('cmd')} en erreur", exc)
        for line in st["access"].pop_events():
            sessionlog.event("Telecommande : " + line)
        preview_step(now)
        if now - st["published"] >= PUBLISH_EVERY_S:
            st["published"] = now
            try:
                st["snapshot"] = snapshot()
            except Exception as exc:
                sessionlog.error("Telecommande : etat non publie", exc)
        refresh_label()

    # ---- demarrage / arret
    def start() -> bool:
        host = os.environ.get("CASUAL_REMOTE_HOST") or rem.pick_private_ip()
        if not host:
            st["msg"] = "aucun reseau local trouve (connecte-toi au Wi-Fi ou a un cable)"
            return False
        try:
            port = int(os.environ.get("CASUAL_REMOTE_PORT", rem.DEFAULT_PORT))
        except ValueError:
            port = rem.DEFAULT_PORT
        st["access"].activate()
        server = rem.RemoteServer(st["access"], lambda: st["snapshot"], command_in, PAGE_DIR, host, port, allowed_hosts={host},
                                  preview_fn=preview_bytes, log=sessionlog.event)
        ok, message = server.start()
        if not ok:
            st["access"].deactivate()
            st["msg"] = message
            return False
        st["server"] = server
        st["snapshot"] = snapshot()
        st["msg"] = ""
        sessionlog.event(f"Telecommande activee : {server.url}")
        return True

    def stop() -> None:
        release_strobe()
        server, st["server"] = st["server"], None
        if server is not None:
            server.stop()
            sessionlog.event("Telecommande coupee")
        st["access"].deactivate()
        st["preview_on"] = False
        drop_preview_files()
        st["msg"] = ""

    def on_toggle() -> None:
        want = bool(var.get())
        if want and not running():
            if not start():
                var.set(0)
                scene_msg.set("Telecommande : " + st["msg"])
        elif not want:
            stop()
        params["remote_on"] = 1.0 if running() else 0.0
        refresh_label(force=True)

    # ---- interface : case, etat, fenetre du QR
    var = tk.IntVar(value=0)
    check = tk.Checkbutton(box, text="Telecommande telephone", variable=var, command=on_toggle)
    check.pack(side="left")
    qr_btn = tk.Button(box, text="QR / code...", command=lambda: open_dialog())
    qr_btn.pack(side="left", padx=(8, 0))
    label = tk.Label(box, text="", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left")
    label.pack(side="left", padx=(8, 0))
    Tooltip(check, "Pilote les scenes, le VJ, le strobe, la montee / drop et les masters depuis un telephone, sur le Wi-Fi local. Desactivee a chaque lancement. "
                   "Le lien contient un jeton secret : scanne le QR depuis la fenetre « QR / code... ». Reseau local seulement, trafic non chiffre.")
    Tooltip(qr_btn, "Affiche le QR code (a scanner avec le telephone), l'adresse et le code de secours a 6 chiffres. Un seul telephone a la fois.")

    def status_line() -> str:
        if not running():
            return st["msg"]
        acc = st["access"]
        if acc.connected():
            return f"telephone connecte ({acc.peer})"
        return f"en attente d'un telephone ({st['server'].host}:{st['server'].port})"

    def refresh_label(force: bool = False) -> None:
        text = status_line()
        if text == st["last_label"] and not force:
            return
        st["last_label"] = text
        try:
            label.configure(text=text)
            qr_btn.configure(state="normal" if running() else "disabled")
            if not running() and var.get():
                var.set(0)
        except tk.TclError:
            pass

    qr_btn.configure(state="disabled")

    def dialog_alive() -> bool:
        win = st.get("dialog")
        try:
            return win is not None and bool(win["win"].winfo_exists())
        except tk.TclError:
            return False

    def open_dialog() -> None:
        if not running():
            scene_msg.set("Telecommande : coche d'abord « Telecommande telephone »")
            return
        if dialog_alive():
            st["dialog"]["win"].lift()
            return
        server, acc = st["server"], st["access"]
        win = tk.Toplevel(root)
        win.title("Telecommande telephone")
        win.configure(bg=a2w.GUI_BG)
        win.resizable(False, False)
        url = f"http://{server.host}:{server.port}/#k={acc.token}"
        matrix = qrcode.encode(url)
        scale, quiet = 6, 4
        n = len(matrix)
        side = (n + 2 * quiet) * scale
        canvas = tk.Canvas(win, width=side, height=side, bg="#ffffff", highlightthickness=0)
        canvas.pack(padx=18, pady=(18, 8))
        for y in range(n):
            for x in range(n):
                if matrix[y][x]:
                    canvas.create_rectangle((x + quiet) * scale, (y + quiet) * scale, (x + quiet + 1) * scale, (y + quiet + 1) * scale, fill="#000000", width=0)
        tk.Label(win, text="Scanne avec l'appareil photo du telephone", bg=a2w.GUI_BG, fg=a2w.GUI_FG, font=("Segoe UI", 11, "bold")).pack()
        tk.Label(win, text=f"ou ouvre http://{server.host}:{server.port}/ et tape le code :", bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG).pack(pady=(6, 0))
        tk.Label(win, text=" ".join([acc.code[:3], acc.code[3:]]), bg=a2w.GUI_BG, fg=SCENE_COLOR, font=("Consolas", 26, "bold")).pack()
        state_lbl = tk.Label(win, text="", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        state_lbl.pack(pady=(8, 0))
        tk.Label(win, text="Reseau local seulement. Trafic non chiffre : ne partage pas ce QR.\nUn seul telephone a la fois : une nouvelle connexion remplace l'ancienne.",
                 bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG, justify="center", wraplength=side + 20).pack(pady=(6, 8))
        buttons = tk.Frame(win, bg=a2w.GUI_BG)
        buttons.pack(pady=(0, 14))
        tk.Button(buttons, text="Deconnecter le telephone", command=lambda: (acc.disconnect(), release_strobe())).pack(side="left", padx=4)
        tk.Button(buttons, text="Fermer", command=win.destroy).pack(side="left", padx=4)
        st["dialog"] = {"win": win, "state": state_lbl}

        def tick() -> None:
            if not dialog_alive() or not running():
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                return
            text = status_line()
            if state_lbl.cget("text") != text:
                state_lbl.configure(text=text)
            win.after(500, tick)
        tick()
        skin = getattr(c, "skin", None)
        if skin is not None:
            skin.apply(win)

    root.bind("<Destroy>", lambda e: stop() if e.widget is root else None, add="+")
    c.ui_every("remote", POLL_MS, poll)
    import types
    return types.SimpleNamespace(open=open_dialog, start=start, stop=stop, running=running, state=st, execute=execute, snapshot=snapshot, poll=poll,
                                 toggle=on_toggle, var=var, command_in=command_in, access=st["access"])
