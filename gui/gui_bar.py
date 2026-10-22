"""Barre des scenes (rangee ambre en haut de la fenetre) : touches de scene, set, Etendre, Mode VJ, projection, PANIQUE, menus.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`) ; `bar_signature` et `refresh_scene_bar` sont rendus par `install` et re-lies dans `build_window`.
"""
from __future__ import annotations

import tkinter as tk
import types

import skin
from core import scenes


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import BANNER_FG, SCENE_COLOR, STATS, choice_dialog          # import tardif : gl_gui importe ce module
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_entry_now = c.add_entry_now
    after = c.after
    apply_entry = c.apply_entry
    ask_delete_set = c.ask_delete_set
    cancel_adding = c.cancel_adding
    console_window = c.console_window
    current_index = c.current_index
    do_panic = c.do_panic
    export_set_now = c.export_set_now
    fill_entry_menu = c.fill_entry_menu
    help_window = c.help_window
    import_set_now = c.import_set_now
    new_scene_from_entry = c.new_scene_from_entry
    new_set_dialog = c.new_set_dialog
    proj_text = c.proj_text
    proj_ui = c.proj_ui
    projection = c.projection
    redo_now = c.redo_now
    refresh_scene_panel = c.refresh_scene_panel
    register_drop = c.register_drop
    rename_set_dialog = c.rename_set_dialog
    root = c.root
    s = c.s
    scene_bar = c.scene_bar
    scene_book = c.scene_book
    scene_entry_var = c.scene_entry_var
    scene_msg = c.scene_msg
    scene_state = c.scene_state
    scene_top = c.scene_top
    set_expanded = c.set_expanded
    set_vj_option = c.set_vj_option
    stage_ref = c.stage_ref
    start_adding = c.start_adding
    switch_set = c.switch_set
    toggle_projection = c.toggle_projection
    toggle_scene_lock = c.toggle_scene_lock
    undo_now = c.undo_now
    update_vj_button = c.update_vj_button
    vj_advance = c.vj_advance
    vj_toggle = c.vj_toggle
    vj_ui = c.vj_ui

    def bar_signature() -> tuple:
        """Tout ce que la barre affiche SAUF la scene active : identique = rien a reconstruire (le VJ qui enchaine les scenes ne la
        detruit plus a chaque changement, elle clignotait)."""
        vjs = scene_book.vj()
        rows_ = scene_book.rows()
        return (
            tuple((r["index"], r["name"], str(r["media"]), r["scene"].get("bg"), r["scene"].get("mode")) for r in rows_),
            scene_book.active_set_name(), tuple((n, len(scene_book.get_set(n)["entries"])) for n in scene_book.set_names()),
            tuple(scene_book.names()), scene_book.undo_label(), scene_book.redo_label(),
            tuple(sorted((k, str(v)) for k, v in vjs.items() if k != "random")),
            bool(scene_state["adding"]), bool(getattr(s, "scenes_expanded", False)), bool(getattr(s, "scene_lock", False)),
            projection is not None, str(root.cget("bg")))

    @STATS.watched("refresh_scene_bar")
    def refresh_scene_bar() -> None:
        sig = bar_signature()
        if proj_ui.get("bar_sig") == sig and proj_ui.get("bar_btns") and proj_ui.get("strip") is not None \
                and scene_bar.winfo_children():
            current_ = current_index(scene_book.rows())
            active_ = None
            for btn_, i_ in proj_ui["bar_btns"]:
                on_ = i_ == current_
                btn_.configure(bg=SCENE_COLOR if on_ else a2w.GUI_BG, fg=BANNER_FG if on_ else a2w.GUI_FG)
                if on_:
                    active_ = btn_
            proj_ui["bar_active"] = active_
            proj_ui["bar_show"]()
            refresh_scene_panel()
            return
        for child in scene_bar.winfo_children():
            child.destroy()
        proj_ui["bar_sig"] = sig
        proj_ui["bar_btns"] = []
        proj_ui["btn"] = None
        proj_ui["strip"] = None
        if projection is not None:
            btn = tk.Button(scene_bar, text=proj_text(), command=toggle_projection, padx=10, width=22)      # largeur fixe : Demarrer / Arreter ne decalent rien
            btn.pack(side="left", padx=(8, 4), pady=5)
            proj_ui["btn"] = btn
            Tooltip(btn, "Ouvre la fenetre de projection : en fenetre, sur un autre ecran que les reglages s'il y en a un "
                         "(plein ecran : case Plein ecran ou touche F). Echap dans cette fenetre la ferme sans fermer les reglages.")
        scenes_title = tk.Label(scene_bar, text="SCENES")
        skin.title(scenes_title, SCENE_COLOR)
        scenes_title.pack(side="left", padx=(8, 6), pady=5)
        # Mode VJ (a droite) : reglages, scene suivante, lecture / pause. Pose AVANT les touches des scenes : en pack, le
        # premier arrive est le premier servi, donc 9 scenes aux noms longs ne repoussent jamais le bouton hors de la fenetre.
        panic_btn = tk.Button(scene_bar, text="PANIQUE", command=do_panic, relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                              font=("Segoe UI", 9, "bold"), bg="#d92b2b", fg="#ffffff", activebackground="#ff4d4d", activeforeground="#ffffff")
        panic_btn.pack(side="right", padx=(2, 6), pady=5)
        Tooltip(panic_btn, "PANIQUE (touche X dans la fenetre de rendu) : coupe d'un geste effets, strobe, montee, composition, teinte et mode "
                           "silence, remet les masters a 100 %, met le VJ en pause et retire le verrou. Le fond, le logo et les medias restent.")
        vj_ui["panic"] = panic_btn
        expanded = bool(getattr(s, "scenes_expanded", False))
        expand = tk.Button(scene_bar, text="\u25b4 Reduire" if expanded else "\u25be Etendre", command=lambda: set_expanded(not expanded),
                           relief="flat", bd=0, padx=8, pady=3, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR,
                           activebackground=a2w.GUI_BG, activeforeground=SCENE_COLOR, font=("Segoe UI", 9, "bold"))
        expand.pack(side="right", padx=(0, 4), pady=5)
        Tooltip(expand, "Etendre : le set en detail (medias lies avec leur vignette, duree et participation au VJ de chaque scene, "
                        "ordre, renommer). La fenetre grandit, et reprend sa taille quand on reduit.")
        opts = tk.Menubutton(scene_bar, text="reglages VJ \u25be", relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                             bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, activebackground=a2w.GUI_BG,
                             activeforeground=a2w.GUI_FG, font=("Segoe UI", 9))
        opts.pack(side="right", padx=(0, 6), pady=5)
        menu = tk.Menu(opts, tearoff=0)
        order_var = tk.StringVar(value=scene_book.vj()["order"])
        when_var = tk.StringVar(value=f"b{scene_book.vj()['bars']}" if scene_book.vj()["unit"] == "bars" else f"s{int(scene_book.vj()['seconds'])}")

        def set_when() -> None:
            kind, value = when_var.get()[0], int(when_var.get()[1:])
            set_vj_option(unit="bars", bars=value) if kind == "b" else set_vj_option(unit="seconds", seconds=float(value))
        for label, value in (("Dans l'ordre", "seq"), ("Au hasard (jamais la meme deux fois)", "random"),
                             ("Full Randomizer (banques d'assets)", "alea")):
            menu.add_radiobutton(label=label, variable=order_var, value=value,
                                 command=lambda: set_vj_option(order=order_var.get()))
        menu.add_separator()
        for value in scenes.VJ_SECONDS:
            label = f"Toutes les {value} s" if value < 60 else f"Toutes les {value // 60} min"
            menu.add_radiobutton(label=label, variable=when_var, value=f"s{value}", command=set_when)
        menu.add_separator()
        for value in scenes.VJ_BARS:
            menu.add_radiobutton(label=f"Toutes les {value} mesure{'s' if value > 1 else ''} (en rythme)", variable=when_var,
                                 value=f"b{value}", command=set_when)
        menu.add_separator()
        drop_var = tk.IntVar(value=int(bool(scene_book.vj()["on_drop"])))
        menu.add_checkbutton(label="Changer de scene sur le drop detecte", variable=drop_var,
                             command=lambda: set_vj_option(on_drop=bool(drop_var.get())))
        menu.add_separator()
        menu.add_command(label="Console VJ (grosses commandes)...", command=console_window)
        menu.add_command(label="Mode scene (scenes en grosses touches, plein ecran)...", command=lambda: stage_ref["open"]())
        menu.add_command(label="Raccourcis clavier (?)...", command=help_window)
        menu.add_command(label="Rapport de fluidite de l'interface...", command=lambda: choice_dialog(
            root, "Fluidite de l'interface", "\n".join(STATS.report(12)), ["Fermer"]))
        opts["menu"] = menu
        Tooltip(opts, "Ordre et duree du VJ (propres a chaque set), console VJ et aide des raccourcis.")
        nxt = tk.Button(scene_bar, text="\u25b6\u25b6", command=vj_advance, relief="flat", bd=0, padx=8, pady=3,
                        cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG, activebackground=SCENE_COLOR,
                        activeforeground=BANNER_FG, font=("Segoe UI", 9, "bold"))
        nxt.pack(side="right", padx=2, pady=5)
        Tooltip(nxt, "Scene suivante tout de suite, avec un flash (fleche droite dans la fenetre de rendu).")
        toggle = tk.Button(scene_bar, text="\u25b6 VJ", command=vj_toggle, relief="flat", bd=0, padx=12, pady=3, width=14,    # largeur fixe : le temps restant ne decale rien
                           cursor="hand2", font=("Segoe UI", 9, "bold"), bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                           activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        toggle.pack(side="right", padx=2, pady=5)
        Tooltip(toggle, "Mode VJ : enchaine les scenes du set tout seul (Espace dans la fenetre de rendu), avec un flash court "
                        "a chaque changement. Le chiffre est le temps avant la prochaine scene. Choisir une scene a la main "
                        "met le VJ en pause ; changer de set le redemarre. Ordre et duree : 'reglages VJ'.")
        vj_ui["toggle"] = toggle
        # Verrou actif : bien visible dans la barre (sinon Play, Suivante et les scenes semblent « mortes » sans qu'on sache pourquoi).
        # La place est TOUJOURS reservee (bouton invisible quand il n'y a pas de verrou) : l'apparition du verrou ne decale rien.
        locked = bool(getattr(s, "scene_lock", False))
        lockbar = tk.Button(scene_bar, text="​Verrou ●", command=toggle_scene_lock if locked else (lambda: None), relief="flat", bd=0, padx=10, pady=3,
                            cursor="hand2" if locked else "arrow", font=("Segoe UI", 9, "bold"), bg=SCENE_COLOR if locked else a2w.GUI_PANEL_BG,
                            fg=BANNER_FG if locked else a2w.GUI_PANEL_BG,
                            activebackground=SCENE_COLOR if locked else a2w.GUI_PANEL_BG, activeforeground=BANNER_FG if locked else a2w.GUI_PANEL_BG,
                            takefocus=0)
        lockbar.pack(side="right", padx=2, pady=5)
        if locked:
            Tooltip(lockbar, "La scene est VERROUILLEE : le VJ (Play, Suivante), les touches de scene, F1..F9 et les tirages sont sans effet. "
                             "Clique pour deverrouiller (touche K dans la fenetre de rendu).")
        update_vj_button()
        kick = tk.Label(scene_bar, text="\u25cf -- BPM", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, font=("Segoe UI", 11, "bold"), width=22, anchor="w")
        kick.pack(side="right", padx=(2, 8), pady=5)
        vj_ui["kick"] = kick
        Tooltip(kick, "Voyant de kick : le rond s'allume a chaque kick detecte par l'analyse audio. A droite, le BPM estime a partir des kicks, en gros et "
                      "en clair des qu'il est fiable (il faut environ 6 kicks reguliers, soit 5 secondes de musique) ; '-- BPM' sinon. 'cassure' / 'MONTEE' "
                      "quand l'analyse repere une baisse des basses / une montee. Le meme BPM sert au tempo auto et est repris dans le titre de la fenetre.")
        # Set actif : menu pour en changer, creer, renommer, supprimer, exporter, importer
        current_set = scene_book.active_set_name()
        set_btn = tk.Menubutton(scene_bar, text=f"SET  {current_set if len(current_set) <= 16 else current_set[:15] + '.'} \u25be",
                                relief="flat", bd=0, padx=10, pady=3, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR,
                                activebackground=a2w.GUI_BG, activeforeground=SCENE_COLOR, font=("Segoe UI", 9, "bold"))
        set_btn.pack(side="left", padx=(0, 8), pady=5)
        set_menu = tk.Menu(set_btn, tearoff=0)
        set_var = tk.StringVar(value=current_set)
        for name in scene_book.set_names():
            n_rows = len(scene_book.get_set(name)["entries"])
            set_menu.add_radiobutton(label=f"{name}  ({n_rows})", variable=set_var, value=name,
                                     command=lambda n=name: switch_set(n))
        set_menu.add_separator()
        set_menu.add_command(label="Nouveau set vide...", command=new_set_dialog)
        set_menu.add_command(label="Dupliquer ce set...", command=lambda: new_set_dialog(copy=True))
        set_menu.add_command(label="Renommer ce set...", command=rename_set_dialog)
        set_menu.add_command(label="Supprimer ce set...", command=ask_delete_set)
        set_menu.add_separator()
        undo_l, redo_l = scene_book.undo_label(), scene_book.redo_label()
        set_menu.add_command(label=f"Annuler : {undo_l}  (Ctrl+Z)" if undo_l else "Annuler  (Ctrl+Z)", command=undo_now,
                             state="normal" if undo_l else "disabled")
        set_menu.add_command(label=f"Retablir : {redo_l}  (Ctrl+Y)" if redo_l else "Retablir  (Ctrl+Y)", command=redo_now,
                             state="normal" if redo_l else "disabled")
        set_menu.add_separator()
        set_menu.add_command(label="Exporter ce set (archive avec medias, ou JSON seul)...", command=export_set_now)
        set_menu.add_command(label="Importer un set (fichier)...", command=import_set_now)
        set_btn["menu"] = set_menu
        Tooltip(set_btn, "Set = un enchainement de scenes (les memes scenes peuvent servir dans plusieurs sets, chacune avec son "
                         "propre media). Changer de set change la rangee, F1..F9 et le VJ (qui redemarre). "
                         "Exporter / importer : un seul fichier a partager.")
        if scene_state["adding"]:
            entry = tk.Entry(scene_bar, textvariable=scene_entry_var, width=16)
            entry.pack(side="left", padx=(6, 2), pady=5)
            entry.bind("<Return>", new_scene_from_entry)
            entry.bind("<Escape>", cancel_adding)
            entry.focus_set()
            entry.select_range(0, "end")
            tk.Button(scene_bar, text="Enregistrer", command=new_scene_from_entry, padx=8).pack(side="left", padx=2)
            tk.Button(scene_bar, text="Annuler", command=cancel_adding, padx=8).pack(side="left", padx=2)
        else:
            add = tk.Menubutton(scene_bar, text="+ Scene \u25be", relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                                bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, activebackground=a2w.GUI_BG,
                                activeforeground=SCENE_COLOR, font=("Segoe UI", 9, "bold"))
            add.pack(side="left", padx=6, pady=5)
            add_menu = tk.Menu(add, tearoff=0)
            add_menu.add_command(label="Nouvelle scene depuis l'etat actuel...", command=start_adding)
            library = scene_book.names()
            if library:
                add_menu.add_separator()
                for name in library:
                    add_menu.add_command(label=f"Ajouter au set : {name}", command=lambda n=name: add_entry_now(n))
            add["menu"] = add_menu
            Tooltip(add, "Nouvelle scene = l'etat actuel (fond audio2wave ou motif genere + overlay), ajoute au set. "
                         "Ou ajoute au set une scene deja enregistree (autant de fois que tu veux, par exemple avec un autre "
                         "media). Reglages a part des presets : retouche d'abord ce que tu veux.")
        # Touches des scenes : une bande qui defile (molette, fleches, ou « ◀ ▶ »), posee EN DERNIER avec expand : elle prend la place
        # que les autres boutons laissent, et un set de 40 scenes ne repousse plus rien hors de la barre.
        strip_bg = a2w.GUI_PANEL_BG
        arrows = {}
        for side, text, step in (("right", "\u25b6", 1), ("left", "\u25c0", -1)):
            arrows[side] = tk.Button(scene_bar, text=text, command=lambda st=step: strip.xview_scroll(st * 6, "units"), relief="flat", bd=0,
                                     padx=5, pady=3, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, activebackground=a2w.GUI_BG,
                                     activeforeground=SCENE_COLOR, font=("Segoe UI", 9, "bold"))
        strip = tk.Canvas(scene_bar, highlightthickness=0, bd=0, bg=strip_bg, height=28, width=40)
        inner = tk.Frame(strip, bg=strip_bg)
        strip.create_window((0, 0), window=inner, anchor="nw")
        proj_ui["strip"] = strip
        rows = scene_book.rows()
        current = current_index(rows)
        active_btn = None
        for row in rows:
            i = row["index"]
            name = row["name"]
            on = i == current
            btn = tk.Button(inner, text=f"{i + 1}  {name if len(name) <= 14 else name[:13] + '.'}"
                            + (" \u25cf" if row["media"] else ""), command=lambda k=i: apply_entry(k), relief="flat", bd=0,
                            padx=12, pady=3, cursor="hand2", font=("Segoe UI", 9, "bold"),
                            bg=SCENE_COLOR if on else a2w.GUI_BG, fg=BANNER_FG if on else a2w.GUI_FG,
                            activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            btn.pack(side="left", padx=2, pady=5)
            if on:
                active_btn = btn
            proj_ui["bar_btns"].append((btn, i))
            register_drop(btn, i)                                      # un fichier depose sur la touche = media de la scene
            menu = tk.Menu(btn, tearoff=0)
            fill_entry_menu(menu, row)
            btn.bind("<Button-3>", lambda e, m=menu: m.tk_popup(e.x_root, e.y_root))
            scene = row["scene"]
            Tooltip(btn, f"{name} - clic: charger la scene ({'F' + str(i + 1) + ' dans la fenetre de rendu' if i < scenes.KEYS else 'pas de touche F au-dela de F9'}). "
                         f"{ {'pattern': 'Fond: fond genere. ', 'video': 'Fond: video. '}.get(scene['bg']) or 'Fond: audio2wave (' + str(scene.get('mode')) + '). '}"
                         + (f"Media: {scenes.media_label(row['media'])}. " if row["media"] else "")
                         + "Glisse un fichier (image / video) dessus pour lui lier un media. "
                         + "Clic droit: mettre a jour, media, deplacer, retirer, supprimer.")
        strip.pack(side="left", fill="x", expand=True, padx=2, pady=0)
        if proj_ui.get("strip_h") is None:                    # hauteur des touches : mesuree une fois (update_idletasks coute ~100 ms a chaque fois)
            with STATS.timed("bar.mesure"):
                scene_bar.update_idletasks()
            proj_ui["strip_h"] = max(inner.winfo_reqheight(), 24) if rows else None
        strip.configure(height=proj_ui["strip_h"] or 28)

        def sync_strip(_e=None) -> None:
            """Region defilable = largeur des touches ; fleches visibles seulement si tout ne tient pas."""
            try:
                need = inner.winfo_reqwidth()
                strip.configure(scrollregion=(0, 0, need, max(inner.winfo_reqheight(), 24)))
                over = need > strip.winfo_width() > 1
                for side, b in arrows.items():
                    if over and not b.winfo_ismapped():
                        b.pack(side=side, padx=1, pady=5, before=strip) if side == "left" else b.pack(side=side, padx=1, pady=5)
                    elif not over and b.winfo_ismapped():
                        b.pack_forget()
                proj_ui["strip_x"] = strip.xview()[0]
            except tk.TclError:
                pass

        def show_active() -> None:
            """Garde la touche active visible : elle est recentree si elle sort de la bande, sinon la position est conservee."""
            try:
                need = max(inner.winfo_reqwidth(), 1)
                view = strip.winfo_width()
                if need <= view:
                    strip.xview_moveto(0)
                    return
                strip.xview_moveto(proj_ui.get("strip_x") or 0.0)
                active_btn = proj_ui.get("bar_active")
                if active_btn is not None:
                    left, right = active_btn.winfo_x(), active_btn.winfo_x() + active_btn.winfo_reqwidth()
                    x0 = strip.canvasx(0)
                    if left < x0 or right > x0 + view:
                        strip.xview_moveto(max(0.0, (left + right - view) / 2 / need))
                sync_strip()
            except tk.TclError:
                pass
        proj_ui["bar_active"] = active_btn
        proj_ui["bar_show"] = show_active
        strip.bind("<Configure>", lambda e: (sync_strip(), show_active()))
        after(60, lambda: (sync_strip(), show_active()))
        refresh_scene_panel()
        with STATS.timed("bar.habillage"):
            skin.apply(scene_top)
        # La rangee ne dicte pas la largeur de la fenetre : trop de touches ou de boutons sont rognes a droite (les boutons du VJ,
        # poses d'abord, restent visibles) au lieu d'elargir la fenetre de reglages.
        if proj_ui.get("bar_h") is None:                      # hauteur mesuree une fois (sans cela la barre n'en aurait plus)
            scene_bar.update_idletasks()
            proj_ui["bar_h"] = scene_bar.winfo_reqheight()
        scene_bar.pack_propagate(False)
        scene_bar.configure(height=proj_ui["bar_h"])

    return types.SimpleNamespace(bar_signature=bar_signature, refresh_scene_bar=refresh_scene_bar)
