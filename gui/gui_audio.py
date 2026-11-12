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
    audio_choices = gl.AudioController.input_choices() if not s.audio.synthetic else []
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

        audio_var.trace_add("write", on_audio_change)

    add_separator(zone_aff, "Synchro son / image")
    lead_var = c.param_slider(zone_aff, "Avance", "sync_lead_ms", 0, 200, 5,
                   "Avance (ms) : sur un morceau regulier, les effets « sur kick » partent ce temps AVANT le kick detecte pour que la lumiere tombe avec le son "
                   "(la chaine micro, analyse, rendu, ecran et videoprojecteur retarde la lumiere). 0 = on reagit a la detection, comme avant. "
                   "La valeur se mesure avec tools/calibrer_avance.py (telephone en ralenti) ; propre a la salle et au materiel. "
                   "Sans pulsation calee (voyant ci-dessous) ou pendant une montee / cassure / drop, rien n'est predit : la reaction reste a la detection.")
    pulse_label = tk.Label(tab_aff, text="pulsation : -", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=320)
    pulse_label.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="we", padx=ROW_PADX, pady=(0, 2))

    calib_state: dict = {"top": None}

    def load_calibrator():
        """tools/calibrer_avance.py, charge a la demande (la fenetre n'en depend pas au demarrage ; il est dans le dossier portable)."""
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "tools" / "calibrer_avance.py"
        spec = importlib.util.spec_from_file_location("calibrer_avance", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def open_calibration(ca=None) -> "tk.Toplevel | None":
        """Fenetre « Calibrer l'avance » : lance l'emission (clic au haut-parleur, flash a la detection) sur l'ecran du videoprojecteur, lit la video du
        telephone et propose l'avance mesuree. `ca` : module de calibration (injectable par les tests)."""
        import threading
        from tkinter import filedialog
        old = calib_state.get("top")
        if old is not None and old.winfo_exists():
            old.lift()
            return old
        ca = ca or load_calibrator()
        top = tk.Toplevel(root)
        calib_state["top"] = top
        top.title("Calibrer l'avance")
        top.configure(bg=a2w.GUI_PANEL_BG)
        top.transient(root)
        tk.Label(top, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, justify="left", anchor="w", wraplength=440, text=(
            "1. Mets les enceintes et le videoprojecteur comme en soiree.\n"
            "2. Indique le coin haut gauche de l'ecran du videoprojecteur (x, y), puis lance l'emission : le programme joue un clic par seconde et "
            "allume l'ecran en blanc quand il l'entend (Echap pour arreter).\n"
            "3. Filme avec ton telephone, en ralenti (120 ou 240 images/s), l'ecran ET le haut-parleur dans le meme plan, une vingtaine de secondes.\n"
            "4. Clique « Analyser la video... » : l'avance mesuree est proposee, puis appliquee au curseur.")).pack(padx=10, pady=(10, 6), anchor="w")
        row = tk.Frame(top, bg=a2w.GUI_PANEL_BG)
        row.pack(padx=10, anchor="w")
        xv, yv = tk.StringVar(value=str(root.winfo_screenwidth())), tk.StringVar(value="0")
        for text, var in (("x", xv), ("y", yv)):
            tk.Label(row, text=text, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left")
            tk.Entry(row, textvariable=var, width=6).pack(side="left", padx=(2, 8))

        def emit() -> None:
            import subprocess
            try:
                cmd = ca.emission_command(int(xv.get() or 0), int(yv.get() or 0))
                subprocess.Popen(cmd)
                result_var.set("Emission lancee (fenetre blanche / noire en plein ecran). Filme, puis reviens ici.")
            except Exception as exc:
                result_var.set(f"Emission impossible : {exc}")

        found: dict = {"ms": None}
        result_var = tk.StringVar(value="")
        apply_btn = tk.Button(top, text="Appliquer l'avance", state="disabled")

        def analyse(path: str | None = None) -> None:
            path = path or filedialog.askopenfilename(parent=top, title="Video du telephone", filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi"), ("Tous", "*.*")])
            if not path:
                return
            result_var.set("Analyse en cours...")

            def work() -> None:
                try:
                    ms, text = ca.advice(ca.analyse_video(path, log=lambda *_a: None))
                except Exception as exc:
                    ms, text = None, f"Analyse impossible : {exc}"
                try:
                    root.after(0, lambda: done(ms, text))
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        def done(ms, text) -> None:
            if not top.winfo_exists():
                return
            found["ms"] = ms
            result_var.set(text)
            apply_btn.configure(state="normal" if ms is not None else "disabled", text=f"Appliquer l'avance ({ms} ms)" if ms is not None else "Appliquer l'avance")

        def apply() -> None:
            if found["ms"] is not None:
                lead_var.set(found["ms"])
                result_var.set(result_var.get() + f"\nAvance reglee a {found['ms']} ms.")

        apply_btn.configure(command=apply)
        btns = tk.Frame(top, bg=a2w.GUI_PANEL_BG)
        btns.pack(padx=10, pady=8, anchor="w")
        emit_btn = tk.Button(btns, text="Lancer l'emission", command=emit)
        analyse_btn = tk.Button(btns, text="Analyser la video...", command=analyse)
        emit_btn.pack(side="left", padx=(0, 6))
        analyse_btn.pack(side="left", padx=(0, 6))
        apply_btn.pack(in_=btns, side="left", padx=(0, 6))
        tk.Label(top, textvariable=result_var, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, justify="left", anchor="w", wraplength=440).pack(padx=10, pady=(0, 10), anchor="w")
        top.calib = {"emit": emit, "analyse": analyse, "apply": apply, "apply_btn": apply_btn, "result": result_var, "found": found, "x": xv, "y": yv, "done": done}
        return top

    calib_btn = tk.Button(tab_aff, text="Calibrer...", command=open_calibration)
    calib_btn.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=(0, 2))
    Tooltip(calib_btn, "Mesure le retard reel de TA installation (micro, ordinateur, ecran, videoprojecteur) avec la camera d'un telephone en ralenti, "
                       "puis regle l'avance. A refaire si le materiel ou la salle change.")

    add_separator(zone_aff, "Affichage")
    frame = tk.Frame(tab_aff)                      # barres debug (H) et rechargement des shaders (R) : touches de la fenetre de rendu seulement
    frame.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, cmd in (("Plein ecran", "fullscreen"), ("Sauver reglages", "save")):
        tk.Button(frame, text=text, command=lambda c=cmd: send(c)).pack(side="left", padx=(0, 6))

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

    diag_btn = tk.Button(tab_aff, text="Diagnostic",
                         command=lambda: choice_dialog(root, "Diagnostic", diagnostic_report(), ["Fermer"]))
    diag_btn.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
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
    doodle_check = tk.Checkbutton(tab_aff, text="Recuperer les medias", variable=doodle_var)
    doodle_check.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
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

    meter = tk.Canvas(tab_aff, width=260, height=58, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    meter.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 2))
    bars = {}
    for i, (name, color) in enumerate((("bass", "#ff5a4d"), ("mid", "#66ff73"), ("high", "#66b3ff"),
                                       ("beat", "#ffe633"))):
        y0 = 4 + i * 13
        meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        bars[name] = meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    status_label = tk.Label(tab_aff, text="", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=320)
    status_label.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="we", padx=ROW_PADX, pady=(4, 6))

    import types
    return types.SimpleNamespace(pulse_label=pulse_label, calib_btn=calib_btn, open_calibration=open_calibration, lead_var=lead_var, remote_box=remote_box, auto_master_var=auto_master_var, bars=bars, diag_btn=diag_btn, diagnostic_report=diagnostic_report, meter=meter, status_label=status_label)
