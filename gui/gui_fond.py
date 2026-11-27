"""Partie fond de la partie LIVE : choix audio2wave / fond genere / fond video, presets de fond, reglages du motif.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

import time
from pathlib import Path
from tkinter import filedialog
import audio2wave_gl as gl
import tkinter as tk

import skin
import theme as T
from core import reaction, sto_fields

MASK_PREVIEW = (32, 18)                                  # vignette du masque (#178) : echantillons, agrandis MASK_ZOOM fois (96 x 54 pixels)
MASK_ZOOM = 3

ICON_ROOT = Path(__file__).resolve().parent.parent      # racine du depot ou du dossier portable (asset/ y est copie)
STO_MENU_PX = 168                                        # largeur fixe de la liste « Champ » avec vignettes (pixels)
STO_MENU_CHARS = 14                                      # sans vignettes (caracteres)


def _sto_icon(w: tk.Misc, value: str):
    """Vignette d'un champ du stochastique (PhotoImage gardee par la racine Tk), ou None si le fichier manque ou est illisible."""
    try:
        from PIL import Image
        return skin.photo(w, Image.open(ICON_ROOT / sto_fields.ICON_DIR / f"{value}.png").convert("RGB"))
    except Exception:          # noqa: BLE001 : dossier absent, Pillow absent, fichier abime -> texte seul
        return None


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

    def sto_field_menu(z, parent) -> tk.Menubutton:
        """Liste « Champ » du stochastique (#229) : une entree par champ, avec sa vignette (asset/sto_fields, tools/make_sto_icons.py). Largeur
        fixe : rien ne bouge quand le choix change. Sans les vignettes (dossier absent) : la meme liste, en texte seul."""
        r = z.next_row()
        add_label(z, "Champ", r, "Ce que les points dessinent : " + " ; ".join(f"{lab} = {desc}" for _v, lab, _i, desc in sto_fields.FIELDS) + ".")
        var = tk.StringVar(value=params["bg_sto_field"])
        bind_param("bg_sto_field", var, str)
        icons = {value: _sto_icon(parent, value) for value in sto_fields.VALUES}
        with_icons = all(icons.values())
        mb = tk.Menubutton(parent, relief="flat", borderwidth=0, highlightthickness=0, anchor="w", compound="left", padx=6, pady=2,
                           indicatoron=0, cursor="hand2", bg=T.PANEL, fg=T.FG, activebackground=T.CARD, activeforeground=T.FG,
                           width=STO_MENU_PX if with_icons else STO_MENU_CHARS)
        mb._noskin = True                                  # dessinee ici (le skin en ferait un champ de liste sans image)
        menu = tk.Menu(mb, tearoff=0)
        for value, lab, _i, _d in sto_fields.FIELDS:
            extra = {"image": icons[value], "compound": "left"} if with_icons else {}
            menu.add_radiobutton(label=f"  {lab}", value=value, variable=var, **extra)
        mb.configure(menu=menu)

        def show(*_a) -> None:
            value = var.get()
            mb.configure(text=f"  {sto_fields.label(value)}  ▾", image=(icons.get(value) or "") if with_icons else "")
        var.trace_add("write", show)
        show()
        mb.grid(row=r, column=z.ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        z.parent.sto_field_menu = mb                      # sonde des tests
        return mb

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
                                 "Un preset de fond = la source et le fond genere (ni le logo, ni les effets).", kind="fond")

    pattern_box = tk.Frame(pattern_holder)
    pattern_box.pack(fill="x")
    pattern_box.columnconfigure(2, weight=1)         # le bandeau "Fond genere" prend toute la largeur
    pz = Zone(pattern_box)
    add_section_title(pz, "Fond genere")
    palette_var = param_radio(pz, "Palette", "bg_palette",
                              (("Arc-en-ciel", "classic"), ("Duo", "duo"), ("Banc de test", "test"), ("Stochastique", "stochastique"),
                               ("Labyrinthe", "labyrinthe")),
                              tooltip="Arc-en-ciel: le visuel d'origine (canaux R/V/B qui defilent). "
                                      "Duo: degrade lisse entre deux couleurs. "
                                      "Banc de test: copie EXACTE du motif de test (carreaux de 80 px, 30 images/s) ; "
                                      "les autres reglages du motif ne s'y appliquent pas. "
                                      "Stochastique: des points (couleur 1) sur un fond (couleur 2) dont la densite dessine un champ qui bouge. "
                                      "Labyrinthe: une reaction-diffusion (motif de Turing) : taches qui se repoussent et se soudent en "
                                      "lignes sinueuses, en trois couches colorees decalees ; elle vit et se reorganise en continu.")
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
                 "Flash et bascule du damier a chaque kick, defilement accelere par les basses. 0 = aucune. "
                 "Stochastique : les basses ajoutent des points, le kick donne un coup de contraste, les aigus font scintiller.")

    # ---- palette STOCHASTIQUE : ses reglages ne s'affichent que pour elle
    sto_box = tk.Frame(pattern_box)
    sto_box.grid(row=pz.next_row(), column=0, columnspan=3, sticky="new")
    sto_box.columnconfigure(2, weight=1)
    sz = Zone(sto_box)
    sto_field_menu(sz, sto_box)
    param_slider(sz, "Taille des points", "bg_sto_size", 3, 60, 1, "Taille des points, en pixels pour une image de 720 px de haut.")
    param_slider(sz, "Regularite", "bg_sto_regular", 0.0, 1.0, 0.05,
                 "1 = points repartis sans amas ni trous (tramage stochastique) ; 0 = grain aleatoire brut.")
    param_radio(sz, "Paliers de taille", "bg_sto_levels", (("Continu", "continuous"), ("2", "2"), ("3", "3")),
                tooltip="Continu = taille qui varie sans palier. 2 ou 3 = chaque point prend l'une des tailles fixes selon le champ "
                        "(rendu facon trame d'imprimerie) ; les basses font passer des points au palier suivant. Remplace la variation de taille.")
    param_slider(sz, "Variation de taille", "bg_sto_vary", 0.0, 1.0, 0.05,
                 "0 = tous les points ont la meme taille ; 1 = gros la ou le champ est fort, fins la ou il est faible.")
    param_slider(sz, "Densite", "bg_sto_density", 0.0, 1.0, 0.02, "Part de points allumes au repos (0 = aucun, 1 = presque tous).")
    param_slider(sz, "Douceur", "bg_sto_soft", 0.0, 1.0, 0.05,
                 "Les points naissent et meurent en grossissant / retrecissant (0 = allumage sec ; plus haut = moins de gresillement aux bords des zones).")
    param_slider(sz, "Arc-en-ciel", "bg_sto_rainbow", 0.0, 1.0, 0.05, "0 = points de la couleur 1 ; plus haut = leur teinte suit le champ.")
    param_slider(sz, "Taille de bordure", "bg_sto_border", 0.0, 12.0, 0.5,
                 "Contour de chaque point, en pixels pour une image de 720 px de haut, trace dans le point (0 = aucun ; "
                 "au moins le rayon = point entierement de la couleur de bordure).")
    color_row(sz, "Couleur de bordure", "bg_sto_border_color")
    param_radio(sz, "Texture de la tache", "bg_sto_tex", (("Aucune", "none"), ("Onde sonore", "wave"), ("Pavage", "tiling")),
                tooltip="Remplit la zone formee par tous les points qui se touchent. Onde sonore : lignes d'oscilloscope (basses = amplitude, aigus = "
                        "frequence, mediums = vitesse, kick = epaisseur). Pavage : motif de reaction-diffusion, une vague le peint puis l'efface "
                        "a chaque intervalle (direction = l'angle).")
    param_slider(sz, "Finesse de la texture", "bg_sto_tex_scale", 0.3, 3.0, 0.1, "Espacement des lignes (onde) ou taille du motif (pavage).")
    param_slider(sz, "Contraste de la texture", "bg_sto_tex_contrast", 0.0, 1.0, 0.05,
                 "Eclaircissement de la couleur 1 sur les lignes / le motif (0 = aplat).")
    param_slider(sz, "Intervalle des vagues", "bg_sto_tex_every", 2, 30, 1, "Secondes entre deux vagues du pavage (sans calage sur la mesure).")
    param_radio(sz, "Mesures entre vagues", "bg_sto_tex_bars", (("1", "1"), ("2", "2"), ("4", "4"), ("8", "8")),
                tooltip="Avec « Caler sur la mesure » : la vague repart toutes les N mesures, sur le temps 1.")
    sto_sync_var = tk.IntVar(value=int(float(params["bg_sto_tex_sync"]) >= 0.5))
    bind_param("bg_sto_tex_sync", sto_sync_var, float)
    tk.Checkbutton(sto_box, text="Caler la vague sur la mesure (BPM)", variable=sto_sync_var).grid(
        row=sz.next_row(), column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    sto_inv_var = tk.IntVar(value=int(float(params["bg_sto_invert"]) >= 0.5))
    bind_param("bg_sto_invert", sto_inv_var, float)
    tk.Checkbutton(sto_box, text="Inverser (couleur 2 sur couleur 1)", variable=sto_inv_var).grid(
        row=sz.next_row(), column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    # ---- palette LABYRINTHE (reaction-diffusion) : ses reglages ne s'affichent que pour elle
    rd_box = tk.Frame(pattern_box)
    rd_box.grid(row=pz.next_row(), column=0, columnspan=3, sticky="new")
    rd_box.columnconfigure(2, weight=1)
    rz = Zone(rd_box)
    param_slider(rz, "Forme", "bg_rd_shape", 0.0, 1.0, 0.05, "Taches (0) ou lignes de labyrinthe (1).")
    param_slider(rz, "Taille du motif", "bg_rd_scale", 0.3, 2.0, 0.05, "Epaisseur des lignes et des taches.")
    param_slider(rz, "Ecart des couches", "bg_rd_offset", 0.0, 0.05, 0.002, "Decalage entre les 3 couleurs.")
    param_slider(rz, "Variete", "bg_rd_mix", 0.0, 1.0, 0.05, "Points, lignes et taches melanges par zones.")
    param_slider(rz, "Vide", "bg_rd_void", 0.0, 1.0, 0.05, "Zones sans matiere (et zones denses) qui derivent.")
    param_slider(rz, "Apparitions", "bg_rd_spawn", 0.0, 1.0, 0.05,
                 "Part des kicks qui font naitre de nouvelles taches (100 % = chaque kick, 0 % = aucune : le motif grandit seul depuis ses bords).")
    rd_bud_var = tk.IntVar(value=int(float(params["bg_rd_bud"]) >= 0.5))
    bind_param("bg_rd_bud", rd_bud_var, float)
    tk.Checkbutton(rd_box, text="Seulement contre le motif", variable=rd_bud_var).grid(
        row=rz.next_row(), column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    param_slider(rz, "Derive", "bg_rd_drift", 0.0, 1.0, 0.05, "Vitesse de deplacement des zones (formes et vides).")
    param_slider(rz, "Epaisseur des couches", "bg_rd_layers", 0.0, 1.0, 0.05, "Couche 1 large dessous, couche 3 fine dessus.")
    param_slider(rz, "Fluidite", "bg_rd_smooth", 0.0, 1.0, 0.05,
                 "Naissances, soudures et ruptures sans saut d'une image a l'autre (0 = rendu brut, 1 = tres coulant).")
    param_radio(rz, "Symetrie", "bg_rd_sym", (("Aucune", "none"), ("Miroir G-D", "mirror_lr"), ("Miroir H-B", "mirror_tb"),
                                               ("Quadrants", "quad"), ("Rotation", "rot")),
                tooltip="Symetrie de la croissance. Changer ce reglage relance le motif.")
    param_slider(rz, "Branches", "bg_rd_branches", 2, 8, 1, "Nombre de branches (actif avec Symetrie = Rotation).")
    param_slider(rz, "Liberte", "bg_rd_free", 0.0, 1.0, 0.05, "0 = symetrie stricte, 1 = motif libre.")
    # masque d'expansion (#178) : limite ou la matiere vit ; vignette calculee ici par la meme fonction pure que le shader
    mask_shape_var = param_radio(rz, "Masque", "bg_rd_mask", (("Aucun", "none"), ("Cercles", "circles"), ("Bandes", "bands"), ("Degrade", "gradient"), ("Bruit", "noise")),
                tooltip="Zone ou le motif a le droit de vivre : il se revele dans des formes qui derivent. Les bords s'amincissent avant de disparaitre.")
    mask_row = rz.next_row()
    add_label(rz, "Apercu du masque", mask_row, "Blanc = la matiere vit, noir = eteinte. Suit les curseurs et la derive de la projection.")
    mask_frame = tk.Frame(rd_box)
    mask_frame.grid(row=mask_row, column=rz.ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    mask_img = tk.Label(mask_frame, width=MASK_PREVIEW[0] * MASK_ZOOM, height=MASK_PREVIEW[1] * MASK_ZOOM, bd=1, relief="sunken", bg="#000000")
    mask_img.pack(side="left")                                       # width / height en pixels une fois l'image posee (garde la place vide)
    rd_mask_inv_var = tk.IntVar(value=int(float(params["bg_rd_mask_invert"]) >= 0.5))
    bind_param("bg_rd_mask_invert", rd_mask_inv_var, float)
    tk.Checkbutton(mask_frame, text="Inverser", variable=rd_mask_inv_var).pack(side="left", padx=(10, 0))
    param_slider(rz, "Zones du masque", "bg_rd_mask_count", 1, 8, 1, "Nombre de cercles, de bandes ou de periodes par hauteur d'image (finesse pour le bruit).")
    param_slider(rz, "Taille du masque", "bg_rd_mask_size", 0.1, 0.9, 0.05, "Part de l'image ou la matiere vit.")
    param_slider(rz, "Angle du masque", "bg_rd_mask_angle", 0.0, 360.0, 5.0, "Rotation de la disposition (degres).")
    param_slider(rz, "Douceur du masque", "bg_rd_mask_soft", 0.0, 1.0, 0.05, "Bords nets (0) ou tres progressifs (1) : le motif s'amincit avant de disparaitre.")
    param_slider(rz, "Derive du masque", "bg_rd_mask_drift", 0.0, 2.0, 0.05, "Vitesse de deplacement des zones (lente : le motif doit pouvoir les suivre).")
    mask_state = {"key": None, "image": None, "at": 0.0}

    def refresh_mask_preview(t: float, force: bool = False) -> None:
        """Redessine la vignette du masque (a ~4 Hz, seulement si un reglage ou la derive a change). `t` = derive publiee par le moteur."""
        now = time.perf_counter()
        if not force and now - mask_state["at"] < 0.25:
            return
        mask_state["at"] = now
        try:
            shape = str(mask_shape_var.get())                     # la variable, pas params : Tk lance les rappels les plus recents d'abord (params pas encore a jour)
            if shape == "none":
                key = ("none",)
            else:
                key = (shape, round(float(params["bg_rd_mask_count"]), 3), round(float(params["bg_rd_mask_size"]), 3), round(float(params["bg_rd_mask_angle"]), 2),
                       round(float(params["bg_rd_mask_soft"]), 3), float(params["bg_rd_mask_invert"]) >= 0.5, round(t, 3), str(params["bg_rd_sym"]), int(params["bg_rd_branches"]))
            if key == mask_state["key"]:
                return
            mask_state["key"] = key
            w, h = MASK_PREVIEW
            if shape == "none":
                rows = [["#ffffff"] * w for _ in range(h)]
            else:
                mk = reaction.Mask(shape=shape, count=key[1], size=key[2], angle=key[3], soft=key[4], invert=key[5], t=t)
                rows = []
                for j in range(h):
                    row = []
                    for i in range(w):
                        g = int(round(255 * reaction.mask_value(mk, (i + 0.5) / w, 1.0 - (j + 0.5) / h, 16 / 9, key[7], key[8])))
                        row.append(f"#{g:02x}{g:02x}{g:02x}")
                    rows.append(row)
            img = tk.PhotoImage(master=mask_img, width=w, height=h)
            img.put(" ".join("{" + " ".join(row) + "}" for row in rows))
            mask_state["image"] = img.zoom(MASK_ZOOM, MASK_ZOOM)      # gardee : Tk ne retient pas l'image a la place du Label
            mask_img.config(image=mask_state["image"], width=w * MASK_ZOOM, height=h * MASK_ZOOM)
        except tk.TclError:
            pass

    refresh_mask_preview(0.0, force=True)
    mask_shape_var.trace_add("write", lambda *_a: refresh_mask_preview(0.0, force=True))      # changer de forme redessine tout de suite (la derive suit ensuite)
    for label, key in (("Couche 1", "bg_rd_color1"), ("Couche 2", "bg_rd_color2"), ("Couche 3", "bg_rd_color3"), ("Fond", "bg_rd_ground")):
        color_row(rz, label, key)

    def show_palette_blocks(*_a) -> None:
        for box, name in ((sto_box, "stochastique"), (rd_box, "labyrinthe")):
            if palette_var.get() == name:
                box.grid()
            else:
                box.grid_remove()

    palette_var.trace_add("write", show_palette_blocks)
    show_palette_blocks()

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
    return types.SimpleNamespace(refresh_mask_preview=refresh_mask_preview, apply_bgv=apply_bgv, bg_color1_var=bg_color1_var, bg_color2_var=bg_color2_var, bg_presets=bg_presets, bg_var=bg_var, bgv_status=bgv_status, bgv_var=bgv_var, bgvid_box=bgvid_box, load_overlay_preset=load_overlay_preset, pattern_box=pattern_box, rd_box=rd_box, save_overlay_preset=save_overlay_preset, sto_box=sto_box, update_overlay_preset=update_overlay_preset)
