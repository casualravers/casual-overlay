"""Onglets d'effets : effets du fond et de la couche logo, halo holographique, fonte acide, cellules, noise.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

import audio2wave_gl as gl
import tkinter as tk
from core import audio_layers


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import Zone, _safe          # import tardif : gl_gui importe ce module
    ROW_PADX = c.ROW_PADX
    ROW_PADY = c.ROW_PADY
    SECTION_GAP = c.SECTION_GAP
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_auto = c.add_auto
    add_separator = c.add_separator
    add_slider = c.add_slider
    bind_param = c.bind_param
    color_row = c.color_row
    compact_scale = c.compact_scale
    param_slider = c.param_slider
    params = c.params
    tab_aura = c.tab_aura
    tab_cell = c.tab_cell
    tab_fx = c.tab_fx
    tab_melt = c.tab_melt
    tab_noise = c.tab_noise
    zone_aura = c.zone_aura
    zone_cell = c.zone_cell
    zone_fx = c.zone_fx
    zone_melt = c.zone_melt
    zone_noise = c.zone_noise

    src_tip = ("Partie du son qui pilote cette couche. Tout = comme avant. Basses : wobble et kicks. Mediums : wobble et glitch sur les mediums. "
               "Aigus : wobble et glitch sur les aigus, sans kick. Kick : seulement ce qui part des kicks (onde de choc, aberration, pulsation). "
               "Rien : la couche ne reagit plus a la musique (le noise et les automations continuent).")

    def source_menu(parent, row, label, key):
        tk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        var = tk.StringVar(value=params[key])
        c.bind_param(key, var, str)
        menu = tk.OptionMenu(parent, var, *audio_layers.SOURCES)
        menu.grid(row=row, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(menu, src_tip)
        return var

    add_separator(zone_fx, "Effets")
    source_menu(tab_fx, zone_fx.next_row(), "Le fond reagit a", "src_bg")
    fx_on_vars: list[tk.IntVar] = []
    fx_int_vars: list[tk.DoubleVar] = []
    fx_labels = ("Wobble", "Onde de choc", "Aberration chromatique", "Glitch", "Logo (pulse/contour)")
    fx_tips = ("Ondulation de l'image, amplitude = basses.",
               "Onde de choc depuis le centre a chaque kick.",
               "Canaux R/V/B decales depuis le centre au kick.",
               "Bandes horizontales decalees sur les gros kicks / aigus.",
               "Interrupteur des reactions du logo (pulsation, tremblement, contour).")
    for i, name in enumerate(fx_labels):
        r = zone_fx.next_row()
        on_var = tk.IntVar(value=int(params["fx_on"][i]))
        fx_on_vars.append(on_var)
        on_var.trace_add("write", lambda *_a, i=i, v=on_var: params["fx_on"].__setitem__(i, int(v.get())))
        cb = tk.Checkbutton(tab_fx, text=name, variable=on_var)
        cb.grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(cb, fx_tips[i])
        int_var = tk.DoubleVar(value=params["fx_int"][i])
        fx_int_vars.append(int_var)
        int_var.trace_add("write", lambda *_a, i=i, v=int_var: params["fx_int"].__setitem__(i, _safe(v, 1.0)))
        compact_scale(tab_fx, 0, 2, 0.05, int_var, 110).grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        add_auto(zone_fx, r, f"fx_int{i}", int_var)

    master_var = tk.DoubleVar(value=params["master"])
    bind_param("master", master_var)
    add_slider(zone_fx, "Intensite globale", 0.0, 2.0, 0.1, master_var, length=110)
    sens_var = tk.DoubleVar(value=params["sensitivity"])
    bind_param("sensitivity", sens_var)
    add_slider(zone_fx, "Sensibilite kick", 0.25, 4.0, 0.05, sens_var, length=110,
               tooltip="Plus haut = kicks detectes plus facilement (aussi touches haut/bas dans la fenetre GL).")

    # ---- colonne A : EFFETS DE LA COUCHE LOGO (le fond garde les reglages ci-dessus)
    add_separator(zone_fx, "Effets sur le logo")
    link_var = tk.IntVar(value=int(float(params["fx_link"]) >= 0.5))
    link_check = tk.Checkbutton(tab_fx, text="Memes effets que le fond", variable=link_var)
    link_check.grid(row=zone_fx.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    Tooltip(link_check, "Coche: le logo subit exactement les memes deformations que le fond. Decoche: le logo a "
                        "ses propres effets (ci-dessous), ses ondes partent de son centre et son glitch tire "
                        "d'autres bandes. Touche L; Maj+1 a 4 dans la fenetre GL agissent sur le logo.")
    logo_fx_box = tk.Frame(tab_fx)
    logo_fx_box.grid(row=zone_fx.next_row(), column=0, columnspan=3, sticky="nw")
    lz = Zone(logo_fx_box)
    source_menu(logo_fx_box, lz.next_row(), "Le logo reagit a", "src_logo")
    fxl_on_vars: list[tk.IntVar] = []
    fxl_int_vars: list[tk.DoubleVar] = []
    for i, name in enumerate(fx_labels[:gl.FX_EFFECTS]):
        r = lz.next_row()
        on_var = tk.IntVar(value=int(params["fxl_on"][i]))
        fxl_on_vars.append(on_var)
        on_var.trace_add("write", lambda *_a, i=i, v=on_var: params["fxl_on"].__setitem__(i, int(v.get())))
        cb = tk.Checkbutton(logo_fx_box, text=name, variable=on_var)
        cb.grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(cb, fx_tips[i] + " (couche logo)")
        int_var = tk.DoubleVar(value=params["fxl_int"][i])
        fxl_int_vars.append(int_var)
        int_var.trace_add("write", lambda *_a, i=i, v=int_var: params["fxl_int"].__setitem__(i, _safe(v, 1.0)))
        compact_scale(logo_fx_box, 0, 2, 0.05, int_var, 110).grid(row=r, column=1, sticky="w", padx=ROW_PADX,
                                                                  pady=ROW_PADY)
        add_auto(lz, r, f"fxl_int{i}", int_var)

    def on_link(*_a) -> None:
        linked = bool(link_var.get())
        if linked != (float(params["fx_link"]) >= 0.5):
            gl.set_fx_link(params, linked)        # delier copie les reglages du fond (rien ne saute)
        if linked:
            logo_fx_box.grid_remove()
        else:
            logo_fx_box.grid()

    link_var.trace_add("write", on_link)
    on_link()

    # ---- colonne A : HALO HOLOGRAPHIQUE autour du logo (non audioreactif par defaut)
    add_separator(zone_aura, "Halo holographique (logo)")
    holo_var = tk.IntVar(value=int(float(params["holo_on"]) >= 0.5))
    bind_param("holo_on", holo_var, float)
    holo_row = tk.Frame(tab_aura)
    holo_row.grid(row=zone_aura.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    holo_check = tk.Checkbutton(holo_row, text="Halo holographique", variable=holo_var)
    holo_check.pack(side="left")
    holo_box = tk.Frame(tab_aura)            # les reglages tiennent dans l'onglet: plus besoin de les replier
    holo_box.grid(row=zone_aura.next_row(), column=0, columnspan=3, sticky="nw")
    cz = Zone(holo_box)
    Tooltip(holo_check, "Lumiere holographique a grande portee autour du logo (image, texte ou video): elle "
                        "deforme le FOND (lentille, ondes qui se propagent), l'irise, et y traine de la "
                        "poussiere d'etoiles. Le logo reste intact. Anime par le temps seulement (reaction a "
                        "l'audio = 0 par defaut). Touche C.")
    param_slider(cz, "Intensite", "holo_intensity", 0.0, 3.0, 0.05, "Force de la lumiere irisee.")
    param_slider(cz, "Portee", "holo_reach", 0.3, 3.0, 0.05, "Etendue du halo autour du logo.")
    param_slider(cz, "Deformation", "holo_warp", 0.0, 3.0, 0.05, "Deformation du fond (lentille et ondes). 0 = aucune.")
    param_slider(cz, "Poussiere", "holo_dust", 0.0, 3.0, 0.05, "Quantite de poussiere d'etoiles. 0 = aucune.")
    param_slider(cz, "Vitesse", "holo_speed", 0.0, 1.0, 0.01, "Vitesse des ondes et de la derive des etoiles.")
    param_slider(cz, "Reaction audio", "holo_react", 0.0, 2.0, 0.05,
                 "0 = aucune (defaut). Sinon: intensite qui monte au kick, animation acceleree par les basses.")

    # ---- FONTE ACIDE du logo (onglet a cote de l'aura) : dissolution sur place puis reformation, en boucle
    add_separator(zone_melt, "Fonte acide (logo)")
    melt_var = tk.IntVar(value=int(float(params["melt_on"]) >= 0.5))
    bind_param("melt_on", melt_var, float)
    melt_row = tk.Frame(tab_melt)
    melt_row.grid(row=zone_melt.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    melt_check = tk.Checkbutton(melt_row, text="Fonte acide", variable=melt_var)
    melt_check.pack(side="left")
    Tooltip(melt_check, "Le logo (image, texte ou video) se dissout sur place, ronge par un acide : des trous "
                        "grandissent, leur lisiere brille, puis il se reforme, en boucle. Coupee par defaut, "
                        "anime par le temps seulement (reaction a l'audio = 0). Touche M.")
    param_slider(zone_melt, "Profondeur", "melt_depth", 0.0, 1.0, 0.02,
                 "Degre maximal de dissolution. 1 = le logo disparait entierement au plus fort du cycle.")
    param_slider(zone_melt, "Duree du cycle", "melt_period", 4.0, 60.0, 1.0,
                 "Secondes pour un cycle complet : intact, fond, dissous, se reforme.")
    param_slider(zone_melt, "Grain", "melt_scale", 0.3, 4.0, 0.05,
                 "Finesse des trous : petit = grosses plaques, grand = fines piqures.")
    param_slider(zone_melt, "Bord acide", "melt_edge", 0.0, 2.0, 0.05,
                 "Largeur et intensite de la lisiere acide autour des trous.")
    melt_color_var = color_row(zone_melt, "Couleur de l'acide", "melt_color")
    param_slider(zone_melt, "Reaction audio", "melt_react", 0.0, 2.0, 0.05,
                 "0 = aucune (defaut). Sinon: le cycle s'accelere avec les basses.")

    # ---- CELLULES du logo (Voronoi + metaballes, cell shading)
    add_separator(zone_cell, "Cellules organiques (logo)")
    cell_var = tk.IntVar(value=int(float(params["cell_on"]) >= 0.5))
    bind_param("cell_on", cell_var, float)
    cell_row = tk.Frame(tab_cell)
    cell_row.grid(row=zone_cell.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    cell_check = tk.Checkbutton(cell_row, text="Cellules", variable=cell_var)
    cell_check.pack(side="left")
    Tooltip(cell_check, "Le logo (image, texte ou video) se DECOMPOSE en cellules organiques (Voronoi) : "
                        "il garde sa forme mais est fait de bulles qui glissent, se retrecissent et "
                        "s'ecartent, rendues en aplats avec contour et reflet (cell shading). Coupee par defaut, anime par le temps "
                        "seulement (reaction a l'audio = 0). Touche V.")
    param_slider(zone_cell, "Decomposition", "cell_amount", 0.0, 1.0, 0.02,
                 "0 = logo intact, 1 = entierement decompose en cellules separees.")
    param_slider(zone_cell, "Rayon des cellules", "cell_fusion", 0.2, 1.2, 0.02,
                 "Petit = bulles isolees, grand = les cellules se touchent et se chevauchent.")
    param_slider(zone_cell, "Taille des cellules", "cell_scale", 0.3, 3.0, 0.05,
                 "Grand = beaucoup de petites cellules, petit = quelques grosses.")
    param_slider(zone_cell, "Vitesse", "cell_speed", 0.0, 2.0, 0.02, "Vitesse de derive des cellules.")
    param_slider(zone_cell, "Aplat", "cell_flat", 0.0, 1.0, 0.02,
                 "0 = couleurs d'origine du logo, 1 = chaque cellule en aplat de la couleur de son centre.")
    cell_ink_var = color_row(zone_cell, "Couleur du contour", "cell_ink")
    param_slider(zone_cell, "Reaction audio", "cell_react", 0.0, 2.0, 0.05,
                 "0 = aucune (defaut). Sinon: derive acceleree par les basses, cellules gonflees au kick.")

    # ---- NOISE : un seul hasard, prereglé ; on choisit sa force et sur quoi il agit
    add_separator(zone_noise, "Noise (hasard)")
    param_slider(zone_noise, "Intensite", "noise_amount", 0.0, 1.0, 0.05,
                 "Force du hasard. 0 = aucun effet. Il ajoute des ondulations et des coups au hasard, comme une musique "
                 "imaginaire, aux effets coches ci-dessous (meme sans son).")
    add_separator(zone_noise, "Le noise agit sur")
    noise_fx_vars: list[tk.IntVar] = []
    noise_tips = ("Le fond et le logo ondulent au hasard.", "Des ondes de choc partent au hasard, comme sur un kick.",
                  "Des decalages de couleurs au hasard.", "Des bandes decalees (glitch) au hasard.",
                  "Le logo pulse, tremble et s'illumine au hasard.")
    for i, name in enumerate(fx_labels):
        var = tk.IntVar(value=int(params["noise_fx"][i]))
        var.trace_add("write", lambda *_a, i=i, v=var: params["noise_fx"].__setitem__(i, int(v.get())))
        noise_fx_vars.append(var)
        cb = tk.Checkbutton(tab_noise, text=name, variable=var)
        cb.grid(row=zone_noise.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(cb, noise_tips[i] + " Seuls les effets allumes dans l'onglet Effets bougent.")
    noise_meter = tk.Canvas(tab_noise, width=260, height=32, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    noise_meter.grid(row=zone_noise.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 2))
    noise_bars = {}
    for i, (name, color) in enumerate((("houle", "#b392ff"), ("coup", "#ffe633"))):
        y0 = 4 + i * 13
        noise_meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        noise_bars[name] = noise_meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    import types
    return types.SimpleNamespace(cell_row=cell_row, cell_var=cell_var, fx_int_vars=fx_int_vars, fx_on_vars=fx_on_vars, fxl_int_vars=fxl_int_vars, fxl_on_vars=fxl_on_vars, holo_box=holo_box, holo_var=holo_var, link_var=link_var, logo_fx_box=logo_fx_box, master_var=master_var, melt_row=melt_row, melt_var=melt_var, noise_bars=noise_bars, noise_fx_vars=noise_fx_vars, noise_meter=noise_meter, sens_var=sens_var)
