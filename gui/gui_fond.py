"""Partie fond de la partie LIVE : choix audio2wave / fond genere / fond video, presets de fond, reglages du motif.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
import audio2wave_gl as gl
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import MODE_BANNERS, OVERLAY_COLOR, Zone, style_band          # import tardif : gl_gui importe ce module
    ROW_PADX = c.ROW_PADX
    ROW_PADY = c.ROW_PADY
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_label = c.add_label
    add_section_title = c.add_section_title
    banner_live = c.banner_live
    bg_state = c.bg_state
    bind_param = c.bind_param
    canvas = c.canvas
    color_row = c.color_row
    fit = c.fit
    host_item = c.host_item
    make_preset_bar = c.make_preset_bar
    mode_ref = c.mode_ref
    overlay_presets = c.overlay_presets
    param_radio = c.param_radio
    param_slider = c.param_slider
    params = c.params
    pattern_holder = c.pattern_holder
    pattern_item = c.pattern_item
    tab_fond = c.tab_fond
    zone_f = c.zone_f

    load_overlay_preset, update_overlay_preset = overlay_presets["load"], overlay_presets["update"]
    save_overlay_preset = overlay_presets["save"]

    # ---- colonne A : FOND. Un choix en haut ; dessous, UNE des deux faces : le panneau d'audio2wave, ou le fond genere.
    bg_var = tk.StringVar(value=params["bg_mode"])
    r = zone_f.next_row()
    bg_title = tk.Label(tab_fond, text="FOND")
    bg_title.grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    style_band(bg_title, a2w, color=OVERLAY_COLOR)
    Tooltip(bg_title, "Audio2wave = le visuel du mode choisi dessous (Live, Snap ou Ridge). Fond genere = degrades et damier "
                      "calcules par la carte graphique. Video = un fichier video brut lu en boucle plein cadre. Touche B : "
                      "passe de l'un a l'autre. Un seul des trois est affiche a la fois.")
    bg_frame = tk.Frame(tab_fond)
    bg_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, value in (("Audio2wave", "live"), ("Fond genere", "pattern"), ("Video", "video")):
        tk.Radiobutton(bg_frame, text=text, variable=bg_var, value=value).pack(side="left")
    bind_param("bg_mode", bg_var, str)

    bg_preset_bar = tk.Frame(pattern_holder)
    bg_preset_bar.pack(fill="x")
    bg_presets = make_preset_bar(bg_preset_bar, "Presets fond", gl.BACKGROUND_BOOK, gl.capture_background,
                                 gl.background_preset_values,
                                 "Un preset de fond = la source et le fond genere (ni le logo, ni les effets).")

    pattern_box = tk.Frame(pattern_holder)
    pattern_box.pack(fill="x")
    pattern_box.columnconfigure(2, weight=1)         # le bandeau "Fond genere" prend toute la largeur
    pz = Zone(pattern_box)
    add_section_title(pz, "Fond genere")
    param_radio(pz, "Palette", "bg_palette", (("Arc-en-ciel", "classic"), ("Duo", "duo"), ("Banc de test", "test")),
                tooltip="Arc-en-ciel: le visuel d'origine (canaux R/V/B qui defilent). "
                        "Duo: degrade lisse entre deux couleurs. "
                        "Banc de test: copie EXACTE du motif de test (carreaux de 80 px, 30 images/s) ; "
                        "les autres reglages du motif ne s'y appliquent pas.")
    r = pz.next_row()
    add_label(pz, "Couleurs 1 / 2", r, "Les deux couleurs du degrade de la palette Duo.")
    pair = tk.Frame(pattern_box)
    pair.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    bg_color1_var = color_row(pz, "Couleur 1", "bg_color1", into=pair)
    bg_color2_var = color_row(pz, "Couleur 2", "bg_color2", into=pair)
    param_slider(pz, "Angle du degrade", "bg_angle", 0, 360, 5, "Direction du degrade de la palette Duo, en degres.")
    param_slider(pz, "Teinte", "bg_hue", 0.0, 1.0, 0.01, "Rotation des couleurs (0 = d'origine).")
    param_slider(pz, "Vitesse", "bg_speed", 0.0, 4.0, 0.05, "Vitesse de defilement des degrades.")
    param_slider(pz, "Taille des carreaux", "bg_tile", 20, 300, 5,
                 "Cote d'un carreau du damier, en pixels pour une image de 720 px de haut.")
    param_slider(pz, "Contraste du damier", "bg_checker", 0.0, 1.0, 0.02)
    param_slider(pz, "Cadence du damier", "bg_flip", 0.0, 8.0, 0.25, "Nombre de bascules du damier par seconde.")
    param_slider(pz, "Reaction a l'audio", "bg_react", 0.0, 2.0, 0.1,
                 "Flash et bascule du damier a chaque kick, defilement accelere par les basses. 0 = aucune.")

    # ---- fond VIDEO : un fichier brut lu en boucle plein cadre (le decodage est dans le fil GL : BgVideoManager)
    bgvid_box = tk.Frame(pattern_holder)
    bgvid_box.columnconfigure(2, weight=1)
    bz = Zone(bgvid_box)
    add_section_title(bz, "Fond video")
    r = bz.next_row()
    add_label(bz, "Fichier", r, "Video ou animation lue en boucle, plein cadre, a sa cadence. Formats : mp4, mov, mkv, webm, "
                                "avi, gif... Tu peux aussi glisser un fichier sur ce bloc.")
    bgv_frame = tk.Frame(bgvid_box)
    bgv_frame.grid(row=r, column=1, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    bgv_var = tk.StringVar(value=params["bg_video"])
    bgv_entry = tk.Entry(bgv_frame, textvariable=bgv_var, width=26)
    bgv_entry.pack(side="left")

    def apply_bgv(_evt=None) -> None:
        params["bg_video"] = bgv_var.get().strip()

    bgv_entry.bind("<Return>", apply_bgv)
    bgv_entry.bind("<FocusOut>", apply_bgv)

    def browse_bgv() -> None:
        initial = Path(bgv_var.get()).parent if bgv_var.get() else gl.SCRIPT_DIR / "asset"
        chosen = filedialog.askopenfilename(
            title="Choisir la video du fond", initialdir=str(initial if initial.is_dir() else gl.SCRIPT_DIR),
            filetypes=[("Videos et animations", gl.LOGO_VIDEO_EXTENSIONS + " *.m4v"), ("Tous les fichiers", "*.*")])
        if chosen:
            bgv_var.set(chosen)
            apply_bgv()

    bgv_browse = tk.Button(bgv_frame, text="...", command=browse_bgv, padx=6)
    bgv_browse.pack(side="left", padx=(6, 0))
    Tooltip(bgv_browse, "Parcourir...")
    tk.Button(bgv_frame, text="Aucune", command=lambda: (bgv_var.set(""), apply_bgv())).pack(side="left", padx=(4, 0))
    param_radio(bz, "Cadrage", "bg_video_fit", (("Remplir (recadre)", "cover"), ("Etirer", "stretch")),
                tooltip="Remplir : la video couvre tout le cadre, le debordement est recadre (le ratio est garde). "
                        "Etirer : la video est etiree aux proportions du rendu.")
    bgv_status = tk.StringVar(value="")
    r = bz.next_row()
    tk.Label(bgvid_box, textvariable=bgv_status, anchor="w", justify="left", wraplength=360, fg=a2w.GUI_MUTED_FG).grid(
        row=r, column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    def show_bg(*_a) -> None:
        """Affiche le panneau d'audio2wave, le fond genere OU le fond video : un seul des trois (fenetres du meme canvas)."""
        bg_state["mode"] = bg_var.get() if bg_var.get() in ("pattern", "video") else "live"
        own = bg_state["mode"] != "live"
        canvas.itemconfigure(host_item, state="hidden" if own else "normal")
        canvas.itemconfigure(pattern_item, state="normal" if own else "hidden")
        pattern_box.pack_forget()
        bgvid_box.pack_forget()
        if bg_state["mode"] == "pattern":
            pattern_box.pack(fill="x", after=bg_preset_bar)
        elif bg_state["mode"] == "video":
            bgvid_box.pack(fill="x", after=bg_preset_bar)
        canvas.yview_moveto(0)
        banner_live.config(text={"pattern": "LIVE  -  fond genere (casual-overlay)",
                                 "video": "LIVE  -  fond video (casual-overlay)"}.get(bg_state["mode"], MODE_BANNERS[mode_ref["v"]]))
        fit()

    bg_var.trace_add("write", show_bg)
    show_bg()
    tab_fond.update_idletasks()

    import types
    return types.SimpleNamespace(apply_bgv=apply_bgv, bg_color1_var=bg_color1_var, bg_color2_var=bg_color2_var, bg_presets=bg_presets, bg_var=bg_var, bgv_status=bgv_status, bgv_var=bgv_var, bgvid_box=bgvid_box, load_overlay_preset=load_overlay_preset, pattern_box=pattern_box, save_overlay_preset=save_overlay_preset, update_overlay_preset=update_overlay_preset)
