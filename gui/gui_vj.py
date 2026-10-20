"""Espace de controle global du Mode VJ : panneau a onglets (masters, strobe, audio, composition, auto, memoires, historique, affichage), historique des scenes, montee / drop, garde-fous (panique, tap tempo, aide, console, auto-sauvegarde).

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import scenes
from core import sessionlog
from tkinter import ttk
import audio2wave_gl as gl
import json
import icons
import skin
import time
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import BANNER_FG, SCENE_COLOR, SHORTCUTS_HELP          # import tardif : gl_gui importe ce module
    Tooltip = c.Tooltip
    a2w = c.a2w
    after = c.after
    ui_every = c.ui_every
    ui_remove = c.ui_remove
    apply_scene_dict = c.apply_scene_dict
    history_ui = c.history_ui
    master_book = c.master_book
    params = c.params
    projection = c.projection
    refresh_scene_bar = c.refresh_scene_bar
    rhythm_source = c.rhythm_source
    root = c.root
    s = c.s
    scene_book = c.scene_book
    scene_locked = c.scene_locked
    scene_msg = c.scene_msg
    scene_top = c.scene_top
    scene_ui = c.scene_ui
    sync_widgets = c.sync_widgets
    tab_aff = c.tab_aff
    toggle_scene_lock = c.toggle_scene_lock
    update_lock_button = c.update_lock_button
    update_vj_button = c.update_vj_button
    vj = c.vj
    vj_advance = c.vj_advance
    vj_remaining_text = c.vj_remaining_text
    vj_toggle = c.vj_toggle
    vj_ui = c.vj_ui

    build_ui: dict = {"t0": None}
    SYM_MODES = (("Aucune", 0), ("Miroir G", 1), ("Miroir H", 2), ("Quadrants", 3), ("Kaleido", 4))

    def strobe_text() -> str:
        mode = params.get("strobe_mode", "manual")
        suffix = {"kick": " kick", "light": f" 1/{int(params.get('strobe_div', 4))}"}.get(mode, "")
        return "Strobe" + suffix + (" \u25cf" if mode != "manual" and float(params.get("strobe_on", 0.0)) >= 0.5 else "")

    def build_text() -> str:
        if build_ui["t0"] is None:
            return "Montee"
        return f"Montee {round(float(params['build']) * 100)}%"

    def detected_bpm() -> float | None:
        r = rhythm_source()
        value = getattr(r, "bpm", None)
        return float(value) if value else None

    def bpm_text() -> str:
        """BPM detecte (kicks) s'il est fiable, sinon celui du tap tempo."""
        det = detected_bpm()
        if det:
            return f"{det:.0f} BPM auto"
        return f"{tap_state['bpm']:.0f} BPM (tap)" if tap_state["bpm"] else "\u2014 BPM"

    def update_master_panel() -> None:
        """Libelles des boutons du panneau de controle (les touches S / K peuvent les avoir changes) et voyant du mode silence."""
        sb = vj_ui.get("strobe")
        if sb is not None:
            try:
                if sb.cget("text") != strobe_text():
                    sb.configure(text=strobe_text())
            except tk.TclError:
                vj_ui.pop("strobe", None)
        update_lock_button()
        bpm = vj_ui.get("bpm")
        if bpm is not None:
            try:
                bpm.configure(text=bpm_text())
            except tk.TclError:
                vj_ui.pop("bpm", None)
        try:
            text = "casual-overlay GL - " + status_text()          # l'etat du spectacle lisible jusque dans la barre des taches
            if root.title() != text:
                root.title(text)
        except tk.TclError:
            pass
        idle = vj_ui.get("idle")
        if idle is not None:
            try:
                idle.configure(text=f"idle {round(float(params['idle']) * 100)} %")
            except tk.TclError:
                vj_ui.pop("idle", None)

    def sync_master_panel() -> None:
        """Curseurs et boutons radio relus depuis `params` (apres le rappel d'une memoire de masters)."""
        for key, var, factor, dec, text, unit in vj_ui.get("knobs", []):
            try:
                var.set(round(float(params[key]) * factor, dec))
            except (tk.TclError, KeyError, ValueError):
                pass
        for key, var in vj_ui.get("vars", {}).items():
            try:
                var.set(params[key] if isinstance(params[key], str) else int(round(float(params[key]))))
            except (tk.TclError, KeyError, ValueError):
                pass
        if vj_ui.get("div_sync"):
            vj_ui["div_sync"]()
        update_master_panel()

    # ---- historique des scenes jouees et retour a la precedente
    def record_history(scene: dict, media, index, a2w, bgv) -> None:
        if history_ui["replay"]:
            return
        hist = s.__dict__.setdefault("scene_history", [])
        hist.append({"name": scene.get("name", "?"), "scene": json.loads(json.dumps(scene)), "media": media, "index": index,
                     "a2w": a2w, "bgv": bgv})
        del hist[:-50]
        refresh_history()

    def refresh_history() -> None:
        box = history_ui.get("list")
        if box is None:
            return
        try:
            box.delete(0, "end")
            for i, entry in enumerate(reversed(getattr(s, "scene_history", [])[-12:])):
                box.insert("end", ("\u25b6 " if i == 0 else "   ") + entry["name"])
        except tk.TclError:
            history_ui["list"] = None

    def replay_entry(entry: dict, flash: bool = True) -> None:
        paused = vj["running"]
        vj["running"] = False                              # comme un choix a la main : le VJ se met en pause
        if flash:
            s.commands.put("flash")
        history_ui["replay"] = True
        try:
            apply_scene_dict(entry["scene"], entry["media"], entry["index"], entry["a2w"], entry["bgv"])
        finally:
            history_ui["replay"] = False
        if paused:
            scene_msg.set(scene_msg.get() + "  (VJ en pause)")
        update_vj_button()

    def back_scene() -> None:
        """Bouton Retour / Retour arriere dans la fenetre de rendu : rejoue la scene d'avant (media, videos et fond propres a l'entree compris)."""
        if scene_locked():
            return
        hist = getattr(s, "scene_history", [])
        if len(hist) < 2:
            scene_msg.set("Pas de scene precedente")
            return
        hist.pop()                                         # la scene courante
        replay_entry(hist[-1])
        refresh_history()

    def history_pick(_evt=None) -> None:
        box = history_ui.get("list")
        if box is None or scene_locked():
            return
        sel = box.curselection()
        hist = getattr(s, "scene_history", [])[-12:]
        if sel and sel[0] < len(hist):
            entry = hist[::-1][sel[0]]
            replay_entry(entry)
            record_history(entry["scene"], entry["media"], entry["index"], entry["a2w"], entry["bgv"])

    # ---- montee / drop
    def build_tick() -> None:
        if build_ui["t0"] is None:
            return
        total = max(float(params.get("build_time", 45.0)), 1.0)
        params["build"] = min((time.monotonic() - build_ui["t0"]) / total, 1.0)
        btn = vj_ui.get("build")
        if btn is not None:
            try:
                btn.configure(text=build_text())
            except tk.TclError:
                vj_ui.pop("build", None)
        after(50, build_tick)

    def start_build() -> None:
        if build_ui["t0"] is not None:                    # deuxieme appui = drop
            do_drop()
            return
        build_ui["t0"] = time.monotonic()
        params["build"] = 0.0
        scene_msg.set(f"Montee sur {float(params.get('build_time', 45.0)):.0f} s : reactivite, strobe et teinte montent puis restent au maximum jusqu'au drop (bouton Drop)")
        build_tick()

    def do_drop() -> None:
        """Relache d'un coup : flash blanc et tout retombe aux reglages normaux."""
        was = build_ui["t0"] is not None
        build_ui["t0"] = None
        params["build"] = 0.0
        if was:
            s.commands.put("flash")
            params["strobe_hits"] = int(params.get("strobe_hits", 0)) + 1
        btn = vj_ui.get("build")
        if btn is not None:
            try:
                btn.configure(text=build_text())
            except tk.TclError:
                vj_ui.pop("build", None)
        scene_msg.set("DROP" if was else "Pas de montee en cours")

    def master_box() -> tk.Frame:
        """L'espace de controle global est construit une seule fois (parent persistant : la barre) puis replace a droite de chaque
        nouvelle liste ; le reconstruire a chaque rafraichissement du panneau gelait la fenetre pendant plus d'une seconde."""
        box = vj_ui.get("master_box")
        try:
            if box is not None and box.winfo_exists():
                return box
        except tk.TclError:
            pass
        box = vj_ui["master_box"] = build_master_panel(scene_top)
        skin.apply(box)
        return box

    def build_master_panel(parent) -> tk.Frame:
        """Espace de controle global (a droite de la liste du panneau etendu), en onglets : masters, live (strobe, verrou, montee), audio
        (sensibilite du kick, mode silence), composition (symetries, pixelisation, trainee, deformation), auto (cycle de teinte,
        respiration), memoires de masters et historique des scenes. Reglages de salle, relus a chaque image par le rendu : jamais captures
        par les scenes ni les presets."""
        box = tk.Frame(parent, bg=a2w.GUI_PANEL_BG)
        nb = ttk.Notebook(box)
        nb.pack(fill="both", expand=True, padx=2, pady=(2, 0))
        knobs: list = []
        vars_: dict = {}
        vj_ui["knobs"], vj_ui["vars"] = knobs, vars_

        pending: dict = {}                                  # onglet -> fonction qui le remplit (a la premiere selection : le panneau est cher a habiller)

        def new_tab(name: str, fill) -> None:
            f = tk.Frame(nb, bg=a2w.GUI_PANEL_BG)
            f.columnconfigure(1, weight=1)
            f.columnconfigure(4, weight=1)
            nb.add(f, text=name)
            pending[str(f)] = fill

        def build_current(_evt=None) -> None:
            try:
                f = nb.nametowidget(nb.select())
            except (tk.TclError, KeyError):
                return
            fill = pending.pop(str(f), None)
            if fill is not None:
                fill(f)
                skin.apply(f)
            setattr(s, "master_tab", nb.index("current"))

        def slider(f, i, key, label, lo, hi, step, factor, unit, default, tip, dec=0, var_key=None) -> None:
            row, col = i if isinstance(i, tuple) else (i % 3, 3 * (i // 3))
            var = tk.DoubleVar(value=round(float(params.get(key, default / factor)) * factor, dec))
            shown = tk.StringVar(value=f"{var.get():.{dec}f}{unit}")

            def moved(v, k=key, fac=factor, u=unit, text=shown, d=dec) -> None:
                params[k] = float(v) / fac
                text.set(f"{float(v):.{d}f}{u}")

            name = tk.Label(f, text=label, width=9, anchor="w", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
            name.grid(row=row, column=col, sticky="w", padx=(6, 2))
            scale = tk.Scale(f, from_=lo, to=hi, resolution=step, orient="horizontal", showvalue=False, variable=var, length=96,
                             width=10, sliderlength=14, bd=0, highlightthickness=0, bg=a2w.GUI_PANEL_BG, command=moved)
            scale.grid(row=row, column=col + 1, sticky="ew")
            tk.Label(f, textvariable=shown, width=7, anchor="e", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).grid(
                row=row, column=col + 2, sticky="e", padx=(2, 6))
            name.bind("<Double-Button-1>", lambda e, v=var, d=default: v.set(d))
            Tooltip(name, tip + " Double-clic sur le nom : valeur d'origine.")
            Tooltip(scale, tip)
            knobs.append((key, var, factor, dec, shown, unit))

        def toggle(f, key, text, tip, **grid) -> tk.Checkbutton:
            """Case a cocher liee a un reglage 0 / 1 (relue par `sync_master_panel` : la panique la decoche)."""
            var = tk.IntVar(value=int(round(float(params.get(key, 0.0)))))
            vars_[key] = var
            cb = tk.Checkbutton(f, text=text, variable=var, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, selectcolor=a2w.GUI_BG, bd=0,
                                activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG,
                                command=lambda: params.__setitem__(key, float(var.get())))
            cb.grid(**grid)
            Tooltip(cb, tip)
            return cb

        def button(f, text, command=None, **grid) -> tk.Button:
            b = tk.Button(f, text=text, command=command, relief="flat", bd=0, padx=8, pady=2, cursor="hand2", bg=a2w.GUI_BG,
                          fg=a2w.GUI_FG, activebackground=SCENE_COLOR, activeforeground=BANNER_FG, font=("Segoe UI", 9, "bold"))
            b.grid(**grid)
            return b

        def radios(f, key, choices, row, col=0, span=6, tip="", on_change=None) -> None:
            holder = tk.Frame(f, bg=a2w.GUI_PANEL_BG)
            holder.grid(row=row, column=col, columnspan=span, sticky="w", padx=6, pady=(2, 0))
            cur = params.get(key, 0)
            var = tk.StringVar(value=cur) if isinstance(cur, str) else tk.IntVar(value=int(round(float(cur))))
            vars_[key] = var
            for text, value in choices:
                rb = tk.Radiobutton(holder, text=text, variable=var, value=value, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG,
                                    selectcolor=a2w.GUI_BG, activebackground=a2w.GUI_PANEL_BG,
                                    command=lambda k=key, v=var: (params.__setitem__(k, v.get() if isinstance(v.get(), str) else float(v.get())),
                                                                  on_change() if on_change else None))
                rb.pack(side="left", padx=2)
                if tip:
                    Tooltip(rb, tip)

        # ---- Masters
        def fill_0(f) -> None:
            for i, args in enumerate((
                    ("react_master", "Reac", 0, 200, 5, 100.0, "%", 100, "Master de l'audioreactivite : multiplie tout ce qui vient du son (basses, mediums, aigus, kick) "
                                                                        "pour tous les effets. 0 % = le visuel ne reagit plus a l'audio, 100 % = normal, 200 % = double."),
                    ("tempo", "Tempo", 0, 240, 1, 120.0, " BPM", 120, "Tempo des animations (halo, fonte, cellules, fond anime, noise) et des courbes d'automation, en BPM : "
                                                                   "120 BPM = vitesse normale, 60 BPM = deux fois plus lent. Le tap tempo et « Tempo auto » (BPM detecte) "
                                                                   "le posent pour toi. La video de fond et le logo anime gardent leur cadence."),
                    ("overlay_master", "Overlay", 0, 100, 5, 100.0, "%", 100, "Presence de l'overlay (logo, texte, contour, halo) : 100 % = normal, 0 % = invisible, "
                                                                              "les effets du fond continuent."),
                    ("bg_master", "Fond", 0, 100, 5, 100.0, "%", 100, "Luminosite du fond (video, spectre ou motif) : 100 % = normal, 0 % = fond noir, "
                                                                      "il ne reste que le logo."),
                    ("hue_master", "Teinte", 0, 360, 5, 1.0, "\u00b0", 0, "Teinte globale : fait tourner les couleurs de toute l'image (fond et logo). "
                                                                          "0 = couleurs d'origine, 180 = couleurs opposees."),
                    ("fx_master", "Effets", 0, 200, 5, 100.0, "%", 100, "Master des effets : multiplie l'intensite de TOUS les effets (deformations, "
                                                                        "halo, fonte, cellules, noise). 0 % = plus aucun effet, 100 % = normal."))):
                slider(f, i, *args)
            tap = button(f, "Tap tempo", do_tap, row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            Tooltip(tap, "Tape le rythme (au moins deux appuis, touche point dans la fenetre de rendu) : le master du tempo suit le BPM "
                         "(120 BPM = 100 %). Une pause de 2 s recommence.")
            bpm = vj_ui["bpm"] = tk.Label(f, text="\u2014 BPM", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG)
            bpm.grid(row=3, column=3, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            toggle(f, "bpm_auto", "Tempo auto", "Le master du tempo suit le BPM detecte a partir des kicks (120 BPM = 100 %). "
                                                 "Il faut des kicks reguliers : sans BPM fiable, le tempo ne bouge pas.",
                   row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(3, 0))
            toggle(f, "drop_auto", "Montee / drop auto", "Une cassure detectee (les basses disparaissent un moment) lance la montee ; le retour des basses "
                                                         "= le drop : flash blanc et retour aux reglages normaux. Le VJ peut aussi changer de scene au "
                                                         "drop (menu « reglages VJ »).",
                   row=4, column=3, columnspan=3, sticky="w", padx=6, pady=(3, 0))
            toggle(f, "gpu_cap", "Securite GPU", "Quand le VJ enchaine une scene, ses effets couteux (cellules, halo, fonte) sont coupes si, ajoutes a la charge mesuree "
                                                  "du GPU, ils ne tiendraient plus dans le budget d'une image. Une scene choisie a la main n'est jamais touchee.",
                   row=5, column=0, columnspan=3, sticky="w", padx=6, pady=(3, 0))

            lock = button(f, "Verrou", toggle_scene_lock, row=5, column=3, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            vj_ui["lock"] = lock
            update_lock_button()
            Tooltip(lock, "Verrou de scene : gele la scene courante. Le VJ se met en pause et ni les touches de scene, F1..F9, la fleche "
                          "droite ni les tirages ne la changent ; les masters restent actifs. Touche K dans la fenetre de rendu.")

        new_tab("Masters", fill_0)

        # ---- Live : strobe, verrou, montee / drop
        def fill_1(f) -> None:
            sb = button(f, strobe_text(), row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            vj_ui["strobe"] = sb
            sb.bind("<ButtonPress-1>", lambda e: (gl.strobe_press(params), sb.configure(text=strobe_text())), add="+")
            sb.bind("<ButtonRelease-1>", lambda e: (gl.strobe_release(params), sb.configure(text=strobe_text())), add="+")
            Tooltip(sb, "Strobe : eclair blanc court a la demande. Touche S dans la fenetre de rendu. "
                        "Au kick / en leger, un clic arme, un autre desarme.")

            def set_strobe(**kw) -> None:
                params.update(kw)
                params["strobe_on"] = 0.0                              # un changement de mode desarme
                sb.configure(text=strobe_text())

            radios(f, "strobe_mode", (("Manuel", "manual"), ("Kick", "kick"), ("Leger", "light")), row=1, span=4,
                   tip="Manuel : un eclair par appui. Kick / Leger : un clic arme, un autre desarme. "
                       "Leger : un seul kick sur N (reglage « 1 kick sur », 4 par defaut = un eclair par mesure).",
                   on_change=lambda: set_strobe())
            slider(f, (3, 0), "strobe_max_hz", "Limite", 1, 20, 1, 1.0, " Hz", 10, "Limiteur de securite : jamais plus de ce nombre d'eclairs par seconde, "
                                                                                    "quelle que soit la cadence demandee (photosensibilite du public).")
            slider(f, (3, 3), "strobe_max_s", "Duree max", 1, 60, 1, 1.0, " s", 8, "Limiteur de securite : une rafale continue de strobe est coupee apres cette duree, "
                                                                                  "puis pause forcee de 3 s.")
            slider(f, (4, 0), "strobe_dark", "Sombre", 5, 100, 5, 100.0, "%", 100, "Le strobe ne blanchit que les parties SOMBRES de l'image : seuil de luminosite, "
                                                                                    "plus il est bas, plus le strobe est limite aux zones les plus noires. 100 % = toute l'image.")
            div_box = tk.Frame(f, bg=a2w.GUI_PANEL_BG)
            div_box.grid(row=4, column=3, columnspan=3, sticky="w", padx=6)
            tk.Label(div_box, text="1 kick sur", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG).grid(row=0, column=0, padx=(0, 4))
            div_shown = tk.StringVar()

            def div_sync() -> None:
                div_shown.set(str(int(params.get("strobe_div", 4))))

            def div_step(more: bool) -> None:
                v = int(params.get("strobe_div", 4))
                params["strobe_div"] = min(16, v * 2) if more else max(2, v // 2)
                div_sync()
                sb.configure(text=strobe_text())

            div_less = button(div_box, "\u25c0", lambda: div_step(False), row=0, column=1)
            tk.Label(div_box, textvariable=div_shown, width=3, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG).grid(row=0, column=2)
            div_more = button(div_box, "\u25b6", lambda: div_step(True), row=0, column=3)
            Tooltip(div_less, "Strobe leger : un eclair tous les N kicks. Divise N par deux (plus d'eclairs). N vaut 2, 4, 8 ou 16.")
            Tooltip(div_more, "Strobe leger : un eclair tous les N kicks. Double N (moins d'eclairs). N vaut 2, 4, 8 ou 16.")
            vj_ui["div_sync"] = div_sync
            div_sync()
            slider(f, (2, 3), "build_time", "Duree", 2, 60, 1, 1.0, " s", 45, "Duree de la montee, en secondes : reactivite, strobe (de 0,5 a 16 Hz, lent au depart) et teinte montent sur cette duree, puis restent au maximum jusqu'au drop (bouton Drop, 2e appui sur Montee, ou drop detecte en mode Montee / drop auto).")
            bb = button(f, build_text(), start_build, row=1, column=3, sticky="w", padx=6)
            vj_ui["build"] = bb
            Tooltip(bb, "Montee : en quelques secondes, la reactivite, le strobe (de 0,5 a 16 Hz, lent au depart) et la teinte (jusqu'a +120 degres) montent progressivement. "
                        "Un deuxieme appui (ou Drop) relache d'un coup : flash blanc et retour aux reglages normaux.")
            drop = button(f, "Drop", do_drop, row=1, column=4, sticky="w")
            Tooltip(drop, "Relache la montee d'un coup : flash blanc, tout retombe aux reglages normaux.")

        new_tab("Live", fill_1)

        # ---- Audio : sensibilite du kick (voyant) et mode silence
        def fill_2(f) -> None:
            slider(f, 0, "sensitivity", "Kick", 0.25, 4.0, 0.05, 1.0, "", 1.0,
                   "Sensibilite du detecteur de kick (comme les fleches haut / bas dans la fenetre de rendu) : plus haut = detecte des kicks plus faibles.", dec=2)
            led = vj_ui["led"] = tk.Label(f, text=" \u25cf kick ", bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG, font=("Segoe UI", 9, "bold"))
            led.grid(row=0, column=3, columnspan=2, sticky="w", padx=6)
            Tooltip(led, "Voyant : s'allume a chaque kick detecte.")
            sil = tk.IntVar(value=int(float(params["silence_on"]) >= 0.5))
            cb = tk.Checkbutton(f, text="Mode silence", variable=sil, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, selectcolor=a2w.GUI_BG,
                                activebackground=a2w.GUI_PANEL_BG, command=lambda: params.__setitem__("silence_on", float(sil.get())))
            cb.grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
            vars_["silence_on"] = sil
            Tooltip(cb, "Quand le son tombe sous le seuil pendant le delai, le visuel passe en idle : tempo reduit et overlay efface (fond seul). "
                        "Il revient des que le son revient.")
            idle = tk.Label(f, text="idle 0 %", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG)
            idle.grid(row=1, column=3, columnspan=2, sticky="w", padx=6)
            vj_ui["idle"] = idle
            slider(f, (2, 0), "silence_level", "Seuil", 0, 20, 0.5, 100.0, " %", 3, "Niveau sonore (0 a 20 %) sous lequel on parle de silence.", dec=1)
            slider(f, (2, 3), "silence_delay", "Delai", 1, 30, 1, 1.0, " s", 4, "Secondes de silence avant de passer en idle.")
            slider(f, (3, 0), "silence_tempo", "Tempo idle", 0, 100, 5, 100.0, "%", 30, "Tempo relatif en idle (100 % = inchange).")

        new_tab("Audio", fill_2)

        # ---- Composition : symetries et deformations
        def fill_3(f) -> None:
            radios(f, "comp_sym", SYM_MODES, row=0, span=6, tip="Symetrie appliquee a toute l'image : miroir gauche / haut, quatre quadrants, kaleidoscope.")
            slider(f, (1, 0), "comp_seg", "Segments", 2, 16, 1, 1.0, "", 6, "Nombre de segments du kaleidoscope.")
            slider(f, (2, 0), "comp_rot", "Rotation", -90, 90, 1, 1.0, "\u00b0/s", 0, "Rotation du kaleidoscope, en degres par seconde.")
            slider(f, (1, 3), "comp_pix", "Pixel", 0, 100, 1, 100.0, "%", 0, "Pixelisation de toute l'image (0 = aucune).")
            slider(f, (2, 3), "comp_trail", "Trainee", 0, 97, 1, 100.0, "%", 0, "Trainee : l'image precedente s'estompe lentement (feedback). 0 = aucune.")
            slider(f, (3, 0), "comp_warp", "Deform", 0, 100, 1, 100.0, "%", 0, "Deformation globale : ondes lentes sur tout le cadre.")

        new_tab("Compo", fill_3)

        # ---- Auto : cycle de teinte, respiration
        def fill_4(f) -> None:
            slider(f, 0, "hue_cycle", "Cycle teinte", -90, 90, 1, 1.0, "\u00b0/s", 0, "La teinte tourne toute seule, en degres par seconde (0 = fixe, negatif = sens inverse).")
            slider(f, 1, "breath_overlay", "Resp. overlay", 0, 100, 1, 100.0, "%", 0, "Respiration de l'overlay : sa presence oscille doucement (0 = aucune).")
            slider(f, 2, "breath_bg", "Resp. fond", 0, 100, 1, 100.0, "%", 0, "Respiration du fond, en opposition de phase avec l'overlay (0 = aucune).")
            slider(f, 3, "breath_period", "Periode", 2, 30, 1, 1.0, " s", 8, "Periode de la respiration, en secondes.")

        new_tab("Auto", fill_4)

        # ---- Memoires de masters
        def fill_5(f) -> None:
            armed = {"on": False}
            slot_btns: list = []

            def slot_label(i: int) -> str:
                slot = master_book.get(i)
                return f"{i + 1}  {slot['name'] if len(slot['name']) <= 12 else slot['name'][:11] + '.'}" if slot else f"{i + 1}  \u2014"

            def save_slot(i: int) -> None:
                slot = master_book.get(i)
                name = scene_ui["ask_name"]("Memoire de masters", "Nom de la memoire :", slot["name"] if slot else f"Memoire {i + 1}")
                if name is None:
                    return
                master_book.save(i, name, params)
                slot_btns[i].configure(text=slot_label(i))
                scene_msg.set(f"Masters enregistres dans la memoire {i + 1}")

            def slot_click(i: int) -> None:
                if armed["on"]:
                    armed["on"] = False
                    arm.configure(text="Sauver")
                    save_slot(i)
                    return
                slot = master_book.get(i)
                if slot is None:
                    scene_msg.set(f"Memoire {i + 1} vide : clique Sauver puis une memoire (ou clic droit)")
                    return
                params.update(slot["values"])
                for key in ("idle", "build"):
                    params[key] = 0.0
                sync_master_panel()
                scene_msg.set(f"Memoire {i + 1} '{slot['name']}' rappelee")

            for i in range(scenes.MASTER_SLOTS):
                b = button(f, slot_label(i), lambda k=i: slot_click(k), row=0 if i < 2 else 1, column=0 if i % 2 == 0 else 3, columnspan=3,
                           sticky="ew", padx=6, pady=(3, 0))
                b.bind("<Button-3>", lambda e, k=i: save_slot(k), add="+")
                slot_btns.append(b)
                Tooltip(b, "Rappelle cette combinaison de masters (reglages de salle). Clic droit : enregistre les masters actuels ici.")

            def arm_toggle() -> None:
                armed["on"] = not armed["on"]
                arm.configure(text="Sauver : choisis" if armed["on"] else "Sauver")

            arm = button(f, "Sauver", arm_toggle, row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(3, 0))
            Tooltip(arm, "Clique ici puis sur une memoire pour y enregistrer les masters actuels (teinte, fond, overlay, composition, strobe...).")

        new_tab("Memoires", fill_5)

        # ---- Historique des scenes
        def fill_6(f) -> None:
            button(f, "\u25c0 Retour", back_scene, row=0, column=0, sticky="w", padx=6, pady=(3, 0))
            lb = tk.Listbox(f, height=4, activestyle="none", exportselection=False, bg=a2w.GUI_BG, fg=a2w.GUI_FG, bd=0, highlightthickness=0,
                            selectbackground=SCENE_COLOR, selectforeground=BANNER_FG)
            lb.grid(row=0, column=1, rowspan=3, columnspan=4, sticky="nsew", padx=(8, 6), pady=3)
            lb.bind("<Double-Button-1>", history_pick)
            history_ui["list"] = lb
            refresh_history()
            Tooltip(lb, "Les dernieres scenes jouees (la plus recente en haut). Double-clic : la rejouer. Retour arriere dans la fenetre de rendu = scene precedente.")

        new_tab("Historique", fill_6)

        # ---- Affichage et analyse (contenu construit une fois avec la fenetre, pose ici)
        def fill_7(f) -> None:
            tab_aff.pack(in_=f, side="top", fill="both", expand=True)
            tab_aff.lift()

        new_tab("Affichage", fill_7)

        # onglet garde d'une reconstruction a l'autre
        try:
            nb.select(int(getattr(s, "master_tab", 0)))
        except (tk.TclError, ValueError):
            pass
        build_current()
        nb.bind("<<NotebookTabChanged>>", build_current)
        vj_ui["tabs"] = (nb, build_current, pending)

        def led_tick() -> None:
            led = vj_ui.get("led")
            try:
                if led is not None:
                    led.configure(fg=SCENE_COLOR if float(s.audio.analyzer.latest().get("beat", 0.0)) > 0.4 else a2w.GUI_MUTED_FG)
            except tk.TclError:
                vj_ui.pop("led", None)
            except Exception:
                pass
            if not box.winfo_exists():
                ui_remove("led")

        ui_every("led", 60, led_tick)
        return box

    # ---- garde-fous d'exploitation : panique, tap tempo, aide, console, auto-sauvegarde
    TAP_REF_BPM = 120.0                                    # tempo 100 % = 120 BPM
    tap_state: dict = {"times": [], "bpm": None}

    def do_panic(from_gl: bool = False) -> None:
        """PANIQUE : coupe effets, strobe, montee, composition, teinte, mode silence ; masters remis a 100 % ; le VJ se met en pause et le
        verrou saute (la main est rendue). Les medias, le fond et le logo ne sont pas touches."""
        changed = gl.panic(params)
        build_ui["t0"] = None
        vj["running"] = False
        s.lock_resume = False
        was_locked = bool(getattr(s, "scene_lock", False))
        s.scene_lock = False
        if not from_gl:                                    # depuis la touche X, le fil GL l'a deja journalise et a fait son flash
            sessionlog.event("PANIQUE : " + (", ".join(changed) if changed else "rien a couper"))
            s.commands.put("flash")
        try:
            sync_widgets()
        except Exception as exc:
            sessionlog.error("Panique : widgets non resynchronises", exc)
        sync_master_panel()
        update_vj_button()
        update_lock_button()
        if was_locked:
            refresh_scene_bar()
        scene_msg.set("PANIQUE : effets, strobe et montee coupes, VJ en pause" + (", verrou retire" if was_locked else ""))

    def do_tap() -> None:
        """Tap tempo (bouton ou touche point) : BPM moyen des derniers appuis -> master du tempo (120 BPM = 100 %) ; en strobe a cadence
        fixe, la cadence suit (BPM / 60 eclairs par seconde, dans les limites du strobe)."""
        times = tap_state["times"]
        times.append(time.monotonic())
        del times[:-12]
        bpm = gl.tap_bpm(times)
        if bpm is None:
            scene_msg.set("Tap tempo : un deuxieme appui donne le BPM")
            return
        bpm = min(max(bpm, 30.0), 300.0)
        tap_state["bpm"] = bpm
        params["tempo"] = min(max(bpm / TAP_REF_BPM, 0.0), 2.0)
        sync_master_panel()
        scene_msg.set(f"Tap tempo : {bpm:.0f} BPM")

    def status_text() -> str:
        """Une ligne lisible de l'etat du spectacle : scene, VJ (lecture, pause, verrou), BPM, projection."""
        scene = getattr(s, "active_scene", None) or "aucune scene"
        if getattr(s, "scene_lock", False):
            vj_txt = "VERROUILLE"
        elif vj["running"]:
            vj_txt = f"VJ en marche (suivante : {vj_remaining_text()})"
        else:
            vj_txt = "VJ en pause"
        parts = [scene, vj_txt]
        if detected_bpm() or tap_state["bpm"]:
            parts.append(bpm_text())
        if projection is not None and not projection.running():
            parts.append("projection fermee")
        return "  |  ".join(parts)

    def help_window() -> None:
        """Fenetre d'aide des raccourcis (touche ? ou /, menu « reglages VJ »)."""
        old = vj_ui.get("help")
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        win = vj_ui["help"] = tk.Toplevel(root)
        win.title("Raccourcis")
        win.configure(bg=a2w.GUI_PANEL_BG)
        tk.Label(win, text=SHORTCUTS_HELP, justify="left", anchor="nw", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG,
                 font=("Consolas", 10)).pack(padx=14, pady=(12, 6))
        tk.Button(win, text="Fermer", command=win.destroy, padx=14).pack(pady=(0, 12))
        win.bind("<Escape>", lambda e: win.destroy())
        win.transient(root)

    scene_ui["help"] = help_window

    def key_help(evt) -> None:
        if evt.widget.winfo_class() not in ("Entry", "Text", "TEntry", "Spinbox"):    # taper un ? dans un champ n'ouvre rien
            scene_ui["help"]()

    root.bind_all("<question>", key_help)

    def console_window() -> None:
        """Console VJ : une vue epuree avec les grosses commandes (retour, lecture, suivante, strobe, montee, drop, verrou, PANIQUE)
        et l'etat du spectacle, a garder sous la main pendant que le panneau etendu reste ferme."""
        old = vj_ui.get("console")
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                return
        except tk.TclError:
            pass
        win = vj_ui["console"] = tk.Toplevel(root)
        win.title("Console VJ")
        win.configure(bg=a2w.GUI_PANEL_BG)
        big = ("Segoe UI", 14, "bold")
        widgets_: dict = {}

        def btn(text, command, row, col, span=1, bg=None, fg=None, key=None, icon=None):
            b = tk.Button(win, text=text, command=command, font=big, relief="flat", bd=0, padx=10, pady=14, cursor="hand2",
                          bg=bg or a2w.GUI_BG, fg=fg or a2w.GUI_FG, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            if icon:                                  # icone a gauche du libelle (la console se lit de loin, dans le noir)
                b._console_icon = skin.photo(b, icons.image(icon, 26, fg or a2w.GUI_FG))
                b.configure(image=b._console_icon, compound="left", padx=14)
            b.grid(row=row, column=col, columnspan=span, sticky="nsew", padx=4, pady=4)
            if key:
                widgets_[key] = b
            return b

        for c in range(3):
            win.columnconfigure(c, weight=1, uniform="c")
        btn("\u25c0 Retour", back_scene, 0, 0, key="back")
        btn("\u25b6 VJ", vj_toggle, 0, 1, key="play")
        btn("Suivante \u25b6\u25b6", vj_advance, 0, 2, key="next")
        sb = btn(strobe_text(), None, 1, 0, key="strobe", icon="bolt")
        sb.bind("<ButtonPress-1>", lambda e: (gl.strobe_press(params), sb.configure(text=strobe_text())), add="+")
        sb.bind("<ButtonRelease-1>", lambda e: (gl.strobe_release(params), sb.configure(text=strobe_text())), add="+")
        btn(build_text(), start_build, 1, 1, key="build", icon="up")
        btn("Drop", do_drop, 1, 2, key="drop", icon="down")
        btn("Verrou", toggle_scene_lock, 2, 0, key="lock", icon="lock")
        btn("Tap tempo", do_tap, 2, 1, key="tap", icon="tap")
        btn("?", help_window, 2, 2, key="help")
        btn("PANIQUE", do_panic, 3, 0, span=3, bg="#d92b2b", fg="#ffffff", key="panic", icon="alert")
        status = tk.Label(win, text="", anchor="w", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, font=("Segoe UI", 11), wraplength=520, justify="left")
        status.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(6, 0))
        meter = tk.Canvas(win, height=26, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
        meter.grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(4, 8))
        widgets_["status"], widgets_["meter"] = status, meter
        vj_ui["console_widgets"] = widgets_

        def closed() -> None:
            vj_ui.pop("console", None)
            vj_ui.pop("console_widgets", None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", closed)
        win.attributes("-topmost", True)
        console_tick()

    def console_tick() -> None:
        w = vj_ui.get("console_widgets")
        if not w:
            return
        try:
            w["status"].configure(text=status_text())
            on = bool(vj["running"])
            w["play"].configure(text="\u25a0 VJ" if on else "\u25b6 VJ", bg=SCENE_COLOR if on else a2w.GUI_BG, fg=BANNER_FG if on else a2w.GUI_FG)
            locked = bool(getattr(s, "scene_lock", False))
            w["lock"].configure(text="Verrou \u25cf" if locked else "Verrou", bg=SCENE_COLOR if locked else a2w.GUI_BG,
                                fg=BANNER_FG if locked else a2w.GUI_FG)
            w["build"].configure(text=build_text())
            w["strobe"].configure(text=strobe_text())
            state = s.audio.analyzer.latest()
            canvas_ = w["meter"]
            canvas_.delete("all")
            width = max(canvas_.winfo_width(), 100)
            for i, (key, color) in enumerate((("bass", "#3b82f6"), ("mid", "#22c55e"), ("high", "#f59e0b"))):
                level = min(max(float(state.get(key, 0.0)), 0.0), 1.0)
                x0 = 4 + i * (width // 4)
                canvas_.create_rectangle(x0, 4, x0 + (width // 4 - 8) * level + 1, 22, fill=color, width=0)
            if float(state.get("beat", 0.0)) > 0.4:
                canvas_.create_oval(width - 26, 4, width - 8, 22, fill=SCENE_COLOR, width=0)
        except tk.TclError:
            vj_ui.pop("console_widgets", None)
            return
        except Exception:
            pass
        after(150, console_tick)

    scene_ui["console"] = console_window

    def autosave_tick() -> None:
        """Instantane de l'etat courant toutes les minutes : un plantage ne fait pas tout perdre (voir `recover_autosave`)."""
        try:
            gl.write_autosave(params, {"scene": getattr(s, "active_scene", None), "set": scene_book.active_set_name()})
        except Exception as exc:
            sessionlog.error("Auto-sauvegarde impossible", exc)
        after(int(gl.AUTOSAVE_EVERY_S * 1000), autosave_tick)

    after(int(gl.AUTOSAVE_EVERY_S * 1000), autosave_tick)

    # ---- rythme detecte : tempo automatique, montee / drop automatiques, changement de scene au drop
    rhythm_ui: dict = {"drops": None, "builds": None, "auto_build": False}

    def handle_drop() -> None:
        """Un drop vient d'etre detecte (retour des basses apres une cassure)."""
        auto = float(params.get("drop_auto", 0.0)) >= 0.5
        if auto and build_ui["t0"] is not None:
            do_drop()                                        # relache la montee : flash + eclair
        elif auto:
            s.commands.put("flash")
            params["strobe_hits"] = int(params.get("strobe_hits", 0)) + 1
            scene_msg.set("DROP detecte")
        else:
            scene_msg.set("Drop detecte")
        rhythm_ui["auto_build"] = False
        if vj["running"] and scene_book.vj().get("on_drop"):
            vj_advance()                                     # le changement de scene a son propre flash

    def rhythm_step() -> None:
        """Un pas (100 ms) : lit le rythme detecte par l'analyse et agit selon les reglages. N'agit que sur les EVENEMENTS survenus depuis
        le dernier pas (compteurs de drops et de montees), jamais sur ceux d'avant l'ouverture de la fenetre."""
        r = rhythm_source()
        if r is None:
            return
        if rhythm_ui["drops"] is None:
            rhythm_ui["drops"], rhythm_ui["builds"] = int(r.drops), int(r.builds)
        bpm = detected_bpm()
        if bpm and float(params.get("bpm_auto", 0.0)) >= 0.5:
            tempo = min(max(bpm / TAP_REF_BPM, 0.0), 2.0)
            if abs(tempo - float(params.get("tempo", 1.0))) > 0.01:
                params["tempo"] = tempo
                sync_master_panel()
        if int(r.builds) != rhythm_ui["builds"]:
            rhythm_ui["builds"] = int(r.builds)
            if float(params.get("drop_auto", 0.0)) >= 0.5 and build_ui["t0"] is None:
                start_build()
                rhythm_ui["auto_build"] = True
                scene_msg.set("Cassure detectee : montee automatique")
        if int(r.drops) != rhythm_ui["drops"]:
            rhythm_ui["drops"] = int(r.drops)
            handle_drop()
        elif rhythm_ui["auto_build"]:
            if build_ui["t0"] is None:
                rhythm_ui["auto_build"] = False
            elif r.state == "idle":                          # la cassure s'est terminee sans drop : la montee automatique est annulee
                build_ui["t0"] = None
                params["build"] = 0.0
                rhythm_ui["auto_build"] = False
                btn = vj_ui.get("build")
                if btn is not None:
                    try:
                        btn.configure(text=build_text())
                    except tk.TclError:
                        vj_ui.pop("build", None)

    def kick_tick() -> None:
        """Voyant de la barre : s'allume a chaque kick detecte, avec le BPM detecte (et la cassure / montee reperee)."""
        lbl = vj_ui.get("kick")
        try:
            if lbl is not None and lbl.winfo_exists():
                latest = s.audio.analyzer.latest()
                on = float(latest.get("beat", 0.0)) > 0.4
                r = rhythm_source()
                det = detected_bpm()
                text = "\u25cf " + (f"{det:.0f} BPM" if det else "-- BPM")
                state = getattr(r, "state", "idle")
                if state != "idle":
                    text += "  cassure" if state == "break" else "  MONTEE"
                if lbl.cget("text") != text:
                    lbl.configure(text=text)
                color = SCENE_COLOR if on else a2w.GUI_MUTED_FG
                if str(lbl.cget("fg")) != color:
                    lbl.configure(fg=color)
        except tk.TclError:
            vj_ui.pop("kick", None)
        except Exception:
            pass

    ui_every("kick", 50, kick_tick, first_ms=300)

    def rhythm_tick() -> None:
        try:
            rhythm_step()
        except Exception:
            import traceback
            traceback.print_exc()

    ui_every("rhythm", 100, rhythm_tick, first_ms=500)

    import types
    return types.SimpleNamespace(rhythm_step=rhythm_step, autosave_tick=autosave_tick, back_scene=back_scene, console_window=console_window, do_drop=do_drop, do_panic=do_panic, do_tap=do_tap, help_window=help_window, master_box=master_box, record_history=record_history, start_build=start_build, status_text=status_text, update_master_panel=update_master_panel)
