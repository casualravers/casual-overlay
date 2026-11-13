"""Analyse audio (effets) et affichage : entree sounddevice, plein ecran, barres de debug, rechargement des shaders, sauvegarde, diagnostic, mesures et statut.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from pathlib import Path
import audio2wave_gl as gl
from core import params_schema
from engine import diagnostics
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import choice_dialog          # import tardif : gl_gui importe ce module
    ROW_PADX = c.ROW_PADX
    ROW_PADY = c.ROW_PADY
    SECTION_GAP = c.SECTION_GAP
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_label = c.add_label
    add_separator = c.add_separator
    automation = c.automation
    bind_param = c.bind_param
    params = c.params
    root = c.root
    s = c.s
    send = c.send
    tab_aff = c.tab_aff
    zone_aff = c.zone_aff

    add_separator(zone_aff, "Analyse audio (effets)")
    audio_choices = gl.AudioController.input_choices(s.audio.name) if not s.audio.synthetic else []
    if not audio_choices:
        tk.Label(tab_aff, text=s.audio.name, fg=a2w.GUI_MUTED_FG, wraplength=300, justify="left").grid(
            row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    else:
        labels = {label: idx for idx, label in audio_choices}
        current = next((label for idx, label in audio_choices if s.audio.name in label), s.audio.name)
        audio_var = tk.StringVar(value=current)
        r = zone_aff.next_row()
        add_label(zone_aff, "Entree", r, "Entree sounddevice qui alimente les effets (kick, basses...). "
                                       "Independante de l'entree ffmpeg du fond.")
        menu = tk.OptionMenu(tab_aff, audio_var, *labels.keys())
        a2w.style_option_menu(menu)
        menu.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

        def on_audio_change(*_a) -> None:
            try:
                name = s.audio.switch(labels[audio_var.get()])
                s.status["msg"] = f"Analyse sur: {name}"
            except Exception as exc:
                s.status["msg"] = f"Entree refusee: {exc}"

        def refill_menu() -> None:
            """A l'ouverture du menu : relit les peripheriques (micro branche depuis le lancement). Pas pendant un enregistrement
            (la reinitialisation coupe le flux un instant)."""
            rec = getattr(s, "recording", None)
            if rec is not None and rec.active:
                return
            try:
                if not s.audio.refresh_devices():
                    return
                fresh = gl.AudioController.input_choices(s.audio.name)
            except Exception:
                return
            if not fresh:
                return
            labels.clear()
            labels.update({label: idx for idx, label in fresh})
            inner = menu["menu"]
            inner.delete(0, "end")
            for label in labels:
                inner.add_command(label=label, command=lambda v=label: audio_var.set(v))
            current_now = next((label for idx, label in fresh if s.audio.name in label), None)
            if current_now is not None and audio_var.get() != current_now:
                audio_var.trace_remove("write", trace_id[0])           # l'entree courante n'a pas change : pas de rebascule
                audio_var.set(current_now)
                trace_id[0] = audio_var.trace_add("write", on_audio_change)

        trace_id = [None]
        menu["menu"].configure(postcommand=refill_menu)

        trace_id[0] = audio_var.trace_add("write", on_audio_change)

    add_separator(zone_aff, "Synchro son / image")
    # Avance : automatique (estimee en direct, core.latency) par defaut, ou manuelle (curseur). Plus de calibration au telephone dans l'interface
    # (tools/calibrer_avance.py reste un outil de developpement).
    lead_mode = tk.StringVar(value="auto" if float(params.get("sync_lead_auto", 1.0)) >= 0.5 else "manuel")
    r = zone_aff.next_row()
    add_label(zone_aff, "Mode", r, "Auto : l'avance est estimee en continu (retard du son mesure + detection + image + affichage suppose ~25 ms) ; "
                                   "juste a quelques dizaines de ms pres. Manuelle : la valeur du curseur ci-dessous.")
    mode_row = tk.Frame(tab_aff)
    mode_row.grid(row=r, column=1, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    def on_lead_mode() -> None:
        params["sync_lead_auto"] = 1.0 if lead_mode.get() == "auto" else 0.0
        lead_gate(params["sync_lead_auto"] >= 0.5)

    for text, val in (("Auto", "auto"), ("Manuelle", "manuel")):
        tk.Radiobutton(mode_row, text=text, value=val, variable=lead_mode, command=on_lead_mode).pack(side="left", padx=(0, 10))
    lead_var = c.param_slider(zone_aff, "Avance", "sync_lead_ms", 0, 200, 5,
                   "Avance manuelle (ms), utilisee en mode Manuelle : sur un morceau regulier, les effets Â« sur kick Â» partent ce temps AVANT le kick detecte pour "
                   "que la lumiere tombe avec le son. 0 = on reagit a la detection. Sans pulsation calee (voyant ci-dessous) ou pendant une montee / cassure / drop, "
                   "rien n'est predit : la reaction reste a la detection.")
    # Ligne de l'estimation, hauteur FIXE (une ligne) : son texte change chaque seconde, rien ne doit bouger autour
    lead_label = tk.Label(tab_aff, text="Avance auto : estimee quand la projection est ouverte", fg=a2w.GUI_FG, anchor="w", justify="left", height=1)
    lead_label.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="we", padx=ROW_PADX, pady=(0, 2))
    lead_scale: dict = {}

    def lead_gate(auto: bool) -> None:
        """Curseur manuel grise en mode Auto (un seul configure, et seulement si l'etat change : piege de la valeur perimee d'un Scale)."""
        if "w" not in lead_scale:
            def walk(w):
                yield w
                for ch in w.winfo_children():
                    yield from walk(ch)
            lead_scale["w"] = next((w for w in walk(tab_aff) if w.winfo_class() == "Scale" and str(w.cget("variable")) == str(lead_var)), None)
        w = lead_scale["w"]
        if w is None:
            return
        want = "disabled" if auto else "normal"
        try:
            if str(w.cget("state")) != want:
                w.configure(state=want)
                lead_var.set(float(params.get("sync_lead_ms", 0.0)))
        except tk.TclError:
            lead_scale.pop("w", None)

    # Hauteur FIXE (2 lignes) : le texte du voyant change avec l'etat de la pulsation ; une ligne qui en prenait 2 ou 3 faisait bouger toute la section
    # (regle de l'interface : rien ne decale ce que l'utilisateur vise). Texte long : voir `explain.pulse_text` (une phrase, 2 lignes au plus a cette largeur).
    pulse_label = tk.Label(tab_aff, text="pulsation : -", fg=a2w.GUI_MUTED_FG, anchor="nw", justify="left", wraplength=360, height=2)
    pulse_label.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="we", padx=ROW_PADX, pady=(0, 2))
    lead_gate(lead_mode.get() == "auto")

    add_separator(zone_aff, "Affichage")
    frame = tk.Frame(tab_aff)                      # barres debug (H) et rechargement des shaders (R) : touches de la fenetre de rendu seulement
    frame.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, cmd in (("Plein ecran", "fullscreen"), ("Sauver reglages", "save")):
        tk.Button(frame, text=text, command=lambda c=cmd: send(c)).pack(side="left", padx=(0, 6))
    # Diagnostic est sur la meme ligne que Plein ecran / Sauver (une ligne par bouton rendait l'onglet bien trop haut)

    def diagnostic_report() -> str:
        """Rapport du diagnostic : verifications refaites maintenant, sauf OpenGL (resultat du demarrage : on n'ouvre pas un
        second contexte GL pendant que le rendu tourne)."""
        a = getattr(s, "args", None)
        startup = getattr(a, "diag_checks", None) or []
        checks = diagnostics.run_checks(Path(getattr(a, "a2w_dir", gl.DEFAULT_A2W_DIR)), bool(getattr(a, "synthetic", False)),
                                        True, opengl=False)
        gl_check = next((x for x in startup if x.name == "OpenGL"), None)
        if gl_check is not None:
            checks.insert(next((i for i, x in enumerate(checks) if x.name == "Micro"), len(checks)), gl_check)
        return diagnostics.format_report(checks)

    diag_btn = tk.Button(frame, text="Diagnostic",
                         command=lambda: choice_dialog(root, "Diagnostic", diagnostic_report(), ["Fermer"]))
    diag_btn.pack(side="left", padx=(0, 6))
    Tooltip(diag_btn, "Verifie ce PC : ffmpeg, OpenGL, micro, modules Python (glisser-deposer...), audio2wave, dossier des reglages.")

    auto_master_var = tk.IntVar(value=int(float(params["auto_master"]) >= 0.5))
    bind_param("auto_master", auto_master_var, float)
    frame = tk.Frame(tab_aff)
    frame.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    master_check = tk.Checkbutton(frame, text="Automations actives", variable=auto_master_var)
    master_check.pack(side="left")
    Tooltip(master_check, "Interrupteur general (touche T): decoche, toutes les valeurs restent ou elles sont. "
                          "Les cases '~' a cote de chaque curseur choisissent ce qui varie, le bouton a cote "
                          "ouvre l'editeur de courbe.")

    def reset_automation() -> None:
        params["_automation"] = gl.default_automation()
        automation.apply(params["_automation"])
        s.status["msg"] = "Automations: ambiance par defaut"

    tk.Button(frame, text="Ambiance par defaut", command=reset_automation).pack(side="left", padx=(8, 0))

    doodle_var = tk.IntVar(value=int(float(params.get("doodle_on", 0.0)) >= 0.5))
    bind_param("doodle_on", doodle_var, float)
    doodle_check = tk.Checkbutton(frame, text="Recuperer les medias", variable=doodle_var)         # meme ligne que les automations (gain de hauteur)
    doodle_check.pack(side="left", padx=(8, 0))
    Tooltip(doodle_check, "Recupere les doodles deposes sur le site (config : ~/.audio2wave/depot.json) et les affiche par-dessus la projection. "
                          "Seuls les fichiers apparus apres l'activation sont montres. Jamais enregistre dans un preset ni une scene.")

    qr_row = tk.Frame(tab_aff)                       # QR code permanent dans un coin de la projection (pour que le public rejoigne le site)
    qr_row.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    qr_var = tk.IntVar(value=int(float(params.get("qr_on", 0.0)) >= 0.5))
    qr_url_var = tk.StringVar(value=str(params.get("qr_url", "")))
    qr_corner_var = tk.StringVar(value=str(params.get("qr_corner", "bas droite")))
    bind_param("qr_on", qr_var, float)
    bind_param("qr_url", qr_url_var, lambda v: str(v).strip())
    bind_param("qr_corner", qr_corner_var, str)
    qr_check = tk.Checkbutton(qr_row, text="QR sur la projection", variable=qr_var)
    qr_check.pack(side="left")
    qr_entry = tk.Entry(qr_row, textvariable=qr_url_var, width=24)
    qr_entry.pack(side="left", padx=(6, 0))
    qr_menu = tk.OptionMenu(qr_row, qr_corner_var, *params_schema.BY_KEY["qr_corner"].choices)
    a2w.style_option_menu(qr_menu)
    qr_menu.pack(side="left", padx=(6, 0))
    Tooltip(qr_check, "Affiche en permanence un QR code dans un coin de la projection (par-dessus tout, droit, sans effet : un telephone doit pouvoir le lire). "
                      "Colle l'adresse du site a droite ; le menu choisit le coin. Sauve avec les reglages, jamais dans un preset ni une scene.")

    def qr_check_url(*_a) -> None:
        url = qr_url_var.get().strip()
        if qr_var.get() and not url:
            s.status["msg"] = "QR : colle l'adresse du site dans le champ"
        elif len(url.encode("utf-8")) > 213:
            s.status["msg"] = "QR : adresse trop longue (213 octets au plus), le code n'est pas affiche"

    qr_var.trace_add("write", qr_check_url)
    qr_url_var.trace_add("write", qr_check_url)

    remote_box = tk.Frame(tab_aff)                 # la ligne de la telecommande telephone (remplie par gui_remote)
    remote_box.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    meter_row = tk.Frame(tab_aff)                  # mesures a gauche, statut a droite : une seule ligne, de hauteur fixe (le texte du statut ne decale plus rien)
    meter_row.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 4))
    meter = tk.Canvas(meter_row, width=260, height=58, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    meter.pack(side="left")
    bars = {}
    for i, (name, color) in enumerate((("bass", "#ff5a4d"), ("mid", "#66ff73"), ("high", "#66b3ff"),
                                       ("beat", "#ffe633"))):
        y0 = 4 + i * 13
        meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        bars[name] = meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    status_label = tk.Label(meter_row, text="", fg=a2w.GUI_MUTED_FG, anchor="nw", justify="left", wraplength=230, width=32, height=5)
    status_label.pack(side="left", padx=(10, 0), anchor="n")

    import types
    return types.SimpleNamespace(pulse_label=pulse_label, lead_label=lead_label, lead_gate=lead_gate, lead_mode=lead_mode, lead_var=lead_var, remote_box=remote_box, auto_master_var=auto_master_var, bars=bars, diag_btn=diag_btn, diagnostic_report=diagnostic_report, meter=meter, status_label=status_label)
