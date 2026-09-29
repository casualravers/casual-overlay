"""Fenetre de reglages tkinter de audio2wave_gl (--gui).

Meme theme, memes conventions et memes helpers d'aspect qu'audio2wave_live.py --gui
(style_gui, Tooltip, style_option_menu importes du depot audio2wave), en trois panneaux:

  - LIVE   : le visuel de fond (style, couleurs, spectre, entree ffmpeg). Chaque changement
             remplace le PRODUCTEUR ffmpeg a chaud, anti-rebond de 400 ms, sans jamais
             toucher a la fenetre GL (voir ProducerManager dans audio2wave_gl.py);
  - LOGO   : fichier, position, taille, opacite et reactions a l'audio (pulsation,
             tremblement, contour lumineux): relus a chaque image, effet immediat;
  - EFFETS : les 5 effets, intensite globale, sensibilite du kick, entree d'analyse.

tkinter garde le fil principal, la fenetre GL tourne dans un fil (comme run() dans les
scripts d'audio2wave): toute action qui doit s'executer dans le fil GL passe par
`session.commands`.
"""

from __future__ import annotations

import copy
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog

import audio2wave_gl as gl

APPLY_DEBOUNCE_MS = 400    # meme valeur qu'audio2wave_live: chaque redemarrage rouvre le peripherique
REFRESH_MS = 500
METER_MS = 60
PANELS = {"left": (0, 1), "mid": (3, 4), "right": (6, 7)}   # (colonne du label, colonne du controle)
TOTAL_COLUMNS = 8


def run_gui(s, live, on_ready=None) -> None:
    """Construit la fenetre et bloque dans mainloop() jusqu'a la fin de la session.

    `on_ready(controls)` (tests): appele dans le fil tkinter, une fois la fenetre construite,
    avec un dict {"root", "live_vars", "apply_live", "logo_var", ...} pour la piloter."""
    import audio2wave as a2w

    Tooltip = live.Tooltip
    root = tk.Tk()
    root.title("casual-overlay GL - reglages")
    root.resizable(False, False)
    a2w.style_gui(root)

    params = s.params
    manager = s.manager
    ROW_PADX, ROW_PADY, SECTION_GAP = 11, 4, 7
    rows = {"left": 1, "mid": 1, "right": 1}

    def next_row(panel: str) -> int:
        rows[panel] += 1
        return rows[panel] - 1

    def add_label(panel: str, text: str, r: int, tooltip: str | None = None) -> tk.Label:
        label = tk.Label(root, text=text)
        label.grid(row=r, column=PANELS[panel][0], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        if tooltip:
            Tooltip(label, tooltip)
        return label

    def add_section_title(panel: str, title: str) -> None:
        tk.Label(root, text=title.upper(), font=a2w.GUI_FONT_SMALL, fg=a2w.GUI_MUTED_FG).grid(
            row=next_row(panel), column=PANELS[panel][0], columnspan=2, sticky="w",
            padx=ROW_PADX, pady=(0, 2))

    def add_separator(panel: str, title: str | None = None) -> None:
        tk.Frame(root, bg=a2w.GUI_PANEL_BG, height=1).grid(
            row=next_row(panel), column=PANELS[panel][0], columnspan=2, sticky="ew",
            padx=ROW_PADX, pady=(SECTION_GAP, SECTION_GAP if title is None else 4))
        if title:
            add_section_title(panel, title)

    tk.Label(root, text="Reglages casual-overlay GL", font=a2w.GUI_FONT_HEADING, fg=a2w.GUI_ACCENT).grid(
        row=0, column=0, columnspan=TOTAL_COLUMNS, sticky="w", padx=ROW_PADX, pady=(10, SECTION_GAP))

    # ---------------------------------------------------------------- fabriques de widgets
    # Deux familles: les variables "live" (rejouent le producteur ffmpeg, anti-rebond) et les
    # variables "param" (ecrites tout de suite dans session.params, relues a chaque image).
    live_vars: dict[str, tuple[tk.Variable, object]] = {}
    apply_after = {"id": None}

    def schedule_apply(*_a) -> None:
        if apply_after["id"] is not None:
            root.after_cancel(apply_after["id"])

        def run() -> None:
            apply_after["id"] = None
            apply_live()

        apply_after["id"] = root.after(APPLY_DEBOUNCE_MS, run)

    def bind_param(key: str, var: tk.Variable, conv=float) -> None:
        def on_write(*_a) -> None:
            try:
                params[key] = conv(var.get())
            except (ValueError, tk.TclError):
                pass    # champ en cours de saisie

        var.trace_add("write", on_write)

    def add_slider(panel: str, label: str, lo: float, hi: float, step: float, var: tk.Variable,
                   tooltip: str | None = None, length: int = 170) -> tk.Scale:
        r = next_row(panel)
        add_label(panel, label, r, tooltip)
        scale = tk.Scale(root, from_=lo, to=hi, resolution=step, orient="horizontal", variable=var,
                         length=length, showvalue=True)
        scale.grid(row=r, column=PANELS[panel][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        return scale

    def param_slider(panel: str, label: str, key: str, lo: float, hi: float, step: float,
                     tooltip: str | None = None) -> tk.DoubleVar:
        var = tk.DoubleVar(value=params[key])
        bind_param(key, var)
        add_slider(panel, label, lo, hi, step, var, tooltip)
        return var

    def live_slider(label: str, attr: str, lo: float, hi: float, step: float, initial: float, conv,
                    tooltip: str | None = None) -> tk.DoubleVar:
        var = tk.DoubleVar(value=initial)
        var.trace_add("write", schedule_apply)
        live_vars[attr] = (var, conv)
        add_slider("left", label, lo, hi, step, var, tooltip)
        return var

    def live_entry(label: str, attr: str, initial: str, tooltip: str | None = None) -> tk.StringVar:
        r = next_row("left")
        add_label("left", label, r, tooltip)
        var = tk.StringVar(value=initial)
        tk.Entry(root, textvariable=var, width=20).grid(
            row=r, column=PANELS["left"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        var.trace_add("write", schedule_apply)
        live_vars[attr] = (var, str)
        return var

    def live_dropdown(label: str, attr: str, initial: str, choices: tuple[str, ...],
                      tooltip: str | None = None) -> tk.StringVar:
        r = next_row("left")
        add_label("left", label, r, tooltip)
        var = tk.StringVar(value=initial)
        menu = tk.OptionMenu(root, var, *choices)
        a2w.style_option_menu(menu)
        menu.grid(row=r, column=PANELS["left"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        var.trace_add("write", schedule_apply)
        live_vars[attr] = (var, str)
        return var

    def live_radio(label: str, attr: str, initial: str, values: tuple[str, ...],
                   tooltip: str | None = None) -> tk.StringVar:
        r = next_row("left")
        add_label("left", label, r, tooltip)
        var = tk.StringVar(value=initial)
        frame = tk.Frame(root)
        frame.grid(row=r, column=PANELS["left"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        for value in values:
            tk.Radiobutton(frame, text=value, variable=var, value=value).pack(side="left")
        var.trace_add("write", schedule_apply)
        live_vars[attr] = (var, str)
        return var

    # =========================================================== PANNEAU GAUCHE : LIVE
    add_section_title("left", "Live - fond")
    if manager is None:
        tk.Label(root, text="Mode --synthetique: pas de flux ffmpeg a regler.", fg=a2w.GUI_MUTED_FG,
                 wraplength=300, justify="left").grid(
            row=next_row("left"), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    else:
        args0 = s.live_args
        width = s.render_size[0]

        # Entree ffmpeg (dshow). Sans -d au lancement, le flux demarre au premier choix.
        device_var = tk.StringVar(value=args0.device or "")
        device_var.trace_add("write", schedule_apply)
        live_vars["device"] = (device_var, lambda v: v or None)
        r = next_row("left")
        add_label("left", "Entree audio", r, "Entree DirectShow lue par ffmpeg pour le visuel de fond. "
                                              "L'analyse des effets a sa propre entree (panneau de droite).")
        frame = tk.Frame(root)
        frame.grid(row=r, column=0 + 1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        device_menu = tk.OptionMenu(frame, device_var, device_var.get() or "(aucune)")
        a2w.style_option_menu(device_menu)
        device_menu.pack(side="left")

        def refresh_devices() -> None:
            names = live.list_audio_devices()
            if args0.device and args0.device not in names:
                names = [args0.device] + names
            menu = device_menu["menu"]
            menu.delete(0, "end")
            for name in names:
                menu.add_command(label=name, command=lambda n=name: device_var.set(n))
            s.status["msg"] = f"{len(names)} entree(s) audio detectee(s)"

        tk.Button(frame, text="Actualiser", command=refresh_devices).pack(side="left", padx=(8, 0))
        refresh_devices()

        style_var = live_radio("Style", "style", args0.style, ("analyzer", "radio"))
        live_radio("Forme", "shape", args0.shape, ("bar", "line"),
                   tooltip="Uniquement pour le style analyzer: barres (bar) ou courbe (line).")
        previous_style = {"v": args0.style}

        add_separator("left", "Couleurs")
        live_entry("Couleurs", "colors", args0.colors,
                   tooltip="Une ou plusieurs couleurs (separees par |). Un nom inconnu de ffmpeg "
                           "n'arrete pas le flux: il retombe sur du blanc (erreur dans la console). "
                           "Attention: 'grey' n'existe pas pour ffmpeg, ecrire 'gray'.")
        live_entry("Couleur de fond", "bg_color", args0.bg_color)

        add_separator("left", "Spectre")
        live_slider("Barres/points", "bars", 8, 400, 4, live.resolve_bars(args0, width), int)
        gain_var = live_slider("Gain (dB)", "gain", -60, 60, 1, live.resolve_gain(args0), float)
        live_slider("Lissage", "averaging", 1, 30, 1, args0.averaging, int,
                    tooltip="Uniquement pour le style analyzer: trames moyennees, poste de latence principal.")
        live_slider("Espace entre barres", "bar_gap", 0, 1.5, 0.05, args0.bar_gap, float)
        live_slider("Frequence max (Hz)", "max_freq", 1000, 20000, 500, args0.max_freq or 20000, int,
                    tooltip="Frequence la plus haute affichee (style analyzer).")
        live_dropdown("Echelle frequences", "freq_scale", args0.freq_scale, ("lin", "log", "rlog"))
        live_dropdown("Echelle amplitude", "amp_scale", args0.amp_scale, ("lin", "sqrt", "cbrt", "log"))
        stereo_var = tk.BooleanVar(value=args0.stereo)
        stereo_var.trace_add("write", schedule_apply)
        live_vars["stereo"] = (stereo_var, bool)
        tk.Checkbutton(root, text="Stereo", variable=stereo_var).grid(
            row=next_row("left"), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

        def on_style_change(*_a) -> None:
            # Le gain par defaut depend du style (30 dB analyzer, -10 dB radio): on suit le
            # defaut tant que l'utilisateur n'a pas regle le gain a la main.
            new = style_var.get()
            try:
                if abs(float(gain_var.get()) - live.DEFAULT_GAIN_DB[previous_style["v"]]) < 0.5:
                    gain_var.set(live.DEFAULT_GAIN_DB[new])
            except (tk.TclError, KeyError, ValueError):
                pass
            previous_style["v"] = new

        style_var.trace_add("write", on_style_change)

    def apply_live() -> None:
        """Relit les widgets "live" dans une COPIE des options d'audio2wave_live et demande a
        ProducerManager de remplacer le producteur (la copie evite de muter des options qu'un
        lancement en cours est en train de lire)."""
        if manager is None:
            return
        new = copy.copy(s.live_args)
        try:
            for attr, (var, conv) in live_vars.items():
                value = var.get()
                if attr in ("colors", "bg_color") and not str(value).strip():
                    continue       # champ vide en cours de saisie: on garde la valeur precedente
                setattr(new, attr, conv(value))
        except (ValueError, tk.TclError):
            return
        if new.max_freq >= 20000:
            new.max_freq = 0       # 0 = pleine bande
        if not new.device:
            s.status["live"] = "Choisis une entree audio"
            return
        s.live_args = new
        manager.request_restart(new)

    # ========================================================== PANNEAU MILIEU : LOGO
    add_section_title("mid", "Logo")

    r = next_row("mid")
    add_label("mid", "Fichier", r, "PNG avec canal alpha. Vide = aucun logo.")
    file_frame = tk.Frame(root)
    file_frame.grid(row=r, column=PANELS["mid"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    logo_var = tk.StringVar(value=params["logo_path"])
    logo_entry = tk.Entry(file_frame, textvariable=logo_var, width=18)
    logo_entry.pack(side="left")

    def apply_logo_path(_evt=None) -> None:
        params["logo_path"] = logo_var.get().strip()

    logo_entry.bind("<Return>", apply_logo_path)
    logo_entry.bind("<FocusOut>", apply_logo_path)

    def browse_logo() -> None:
        initial = Path(logo_var.get()).parent if logo_var.get() else gl.SCRIPT_DIR / "asset"
        chosen = filedialog.askopenfilename(
            title="Choisir un logo", initialdir=str(initial if initial.is_dir() else gl.SCRIPT_DIR),
            filetypes=[("Images PNG", "*.png"), ("Toutes les images", "*.png *.webp *.gif *.tif *.tiff"),
                       ("Tous les fichiers", "*.*")])
        if chosen:
            logo_var.set(chosen)
            apply_logo_path()

    tk.Button(file_frame, text="Parcourir...", command=browse_logo).pack(side="left", padx=(8, 0))
    tk.Button(file_frame, text="Aucun", command=lambda: (logo_var.set(""), apply_logo_path())).pack(
        side="left", padx=(4, 0))

    add_separator("mid", "Position et taille")
    x_var = param_slider("mid", "Position X", "logo_x", 0.0, 1.0, 0.01, "Centre du logo, 0 = gauche, 1 = droite.")
    y_var = param_slider("mid", "Position Y", "logo_y", 0.0, 1.0, 0.01, "Centre du logo, 0 = haut, 1 = bas.")
    param_slider("mid", "Taille", "logo_scale", 0.05, 0.95, 0.01,
                 "Cote du carre englobant, en fraction de la hauteur de l'image.")
    param_slider("mid", "Opacite", "logo_opacity", 0.0, 1.0, 0.05)
    tk.Button(root, text="Centrer", command=lambda: (x_var.set(0.5), y_var.set(0.5))).grid(
        row=next_row("mid"), column=PANELS["mid"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    add_separator("mid", "Reaction a l'audio")
    param_slider("mid", "Pulsation", "logo_pulse", 0.0, 0.5, 0.01,
                 "Zoom du logo a chaque kick (0,12 = +12 %). Jamais coupe hors cadre.")
    param_slider("mid", "Tremblement", "logo_jitter", 0.0, 0.03, 0.001,
                 "Vibration du logo proportionnelle aux aigus.")
    param_slider("mid", "Contour lumineux", "logo_glow", 0.0, 3.0, 0.1,
                 "Intensite du halo autour du logo, module par les basses.")
    param_slider("mid", "Rayon du contour", "logo_glow_radius", 0.0, 3.0, 0.1)

    r = next_row("mid")
    add_label("mid", "Couleur du contour", r)
    color_frame = tk.Frame(root)
    color_frame.grid(row=r, column=PANELS["mid"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    color_var = tk.StringVar(value=params["logo_glow_color"])
    swatch = tk.Label(color_frame, width=3, bg=params["logo_glow_color"])
    tk.Entry(color_frame, textvariable=color_var, width=9).pack(side="left")
    swatch.pack(side="left", padx=(8, 0))

    def on_color_write(*_a) -> None:
        text = color_var.get().strip().lstrip("#")
        # Seule une valeur complete et valide est poussee: pendant la saisie, l'ancienne reste.
        if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text):
            params["logo_glow_color"] = "#" + text.lower()
            swatch.config(bg=params["logo_glow_color"])

    color_var.trace_add("write", on_color_write)

    def pick_color() -> None:
        chosen = colorchooser.askcolor(color=params["logo_glow_color"], title="Couleur du contour")
        if chosen and chosen[1]:
            color_var.set(chosen[1])

    tk.Button(color_frame, text="Choisir...", command=pick_color).pack(side="left", padx=(8, 0))

    # ========================================================= PANNEAU DROIT : EFFETS
    add_section_title("right", "Effets")
    fx_on_vars: list[tk.IntVar] = []
    fx_labels = ("Wobble", "Onde de choc", "Aberration chromatique", "Glitch", "Logo (pulse/contour)")
    fx_tips = ("Ondulation de l'image, amplitude = basses.",
               "Onde de choc depuis le centre a chaque kick.",
               "Canaux R/V/B decales depuis le centre au kick.",
               "Bandes horizontales decalees sur les gros kicks / aigus.",
               "Interrupteur des reactions du logo (pulsation, tremblement, contour).")
    for i, name in enumerate(fx_labels):
        r = next_row("right")
        on_var = tk.IntVar(value=int(params["fx_on"][i]))
        fx_on_vars.append(on_var)
        on_var.trace_add("write", lambda *_a, i=i, v=on_var: params["fx_on"].__setitem__(i, int(v.get())))
        cb = tk.Checkbutton(root, text=name, variable=on_var)
        cb.grid(row=r, column=PANELS["right"][0], sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(cb, fx_tips[i])
        int_var = tk.DoubleVar(value=params["fx_int"][i])
        int_var.trace_add("write", lambda *_a, i=i, v=int_var: params["fx_int"].__setitem__(i, _safe(v, 1.0)))
        tk.Scale(root, from_=0, to=2, resolution=0.05, orient="horizontal", variable=int_var,
                 length=150, showvalue=True).grid(
            row=r, column=PANELS["right"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    master_var = tk.DoubleVar(value=params["master"])
    bind_param("master", master_var)
    add_slider("right", "Intensite globale", 0.0, 2.0, 0.1, master_var, length=150)
    sens_var = tk.DoubleVar(value=params["sensitivity"])
    bind_param("sensitivity", sens_var)
    add_slider("right", "Sensibilite kick", 0.25, 4.0, 0.05, sens_var, length=150,
               tooltip="Plus haut = kicks detectes plus facilement (aussi touches haut/bas dans la fenetre GL).")

    add_separator("right", "Analyse audio (effets)")
    audio_choices = gl.AudioController.input_choices() if not s.audio.synthetic else []
    if not audio_choices:
        tk.Label(root, text=s.audio.name, fg=a2w.GUI_MUTED_FG, wraplength=300, justify="left").grid(
            row=next_row("right"), column=6, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    else:
        labels = {label: idx for idx, label in audio_choices}
        current = next((label for idx, label in audio_choices if idx_matches(s, idx, label)), s.audio.name)
        audio_var = tk.StringVar(value=current)
        r = next_row("right")
        add_label("right", "Entree", r, "Entree sounddevice qui alimente les effets (kick, basses...). "
                                        "Independante de l'entree ffmpeg du fond.")
        menu = tk.OptionMenu(root, audio_var, *labels.keys())
        a2w.style_option_menu(menu)
        menu.grid(row=r, column=PANELS["right"][1], sticky="w", padx=ROW_PADX, pady=ROW_PADY)

        def on_audio_change(*_a) -> None:
            try:
                name = s.audio.switch(labels[audio_var.get()])
                s.status["msg"] = f"Analyse sur: {name}"
            except Exception as exc:
                s.status["msg"] = f"Entree refusee: {exc}"

        audio_var.trace_add("write", on_audio_change)

    add_separator("right", "Affichage")
    btn_frame = tk.Frame(root)
    btn_frame.grid(row=next_row("right"), column=6, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, cmd in (("Plein ecran", "fullscreen"), ("Barres debug", "hud")):
        tk.Button(btn_frame, text=text, command=lambda c=cmd: s.commands.put(c)).pack(side="left", padx=(0, 6))
    btn_frame2 = tk.Frame(root)
    btn_frame2.grid(row=next_row("right"), column=6, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, cmd in (("Recharger shaders", "reload"), ("Sauver reglages", "save")):
        tk.Button(btn_frame2, text=text, command=lambda c=cmd: s.commands.put(c)).pack(side="left", padx=(0, 6))

    meter = tk.Canvas(root, width=260, height=58, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    meter.grid(row=next_row("right"), column=6, columnspan=2, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 2))
    bars = {}
    for i, (name, color) in enumerate((("bass", "#ff5a4d"), ("mid", "#66ff73"), ("high", "#66b3ff"),
                                       ("beat", "#ffe633"))):
        y0 = 4 + i * 13
        meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        bars[name] = meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    # ---------------------------------------------------------------- statut + boucle
    status_label = tk.Label(root, text="", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=980)
    status_label.grid(row=max(rows.values()) + 1, column=0, columnspan=TOTAL_COLUMNS, sticky="we",
                      padx=ROW_PADX, pady=(SECTION_GAP, 10))

    def refresh() -> None:
        # Recopie les reglages que les touches de la fenetre GL ont pu changer.
        for i, v in enumerate(fx_on_vars):
            if int(params["fx_on"][i]) != v.get():
                v.set(int(params["fx_on"][i]))
        if abs(master_var.get() - params["master"]) > 1e-6:
            master_var.set(params["master"])
        if abs(sens_var.get() - params["sensitivity"]) > 1e-6:
            sens_var.set(params["sensitivity"])
        st = s.status
        parts = [f"{st.get('fps', 0):.0f} fps"]
        for key in ("live", "logo", "msg"):
            if st.get(key):
                parts.append(st[key])
        status_label.config(text="  |  ".join(parts))
        root.after(REFRESH_MS, refresh)

    def update_meter() -> None:
        state = s.audio.analyzer.latest()
        for name, item in bars.items():
            meter.coords(item, 52, meter.coords(item)[1], 52 + 200 * min(max(state[name], 0.0), 1.0),
                         meter.coords(item)[3])
        root.after(METER_MS, update_meter)

    def poll_finished() -> None:
        if s.finished_event.is_set():
            root.destroy()
            return
        root.after(100, poll_finished)

    def on_close() -> None:
        s.stop_event.set()                # le fil GL sort, positionne finished_event, poll_finished ferme
        root.after(3000, root.destroy)    # filet de securite si le fil GL ne repond plus

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    update_meter()
    poll_finished()
    if on_ready is not None:
        root.after(50, lambda: on_ready({
            "root": root, "live_vars": live_vars, "apply_live": apply_live, "logo_var": logo_var,
            "apply_logo_path": apply_logo_path, "color_var": color_var, "x_var": x_var,
            "fx_on_vars": fx_on_vars, "master_var": master_var, "close": on_close}))
    root.mainloop()


def _safe(var: tk.Variable, default: float) -> float:
    try:
        return float(var.get())
    except (ValueError, tk.TclError):
        return default


def idx_matches(s, index: int, label: str) -> bool:
    """Vrai si l'entree sounddevice `index` est celle actuellement ecoutee (par nom)."""
    return s.audio.name in label
