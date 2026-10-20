"""Onglet Logo : source (image, texte, video anime), position, opacite, contour, reaction a l'audio.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import colorchooser
from tkinter import filedialog
import audio2wave_gl as gl
import time
import tkinter as tk
from core import schedule


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import HEX_DIGITS, Zone          # import tardif : gl_gui importe ce module
    ROW_PADX = c.ROW_PADX
    ROW_PADY = c.ROW_PADY
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_label = c.add_label
    add_section_title = c.add_section_title
    add_separator = c.add_separator
    add_slider = c.add_slider
    bind_param = c.bind_param
    color_row = c.color_row
    param_radio = c.param_radio
    param_slider = c.param_slider
    params = c.params
    tab_logo = c.tab_logo
    ui_every = c.ui_every
    zone_aura = c.zone_aura
    zone_logo = c.zone_logo

    add_section_title(zone_logo, "Logo / texte")
    source_var = tk.StringVar(value=params["logo_source"])
    r = zone_logo.next_row()
    add_label(zone_logo, "Source", r, "Image PNG, texte que tu tapes, ou video animee (logo detoure): tout est "
                                   "traite comme un logo (position, opacite, pulsation, tremblement, contour).")
    source_frame = tk.Frame(tab_logo)
    source_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, value in (("Image", "image"), ("Texte", "text"), ("Video", "video")):
        tk.Radiobutton(source_frame, text=text, variable=source_var, value=value).pack(side="left")
    bind_param("logo_source", source_var, str)

    # Les trois blocs occupent la meme cellule; un seul est affiche.
    image_box = tk.Frame(tab_logo)
    text_box = tk.Frame(tab_logo)
    video_box = tk.Frame(tab_logo)
    source_row = zone_logo.next_row()
    for box in (image_box, text_box, video_box):
        box.grid(row=source_row, column=0, columnspan=2, sticky="nw")
    iz = Zone(image_box)
    tz = Zone(text_box)
    vz = Zone(video_box)

    # ---- source image
    r = iz.next_row()
    add_label(iz, "Fichier", r, "PNG avec canal alpha. Vide = aucun logo.")
    file_frame = tk.Frame(image_box)
    file_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    logo_var = tk.StringVar(value=params["logo_path"])
    logo_entry = tk.Entry(file_frame, textvariable=logo_var, width=13)
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

    browse_button = tk.Button(file_frame, text="...", command=browse_logo, padx=6)
    browse_button.pack(side="left", padx=(6, 0))
    Tooltip(browse_button, "Parcourir...")
    tk.Button(file_frame, text="Aucun", command=lambda: (logo_var.set(""), apply_logo_path())).pack(
        side="left", padx=(4, 0))
    scale_var = param_slider(iz, "Taille", "logo_scale", 0.05, 0.95, 0.01,
                             "Cote du carre englobant, en fraction de la hauteur de l'image.")

    # ---- source texte
    r = tz.next_row()
    add_label(tz, "Texte", r, "Tape ton texte (Entree = nouvelle ligne). Il s'affiche en direct.")
    text_widget = tk.Text(text_box, width=22, height=3, wrap="word", undo=True, bg=a2w.GUI_PANEL_BG,
                          fg=a2w.GUI_FG, insertbackground=a2w.GUI_FG, relief="flat", highlightthickness=1,
                          highlightbackground=a2w.GUI_PANEL_BG, highlightcolor=a2w.GUI_ACCENT)
    text_widget.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    text_widget.insert("1.0", params["text_content"])

    def apply_text(_evt=None) -> None:
        params["text_content"] = text_widget.get("1.0", "end-1c")

    text_widget.bind("<KeyRelease>", apply_text)     # couvre aussi le collage (Ctrl+V)
    text_widget.bind("<FocusOut>", apply_text)

    # ---- planning : le texte change tout seul a l'heure de chaque passage
    r = tz.next_row()
    add_label(tz, "Planning", r, "Une ligne par passage : « 22:30 NOM DU DJ » (\\n dans le texte = retour a la ligne). Coche la case : a l'heure dite, la source "
                                 "passe en Texte et le texte devient celui du passage. Les heures avant 18 h comptent apres minuit.")
    sched_frame = tk.Frame(text_box)
    sched_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    sched_var = tk.IntVar(value=int(round(float(params.get("sched_on", 0.0)))))
    bind_param("sched_on", sched_var, float)
    tk.Checkbutton(sched_frame, text="Suivre le planning", variable=sched_var).pack(anchor="w")
    sched_text = tk.Text(sched_frame, width=22, height=4, wrap="none", undo=True, bg=a2w.GUI_PANEL_BG,
                         fg=a2w.GUI_FG, insertbackground=a2w.GUI_FG, relief="flat", highlightthickness=1,
                         highlightbackground=a2w.GUI_PANEL_BG, highlightcolor=a2w.GUI_ACCENT)
    sched_text.pack(anchor="w", pady=(3, 0))
    sched_text.insert("1.0", str(params.get("sched_lines", "")))
    sched_info = tk.Label(sched_frame, text="", anchor="w", justify="left", fg=a2w.GUI_MUTED_FG, wraplength=220)
    sched_info.pack(anchor="w", pady=(2, 0))
    last_planned = {"text": None}

    def apply_sched(_evt=None) -> None:
        params["sched_lines"] = sched_text.get("1.0", "end-1c")
        last_planned["text"] = None                  # le planning a change : le passage en cours est repose au prochain tour

    sched_text.bind("<KeyRelease>", apply_sched)
    sched_text.bind("<FocusOut>", apply_sched)

    def sched_tick(now=None) -> str | None:
        """Pose le texte du passage en cours quand il change (une fois), et dit ou en est le planning. Rend le texte pose, sinon None."""
        entries = schedule.parse(params.get("sched_lines", ""))
        lt = time.localtime() if now is None else now
        minute = lt.tm_hour * 60 + lt.tm_min
        cur = schedule.current(entries, minute)
        nxt = schedule.upcoming(entries, minute)
        sched_info.configure(text=(f"maintenant : {cur.splitlines()[0]}" if cur else "pas encore de passage")
                             + (f"  |  {schedule.clock(nxt[0])} : {nxt[1].splitlines()[0]}" if nxt else "") if entries else "aucun passage valide")
        if float(params.get("sched_on", 0.0)) < 0.5 or cur is None or cur == last_planned["text"]:
            return None
        last_planned["text"] = cur
        source_var.set("text")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", cur)
        apply_text()
        return cur

    ui_every("planning", 5000, sched_tick, 1000)

    fonts = ["auto"] + list(gl.available_fonts())
    if params["text_font"] not in fonts:
        fonts.append(params["text_font"])            # police personnalisee (chemin) sauvee precedemment
    font_var = tk.StringVar(value=params["text_font"])
    bind_param("text_font", font_var, str)
    r = tz.next_row()
    add_label(tz, "Police", r, "Polices Windows courantes (auto = la premiere disponible), ou un fichier "
                               ".ttf/.otf avec Parcourir.")
    font_frame = tk.Frame(text_box)
    font_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    font_menu = tk.OptionMenu(font_frame, font_var, *fonts)
    a2w.style_option_menu(font_menu)
    font_menu.pack(side="left")

    def browse_font() -> None:
        chosen = filedialog.askopenfilename(
            title="Choisir une police", initialdir=str(gl.font_dirs()[0]) if gl.font_dirs()[0].is_dir() else ".",
            filetypes=[("Polices", "*.ttf *.otf"), ("Tous les fichiers", "*.*")])
        if chosen:
            font_menu["menu"].add_command(label=chosen, command=lambda c=chosen: font_var.set(c))
            font_var.set(chosen)

    font_browse = tk.Button(font_frame, text="...", command=browse_font, padx=6)
    font_browse.pack(side="left", padx=(6, 0))
    Tooltip(font_browse, "Parcourir: choisir un fichier .ttf/.otf")
    color_row(tz, "Couleur du texte", "text_color")
    param_radio(tz, "Alignement", "text_align", (("gauche", "left"), ("centre", "center"), ("droite", "right")),
                tooltip="Alignement des lignes entre elles (texte sur plusieurs lignes).")
    param_slider(tz, "Taille du texte", "text_scale", 0.03, 0.6, 0.01,
                 "Hauteur du bloc de texte, en fraction de la hauteur de l'image (marges comprises).")

    # ---- source video (logo anime)
    r = vz.next_row()
    add_label(vz, "Fichier", r, "Video ou GIF lu en boucle. Pour un logo detoure: WebM VP9 avec alpha, "
                                "MOV ProRes 4444, GIF, APNG ou WebP anime. Un MP4 n'a pas d'alpha: utilise "
                                "le detourage par couleur ci-dessous.")
    vfile_frame = tk.Frame(video_box)
    vfile_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    video_var = tk.StringVar(value=params["logo_video"])
    video_entry = tk.Entry(vfile_frame, textvariable=video_var, width=13)
    video_entry.pack(side="left")

    def apply_video_path(_evt=None) -> None:
        params["logo_video"] = video_var.get().strip()

    video_entry.bind("<Return>", apply_video_path)
    video_entry.bind("<FocusOut>", apply_video_path)

    def browse_video() -> None:
        initial = Path(video_var.get()).parent if video_var.get() else gl.SCRIPT_DIR / "asset"
        chosen = filedialog.askopenfilename(
            title="Choisir une video", initialdir=str(initial if initial.is_dir() else gl.SCRIPT_DIR),
            filetypes=[("Videos et animations", gl.LOGO_VIDEO_EXTENSIONS), ("Tous les fichiers", "*.*")])
        if chosen:
            video_var.set(chosen)
            apply_video_path()

    vbrowse = tk.Button(vfile_frame, text="...", command=browse_video, padx=6)
    vbrowse.pack(side="left", padx=(6, 0))
    Tooltip(vbrowse, "Parcourir...")
    tk.Button(vfile_frame, text="Aucune", command=lambda: (video_var.set(""), apply_video_path())).pack(
        side="left", padx=(4, 0))

    r = vz.next_row()
    add_label(vz, "Detourer", r, "Pour un fichier SANS canal alpha (MP4...): rend transparente cette couleur "
                                  "(fond vert = #00ff00). Vide = aucun detourage. Relance le decodage a chaque "
                                  "changement valide.")
    key_frame = tk.Frame(video_box)
    key_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    key_var = tk.StringVar(value=params["logo_key"])
    key_swatch = tk.Label(key_frame, width=3, bg=params["logo_key"] or a2w.GUI_PANEL_BG)
    tk.Entry(key_frame, textvariable=key_var, width=8).pack(side="left")
    key_swatch.pack(side="left", padx=(8, 0))

    def on_key_write(*_a) -> None:
        text = key_var.get().strip().lstrip("#")
        if not text:
            params["logo_key"] = ""
            key_swatch.config(bg=a2w.GUI_PANEL_BG)
        elif len(text) == 6 and all(c in HEX_DIGITS for c in text):
            params["logo_key"] = "#" + text.lower()
            key_swatch.config(bg=params["logo_key"])

    key_var.trace_add("write", on_key_write)

    def pick_key() -> None:
        chosen = colorchooser.askcolor(color=params["logo_key"] or "#00ff00", title="Couleur a detourer")
        if chosen and chosen[1]:
            key_var.set(chosen[1])

    key_pick = tk.Button(key_frame, text="...", command=pick_key, padx=6)
    key_pick.pack(side="left", padx=(6, 0))
    Tooltip(key_pick, "Choisir la couleur a detourer")
    tk.Button(key_frame, text="Vert", command=lambda: key_var.set("#00ff00")).pack(side="left", padx=(4, 0))
    tk.Button(key_frame, text="Aucun", command=lambda: key_var.set("")).pack(side="left", padx=(4, 0))
    # Meme variable que le curseur de l'image (un seul reglage logo_scale, une seule automation).
    add_slider(vz, "Taille", 0.05, 0.95, 0.01, scale_var,
               "Cote du carre englobant, en fraction de la hauteur de l'image.")

    def show_source(*_a) -> None:
        shown = source_var.get()
        for name, box in (("image", image_box), ("text", text_box), ("video", video_box)):
            if name == shown:
                box.grid()
            else:
                box.grid_remove()

    source_var.trace_add("write", show_source)
    show_source()

    add_separator(zone_logo, "Position")
    x_var = param_slider(zone_logo, "Position X", "logo_x", 0.0, 1.0, 0.01, "Centre, 0 = gauche, 1 = droite.")
    y_var = param_slider(zone_logo, "Position Y", "logo_y", 0.0, 1.0, 0.01, "Centre, 0 = haut, 1 = bas.")
    param_slider(zone_logo, "Opacite", "logo_opacity", 0.0, 1.0, 0.05)
    tk.Button(tab_logo, text="Centrer", command=lambda: (x_var.set(0.5), y_var.set(0.5))).grid(
        row=zone_logo.next_row(), column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    add_separator(zone_aura, "Reaction a l'audio")
    param_slider(zone_aura, "Pulsation", "logo_pulse", 0.0, 0.5, 0.01,
                 "Zoom du logo a chaque kick (0,12 = +12 %). Jamais coupe hors cadre.")
    param_slider(zone_aura, "Tremblement", "logo_jitter", 0.0, 0.03, 0.001, "Vibration du logo proportionnelle aux aigus.")
    param_slider(zone_aura, "Contour lumineux", "logo_glow", 0.0, 3.0, 0.1,
                 "Intensite du halo autour du logo, module par les basses.")
    param_slider(zone_aura, "Rayon du contour", "logo_glow_radius", 0.0, 3.0, 0.1)

    import types
    return types.SimpleNamespace(apply_logo_path=apply_logo_path, apply_text=apply_text, apply_video_path=apply_video_path, font_var=font_var, image_box=image_box, key_var=key_var, logo_var=logo_var, source_var=source_var, text_box=text_box, text_widget=text_widget, video_var=video_var, x_var=x_var, sched_tick=sched_tick, sched_var=sched_var, sched_text=sched_text, apply_sched=apply_sched)
