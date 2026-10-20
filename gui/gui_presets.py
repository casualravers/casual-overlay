"""Barre de presets (overlay et fond) : menu structure (favoris, derniers charges, dossiers), recherche, indicateur « modifie » + Revenir,
sauvegarder sous / mettre a jour / renommer / supprimer.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`) ; `make_preset_bar` est rendu par `install` et re-lie dans `build_window`.
"""
from __future__ import annotations

import json
import tkinter as tk
import types

import audio2wave_gl as gl


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import SCENE_COLOR, Zone          # import tardif : gl_gui importe ce module
    ROW_PADX = c.ROW_PADX
    ROW_PADY = c.ROW_PADY
    Tooltip = c.Tooltip
    a2w = c.a2w
    add_section_title = c.add_section_title
    automation = c.automation
    live = c.live
    params = c.params
    refresh_scene_bar = c.refresh_scene_bar
    root = c.root
    scene_book = c.scene_book
    scene_ui = c.scene_ui
    sync_automation_now = c.sync_automation_now
    sync_widgets = c.sync_widgets
    ui_every = c.ui_every

    def make_preset_bar(parent, title: str, book, capture, values_of, hint: str, kind: str = "overlay") -> dict:
        z = Zone(parent)
        add_section_title(z, title)
        name_var = tk.StringVar(value="default")
        msg = tk.StringVar(value=hint)
        mod_var = tk.StringVar(value="")
        filter_var = tk.StringVar(value="")
        base = {"snap": None}                      # etat que donnerait le preset choisi : sert a dire « modifie »

        load_row = tk.Frame(parent)
        load_row.grid(row=z.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        tk.Label(load_row, text="Charger").pack(side="left", padx=(0, 10))
        menu_widget = tk.OptionMenu(load_row, name_var, "")
        a2w.style_option_menu(menu_widget)
        menu_widget.pack(side="left")
        fav_btn = tk.Button(load_row, text="\u2606", width=2)
        fav_btn.pack(side="left", padx=(4, 0))
        Tooltip(fav_btn, "Favori : les favoris sont en tete du menu.")

        def sub_menu(parent_menu):
            opts = {}
            for key in ("bg", "fg", "activebackground", "activeforeground", "font", "relief", "bd"):
                try:
                    opts[key] = parent_menu.cget(key)
                except tk.TclError:
                    pass
            return tk.Menu(parent_menu, tearoff=0, **opts)

        def refresh_menu(select: str | None = None) -> None:
            names = book.names()                       # `default` toujours en tete
            menu = menu_widget["menu"]
            menu.delete(0, "end")
            layout = gl.menu_layout(names, book.favorites(), book.recents(), filter_var.get())

            def add(target, name: str, label: str | None = None) -> None:
                target.add_command(label=label or name, command=lambda n=name: load_preset(n))

            if layout["found"] is not None:
                for n in layout["found"]:
                    add(menu, n)
                if not layout["found"]:
                    menu.add_command(label="(aucun resultat)", state="disabled")
            else:
                if layout["favorites"]:
                    for n in layout["favorites"]:
                        add(menu, n, "\u2605 " + n)
                    menu.add_separator()
                if layout["recents"]:
                    for n in layout["recents"]:
                        add(menu, n, "\u21ba " + n)
                    menu.add_separator()
                for folder, members in layout["folders"].items():
                    sub = sub_menu(menu)
                    for n in members:
                        add(sub, n, gl.split_folder(n)[1])
                    menu.add_cascade(label=f"{folder}  ({len(members)})", menu=sub)
                for n in layout["root"]:
                    add(menu, n)
            if select is not None:
                name_var.set(select)
            elif name_var.get() not in names:
                name_var.set("default")
            show_state()

        def show_state() -> None:
            name = name_var.get()
            fav_btn.configure(text="\u2605" if name in book.favorites() else "\u2606")

        def toggle_fav() -> None:
            name = name_var.get()
            on = book.toggle_favorite(name)
            msg.set(f"'{name}' " + ("ajoute aux favoris" if on else "retire des favoris"))
            refresh_menu()

        fav_btn.configure(command=toggle_fav)

        def baseline(name: str) -> dict | None:
            """Ce que le preset `name` donnerait comme etat (reglages actuels + valeurs du preset)."""
            presets = book.all()
            if name not in presets:
                return None
            values = values_of(presets[name], params)
            auto_state = values.pop("_automation", None)
            snap = json.loads(json.dumps(params))
            snap.update(values)
            if auto_state is not None:
                snap["_automation"] = auto_state
            return capture(snap)

        def select_preset(name: str) -> None:
            """Affiche `name` dans le menu sans rien charger (une scene a pose ses propres valeurs) ; « modifie » dit s'il s'en ecarte."""
            name_var.set(name)
            base["snap"] = baseline(name)
            show_state()
            check_modified()

        def check_modified() -> None:
            snap = base["snap"]
            changed = snap is not None and gl.differs_from(capture(params), snap, params.get("_automation"))
            text = "\u25cf modifie" if changed else ""
            if mod_var.get() != text:
                mod_var.set(text)
            revert_btn.configure(state="normal" if changed else "disabled")

        def load_preset(name: str) -> None:
            name_var.set(name)
            presets = book.all()
            if name not in presets:
                msg.set(f"preset inconnu: {name}")
                return
            sync_automation_now()
            values = values_of(presets[name], params)
            automation_state = values.pop("_automation", None)
            for key, value in values.items():
                params[key] = value                  # lu a chaque image par le fil GL
            if automation_state is not None:
                params["_automation"] = automation_state
                automation.apply(automation_state)
            sync_widgets()
            book.touch(name)
            base["snap"] = baseline(name)
            refresh_menu()
            check_modified()
            msg.set(f"preset '{name}' charge")

        def update_preset() -> None:
            name = name_var.get()
            sync_automation_now()
            book.save(name, capture(params))
            base["snap"] = baseline(name)
            check_modified()
            note = " - sera le point de depart (Supprimer = version d'origine)" if name == "default" else ""
            msg.set(f"preset '{name}' mis a jour ({book.path.name}){note}")

        def delete_preset() -> None:
            name = name_var.get()

            def do_delete() -> None:
                kind = book.delete(name)
                refresh_menu()
                if kind == "reset":
                    name_var.set("default")
                    msg.set("'default' remis a sa version d'origine (il reste toujours en tete)")
                else:
                    msg.set(f"preset '{name}' supprime")
                base["snap"] = baseline(name_var.get())
                check_modified()

            question = (f"Remettre le preset '{name}' a sa version d'origine ?" if name == "default"
                        else f"Supprimer definitivement le preset '{name}' ?\nCette action est irreversible.")
            live.confirm_dialog(root, "Supprimer le preset", question, do_delete)

        tk.Label(load_row, textvariable=mod_var, fg=SCENE_COLOR, width=10, anchor="w").pack(side="left", padx=(6, 0))
        revert_btn = tk.Button(load_row, text="Revenir", command=lambda: load_preset(name_var.get()), state="disabled")
        revert_btn.pack(side="left", padx=(0, 0))
        Tooltip(revert_btn, "Recharge le preset choisi : annule les changements faits depuis.")
        tk.Button(load_row, text="Mettre a jour", command=update_preset).pack(side="left", padx=(8, 0))
        tk.Button(load_row, text="Supprimer", command=delete_preset).pack(side="left", padx=(6, 0))

        def rename_preset() -> None:
            old = name_var.get()
            if old == "default":
                msg.set("'default' ne se renomme pas")
                return
            asked = scene_ui["ask_name"]("Renommer le preset", "Nouveau nom (dossier/nom pour le ranger dans un dossier) :", old)
            if asked is None:
                return
            new = gl.normalize_preset_name(asked)
            if new is None or not book.rename(old, new):
                msg.set("renommage impossible (nom invalide ou deja pris)")
                return
            if new != old:
                changed = scene_book.rename_preset(kind, old, new)
                refresh_scene_bar()
                msg.set(f"'{old}' devient '{new}'" + (f" ({changed} scene(s) mise(s) a jour)" if changed else ""))
            name_var.set(new)
            base["snap"] = baseline(new)
            refresh_menu(select=new)
            check_modified()

        ren_btn = tk.Button(load_row, text="Renommer", command=rename_preset)
        ren_btn.pack(side="left", padx=(6, 0))
        Tooltip(ren_btn, "Change le nom du preset, ou le range dans un dossier (dossier/nom). Les scenes qui le citent suivent.")

        find_row = tk.Frame(parent)
        find_row.grid(row=z.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        tk.Label(find_row, text="Chercher").pack(side="left", padx=(0, 10))
        find_entry = tk.Entry(find_row, textvariable=filter_var, width=14)
        find_entry.pack(side="left")
        Tooltip(find_entry, "Filtre le menu par nom. Un nom `dossier/preset` range le preset dans un dossier du menu.")
        filter_var.trace_add("write", lambda *_: refresh_menu())

        save_row = tk.Frame(parent)
        save_row.grid(row=z.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        tk.Label(save_row, text="Sauvegarder sous").pack(side="left", padx=(0, 10))
        save_name_var = tk.StringVar(value="")
        save_entry = tk.Entry(save_row, textvariable=save_name_var, width=14)
        save_entry.pack(side="left")
        Tooltip(save_entry, "Un nom `dossier/preset` range le preset dans un dossier (un seul niveau).")

        def save_preset(_evt=None) -> None:
            name = gl.normalize_preset_name(save_name_var.get())
            if name is None:
                msg.set("nom de preset vide ou invalide (un seul dossier : dossier/nom)")
                return
            existed = name in book.all()
            sync_automation_now()
            book.save(name, capture(params))
            filter_var.set("")
            refresh_menu(select=name)
            base["snap"] = baseline(name)
            check_modified()
            save_name_var.set("")
            msg.set(f"preset '{name}' " + ("remplace" if existed else "sauvegarde") + f" ({book.path.name})")

        save_entry.bind("<Return>", save_preset)
        tk.Button(save_row, text="Sauvegarder", command=save_preset).pack(side="left", padx=(8, 0))
        tk.Label(parent, textvariable=msg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=380).grid(
            row=z.next_row(), column=0, columnspan=3, sticky="we", padx=ROW_PADX, pady=(0, 2))
        refresh_menu()
        base["snap"] = baseline(name_var.get())
        ui_every("presets-modifie-" + title, 600, check_modified, 600)
        return {"var": name_var, "msg": msg, "menu": menu_widget, "book": book, "load": load_preset,
                "update": update_preset, "delete": delete_preset, "save": save_preset, "save_var": save_name_var,
                "refresh": refresh_menu, "select": select_preset, "fav": toggle_fav, "fav_btn": fav_btn, "filter": filter_var,
                "modified": mod_var, "revert": revert_btn, "check": check_modified, "rename": rename_preset}

    return types.SimpleNamespace(make_preset_bar=make_preset_bar)
