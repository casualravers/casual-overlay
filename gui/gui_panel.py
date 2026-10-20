"""Panneau etendu du Mode VJ : le set en detail (une ligne par entree : media avec vignette, duree, participation au VJ, ordre), corps du Full Randomizer, reconstruction minimale et mise a jour sur place, poignee de hauteur.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import randomizer
from core import scenes
from pathlib import Path
import json
import skin
import time
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import BANNER_FG, MIN_WIN_H, SCENE_COLOR, STATS, THUMB_H          # import tardif : gl_gui importe ce module
    import queue
    import uuid
    Tooltip = c.Tooltip
    a2w = c.a2w
    a2w_clear = c.a2w_clear
    add_random_scene = c.add_random_scene
    add_random_scenes = c.add_random_scenes
    move_entry_to = c.move_entry_to
    apply_entry = c.apply_entry
    bg_clear = c.bg_clear
    current_index = c.current_index
    dnd = c.dnd
    duplicate_last_scene = c.duplicate_last_scene
    master_box = c.master_box
    media_clear = c.media_clear
    panel_state = c.panel_state
    random_bank = c.random_bank
    random_draw_now = c.random_draw_now
    random_keep = c.random_keep
    redo_now = c.redo_now
    refresh_scene_bar = c.refresh_scene_bar
    register_drop = c.register_drop
    register_tree = c.register_tree
    remove_entry_now = c.remove_entry_now
    rename_scene_dialog = c.rename_scene_dialog
    rnd = c.rnd
    root = c.root
    s = c.s
    scene_book = c.scene_book
    scene_msg = c.scene_msg
    scene_panel = c.scene_panel
    scene_ui = c.scene_ui
    set_entry_vj = c.set_entry_vj
    set_random_option = c.set_random_option
    set_vj_option = c.set_vj_option
    thumb_photo = c.thumb_photo
    undo_now = c.undo_now

    PANEL_BODY_H = 200
    PANEL_BODY_MIN = 80
    NAME_MIN, NAME_MAX = 14, 70                     # largeur (caracteres) du bouton du nom : la plus longue entree, bornee
    DUR_W = 13
    BTN_W = {"Garder": 7, "Renommer": 9, "Retirer": 8}
    panel_state.setdefault("sel", set())                 # lignes cochees (hors signature : cocher ne reconstruit rien)
    BOLD = ("Segoe UI", 9, "bold")

    def row_bg(active: bool) -> str:
        return "#3b3320" if active else a2w.GUI_BG

    def card_bg(active: bool) -> str:
        return "#4a3f26" if active else a2w.GUI_PANEL_BG

    def dur_label(row: dict) -> str:
        return "duree " + ("du set" if row["seconds"] is None else f"{row['seconds']:.0f} s") + " \u25be"

    # ---- selection multiple, scenes provisoires, bibliotheque : operations
    def sel_mark(i: int) -> str:
        return "\u2611" if i in panel_state["sel"] else "\u2610"

    def sel_text() -> str:
        n = len(panel_state["sel"])
        return "aucune ligne cochee" if not n else (f"{n} ligne cochee" if n == 1 else f"{n} lignes cochees")

    def refresh_sel() -> None:
        for k, refs in panel_state["rows"].items():
            try:
                refs["box"].configure(text=sel_mark(k))
            except (tk.TclError, KeyError):
                pass
        var = panel_state.get("sel_var")
        if var is not None:
            var.set(sel_text())

    def toggle_sel(i: int, shift: bool = False) -> None:
        """Coche / decoche une ligne ; avec Maj, toute la plage depuis la derniere ligne cochee."""
        sel, last = panel_state["sel"], panel_state.get("sel_last")
        if shift and last is not None:
            lo, hi = sorted((last, i))
            sel.update(range(lo, hi + 1))
        elif i in sel:
            sel.discard(i)
        else:
            sel.add(i)
        panel_state["sel_last"] = i
        refresh_sel()

    def select_all() -> None:
        panel_state["sel"].update(range(len(scene_book.rows())))
        refresh_sel()

    def select_none() -> None:
        panel_state["sel"].clear()
        panel_state["sel_last"] = None
        refresh_sel()

    def picked() -> list[int]:
        n = len(scene_book.rows())
        idx = sorted(i for i in panel_state["sel"] if i < n)
        if not idx:
            scene_msg.set("Aucune ligne cochee : coche la case a gauche des lignes (Maj = une plage)")
        return idx

    def edited(msg: str, keep_sel: bool = False) -> None:
        if not keep_sel:
            panel_state["sel"].clear()
            panel_state["sel_last"] = None
            s.active_entry = None                            # les rangs ont bouge : la scene active se retrouve par son nom
        scene_msg.set(msg)
        refresh_scene_bar()

    def sel_remove() -> int:
        idx = picked()
        if not idx:
            return 0
        n = scene_book.remove_entries(idx)
        edited(f"{n} entree(s) retiree(s) du set (les scenes restent dans la bibliotheque)")
        return n

    def sel_skip(skip: bool) -> int:
        idx = picked()
        if not idx:
            return 0
        n = scene_book.set_entries_vj(idx, skip=skip)
        edited(f"{n} entree(s) : " + ("le VJ les saute" if skip else "le VJ les joue"), keep_sel=True)
        return n

    def sel_seconds(value: float | None) -> int:
        idx = picked()
        if not idx:
            return 0
        n = scene_book.set_entries_vj(idx, seconds=value)
        edited(f"{n} entree(s) : duree " + ("du set" if value is None else f"{value:.0f} s"), keep_sel=True)
        return n

    def sel_duplicate() -> int:
        idx = picked()
        if not idx:
            return 0
        n = scene_book.duplicate_entries(idx)
        edited(f"{n} entree(s) dupliquee(s) (meme scene, juste apres l'original)")
        return n

    def sel_copy_to(set_name: str) -> int:
        idx = picked()
        if not idx:
            return 0
        n = scene_book.copy_entries_to_set(idx, set_name)
        if n < 0:
            scene_msg.set(f"Set '{set_name}' introuvable")
            return 0
        edited(f"{n} entree(s) copiee(s) a la fin du set '{set_name}'", keep_sel=True)
        return n

    def add_at(name: str, pos: int | None) -> bool:
        """Ajoute une scene de la bibliotheque au set actif, au rang `pos` (None = a la fin)."""
        if not scene_book.add_entry(name, at=pos):
            scene_msg.set(f"Scene '{name}' introuvable")
            return False
        panel_state["sel"].clear()
        panel_state["sel_last"] = None
        s.active_entry = None
        scene_msg.set(f"'{name}' ajoutee au set '{scene_book.active_set_name()}'" + ("" if pos is None else f" en position {pos + 1}"))
        refresh_scene_bar()
        return True

    def keep_now(name: str, new_name: str | None = None) -> str | None:
        """Garde une scene provisoire (tiree au hasard), eventuellement renommee. Rend son nom final (None : refus)."""
        final = scene_book.keep_scene(name, new_name)
        if final is None:
            scene_msg.set("Nom de scene vide ou deja pris : la scene reste provisoire")
            refresh_scene_panel(force=True)
            return None
        if getattr(s, "active_scene", None) == name:
            s.active_scene = final
        scene_msg.set(f"Scene '{final}' gardee")
        refresh_scene_bar()
        return final

    def start_keep(index: int) -> None:
        """Bouton « Garder » d'une ligne provisoire : le nom devient un champ (Entree = garder sous ce nom, Echap = annuler)."""
        refs = panel_state["rows"].get(index)
        rows_ = scene_book.rows()
        if refs is None or not 0 <= index < len(rows_):
            return
        name, btn = rows_[index]["name"], refs["name"]
        var = tk.StringVar(value=name)
        ent = tk.Entry(refs["frame"], textvariable=var, width=panel_state["name_w"])
        ent.pack(side="left", fill="y", padx=4, pady=2, before=btn)
        btn.pack_forget()
        ent.focus_set()
        ent.select_range(0, "end")
        ent.bind("<Return>", lambda e: keep_now(name, var.get()))
        ent.bind("<Escape>", lambda e: refresh_scene_panel(force=True))
        refs["keep_entry"] = ent

    def drop_temp(ask: bool = True) -> int:
        """« Retirer les provisoires » : supprime de la bibliotheque (donc de tous les sets) les scenes tirees au hasard jamais gardees."""
        names = scene_book.temp_names()
        if not names:
            scene_msg.set("Aucune scene provisoire (les scenes tirees par + / +5 portent un \u2736 jusqu'a ce qu'on les garde)")
            return 0
        if ask:
            choice = scene_ui["chooser"]("Retirer les scenes provisoires",
                                         f"Supprimer {len(names)} scene(s) provisoire(s) de la bibliotheque et de tous les sets ?\n"
                                         "Celles que tu as gardees ne sont pas touchees. Cette action est irreversible.", ["Retirer", "Annuler"])
            if choice != "Retirer":
                return 0
        for nm in names:
            scene_book.delete(nm)
            if getattr(s, "active_scene", None) == nm:
                s.active_scene, s.active_entry = None, None
        edited(f"{len(names)} scene(s) provisoire(s) retiree(s)")
        return len(names)

    def drop_pos(x_root: int, y_root: int) -> int | None:
        """Rang d'insertion sous le pointeur (coordonnees ecran) si celui-ci est au-dessus de la liste du set, sinon None."""
        cv_ = panel_state.get("cv")
        if cv_ is None:
            return None
        try:
            left, top_ = cv_.winfo_rootx(), cv_.winfo_rooty()
            if not (left <= x_root < left + cv_.winfo_width() and top_ <= y_root < top_ + cv_.winfo_height()):
                return None
            frames = sorted((r["frame"].winfo_rooty(), r["frame"].winfo_height(), r["index"]) for r in panel_state["rows"].values())
        except tk.TclError:
            return None
        for ftop, height, index in frames:
            if y_root < ftop + height:
                return index if y_root < ftop + height / 2 else index + 1
        return len(frames)

    def mark_target(pos: int | None) -> None:
        for r in panel_state["rows"].values():
            try:
                r["frame"].configure(highlightthickness=0)
            except tk.TclError:
                pass
        rows_ = panel_state["rows"]
        if pos is None or not rows_:
            return
        r = rows_.get(min(pos, max(rows_)))
        if r:
            r["frame"].configure(highlightthickness=2, highlightbackground=SCENE_COLOR, highlightcolor=SCENE_COLOR)

    def toggle_library() -> None:
        """Ouvre / ferme la colonne sans reconstruire le panneau : seule la colonne est creee ou detruite."""
        s.library_open = not getattr(s, "library_open", False)
        wrap, body = panel_state.get("wrap"), panel_state.get("body")
        try:
            ok = wrap is not None and body is not None and wrap.winfo_exists()
        except tk.TclError:
            ok = False
        if not ok:
            refresh_scene_panel(force=True)
            return
        if s.library_open:
            build_library(wrap, scene_book.rows(), before=body)
            skin.apply(panel_state["lib"]["box"])
        else:
            lib = panel_state.pop("lib", None)
            if lib:
                lib["box"].destroy()
        btn = panel_state.get("lib_btn")
        if btn is not None:
            try:
                btn.configure(text="Bibliotheque " + ("\u25c0" if s.library_open else "\u25b6"))
            except tk.TclError:
                pass

    LIB_THUMB = (48, 27)

    def lib_photo(scene: dict):
        """Vignette de la bibliotheque (48 x 27) tiree de l'apercu de la scene, gardee en cache ; None sans apercu."""
        name = scene.get("preview")
        if not name:
            return None
        path = scenes.PREVIEW_DIR / Path(name).name
        try:
            key = (str(path), path.stat().st_mtime_ns, "lib")
        except OSError:
            return None
        cache = s.__dict__.setdefault("preview_photos", {})
        if key not in cache:
            try:
                from PIL import Image, ImageTk
                img = Image.open(path)
                img.load()
                img.thumbnail(LIB_THUMB)
                cache[key] = ImageTk.PhotoImage(img)
            except Exception:
                return None
        return cache[key]

    def library_signature(rows: list[dict], needle: str) -> tuple:
        used: dict[str, int] = {}
        for r in rows:
            used[r["name"].lower()] = used.get(r["name"].lower(), 0) + 1
        lib = scene_book.list()
        return (tuple((x["name"], bool(x.get("star")), bool(x.get("temp")), x.get("preview") or "") for x in lib),
                tuple(sorted(used.items())), needle.strip().lower())

    def fill_library(lib: dict, rows: list[dict]) -> None:
        """(Re)remplit l'arbre : favoris, dossiers (un niveau), scenes de la racine ; les dossiers ouverts le restent, la position est gardee."""
        tv = lib["tree"]
        needle = lib["filter"].get()
        sig = library_signature(rows, needle)
        if sig == lib.get("sig"):
            return
        lib["sig"] = sig
        used = dict(sig[1])
        tree = scenes.library_tree(scene_book.list(), needle)
        by_name = {x["name"]: x for x in scene_book.list()}
        top = tv.yview()[0]
        tv.delete(*tv.get_children())
        lib["map"], lib["iids"], names = {}, {}, []
        searching = bool(needle.strip())

        def add(parent: str, iid: str, name: str, shown: str, photo: bool) -> None:
            sc_ = by_name[name]
            label = ("\u2605 " if sc_.get("star") else "") + ("\u2736 " if sc_.get("temp") else "") + shown
            n = used.get(name.lower(), 0)
            if n:
                label += f"   \u00d7{n}"
            tv.insert(parent, "end", iid=iid, text=" " + label, tags=("scene",))
            lib["map"][iid] = name
            lib["iids"].setdefault(name, iid)
            if photo:
                img = lib_photo(sc_)
                if img is not None:
                    tv.item(iid, image=img)

        if tree["starred"]:
            tv.insert("", "end", iid="F:*", text=f" \u2605 Favoris  ({len(tree['starred'])})", open=True, tags=("folder",))
            for name in tree["starred"]:
                add("F:*", "S:*:" + name, name, name, True)
        for folder, members in tree["folders"].items():
            is_open = searching or folder in lib["open"]
            tv.insert("", "end", iid="F:" + folder, text=f" {folder}  ({len(members)})", open=is_open, tags=("folder",))
            for name in members:
                add("F:" + folder, "S:" + name, name, scenes.scene_folder(name)[1], is_open)
        for name in tree["root"]:
            add("", "S:" + name, name, name, True)
        names.extend(n for g in tree["folders"].values() for n in g)
        names.extend(tree["root"])
        names.sort(key=str.lower)
        lib["names"][:] = names
        shown_n, total = tree["shown"], tree["total"]
        lib["title"].configure(text=f"BIBLIOTHEQUE  ({shown_n}" + (f" / {total})" if shown_n != total else ")"))
        try:
            tv.yview_moveto(top)
        except tk.TclError:
            pass

    def refresh_library(rows: list[dict]) -> None:
        """Met la colonne a jour sur place ; rien si rien n'a change."""
        lib = panel_state.get("lib")
        if lib:
            try:
                fill_library(lib, rows)
            except tk.TclError:
                pass

    def build_library(parent, rows: list[dict], before=None) -> None:
        """Colonne « bibliotheque » a gauche de la liste : favoris, dossiers (`dossier/nom`), scenes, avec une vignette quand la scene a un apercu.
        Glisser-deposer vers la liste = ajouter au set a cet endroit. Un champ de recherche filtre par nom ; mise a jour sur place (`refresh_library`)."""
        from tkinter import ttk
        box = tk.Frame(parent, bg=a2w.GUI_PANEL_BG, width=250)
        if before is not None:
            box.pack(side="left", fill="y", padx=(0, 2), before=before)
        else:
            box.pack(side="left", fill="y", padx=(0, 2))
        box.pack_propagate(False)
        title = tk.Label(box, text="BIBLIOTHEQUE", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, font=("Segoe UI", 8, "bold"), anchor="w")
        title.pack(fill="x", padx=6, pady=(3, 0))
        filter_var = tk.StringVar(value="")
        search = tk.Entry(box, textvariable=filter_var)
        search.pack(fill="x", padx=6, pady=(2, 0))
        Tooltip(search, "Tape une partie du nom pour filtrer la bibliotheque (les dossiers s'ouvrent).")
        style = ttk.Style(root)
        style.configure("Lib.Treeview", background=a2w.GUI_BG, fieldbackground=a2w.GUI_BG, foreground=a2w.GUI_FG, rowheight=LIB_THUMB[1] + 4, borderwidth=0)
        style.map("Lib.Treeview", background=[("selected", SCENE_COLOR)], foreground=[("selected", BANNER_FG)])
        style.layout("Lib.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        lbar = skin.scrollbar(box)
        tv = ttk.Treeview(box, style="Lib.Treeview", show="tree", selectmode="browse", yscrollcommand=lbar.set)
        tv.tag_configure("folder", foreground=SCENE_COLOR)
        lbar.config(command=tv.yview)
        lbar.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=2)
        lib = {"tree": tv, "lb": tv, "names": [], "box": box, "title": title, "filter": filter_var, "sig": None,
               "map": {}, "iids": {}, "open": set()}
        panel_state["lib"] = lib
        fill_library(lib, rows)
        filter_var.trace_add("write", lambda *_: refresh_library(scene_book.rows()))
        Tooltip(tv, "Toutes les scenes enregistrees, rangees par dossier (nom `dossier/scene`) avec les favoris en tete. Glisse une scene sur la liste du set "
                    "pour l'y ajouter a cet endroit (autant de fois que tu veux), double-clic = a la fin, clic droit = favori / renommer / ranger / garder. "
                    "\u00d7N = nombre d'entrees dans ce set.")

        def folder_of(iid: str) -> str:
            return iid[2:]

        def on_open(_e=None) -> None:
            iid = tv.focus()
            if iid.startswith("F:") and iid != "F:*":
                lib["open"].add(folder_of(iid))
                for child in tv.get_children(iid):                      # les vignettes des dossiers fermes ne sont chargees qu'a l'ouverture
                    name = lib["map"].get(child)
                    scene = next((x for x in scene_book.list() if x["name"] == name), None)
                    if scene is not None and not tv.item(child, "image"):
                        img = lib_photo(scene)
                        if img is not None:
                            tv.item(child, image=img)

        def on_close(_e=None) -> None:
            iid = tv.focus()
            if iid.startswith("F:"):
                lib["open"].discard(folder_of(iid))

        tv.bind("<<TreeviewOpen>>", on_open)
        tv.bind("<<TreeviewClose>>", on_close)
        d: dict = {}

        def scene_at(y: int) -> str | None:
            iid = tv.identify_row(y)
            return lib["map"].get(iid) if iid else None

        def press(e) -> None:
            name = scene_at(e.y)
            if name is not None:
                d.clear()
                d.update(name=name, x=e.x_root, y=e.y_root, moved=False, ghost=None)
                tv.selection_set(tv.identify_row(e.y))
            else:
                d.clear()

        def motion(e) -> None:
            if not d:
                return
            if not d["moved"] and abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) < 6:
                return
            d["moved"] = True
            if d["ghost"] is None:
                d["ghost"] = tk.Label(root, text=d["name"], bg=SCENE_COLOR, fg=BANNER_FG, font=BOLD, padx=8, pady=2)
            d["ghost"].place(x=e.x_root - root.winfo_rootx() + 12, y=e.y_root - root.winfo_rooty() + 6)
            d["ghost"].lift()
            mark_target(drop_pos(e.x_root, e.y_root))

        def release(e) -> None:
            if not d:
                return
            ghost, moved, name = d.get("ghost"), d["moved"], d["name"]
            d.clear()
            if ghost is not None:
                ghost.destroy()
            mark_target(None)
            if moved:
                pos = drop_pos(e.x_root, e.y_root)
                if pos is not None:
                    add_at(name, pos)

        def double(e) -> None:
            name = scene_at(e.y)
            if name is not None:
                d.clear()
                add_at(name, None)

        def star_now(name: str) -> None:
            on = not any(x["name"] == name and x.get("star") for x in scene_book.list())
            scene_book.set_star(name, on)
            scene_msg.set(f"'{name}' " + ("ajoutee aux favoris" if on else "retiree des favoris"))
            refresh_library(scene_book.rows())

        def move_dialog(name: str) -> None:
            folder, leaf = scenes.scene_folder(name)
            asked = scene_ui["ask_name"]("Ranger la scene", "Dossier (vide = a la racine) :", folder)
            if asked is None:
                return
            asked = asked.strip().strip("/").strip()
            if scenes.FOLDER_SEP in asked:
                scene_msg.set("Un seul niveau de dossier")
                return
            new = f"{asked}{scenes.FOLDER_SEP}{leaf}" if asked else leaf
            if new == name:
                return
            if not scene_book.rename_scene(name, new):
                scene_msg.set("Nom de scene deja pris")
                return
            if getattr(s, "active_scene", None) == name:
                s.active_scene = new
            scene_msg.set(f"'{name}' rangee : '{new}'")
            refresh_scene_bar()

        def menu_at(e) -> None:
            name = scene_at(e.y)
            if name is None:
                return
            tv.selection_set(tv.identify_row(e.y))
            starred = any(x["name"] == name and x.get("star") for x in scene_book.list())
            m = tk.Menu(tv, tearoff=0)
            m.add_command(label="Ajouter a la fin du set", command=lambda: add_at(name, None))
            m.add_command(label="Retirer des favoris" if starred else "Mettre en favori", command=lambda: star_now(name))
            m.add_command(label="Renommer...", command=lambda: rename_scene_dialog(name))
            m.add_command(label="Ranger dans un dossier...", command=lambda: move_dialog(name))
            if any(x["name"] == name and x.get("temp") for x in scene_book.list()):
                m.add_command(label="Garder (n'est plus provisoire)", command=lambda: keep_now(name))
            m.tk_popup(e.x_root, e.y_root)

        tv.bind("<ButtonPress-1>", press, add="+")
        tv.bind("<B1-Motion>", motion)
        tv.bind("<ButtonRelease-1>", release)
        tv.bind("<Double-Button-1>", double, add="+")
        tv.bind("<Button-3>", menu_at)
        lib["star"], lib["move"] = star_now, move_dialog

    def build_selbar(parent) -> tk.Frame:
        """Barre des actions sur les lignes cochees (retirer, VJ off / on, duree, dupliquer, copier vers un autre set) et de la bibliotheque."""
        bar_ = tk.Frame(parent, bg=a2w.GUI_PANEL_BG)
        var = tk.StringVar(value=sel_text())
        panel_state["sel_var"] = var

        def mini(text: str, cmd, tip: str) -> tk.Button:
            b = tk.Button(bar_, text=text, command=cmd, relief="flat", bd=0, padx=8, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
            b.pack(side="left", padx=2, pady=3)
            Tooltip(b, tip)
            return b
        tk.Label(bar_, text="LIGNES COCHEES", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 4))
        mini("Tout", select_all, "Coche toutes les lignes du set.")
        mini("Aucune", select_none, "Decoche tout.")
        tk.Label(bar_, textvariable=var, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=6)
        mini("Retirer du set", sel_remove, "Retire les lignes cochees du set (les scenes restent dans la bibliotheque).")
        mini("VJ off", lambda: sel_skip(True), "Le VJ saute les lignes cochees (elles restent chargeables a la main).")
        mini("VJ on", lambda: sel_skip(False), "Le VJ rejoue les lignes cochees.")
        dur = tk.Menubutton(bar_, text="Duree \u25be", relief="flat", bd=0, padx=8, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                            activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        dur.pack(side="left", padx=2, pady=3)
        dm_ = tk.Menu(dur, tearoff=0)
        dm_.add_command(label="Duree du set", command=lambda: sel_seconds(None))
        for value in scenes.VJ_SECONDS:
            dm_.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min", command=lambda v=value: sel_seconds(float(v)))
        dur["menu"] = dm_
        Tooltip(dur, "Duree propre des lignes cochees dans le VJ.")
        mini("Dupliquer", sel_duplicate, "Copie chaque ligne cochee juste apres elle (meme scene, memes medias, meme duree).")
        others = [n for n in scene_book.set_names() if n != scene_book.active_set_name()]
        cp = tk.Menubutton(bar_, text="Copier vers le set \u25be", relief="flat", bd=0, padx=8, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                           activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        cp.pack(side="left", padx=2, pady=3)
        cm = tk.Menu(cp, tearoff=0)
        for name in others:
            cm.add_command(label=name, command=lambda n=name: sel_copy_to(n))
        if not others:
            cm.add_command(label="(aucun autre set)", state="disabled")
        cp["menu"] = cm
        Tooltip(cp, "Copie les lignes cochees (avec leurs medias) a la fin d'un autre set.")
        mini("Retirer les \u2736", drop_temp, "Supprime toutes les scenes provisoires (\u2736 : tirees par + / +5 et jamais gardees) de la bibliotheque.")
        lib_btn = tk.Button(bar_, text="Bibliotheque " + ("\u25c0" if getattr(s, "library_open", False) else "\u25b6"), command=toggle_library,
                            relief="flat", bd=0, padx=8, cursor="hand2", bg=SCENE_COLOR, fg=BANNER_FG, font=BOLD)
        lib_btn.pack(side="right", padx=8, pady=3)
        panel_state["lib_btn"] = lib_btn
        Tooltip(lib_btn, "Ouvre la liste de toutes les scenes a gauche du set : glisse-en une sur la liste pour l'ajouter a l'endroit voulu.")
        return bar_

    # ---- apercu des scenes : capture reduite de la projection (fichier dans scenes.PREVIEW_DIR, nom dans scene["preview"])
    PREV_W, PREV_H = 96, 54

    def preview_photo(scene: dict):
        name = scene.get("preview")
        if not name:
            return None
        path = scenes.PREVIEW_DIR / Path(name).name
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        cache = s.__dict__.setdefault("preview_photos", {})
        if key not in cache:
            try:
                from PIL import Image, ImageTk
                img = Image.open(path)
                img.load()
                img.thumbnail((PREV_W, PREV_H))
                cache[key] = ImageTk.PhotoImage(img)
            except Exception:
                return None
        return cache[key]

    def request_preview(scene_name: str) -> bool:
        """Demande a la projection une capture reduite de ce qu'elle affiche et l'associe a la scene. False si la projection est fermee."""
        proj = getattr(s, "projection", None)
        if proj is None or not proj.running():
            scene_msg.set("Apercu : ouvre la projection (bouton Demarrer la projection) pour capturer l'image de la scene")
            return False
        fname = f"{uuid.uuid4().hex[:12]}.jpg"
        dest = scenes.PREVIEW_DIR / fname
        s.__dict__.setdefault("preview_requests", queue.SimpleQueue()).put(str(dest))     # (Session l'a deja ; un faux de test non)

        def poll(tries: int = 0) -> None:
            if dest.exists():
                if scene_book.set_preview(scene_name, fname):
                    refresh_scene_bar()
            elif tries < 20:
                root.after(150, lambda: poll(tries + 1))
        root.after(150, poll)
        return True

    def schedule_preview(scene_name: str) -> None:
        """Scene qui vient d'etre posee et qui n'a pas d'apercu : on laisse l'image se former (1,5 s) puis on la capture."""
        proj = getattr(s, "projection", None)
        if proj is None or not proj.running():
            return
        scene = scene_book.get(scene_name)
        if scene is None:
            return
        have = scene.get("preview")
        if have and (scenes.PREVIEW_DIR / Path(have).name).exists():
            return
        if scene_name in panel_state.setdefault("preview_pending", set()):
            return
        panel_state["preview_pending"].add(scene_name)

        def go() -> None:
            panel_state["preview_pending"].discard(scene_name)
            if getattr(s, "active_scene", None) == scene_name:       # toujours a l'ecran : sinon on n'est plus sur la scene, tant pis
                request_preview(scene_name)
        root.after(1500, go)

    def build_panel_row(parent, row: dict, active: bool) -> dict:
        """Une ligne du panneau ; rend ses references (`frame`, `num`, `name`, `dur`, `cards`, `active`) pour la repeindre sur place."""
        bg = row_bg(active)
        i = row["index"]
        refs: dict = {"index": i, "cards": [], "active": active}
        fr = tk.Frame(parent, bg=bg)
        fr.pack(fill="x", pady=1, padx=2)
        refs["frame"] = fr
        temp = bool(row["scene"].get("temp"))                   # scene tiree au hasard et pas encore gardee
        refs["box"] = tk.Label(fr, text=sel_mark(i), width=2, bg=bg, fg=SCENE_COLOR, font=BOLD, cursor="hand2")
        refs["box"].pack(side="left", padx=(2, 0))
        refs["box"].bind("<Button-1>", lambda e, k=i: toggle_sel(k, bool(e.state & 0x1)))
        refs["num"] = tk.Label(fr, text=str(i + 1) + ("\u2736" if temp else ""), width=4, bg=bg, fg=SCENE_COLOR, font=BOLD)
        refs["num"].pack(side="left")
        pbox = tk.Frame(fr, bg=bg, width=PREV_W + 4, height=PREV_H + 2)               # apercu de la scene (colonne de largeur fixe)
        pbox.pack(side="left", padx=2)
        pbox.pack_propagate(False)
        photo = preview_photo(row["scene"])
        pic = tk.Label(pbox, image=photo, bg=bg) if photo else tk.Label(pbox, text="pas d'apercu", bg=bg, fg=a2w.GUI_MUTED_FG, font=("Segoe UI", 7))
        pic.image = photo
        pic.pack(expand=True)
        refs["prevbox"], refs["prevpic"] = pbox, pic
        Tooltip(pic, "Apercu de la scene : capture de la projection (a l'enregistrement, ou au premier passage projection ouverte). "
                     "Clic droit sur la touche de la scene : mettre a jour l'apercu.")
        shown = row["name"]                             # en entier (largeur commune a toutes les lignes : panel_state["name_w"])
        name_btn = tk.Button(fr, text=shown, command=lambda k=i: apply_entry(k), relief="flat", bd=0, cursor="hand2", font=BOLD,
                             bg=SCENE_COLOR if active else a2w.GUI_PANEL_BG, fg=BANNER_FG if active else a2w.GUI_FG,
                             activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        name_btn._skin_stretch = True                  # remplit la hauteur de la ligne ; meme largeur sur toutes les lignes
        name_btn.configure(width=panel_state["name_w"])
        name_btn.pack(side="left", fill="y", padx=4, pady=2)
        refs["name"] = name_btn
        scene = row["scene"]
        snap = scene.get("bg") == "live" and scene.get("mode") == "snap"
        own_a2w = row["a2w"] or {}
        scene_a2w = scenes.scene_a2w_media(scene)

        def zone(title: str, width: int, own: dict | None, inherited: dict | None, clear, target: str, enabled: bool = True) -> None:
            """Une zone de depot : media propre a l'entree (vif, avec sa croix), sinon celui que la scene a capture (en sourdine)."""
            cbg = card_bg(active)                                      # carte de la zone
            z = tk.Frame(fr, bg=cbg, width=width, height=THUMB_H + 16, highlightthickness=0)
            z.pack(side="left", padx=3)
            z.pack_propagate(False)
            refs["cards"].append(z)
            tk.Label(z, text=title, bg=cbg, fg=SCENE_COLOR if enabled else a2w.GUI_MUTED_FG, font=("Segoe UI", 7, "bold"),
                     anchor="w", padx=4).pack(fill="x")
            inner = tk.Frame(z, bg=cbg)
            inner.pack(fill="both", expand=True)
            shown = own or inherited
            if not enabled:
                tk.Label(inner, text="scene Snap seulement", bg=cbg, fg=a2w.GUI_MUTED_FG, anchor="w").pack(side="left")
            elif shown:
                missing = bool(scenes.a2w_missing({"v": shown["logo_video"]})) if target != "overlay" else scenes.media_missing(shown)
                if own:
                    tk.Button(inner, text="\u2715", command=clear, relief="flat", bd=0, cursor="hand2", bg=cbg,
                              fg=a2w.GUI_MUTED_FG).pack(side="right")
                photo = thumb_photo(shown)
                thumb = tk.Label(inner, image=photo, bg=cbg) if photo else tk.Label(
                    inner, text="T" if shown.get("logo_source") == "text" else "\u25a0", width=5, bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG)
                thumb.image = photo
                thumb.pack(side="left")
                label = scenes.media_label(shown) + ("  (introuvable)" if missing else "")
                tk.Label(inner, text=label if len(label) <= 22 else label[:21] + ".", anchor="w", bg=cbg,
                         fg="#ff8a8a" if missing else (a2w.GUI_FG if own else a2w.GUI_MUTED_FG)).pack(side="left", padx=4)
            else:
                tk.Label(inner, text="glisse un fichier ici", bg=cbg, fg=a2w.GUI_MUTED_FG, anchor="w").pack(side="left")
            if enabled:
                register_tree(z, i, target)

        def as_video(path: str | None) -> dict | None:
            return {"logo_source": "video", "logo_video": path} if path else None
        media = row["media"]
        own_media = scenes.capture_media(scene.get("overlay") or {})
        if own_media.get("logo_source", "image") == "image" and not own_media.get("logo_path"):
            own_media = None                               # la scene n'a pas de logo
        zone("OVERLAY", 215, media, own_media, lambda k=i: media_clear(k), "overlay")
        zone("FOND  video", 165, as_video(row.get("bg_video")), as_video(scenes.scene_bg_video(scene)),
             lambda k=i: bg_clear(k), "bgvideo")
        zone("AUDIO2WAVE  video int.", 165, as_video(own_a2w.get("video")), as_video(scene_a2w.get("video")),
             lambda k=i: a2w_clear(k, "video"), "video", snap)
        zone("AUDIO2WAVE  video ext.", 165, as_video(own_a2w.get("video2")), as_video(scene_a2w.get("video2")),
             lambda k=i: a2w_clear(k, "video2"), "video2", snap)
        dur = tk.Menubutton(fr, text=dur_label(row), relief="flat", bd=0, padx=6, cursor="hand2", bg=a2w.GUI_PANEL_BG,
                            fg=a2w.GUI_MUTED_FG, activebackground=a2w.GUI_BG, activeforeground=a2w.GUI_FG)
        dur.configure(width=DUR_W)                       # colonnes alignees : meme largeur sur toutes les lignes
        dur.pack(side="left", fill="y", padx=4, pady=2)
        refs["dur"] = dur
        dmenu = tk.Menu(dur, tearoff=0)
        dmenu.add_command(label="Duree du set", command=lambda k=i: set_entry_vj(k, seconds=None))
        for value in scenes.VJ_SECONDS:
            dmenu.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min",
                              command=lambda k=i, v=value: set_entry_vj(k, seconds=float(v)))
        dur["menu"] = dmenu
        var = tk.IntVar(value=0 if row["skip"] else 1)
        cb = tk.Checkbutton(fr, text="VJ", variable=var, bg=bg, fg=a2w.GUI_FG, selectcolor=a2w.GUI_PANEL_BG, bd=0,
                            activebackground=bg, activeforeground=a2w.GUI_FG,
                            command=lambda k=i, v=var: set_entry_vj(k, skip=not v.get()))
        cb.var = var
        cb.pack(side="left", padx=4)
        Tooltip(cb, "Coche : le VJ joue cette entree. Decoche : elle reste chargeable a la main (clic, F-touche) mais le VJ la saute.")
        acts = [("Renommer", lambda nm=row["name"]: rename_scene_dialog(nm), True), ("Retirer", lambda k=i: remove_entry_now(k), True)]
        if temp:
            acts.insert(0, ("Garder", lambda k=i: start_keep(k), True))
        for text, cmd, ok in acts:
            b = tk.Button(fr, text=text, command=cmd, state="normal" if ok else "disabled", relief="flat", bd=0, padx=6, width=BTN_W[text],
                          cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, disabledforeground=a2w.GUI_MUTED_FG,
                          activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            b._skin_stretch = True                         # meme hauteur que la ligne (et que le bouton du nom)
            b.pack(side="left", fill="y", padx=1, pady=2)
        register_drop(fr, i)
        register_drop(name_btn, i)
        grab = refs["num"]                                  # poignee : glisser le numero pour changer l'ordre
        grab.configure(cursor="fleur")
        Tooltip(grab, ("\u2736 scene provisoire (tiree au hasard) : Garder pour la conserver, Retirer pour l'ecarter. " if temp else "")
                + "Glisse ce numero vers le haut ou le bas pour changer l'ordre des scenes.")
        grab.bind("<ButtonPress-1>", lambda e, k=i: row_drag_start(k))
        grab.bind("<B1-Motion>", lambda e, k=i: row_drag_move(k, e.y_root))
        grab.bind("<ButtonRelease-1>", lambda e, k=i: row_drag_end(k, e.y_root))
        return refs

    def row_target(y_root: int) -> int:
        """Rang de la ligne du panneau sous l'ordonnee ecran `y_root` (borne aux lignes existantes)."""
        frames = sorted(((r["frame"].winfo_rooty(), r["frame"].winfo_height(), r["index"]) for r in panel_state["rows"].values()))
        if not frames:
            return 0
        for top, height, index in frames:
            if y_root < top + height:
                return index
        return frames[-1][2]

    def row_drag_start(index: int) -> None:
        panel_state["drag"] = {"from": index, "to": index}
        refs = panel_state["rows"].get(index)
        if refs:
            refs["frame"].configure(highlightthickness=2, highlightbackground=SCENE_COLOR)

    def row_drag_move(index: int, y_root: int) -> None:
        d = panel_state.get("drag")
        if not d:
            return
        target = row_target(y_root)
        if target != d["to"]:
            for r in panel_state["rows"].values():            # repere : la ligne visee est entouree
                r["frame"].configure(highlightthickness=2 if r["index"] == target else 0, highlightbackground=SCENE_COLOR)
            d["to"] = target

    def row_drag_end(index: int, y_root: int) -> None:
        d = panel_state.pop("drag", None)
        if not d:
            return
        target = row_target(y_root)
        for r in panel_state["rows"].values():
            try:
                r["frame"].configure(highlightthickness=0)
            except tk.TclError:
                pass
        if target != index:
            move_entry_to(index, target)

    def restyle_row(refs: dict, active: bool) -> None:
        """Repeint une ligne quand seule la scene active change : fond de ligne, cartes, bouton du nom (aucun widget reconstruit)."""
        if refs["active"] == active:
            return
        old_card, new_card = card_bg(refs["active"]).lower(), card_bg(active)
        bg = row_bg(active)
        refs["active"] = active
        try:
            refs["frame"].configure(bg=bg)
            refs["num"].configure(bg=bg)
            refs["box"].configure(bg=bg)
            refs["prevbox"].configure(bg=bg)
            refs["prevpic"].configure(bg=bg)
            for w in refs["frame"].winfo_children():
                if w is refs["name"]:
                    w.configure(bg=SCENE_COLOR if active else a2w.GUI_PANEL_BG, fg=BANNER_FG if active else a2w.GUI_FG)
                    skin.retint(w, bg)
                elif w in refs["cards"]:
                    stack = [w]
                    while stack:
                        node = stack.pop()
                        cls = node.winfo_class()
                        if cls in ("Frame", "Label") and str(node.cget("bg")).lower() == old_card:
                            node.configure(bg=new_card)
                        elif cls == "Button":
                            node.configure(bg=new_card)
                            skin.retint(node, new_card)
                        stack.extend(node.winfo_children())
                elif w.winfo_class() in ("Button", "Checkbutton", "Menubutton"):
                    skin.retint(w, bg)
        except tk.TclError:
            pass                                           # widget detruit entre-temps (reconstruction du panneau)

    def build_random_body(inner, settings: dict) -> None:
        """Corps du panneau etendu en ordre « Full Randomizer » : banques d'assets, sources du fond et de l'overlay, verrous."""
        rs = settings["random"]
        bg = a2w.GUI_BG
        small = ("Segoe UI", 8, "bold")

        def line(title: str) -> tk.Frame:
            fr = tk.Frame(inner, bg=bg)
            fr.pack(fill="x", padx=8, pady=3)
            tk.Label(fr, text=title, width=15, anchor="w", bg=bg, fg=SCENE_COLOR, font=small).pack(side="left")
            return fr

        def check(parent, text: str, checked: bool, command, tip: str = "") -> tk.Checkbutton:
            var = tk.IntVar(value=int(checked))
            cb = tk.Checkbutton(parent, text=text, variable=var, command=lambda: command(bool(var.get())), bg=bg, fg=a2w.GUI_FG,
                                selectcolor=a2w.GUI_PANEL_BG, bd=0, activebackground=bg, activeforeground=a2w.GUI_FG)
            cb.var = var
            cb.pack(side="left", padx=(0, 10))
            if tip:
                Tooltip(cb, tip)
            return cb

        def member(key: str, value: str):
            def toggle(on: bool) -> None:
                items = [x for x in scene_book.vj()["random"][key] if x != value]
                set_random_option(**{key: items + [value] if on else items})
            return toggle

        # ---- banques : FOND (videos plein cadre) et OVERLAY (images, animations, videos), autant de dossiers que l'on veut.
        # C'est le dossier qui classe, jamais l'extension (un .mov peut etre une animation comme une video).
        banks = random_bank(block=False)                  # scan en arriere-plan : le panneau s'ouvre tout de suite

        def bank_block(which: str, title: str, tip: str) -> None:
            bank = banks[which]
            fkey, ckey = f"folders_{which}", f"categories_{which}"
            fr = line(title)
            for folder in rs[fkey]:
                box = tk.Frame(fr, bg=a2w.GUI_PANEL_BG)
                box.pack(side="left", padx=(0, 6))
                ok = Path(folder).is_dir()
                tk.Label(box, text=("" if ok else "\u26a0 ") + (folder if len(folder) <= 30 else "..." + folder[-27:]),
                         bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG if ok else "#ff8a8a", padx=6).pack(side="left")
                tk.Button(box, text="\u2715", command=lambda f=folder: set_random_option(
                    refresh=True, **{fkey: [x for x in scene_book.vj()["random"][fkey] if x != f]}),
                    relief="flat", bd=0, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left")

            def add_folder() -> None:
                folder = scene_ui["ask_dir"]()
                if folder and folder not in scene_book.vj()["random"][fkey]:
                    set_random_option(refresh=True, **{fkey: scene_book.vj()["random"][fkey] + [folder]})

            add_btn = tk.Button(fr, text="+ Dossier", command=add_folder, relief="flat", bd=0, padx=8, cursor="hand2",
                                bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR)
            add_btn.pack(side="left", padx=2)
            Tooltip(add_btn, tip)
            n_img = sum(1 for a in bank if a.kind == "image")
            if which == "fond":
                count = f"{len(bank)} video(s)"
            else:
                count = f"{n_img} image(s), {len(bank) - n_img} video(s) / animation(s)"
            tk.Label(fr, text="scan en cours..." if random_bank.busy() else count if bank else ("vide : ajoute un dossier" if not rs[fkey] else "aucun fichier trouve"),
                     bg=bg, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
            if which == "overlay":
                scan_btn = tk.Button(fr, text="Rescanner", command=lambda: (random_bank(True, block=False), refresh_scene_panel(force=True)), relief="flat",
                                     bd=0, padx=8, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
                scan_btn.pack(side="left", padx=2)
                Tooltip(scan_btn, "Relit les dossiers des deux banques (nouveaux fichiers ajoutes depuis).")
            cats = randomizer.categories(bank)
            if len(cats) > 1:
                cfr = line("  categories")
                chosen = set(rs[ckey] or cats)
                for cat in cats:
                    def toggle_cat(on: bool, cat=cat, ckey=ckey, cats=cats) -> None:
                        now = set(scene_book.vj()["random"][ckey] or cats)
                        now = (now | {cat}) if on else (now - {cat})
                        set_random_option(**{ckey: [] if now >= set(cats) else sorted(now)})
                    check(cfr, f"{cat} ({sum(1 for a in bank if a.category == cat)})", cat in chosen, toggle_cat,
                          "Les categories (sous-dossiers) de cette banque dans lesquelles piocher.")

        bank_block("fond", "BANQUE FOND", "Dossiers de VIDEOS plein cadre : elles servent de fond. Pour qu'une video serve aussi de logo, "
                                          "declare son dossier dans la banque OVERLAY.")
        bank_block("overlay", "BANQUE OVERLAY", "Dossiers d'images, d'animations et de videos : ils servent de logo (le dossier decide, "
                                                "pas l'extension : un .mov peut etre une animation).")
        # ---- fond
        fr = line("FOND")
        for key, label, tip in (("preset", "preset de fond", "Un preset de fond genere (section PRESETS FOND)."),
                                ("a2w", "preset audio2wave", "Un preset du mode audio2wave courant (Live / Snap / Ridge) : le mode ne change pas."),
                                ("video", "video de la banque FOND", "Une video de la banque FOND, en fond video."),
                                ("params", "config aleatoire", "Un fond entierement nouveau : soit un fond genere aux reglages tires au hasard (palette, couleurs, "
                                                               "teinte, angle, vitesse...), soit un visuel audio2wave dans un mode et un preset de ce mode "
                                                               "tires au hasard (modes ci-dessous).")):
            check(fr, label, key in rs["bg_sources"], member("bg_sources", key), tip)
        fr = line("  modes a2w")
        for key, label in (("live", "Live"), ("snap", "Snap"), ("ridge", "Ridge")):
            check(fr, label, key in rs["modes"], member("modes", key),
                  "Modes audio2wave que la config aleatoire du fond peut tirer. Changer de mode reconstruit la fenetre (environ 1 s, "
                  "masque en partie par le flash) ; Ridge demande une entree audio.")
        # ---- overlay
        fr = line("OVERLAY")
        for key, label, tip in (("overlay_preset", "preset overlay", "Un des presets overlay enregistres."),
                                ("params", "config aleatoire", "Une configuration entierement nouvelle : logo (taille, position, contour), effets, halo / "
                                                               "fonte / cellules et noise tires au hasard, sans partir d'un preset."),
                                ("fx", "effets du logo", "Halo, fonte, cellules, noise et effets allumes au hasard (au plus deux effets couteux)."),
                                ("media", "media de la banque OVERLAY", "Une image, animation ou video de la banque OVERLAY comme logo."),
                                ("text", "texte", "Le texte ci-contre comme logo (couleur tiree au hasard), au meme titre qu'un fichier de la banque.")):
            check(fr, label, key in rs["overlay"], member("overlay", key), tip)
        text_var = tk.StringVar(value=rs["text"])
        text_entry = tk.Entry(fr, textvariable=text_var, width=24)
        text_entry.pack(side="left", padx=(0, 6))
        Tooltip(text_entry, "Texte tire comme logo (defaut CASUAL RAVERS). Entree ou changer de champ pour valider ; vide = jamais de texte.")
        for ev in ("<Return>", "<FocusOut>"):
            text_entry.bind(ev, lambda e: set_random_option(text=text_var.get()) if text_var.get().strip()[:randomizer.MAX_TEXT] != scene_book.vj()["random"]["text"] else None)
        # ---- verrous
        fr = line("VERROUS")
        for key, label, tip in (("lock_bg", "fond", "Le fond reste celui du moment pendant que l'overlay varie."),
                                ("lock_media", "media", "Le logo / texte / video reste celui du moment."),
                                ("lock_fx", "effets", "Les effets et le preset overlay restent ceux du moment.")):
            check(fr, label, bool(rs[key]), lambda on, key=key: set_random_option(**{key: on}), tip)
        # ---- actions
        fr = line("TIRAGE")
        draw_btn = tk.Button(fr, text="Tirer maintenant", command=random_draw_now, relief="flat", bd=0, padx=10, cursor="hand2",
                             bg=SCENE_COLOR, fg=BANNER_FG, font=BOLD)
        draw_btn.pack(side="left", padx=(0, 6))
        Tooltip(draw_btn, "Tire et applique une scene tout de suite (avec le flash).")
        keep_btn = tk.Button(fr, text="Garder ce tirage...", command=random_keep, relief="flat", bd=0, padx=10, cursor="hand2",
                             bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
        keep_btn.pack(side="left", padx=2)
        Tooltip(keep_btn, "Enregistre le dernier tirage : une fenetre propose les noms du preset de fond, du preset overlay et de la "
                          "scene (champ vide = pas enregistre).")
        tk.Label(fr, text=rnd["info"], bg=bg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=760).pack(
            side="left", padx=10, fill="x")


    def panel_summary(rows: list[dict], settings: dict) -> str:
        played = [r for r in rows if not r["skip"]]
        secs = settings["seconds"]
        loop = sum(r["seconds"] or secs for r in played)
        if settings["order"] == "alea":
            return f"Full Randomizer : un tirage toutes les {secs:.0f} s"
        return (f"{len(played)}/{len(rows)} scenes dans le VJ, boucle ~ "
                + (f"{loop / 60:.1f} min" if loop >= 90 else f"{loop:.0f} s"))

    ROWS_FIRST, ROWS_BATCH = 10, 4

    def panel_signature(rows: list[dict], settings: dict) -> str:
        """Tout ce qui, hors la scene active, change ce que le panneau affiche : s'il est identique, on ne reconstruit rien."""
        return json.dumps([settings, rows, rnd["info"] if settings["order"] == "alea" else "",
                           [len(b) for b in rnd["bank"].values()], sum(1 for v in getattr(s, "thumb_cache", {}).values() if v != "pending"), dnd["ok"],
                           scene_book.active_set_name(), scene_book.set_names()], sort_keys=True, default=str)

    def refresh_scene_panel(force: bool = False) -> None:
        """Construit / met a jour le panneau etendu. Rien n'est reconstruit si seul l'ordre d'affichage actif a change (la ligne
        active est alors repeinte sur place) ; une reconstruction garde la position de l'ascenseur et rehabille les widgets."""
        if not getattr(s, "scenes_expanded", False):
            for child in scene_panel.winfo_children():
                child.destroy()
            scene_panel.pack_forget()
            panel_state["gen"] = panel_state.get("gen", 0) + 1
            panel_state.update(sig=None, rows={}, cv=None, summary=None, dur={})
            return
        rows = scene_book.rows()
        settings = scene_book.vj()
        current = current_index(rows)
        sig = panel_signature(rows, settings)
        if not force and sig == panel_state["sig"] and scene_panel.winfo_children():
            for refs in panel_state["rows"].values():
                restyle_row(refs, refs["index"] == current)
            refresh_library(rows)
            return
        from tkinter import font as tkfont
        face = tkfont.Font(root=root, font=BOLD)
        longest = max([face.measure(r["name"]) for r in rows] + [0])
        panel_state["name_w"] = max(NAME_MIN, min(NAME_MAX, -(-longest // max(face.measure("0"), 1)) + 2))
        frac = 0.0
        cv_old = panel_state["cv"]
        if cv_old is not None:
            try:
                frac = cv_old.yview()[0]
            except tk.TclError:
                frac = 0.0
        for child in scene_panel.winfo_children():
            child.destroy()
        panel_state["sel"].intersection_update(range(len(rows)))
        panel_state["gen"] = panel_state.get("gen", 0) + 1          # abandonne le remplissage par lots d'une construction precedente
        panel_state.update(sig=sig, rows={}, cv=None, summary=None, dur={})
        scene_panel.pack(fill="x")
        head = tk.Frame(scene_panel, bg=a2w.GUI_PANEL_BG)
        head.pack(fill="x")
        tk.Label(head, text=f" SET  {scene_book.active_set_name()}", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, font=BOLD).pack(
            side="left", padx=(6, 12), pady=4)
        order = tk.Menubutton(head, text="ordre : " + {"seq": "dans l'ordre", "random": "au hasard", "alea": "Full Randomizer"}[settings["order"]] + " \u25be",
                              relief="flat", bd=0, padx=8, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                              activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        order.pack(side="left", padx=2)
        om = tk.Menu(order, tearoff=0)
        om.add_command(label="Dans l'ordre", command=lambda: set_vj_option(order="seq"))
        om.add_command(label="Au hasard (jamais la meme deux fois)", command=lambda: set_vj_option(order="random"))
        om.add_command(label="Full Randomizer (fond, overlay et media tires a chaque fois)", command=lambda: set_vj_option(order="alea"))
        order["menu"] = om
        secs = settings["seconds"]
        per = f"{settings['bars']} mesure{'s' if settings['bars'] > 1 else ''}" if settings["unit"] == "bars" else f"{secs:.0f} s"
        default = tk.Menubutton(head, text=f"duree par defaut : {per} \u25be", relief="flat", bd=0, padx=8, cursor="hand2",
                                bg=a2w.GUI_BG, fg=a2w.GUI_FG, activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        default.pack(side="left", padx=2)
        dm = tk.Menu(default, tearoff=0)
        for value in scenes.VJ_SECONDS:
            dm.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min",
                           command=lambda v=value: set_vj_option(unit="seconds", seconds=float(v)))
        dm.add_separator()
        for value in scenes.VJ_BARS:
            dm.add_command(label=f"{value} mesure{'s' if value > 1 else ''} (en rythme)",
                           command=lambda v=value: set_vj_option(unit="bars", bars=v))
        default["menu"] = dm
        summary = tk.StringVar(value=panel_summary(rows, settings))
        panel_state["summary"] = summary
        tk.Label(head, textvariable=summary, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
        tk.Label(head, text="glisser-deposer d'un fichier sur une zone (overlay / audio2wave) = lier ce media"
                 if dnd["ok"] else "glisser-deposer indisponible (pip install tkinterdnd2)", bg=a2w.GUI_PANEL_BG,
                 fg=a2w.GUI_MUTED_FG).pack(side="right", padx=10)
        wrap = tk.Frame(scene_panel, bg=a2w.GUI_BG)                 # la liste a gauche, l'espace de controle global a droite
        wrap.pack(fill="x")
        with STATS.timed("panel.master_box"):
            mbox = master_box()
        mbox.pack(in_=wrap, side="right", fill="y")
        mbox.lift()                                                 # un esclave place « dans » un autre doit le surplomber
        if getattr(s, "library_open", False):
            build_library(wrap, rows)
        else:
            panel_state.pop("lib", None)
        body = tk.Frame(wrap, bg=a2w.GUI_BG, height=getattr(s, "panel_body_h", PANEL_BODY_H))
        body.pack(side="left", fill="both", expand=True)
        panel_state["wrap"], panel_state["body"] = wrap, body
        body.pack_propagate(False)
        foot = tk.Frame(scene_panel, bg=a2w.GUI_PANEL_BG)           # sous la liste : ajouter a la suite
        foot.pack(fill="x")
        plus = tk.Button(foot, text="+", command=add_random_scene, relief="flat", bd=0, padx=14, cursor="hand2", bg=SCENE_COLOR,
                         fg=BANNER_FG, font=BOLD, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        plus.pack(side="left", padx=(8, 4), pady=3)
        Tooltip(plus, "Ajoute a la suite du set une scene tiree au hasard (reglages du Full Randomizer), sous un nom aleatoire. "
                      "Aucun preset n'est enregistre et la scene n'est pas appliquee.")
        plus5 = tk.Button(foot, text="+5", command=add_random_scenes, relief="flat", bd=0, padx=10, cursor="hand2", bg=SCENE_COLOR,
                          fg=BANNER_FG, font=BOLD, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        plus5.pack(side="left", padx=4, pady=3)
        Tooltip(plus5, "Ajoute a la suite du set cinq scenes tirees au hasard d'un coup (comme +, cinq fois). "
                       "Aucun preset n'est enregistre et rien n'est applique.")
        un = tk.Button(foot, text="Annuler", command=undo_now, relief="flat", bd=0, padx=10, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        un.pack(side="right", padx=(2, 8), pady=3)
        Tooltip(un, "Annule la derniere modification du set ou des scenes (ajout, retrait, deplacement, +5, duree...) : Ctrl+Z. "
                    + (f"Prochaine annulation : {scene_book.undo_label()}." if scene_book.undo_label() else "Rien a annuler pour l'instant."))
        re_ = tk.Button(foot, text="Retablir", command=redo_now, relief="flat", bd=0, padx=10, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        re_.pack(side="right", padx=2, pady=3)
        Tooltip(re_, "Refait ce qui vient d'etre annule : Ctrl+Y.")
        dup = tk.Button(foot, text="Dupliquer la derniere", command=duplicate_last_scene, relief="flat", bd=0, padx=10, cursor="hand2",
                        bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        dup.pack(side="left", padx=4, pady=3)
        Tooltip(dup, "Ajoute a la suite une copie de la derniere scene (meme fond, overlay, medias, duree) puis propose de la renommer. "
                     "Change ensuite ses medias en glissant un fichier sur sa ligne, sa duree ou sa case VJ.")
        build_selbar(scene_panel).pack(fill="x")
        grip = tk.Label(scene_panel, text="⋯", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, cursor="sb_v_double_arrow", pady=0)
        grip.pack(fill="x")
        Tooltip(grip, "Glisse vers le haut ou le bas pour agrandir ou reduire la liste (la fenetre suit).")
        drag: dict = {}

        def grip_press(e) -> None:
            # tout est calcule depuis l'etat au moment de l'appui (jamais depuis la taille courante de la fenetre, qui n'est pas encore a jour
            # entre deux mouvements : l'ancien calcul incremental se decalait et faisait trembler la liste et la fenetre pendant le glisser)
            drag.update(y=e.y_root, h=int(body.cget("height")), win=root.winfo_height(), w=root.winfo_width(),
                        overflow=getattr(s, "panel_overflow", 0), panel=getattr(s, "scene_panel_h", 0), want=None, job=None)

        def grip_apply() -> None:
            """Un seul redimensionnement par passage de la boucle d'evenements, avec la derniere position du pointeur."""
            if not drag:
                return
            drag["job"] = None
            want = drag["want"]
            if want is None:
                return
            delta = want - drag["h"]
            # la fenetre suit tant qu'elle tient dans l'ecran ; au-dela la liste grandit quand meme (la page du dessous se reduit,
            # elle defile) et ce « trop-plein » est rendu en premier quand on reduit
            if delta >= 0:
                win = min(delta, max(root.winfo_screenheight() - 80 - drag["win"], 0))
                overflow = drag["overflow"] + delta - win
            else:
                take = min(drag["overflow"], -delta)
                overflow = drag["overflow"] - take
                win = delta + take
            s.panel_overflow = overflow
            body.configure(height=want)
            s.panel_body_h = want
            s.scene_panel_h = drag["panel"] + win              # a la fermeture, la fenetre rend exactement ce qu'elle a pris
            height = max(MIN_WIN_H, drag["win"] + win)
            if height != root.winfo_height():
                root.geometry(f"{drag['w']}x{height}")

        def grip_move(e) -> None:
            if not drag:
                return
            want = max(PANEL_BODY_MIN, drag["h"] + e.y_root - drag["y"])
            want = min(want, max(root.winfo_screenheight() - 200, PANEL_BODY_MIN))
            if want == drag["want"] or (want == drag["h"] and drag["want"] is None):
                return
            drag["want"] = want
            # pas de redimensionnement en direct (la fenetre laissait des traces a chaque pas) : la poignee montre seulement la
            # hauteur visee, la liste et la fenetre changent d'un coup au relachement
            diff = want - drag["h"]
            grip.configure(text=f"\u22ef  {diff:+d} px" if diff else "\u22ef", fg=a2w.GUI_ACCENT)

        def grip_release(e) -> None:
            if drag:
                grip_apply()
                drag.clear()
                try:
                    grip.configure(text="\u22ef", fg=a2w.GUI_MUTED_FG)
                except tk.TclError:
                    pass          # le panneau a ete reconstruit par le redimensionnement

        grip.bind("<ButtonPress-1>", grip_press)
        grip.bind("<B1-Motion>", grip_move)
        grip.bind("<ButtonRelease-1>", grip_release)
        bar = skin.scrollbar(body)
        hbar = skin.scrollbar(body, orient="horizontal")             # seulement si un nom tres long depasse la largeur
        cv = tk.Canvas(body, highlightthickness=0, bd=0, bg=a2w.GUI_BG, yscrollcommand=bar.set, xscrollcommand=hbar.set)
        bar.config(command=cv.yview)
        hbar.config(command=cv.xview)
        bar.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=a2w.GUI_BG)
        item = cv.create_window((0, 0), window=inner, anchor="nw")

        def fit_width(_e=None) -> None:
            need = inner.winfo_reqwidth()
            cv.itemconfigure(item, width=max(cv.winfo_width(), need))
            if need > cv.winfo_width() + 1:
                if not hbar.winfo_ismapped():
                    hbar.pack(side="bottom", fill="x", before=cv)
            else:
                if hbar.winfo_ismapped():
                    hbar.pack_forget()
                cv.xview_moveto(0)
        cv.bind("<Configure>", fit_width)
        restore = {"until": time.monotonic() + 0.5 if frac else 0.0}

        def on_inner(_e=None) -> None:
            fit_width()
            cv.configure(scrollregion=cv.bbox("all"))
            if time.monotonic() < restore["until"]:        # la mise en page se fait en plusieurs passes : on remet la position a chacune
                cv.yview_moveto(frac)
        inner.bind("<Configure>", on_inner)
        panel_state["cv"] = cv
        if settings["order"] == "alea":
            build_random_body(inner, settings)
        else:
            if not rows:
                tk.Label(inner, text="Ce set est vide : '+ Scene' pour en ajouter.", bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG).pack(pady=20)
            # Un gros set (plusieurs dizaines de lignes) ne gele plus la fenetre : les premieres lignes sont construites tout de suite, les
            # autres par petits lots entre deux evenements (ROWS_FIRST puis ROWS_BATCH ; un nouveau rafraichissement abandonne le lot en cours).
            def add_rows(batch: list[dict], dress: bool) -> None:
                for row in batch:
                    refs = build_panel_row(inner, row, row["index"] == current)
                    panel_state["rows"][row["index"]] = refs
                    panel_state["dur"][row["index"]] = refs["dur"]
                    if dress:
                        skin.apply(refs["frame"])

            with STATS.timed("panel.lignes"):
                add_rows(rows[:ROWS_FIRST], False)
            rest = rows[ROWS_FIRST:]
            if rest:
                gen = panel_state["gen"]

                def more() -> None:
                    if panel_state.get("gen") != gen or not inner.winfo_exists():
                        return
                    with STATS.timed("panel.lot"):
                        add_rows([rest.pop(0) for _ in range(min(ROWS_BATCH, len(rest)))], True)
                    if rest:
                        root.after(1, more)
                root.after(1, more)
        with STATS.timed("panel.habillage"):
            skin.apply(scene_panel)                    # TOUJOURS : un panneau reconstruit sans habillage (vignettes pretes, Rescanner...) laissait les cases brutes

    def panel_update_in_place(index: int, kw: dict) -> bool:
        """Duree propre ou case VJ d'une entree : on met a jour le resume et le bouton de duree, sans reconstruire le panneau."""
        summary, btn = panel_state["summary"], panel_state["dur"].get(index)
        if summary is None or not getattr(s, "scenes_expanded", False) or btn is None:
            return False
        rows = scene_book.rows()
        if not 0 <= index < len(rows):
            return False
        summary.set(panel_summary(rows, scene_book.vj()))
        if "seconds" in kw:
            btn.configure(text=dur_label(rows[index]))
        panel_state["sig"] = panel_signature(rows, scene_book.vj())
        return True

    def set_expanded(flag: bool) -> None:
        """Ouvre / ferme le panneau etendu : la fenetre grandit (ou reprend sa taille) de la hauteur du panneau."""
        if bool(getattr(s, "scenes_expanded", False)) == bool(flag):
            return
        s.scenes_expanded = bool(flag)
        s.panel_overflow = 0
        refresh_scene_bar()
        if root.winfo_ismapped():
            root.update_idletasks()
            if flag:
                s.scene_panel_h = scene_panel.winfo_reqheight()
            delta = getattr(s, "scene_panel_h", 0) * (1 if flag else -1)
            height = max(MIN_WIN_H, min(root.winfo_height() + delta, root.winfo_screenheight() - 80))
            root.geometry(f"{root.winfo_width()}x{height}")

    import types
    ops = types.SimpleNamespace(toggle_sel=toggle_sel, select_all=select_all, select_none=select_none, sel_remove=sel_remove, sel_skip=sel_skip,
                                sel_seconds=sel_seconds, sel_duplicate=sel_duplicate, sel_copy_to=sel_copy_to, add_at=add_at, keep_now=keep_now,
                                start_keep=start_keep, drop_temp=drop_temp, drop_pos=drop_pos, toggle_library=toggle_library,
                                request_preview=request_preview, schedule_preview=schedule_preview, preview_photo=preview_photo)
    return types.SimpleNamespace(panel_update_in_place=panel_update_in_place, refresh_scene_panel=refresh_scene_panel, set_expanded=set_expanded, ops=ops)
