"""Espace de controle global du Mode VJ : panneau a onglets (masters, strobe, audio, composition, auto, memoires, historique, affichage, rec), historique des scenes, montee / drop, garde-fous (panique, tap tempo, aide, console, auto-sauvegarde).

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import explain
from core import recording as core_rec
from core import scenes
from core import sessionlog
from tkinter import colorchooser, ttk
import audio2wave_gl as gl
import json
import shutil
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
        return "Strobe" + (" auto \u25cf" if float(params.get("strobe_auto", 0.0)) >= 0.5 else "")

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

    def knob_text(key: str, value: float, dec: int, unit: str) -> str:
        """Valeur affichee a droite d'un curseur de master. Tempo auto sans BPM detecte : « -- BPM » (le curseur reste a 120, mais ce n'est pas un tempo mesure)."""
        if key == "tempo" and float(params.get("bpm_auto", 0.0)) >= 0.5 and not detected_bpm() and not tap_state["bpm"]:
            return "-- BPM"
        return f"{value:.{dec}f}{unit}"

    def sources_line() -> str:
        info = s.status.get("phase_info") or {}
        bg, logo, bg_auto, logo_auto = s.status.get("sources", ("tout", "tout", False, False))
        on = float(params.get("auto_phase", 1.0)) >= 0.5
        return explain.sources_text(bg, logo, bg_auto, logo_auto, info.get("name") if (on and info) else None) + "\n" + explain.fx_line(s.status.get("fx_now"))

    def phase_fx_line() -> str:
        info = s.status.get("phase_info") or {}
        on = float(params.get("auto_phase", 1.0)) >= 0.5
        return explain.phase_effects_text(info.get("mult") if info else None, on)

    def phase_why_line() -> str:
        info = s.status.get("phase_info") or {}
        return explain.phase_measures_text(info if (info and float(params.get("auto_phase", 1.0)) >= 0.5) else None)

    def strobe_line() -> str:
        if params.get("strobe_kind") == "foudre":
            return explain.foudre_info_text(s.status.get("foudre"))
        info = s.status.get("strobe_info")
        armed = float(params.get("strobe_auto", 0.0)) >= 0.5
        return explain.strobe_info_text(info[0] if info else None, time.monotonic() - info[1] if info else None, armed)

    def sync_kind() -> None:
        """Mode Foudre : ses reglages sont actifs et « Sombre » est grise ; mode Strobe : l'inverse (jamais retires : la mise en page ne bouge pas)."""
        foudre = params.get("strobe_kind") == "foudre"
        for key, on in (("foudre_widgets", foudre), ("strobe_widgets", not foudre)):
            for w in vj_ui.get(key, []):
                try:
                    w.configure(state="normal" if on else "disabled")
                except tk.TclError:
                    pass

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
        for key, var, factor, dec, text, unit in vj_ui.get("knobs", []):
            if key == "tempo":
                try:
                    want = knob_text(key, float(var.get()), dec, unit)
                    if text.get() != want:
                        text.set(want)
                except tk.TclError:
                    pass
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
        for key, make in (("sources", sources_line), ("phase_fx", phase_fx_line), ("phase_why", phase_why_line), ("strobe_info", strobe_line)):
            lbl = vj_ui.get(key)
            if lbl is not None:
                try:
                    text = make()
                    if lbl.cget("text") != text:
                        lbl.configure(text=text)
                except tk.TclError:
                    vj_ui.pop(key, None)
        ph = vj_ui.get("phase")
        if ph is not None:
            try:
                info = s.status.get("phase_info") or {}
                ph.configure(text=("phase : " + info["label"]) if info and float(params.get("auto_phase", 1.0)) >= 0.5 else "phase : -")
            except tk.TclError:
                vj_ui.pop("phase", None)
        rec_refresh()

    rec_cache: dict = {}
    vj_ui["rec_cache"] = rec_cache

    def rec_refresh() -> None:
        """Onglet « Rec » : texte et etat du bouton (grise sans projection ni ffmpeg, rouge « Arreter » pendant l'enregistrement) et voyant (hauteur fixe)."""
        btn, label, bar_btn = vj_ui.get("rec_btn"), vj_ui.get("rec_label"), vj_ui.get("rec_bar_btn")
        if btn is None and bar_btn is None:
            return
        try:
            ctl = getattr(s, "recording", None)
            recording = ctl is not None and ctl.active
            if "ffmpeg" not in rec_cache:
                rec_cache["ffmpeg"] = shutil.which("ffmpeg") is not None          # le dossier portable met ffmpeg\ sur le PATH des le lancement
            src = getattr(s.audio, "src", None)
            audio_name = s.audio.name if src is not None and hasattr(src, "tap") else None            # l'entree synthetique n'a pas de point d'ecoute
            text, enabled = explain.rec_panel(recording, s.status.get("rec"), projection is None or projection.running(), rec_cache["ffmpeg"], s.status.get("rec_done"),
                                              core_rec.format_label(params.get("rec_format", core_rec.DEFAULT_FORMAT)), core_rec.clean_fps(params.get("rec_fps", 30)), audio_name)
            state = "normal" if enabled else "disabled"
            if bar_btn is not None:                                                  # bouton de la barre (a la place de PANIQUE) : toujours present
                secs = int(float((s.status.get("rec") or {}).get("seconds", 0.0)))
                want_bar = f"\u25a0 {secs // 60:02d}:{secs % 60:02d}" if recording else "\u25cf REC"
                try:
                    if bar_btn.cget("text") != want_bar:
                        bar_btn.configure(text=want_bar)
                    if str(bar_btn.cget("state")) != state:
                        bar_btn.configure(state=state)
                except tk.TclError:
                    vj_ui.pop("rec_bar_btn", None)
            if btn is not None and label is not None:
                want = "■ Arreter" if recording else "● Enregistrer"
                if btn.cget("text") != want:
                    btn.configure(text=want, bg="#c0392b" if recording else a2w.GUI_BG)
                if str(btn.cget("state")) != state:
                    btn.configure(state=state)
                if label.cget("text") != text:
                    label.configure(text=text)
        except tk.TclError:
            vj_ui.pop("rec_btn", None)
            vj_ui.pop("rec_label", None)
    vj_ui["rec_refresh"] = rec_refresh

    def sync_master_panel() -> None:
        """Curseurs et boutons radio relus depuis `params` (apres le rappel d'une memoire de masters)."""
        for key, var, factor, dec, text, unit in vj_ui.get("knobs", []):
            try:
                var.set(round(float(params[key]) * factor, dec))
                text.set(knob_text(key, float(var.get()), dec, unit))          # (var.set ne rappelle pas la commande du curseur : la valeur affichee se met a jour ici)
            except (tk.TclError, KeyError, ValueError):
                pass
        for key, var in vj_ui.get("vars", {}).items():
            try:
                var.set(params[key] if isinstance(params[key], str) else int(round(float(params[key]))))
            except (tk.TclError, KeyError, ValueError):
                pass
        cvar = vj_ui.get("foudre_color")
        if cvar is not None:
            try:
                cvar.set(str(params["foudre_color"]))
            except (tk.TclError, KeyError):
                pass
        sync_kind()
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
        if flash and float(params.get("trans_time", 0.0)) <= 0.001:
            s.commands.put("flash")                        # sans transition : l'ancien flash (avec une transition, `apply_scene_dict` s'en charge)
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
        total = max(float(params.get("build_time", 16.0)), 1.0)
        params["build"] = min((time.monotonic() - build_ui["t0"]) / total, 1.0)
        btn = vj_ui.get("build")
        if btn is not None:
            try:
                btn.configure(text=build_text())
            except tk.TclError:
                vj_ui.pop("build", None)
        after(50, build_tick)

    def start_build(auto: bool = False) -> None:
        if build_ui["t0"] is not None:                    # deuxieme appui = drop
            do_drop()
            return
        build_ui["t0"] = time.monotonic()
        params["build"] = 0.0
        rhythm_ui["manual_build"] = not auto              # montee lancee a la main : seul le bouton Drop la relache
        rhythm_ui["auto_build"] = False                   # une montee lancee a la main n'est jamais annulee par les detecteurs (les appels automatiques le reposent apres)
        scene_msg.set(f"Montee sur {float(params.get('build_time', 16.0)):.0f} s : reactivite, strobe et teinte montent puis restent au maximum jusqu'au drop (bouton Drop)")
        build_tick()

    def do_drop() -> None:
        """Relache d'un coup : flash blanc et tout retombe aux reglages normaux."""
        was = build_ui["t0"] is not None
        rhythm_ui["manual_build"] = False
        build_ui["t0"] = None
        params["build"] = 0.0
        rhythm_ui["auto_build"] = False
        if was:
            if params.get("strobe_kind") != "foudre":                              # mode Foudre : la grande decharge remplace le flash blanc plein ecran
                s.commands.put("flash")
            params["strobe_hits"] = int(params.get("strobe_hits", 0)) + 1
            params["foudre_drop"] = int(params.get("foudre_drop", 0)) + 1          # mode Foudre : grande decharge a pleine profondeur
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

        def slider(f, i, key, label, lo, hi, step, factor, unit, default, tip, dec=0, var_key=None) -> tuple:
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
            return name, scale

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
            one = button(f, "Temps 1", do_one, row=3, column=2, sticky="w", padx=2, pady=(3, 0))
            vj_ui["one"] = one
            Tooltip(one, "Appuie sur le premier temps de la mesure (touche virgule dans la fenetre de rendu) : le strobe auto tombe alors sur les 1 "
                         "et le VJ « en rythme » change de scene exactement sur un 1. La grille se recale seule sur les kicks et s'efface quand ils "
                         "disparaissent.")
            bpm = vj_ui["bpm"] = tk.Label(f, text="\u2014 BPM", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG)
            bpm.grid(row=3, column=3, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            toggle(f, "bpm_auto", "Tempo auto", "Le master du tempo suit le BPM detecte a partir des kicks (120 BPM = 100 %). "
                                                 "Il faut des kicks reguliers : sans BPM fiable, le tempo ne bouge pas.",
                   row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(3, 0))
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
            params["strobe_mode"] = "manual"              # plus de choix de mode : le bouton fait un eclair, « Strobe auto » decide seul
            radios(f, "strobe_kind", (("Strobe", "strobe"), ("Foudre", "foudre")), row=0, span=6, on_change=lambda: sync_kind(),
                   tip="Ce que fait le strobe : des eclairs blancs (Strobe) ou un arbre de lumiere fractal qui part du centre sur chaque kick, s'amplifie "
                       "d'un kick a l'autre et s'eteint sans kick (Foudre). Le bouton, Strobe auto, la Montee et le Drop suivent le mode choisi.")
            sb = button(f, strobe_text(), row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            vj_ui["strobe"] = sb
            sb.bind("<ButtonPress-1>", lambda e: (gl.strobe_press(params), sb.configure(text=strobe_text())), add="+")
            sb.bind("<ButtonRelease-1>", lambda e: (gl.strobe_release(params), sb.configure(text=strobe_text())), add="+")
            Tooltip(sb, "Strobe : eclair blanc court a la demande, tant que c'est maintenu. Touche S dans la fenetre de rendu.")
            toggle(f, "strobe_auto", "Strobe auto", "Le strobe decide seul quand eclairer, sans aucun reglage : jamais en silence, en ambiance ni en cassure ; "
                                                    "un accent discret de temps en temps en groove (sur le « 1 » si la grille est connue, sinon sur une frappe des "
                                                    "aigus) ; une cadence qui accelere pendant une montee ; un gros eclair au drop ; un eclair a chaque kick au pic. "
                                                    "La montee et le drop partent aussi tout seuls (montee quand l'energie monte ou que les basses disparaissent, gros eclair au drop). "
                                                    "Le limiteur de securite (10 eclairs par seconde, rafales de 8 s) reste applique.",
                   row=1, column=3, columnspan=3, sticky="w", padx=6, pady=(3, 0))
            slider(f, (3, 0), "build_time", "Duree", 2, 60, 1, 1.0, " s", 16, "Duree de la montee, en secondes : reactivite, strobe (de 0,5 a 16 Hz, lent au depart) et teinte montent sur cette duree, puis restent au maximum jusqu'au drop (bouton Drop, 2e appui sur Montee, ou drop detecte en mode Montee / drop auto).")
            bb = button(f, build_text(), start_build, row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            vj_ui["build"] = bb
            Tooltip(bb, "Montee : en quelques secondes, la reactivite, le strobe (de 0,5 a 16 Hz, lent au depart) et la teinte (jusqu'a +120 degres) montent progressivement. "
                        "Un deuxieme appui (ou Drop) relache d'un coup : flash blanc et retour aux reglages normaux.")
            drop = button(f, "Drop", do_drop, row=2, column=3, sticky="w", padx=6, pady=(3, 0))
            Tooltip(drop, "Relache la montee d'un coup : flash blanc (mode Foudre : grande decharge a pleine profondeur), tout retombe aux reglages normaux.")
            sinfo = tk.Label(f, text="strobe auto : -", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=420)
            dark = slider(f, (4, 0), "strobe_dark", "Sombre", 5, 100, 5, 100.0, "%", 100,
                          "Les eclairs ne blanchissent que les parties SOMBRES de l'image : seuil de luminosite (100 % = toute l'image, plus bas = seulement ce qui est plus "
                          "sombre que ce seuil, les parties claires ne bougent pas). S'applique a tous les eclairs : le bouton, ceux de la montee et le strobe auto "
                          "(dont l'accent discret de groove est deja limite a 50 %). Double-clic sur le nom : retour a 100 %. Sans objet en mode Foudre.")
            reach = slider(f, (3, 3), "foudre_reach", "Portee", 40, 150, 5, 100.0, "%", 100,
                           "Foudre : jusqu'ou vont les branches (100 % = jusqu'au bord de l'image).")
            amount = slider(f, (4, 3), "foudre_amount", "Intensite", 0, 150, 5, 100.0, "%", 70,
                            "Foudre : intensite de la lumiere (le plafond reste sous le blanc plein ecran).")
            crow = tk.Frame(f, bg=a2w.GUI_PANEL_BG)
            crow.grid(row=5, column=0, columnspan=6, sticky="w", padx=6, pady=(3, 0))
            cname = tk.Label(crow, text="Couleur", width=9, anchor="w", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
            cname.pack(side="left")
            cvar = tk.StringVar(value=str(params.get("foudre_color", "#3a7bff")))
            vj_ui["foudre_color"] = cvar
            swatch = tk.Label(crow, width=3, bg=cvar.get())
            centry = tk.Entry(crow, textvariable=cvar, width=8)
            centry.pack(side="left")
            swatch.pack(side="left", padx=(8, 0))

            def color_written(*_a) -> None:
                text = cvar.get().strip().lstrip("#")
                if len(text) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in text):             # seule une valeur complete est poussee
                    params["foudre_color"] = "#" + text.lower()
                    swatch.configure(bg=params["foudre_color"])

            cvar.trace_add("write", color_written)

            def pick_color() -> None:
                chosen = colorchooser.askcolor(color=params["foudre_color"], title="Couleur de la foudre")
                if chosen and chosen[1]:
                    cvar.set(chosen[1])

            cpick = tk.Button(crow, text="...", command=pick_color, padx=6)
            cpick.pack(side="left", padx=(6, 0))
            Tooltip(cpick, "Couleur du halo de la foudre (le coeur est toujours blanc).")
            sinfo.grid(row=6, column=0, columnspan=6, sticky="w", padx=6, pady=(6, 0))
            vj_ui["strobe_info"] = sinfo
            Tooltip(sinfo, "Strobe auto : la raison de son dernier eclair (accent de groove, kick au pic, drop, montee...). Mode Foudre : profondeur et charge de l'arbre.")
            vj_ui["foudre_widgets"] = [*reach, *amount, cname, centry, cpick]
            vj_ui["strobe_widgets"] = [*dark]
            sync_kind()

        new_tab("Live", fill_1)

        # ---- Audio : sensibilite du kick (voyant) et mode silence
        def fill_2(f) -> None:
            slider(f, 0, "sensitivity", "Kick", 0.25, 4.0, 0.05, 1.0, "", 1.0,
                   "Sensibilite du detecteur de kick (comme les fleches haut / bas dans la fenetre de rendu) : plus haut = detecte des kicks plus faibles.", dec=2)
            led = vj_ui["led"] = tk.Label(f, text=" \u25cf kick ", bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG, font=("Segoe UI", 9, "bold"))
            led.grid(row=0, column=3, columnspan=2, sticky="w", padx=6)
            Tooltip(led, "Voyant : s'allume a chaque kick detecte.")
            sil = tk.IntVar(value=int(float(params["auto_phase"]) >= 0.5))
            cb = tk.Checkbutton(f, text="Phases auto", variable=sil, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, selectcolor=a2w.GUI_BG,
                                activebackground=a2w.GUI_PANEL_BG, command=lambda: params.__setitem__("auto_phase", float(sil.get())))
            cb.grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
            vars_["auto_phase"] = sil
            Tooltip(cb, "Le visuel suit les phases du morceau tout seul, sans reglage : le silence et les passages calmes ralentissent tres doucement les "
                        "animations (le visuel ne s'eteint jamais), la montee et le pic donnent plus de reactivite et d'effets, la cassure ralentit. "
                        "Les phases sont reconnues a l'energie, aux kicks et a la repartition des frequences ; les seuils s'adaptent au morceau et au micro. "
                        "C'est aussi elles qui lancent la Montee (phase montee) et le Drop (passage au pic) automatiques : flash et retour au normal.")
            phase = tk.Label(f, text="phase : -", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="w")
            phase.grid(row=1, column=3, columnspan=2, sticky="w", padx=6)
            vj_ui["phase"] = phase
            sources = tk.Label(f, text="sources : -\neffets : -", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=420)
            sources.grid(row=2, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 0))
            vj_ui["sources"] = sources
            Tooltip(sources, "Partie du son qui pilote le fond et le logo (menus « reagit a » de l'onglet Effets). Sur Auto, elle change avec la phase : "
                             "groove = fond sur les basses et logo sur les aigus, montee = mediums et frappes, cassure = seul le logo reagit, pic = tout sur le kick. "
                             "La 2e ligne dit ce qui fait bouger chaque effet (ondulation, choc, glitch, aberration) : en auto chaque effet a sa propre source, et ceux qui n'ont rien a dire restent au repos.")
            effects = tk.Label(f, text="phases auto : -", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=420)
            effects.grid(row=3, column=0, columnspan=6, sticky="w", padx=6, pady=(2, 0))
            vj_ui["phase_fx"] = effects
            Tooltip(effects, "Ce que la phase du morceau change a tes reglages en ce moment (multiplicateurs lisses) : vitesse des animations, reactivite au son, "
                             "intensite des effets, luminosite du fond et derive de teinte. x1,00 = rien ne change.")

            why = tk.Label(f, text="mesures : -", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=420)
            why.grid(row=4, column=0, columnspan=6, sticky="w", padx=6, pady=(2, 0))
            vj_ui["phase_why"] = why
            Tooltip(why, "Ce que le detecteur de phases mesure, pour comprendre (ou regler) sa decision : kicks par seconde (>= 1,2 = groove), "
                         "energie par rapport a la moyenne des passages a kicks (x1,2 ou plus = pic), et gain des mediums + aigus sur 8 s et 20 s "
                         "(montee quand ca grimpe vite, x1,5 en 8 s ou x1,8 en 20 s).")

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
            slider(f, 4, "hue_spectrum", "Teinte audio", 0, 100, 1, 100.0, "%", 0, "La teinte suit la musique : les graves gardent les couleurs chaudes d'origine, les aigus les tirent vers le froid (0 = aucun effet).")
            slider(f, 1, "breath_overlay", "Resp. overlay", 0, 100, 1, 100.0, "%", 0, "Respiration de l'overlay : sa presence oscille doucement (0 = aucune).")
            slider(f, 2, "breath_bg", "Resp. fond", 0, 100, 1, 100.0, "%", 0, "Respiration du fond, en opposition de phase avec l'overlay (0 = aucune).")
            slider(f, 3, "breath_period", "Periode", 2, 30, 1, 1.0, " s", 8, "Periode de la respiration, en secondes.")
            slider(f, (4, 3), "breath_beats", "Temps", 0, 64, 1, 1.0, "", 0,
                   "Periode de la respiration lente (« Periode » ci-dessus) en TEMPS de la pulsation : un cycle dure ce nombre de temps de la musique (16 = quatre mesures), quel que soit le BPM. 0 = la periode en secondes ci-dessus. "
                   "Demande une pulsation calee ou libre.")
            slider(f, (4, 0), "breath_pulse", "Pulsation", 0, 100, 1, 100.0, "%", 0,
                   "Respiration sur la pulsation : la presence de l'overlay, la luminosite du fond et sa derive suivent le temps (creux entre deux temps, sommet sur le temps) et le corps des basses. "
                   "Courbe douce, pas un clignotement. Demande une pulsation calee (voyant de l'onglet Affichage) ; sinon elle suit seulement les basses. 0 = aucune.")
            slider(f, 5, "trans_time", "Transition", 0, 3, 0.1, 1.0, " s", 0.8, "Duree de la transition quand la scene change (VJ, touche, tirage) : l'ancienne image se fond dans la nouvelle. "
                                                                          "0 = pas de transition (flash blanc quand c'est le VJ, rien a la main).", dec=1)
            radios(f, "trans_kind", (("Fondu enchaine", "fade"), ("Zoom", "zoom")), row=3, col=0, span=6,
                   tip="Style de la transition : fondu enchaine, ou zoom (l'ancienne image fonce vers l'ecran en separant ses couleurs).")

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
                for key in ("build",):
                    params[key] = 0.0
                sync_master_panel()
                scene_msg.set(f"Memoire {i + 1} '{slot['name']}' rappelee")

            for i in range(scenes.MASTER_SLOTS):
                b = button(f, slot_label(i), lambda k=i: slot_click(k), row=i // 2, column=0 if i % 2 == 0 else 3, columnspan=3,
                           sticky="ew", padx=6, pady=(3, 0))
                b.bind("<Button-3>", lambda e, k=i: save_slot(k), add="+")
                slot_btns.append(b)
                Tooltip(b, "Rappelle cette combinaison de masters (reglages de salle). Clic droit : enregistre les masters actuels ici.")

            def arm_toggle() -> None:
                armed["on"] = not armed["on"]
                arm.configure(text="Sauver : choisis" if armed["on"] else "Sauver")

            arm = button(f, "Sauver", arm_toggle, row=(scenes.MASTER_SLOTS + 1) // 2, column=0, columnspan=3, sticky="w", padx=6, pady=(3, 0))
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

        # ---- Enregistrement video + audio : bouton, format, cadence, encodeur, delai audio, dossier, voyant (issue #140). Onglet a part : l'onglet Affichage est deja le
        # plus haut, et c'est lui qui donne sa hauteur au panneau etendu.
        def fill_8(f) -> None:
            from tkinter import filedialog
            rec_btn = button(f, "● Enregistrer", None, row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(3, 0))
            rec_btn.configure(width=14, command=lambda: s.commands.put("rec"))          # le fil GL bascule (touche E dans la fenetre de rendu : meme commande)
            vj_ui["rec_btn"] = rec_btn
            Tooltip(rec_btn, "Enregistre ce que la projection affiche, avec le son de l'entree audio choisie (onglet Affichage), dans un MP4 pret a poster ou a monter. "
                             "Touche E dans la fenetre de rendu. La fenetre de projection montre l'image enregistree : changer sa taille ne change pas le fichier.")
            radios(f, "rec_format", (("Vertical", "vertical"), ("Vertical HD", "vertical_hd"), ("Horizontal", "horizontal"), ("Carre", "carre")), row=0, col=2, span=4,
                   tip="Format de l'image : vertical 1080x1920 (TikTok, Reels), vertical HD 720x1280 (plus leger : pour les looks lourds comme les cellules, qui ne tiennent pas "
                       "30 images/s en 1080x1920 sur un GPU integre), horizontal 1920x1080, carre 1080x1080. Pris au demarrage de l'enregistrement.")
            radios(f, "rec_fps", (("30 i/s", 30), ("60 i/s", 60)), row=1, col=0, span=3,
                   tip="Images par seconde. 60 ne tient pas en 1080x1920 sur un GPU integre (l'alerte « images dupliquees » le dira) : reserve-le au vertical HD.")
            radios(f, "rec_encoder", (("x264", "libx264"), ("Materiel", "materiel")), row=1, col=3, span=3,
                   tip="x264 : qualite previsible, le processeur fait le travail (par defaut). Materiel : nvenc, amf ou qsv s'ils fonctionnent sur ce PC, sinon x264.")
            slider(f, (2, 0), "rec_delay_ms", "Delai audio", 0, 300, 5, 1.0, " ms", 40,
                   "Retard du son par rapport a l'image dans le fichier : le visuel reagit apres le son (capture, analyse, rendu), donc le son est retarde pour que "
                   "l'effet tombe dessus. 40 ms par defaut ; a regler en regardant un enregistrement (ou avec tools/calibrer_avance.py).")
            dir_row = tk.Frame(f, bg=a2w.GUI_PANEL_BG)
            dir_row.grid(row=3, column=0, columnspan=6, sticky="we", padx=6, pady=(3, 0))
            tk.Label(dir_row, text="Dossier", width=9, anchor="w", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG).pack(side="left")
            dir_var = tk.StringVar(value=str(params.get("rec_dir", core_rec.DEFAULT_DIR)))
            dir_var.trace_add("write", lambda *_a: params.__setitem__("rec_dir", dir_var.get().strip()))
            dir_entry = tk.Entry(dir_row, textvariable=dir_var, width=32)
            dir_entry.pack(side="left", fill="x", expand=True)
            vj_ui["rec_dir_var"] = dir_var

            def ask_dir() -> str:
                return filedialog.askdirectory(parent=root, initialdir=core_rec.resolve_dir(dir_var.get()), title="Dossier des enregistrements")
            vj_ui["rec_ask_dir"] = ask_dir

            def pick_dir() -> None:
                chosen = vj_ui["rec_ask_dir"]()                                   # remplacable par les tests : jamais de fenetre bloquante
                if chosen:
                    dir_var.set(chosen)
            dir_btn = tk.Button(dir_row, text="Parcourir...", command=pick_dir)
            dir_btn.pack(side="left", padx=(6, 0))
            Tooltip(dir_entry, "Dossier des fichiers MP4 (~ = ton dossier d'utilisateur). Cree s'il n'existe pas. Un nom de fichier unique est donne a chaque enregistrement (date et heure).")
            # Voyant de HAUTEUR FIXE (4 lignes) : le texte change (pret, REC en cours, avertissements, dernier fichier) et ne doit rien decaler.
            rec_label = tk.Label(f, text="", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="nw", justify="left", wraplength=430, width=60, height=4)
            rec_label.grid(row=4, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 0))
            vj_ui["rec_label"] = rec_label
            Tooltip(rec_label, "Pendant l'enregistrement : duree, taille (estimee), crete du son en dBFS (SATURE = le son a touche la pleine echelle : l'enregistrement est brut, "
                               "baisse le niveau a la source), images dupliquees (le GPU n'a pas suivi) et encodeur. Les avertissements s'affichent dessous.")
            rec_refresh()

        new_tab("Rec", fill_8)

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

    def do_one() -> None:
        """« Temps 1 » (bouton, touche virgule) : le temps qui vient de tomber est le premier de la mesure. Ancre la grille du temps : le
        strobe auto tombe sur les 1, et le VJ « en rythme » change de scene pile sur un 1. Il faut un tempo (detecte ou tape) ; la grille
        s'efface quand les kicks disparaissent. Un second appui reancre."""
        rhythm = rhythm_source()
        if rhythm is None:
            scene_msg.set("Temps 1 : analyse audio indisponible")
            return
        bpm = detected_bpm() or tap_state["bpm"] or 120.0 * float(params.get("tempo", 1.0))
        try:
            last = s.audio.analyzer.t_beat
        except Exception:
            last = None
        rhythm.grid.set_one(time.monotonic(), bpm, last)
        scene_msg.set(f"Temps 1 : grille calee sur {bpm:.0f} BPM (strobe auto et VJ en rythme sur le 1)")

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

    def auto_on() -> bool:
        """Montee / drop automatiques : plus de case a part. Ils partent des qu'une fonction intelligente les utilise : « Phases auto » (la phase
        montee / le passage au pic les declenchent) ou « Strobe auto » (ses eclairs de montee et de drop). `drop_auto` reste lu pour les anciens fichiers."""
        return any(float(params.get(k, 0.0)) >= 0.5 for k in ("auto_phase", "strobe_auto", "drop_auto"))

    def handle_drop(why: str = "drop (retour des basses)") -> None:
        """Un drop vient d'etre detecte (retour des basses apres une cassure, ou passage de la montee au pic)."""
        auto = auto_on()
        if build_ui["t0"] is not None and rhythm_ui.get("manual_build"):
            return                                           # montee lancee a la main : les detecteurs ne la relachent pas
        recent = time.monotonic() - rhythm_ui.get("t_drop", -99.0) < 4.0       # deux detecteurs voient le meme drop : un seul flash
        if auto and build_ui["t0"] is not None:
            params["strobe_why"] = why
            do_drop()                                        # relache la montee : flash + eclair
            rhythm_ui["t_drop"] = time.monotonic()
        elif auto and not recent:
            if params.get("strobe_kind") != "foudre":
                s.commands.put("flash")
            params["strobe_why"] = why
            params["strobe_hits"] = int(params.get("strobe_hits", 0)) + 1
            params["foudre_drop"] = int(params.get("foudre_drop", 0)) + 1
            rhythm_ui["t_drop"] = time.monotonic()
            scene_msg.set("DROP detecte")
        elif not auto:
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
            if auto_on() and build_ui["t0"] is None:
                start_build(auto=True)
                rhythm_ui["auto_build"] = True
                rhythm_ui["auto_src"] = "rhythm"
                scene_msg.set("Cassure detectee : montee automatique")
        phase_step()
        if int(r.drops) != rhythm_ui["drops"]:
            rhythm_ui["drops"] = int(r.drops)
            handle_drop()
        elif rhythm_ui["auto_build"] and rhythm_ui.get("auto_src", "rhythm") == "rhythm":
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

    BPM_HOLD_S = 4.0                                         # le BPM reste affiche ce temps apres l'avoir perdu (pas de clignotement)

    def phase_step() -> None:
        """Montee et drop par les PHASES du morceau (en plus des evenements du detecteur de rythme) : quand la phase « montee » est reconnue
        (energie qui monte sans kick), la montee demarre ; le passage au « pic » apres une montee ou une cassure est le drop ; une montee qui
        retombe (ambiance, groove) est annulee. Seulement si la montee / le drop automatiques sont actifs (voir `auto_on`)."""
        info = s.status.get("phase_info") or {}
        name = info.get("name") if (info and float(params.get("auto_phase", 1.0)) >= 0.5) else None
        prev, rhythm_ui["phase"] = rhythm_ui.get("phase"), name
        drops = int(info.get("drops", 0)) if info else 0
        seen, rhythm_ui["pdrops"] = rhythm_ui.get("pdrops"), drops
        if seen is not None and drops != seen and name is not None and auto_on():
            handle_drop("drop (retour du kick)")             # le kick revient apres un vide : le drop part sur CE kick, sans attendre que la phase change
        if name == prev or not auto_on():
            return
        if name == "build" and build_ui["t0"] is None:
            start_build(auto=True)
            rhythm_ui["auto_build"] = True
            rhythm_ui["auto_src"] = "phase"
            scene_msg.set("Montee detectee (l'energie monte) : montee automatique")
        elif name == "peak" and prev in ("build", "break") and (build_ui["t0"] is not None or rhythm_ui["auto_build"]):
            handle_drop("drop (passage au pic)")
        elif name not in ("build", "break", "peak") and rhythm_ui["auto_build"] and rhythm_ui.get("auto_src") == "phase":
            build_ui["t0"] = None                            # la montee detectee a retombe sans drop : annulee
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
                now_ = time.monotonic()
                if det:
                    rhythm_ui["bpm_seen"] = (det, now_)
                elif rhythm_ui.get("bpm_seen") and now_ - rhythm_ui["bpm_seen"][1] < BPM_HOLD_S:
                    det = rhythm_ui["bpm_seen"][0]                 # la detection decroche un instant (kick manque) : la valeur reste affichee
                text = "\u25cf " + (f"{det:.0f} BPM" if det else "-- BPM")
                state = getattr(r, "state", "idle")
                if state != "idle":
                    text += "  cassure" if state == "break" else "  MONTEE"
                if lbl.cget("text") != text:
                    lbl.configure(text=text)
                color = SCENE_COLOR if on else (a2w.GUI_FG if det else a2w.GUI_MUTED_FG)       # BPM fiable = texte clair, sinon grise
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
    def voyants() -> dict:
        """Les textes des voyants de l'espace de controle (pour la telecommande telephone : lecture seule)."""
        return {"phase": phase_why_line(), "strobe": strobe_line(), "sources": sources_line()}

    return types.SimpleNamespace(voyants=voyants, sync_master_panel=sync_master_panel, detected_bpm=detected_bpm, rhythm_step=rhythm_step, autosave_tick=autosave_tick, back_scene=back_scene, console_window=console_window, do_drop=do_drop, do_panic=do_panic, do_tap=do_tap, do_one=do_one, help_window=help_window, master_box=master_box, record_history=record_history, start_build=start_build, status_text=status_text, update_master_panel=update_master_panel)
