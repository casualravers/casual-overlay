"""Medias des scenes : glisser-deposer (tkinterdnd2), vignettes, edition d'une entree (renommer, duree, participation au VJ).

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import scenes
from pathlib import Path
import json
import threading
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import a2w_from_file, gui_gates, make_thumb, media_from_file          # import tardif : gl_gui importe ce module
    apply_bgv = c.apply_bgv
    apply_entry_core = c.apply_entry_core
    bg_var = c.bg_var
    bgv_var = c.bgv_var
    bgvid_box = c.bgvid_box
    current_index = c.current_index
    panel_update_in_place = c.panel_update_in_place
    refresh_scene_bar = c.refresh_scene_bar
    root = c.root
    s = c.s
    scene_book = c.scene_book
    scene_msg = c.scene_msg
    scene_ui = c.scene_ui

    dnd = {"ok": False, "files": None}
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        if not getattr(root, "_casual_dnd", False):
            TkinterDnD._require(root)
            root._casual_dnd = True
        dnd.update(ok=True, files=DND_FILES)
    except Exception:
        pass

    A2W_TARGETS = {"video": "video interieure", "video2": "video exterieure"}

    def drop_media(index: int, data: str, target: str = "overlay") -> bool:
        """Fichier(s) depose(s) sur l'entree `index` du set : le premier devient un de ses medias. `target` : `overlay` (logo /
        texte / video du logo) ou `video` / `video2` (videos interieure / exterieure d'audio2wave, scenes Snap seulement)."""
        files = [f for f in root.tk.splitlist(data) if f]
        rows = scene_book.rows()
        if not files or not 0 <= index < len(rows):
            return False
        name = rows[index]["name"]
        extra = f" ({len(files) - 1} autre(s) fichier(s) ignore(s))" if len(files) > 1 else ""
        if target == "overlay":
            media = media_from_file(files[0])
            if media is None:
                scene_msg.set(f"Format non pris en charge: {Path(files[0]).name} (images png / jpg / bmp, videos gif / webm / mp4 / mov...)")
                return False
            scene_book.set_entry_media(index, media)
            scene_msg.set(f"Overlay de '{name}' : {scenes.media_label(media)}{extra}")
        elif target == "bgvideo":
            path = a2w_from_file(files[0])
            if path is None:
                scene_msg.set(f"Il faut une video pour le fond: {Path(files[0]).name}")
                return False
            scene_book.set_entry_bg_video(index, path)
            scene_msg.set(f"Fond de '{name}' : video {Path(path).name}{extra}")
        else:
            scene = rows[index]["scene"]
            if not (scene.get("bg") == "live" and scene.get("mode") == "snap"):
                scene_msg.set(f"'{name}' n'est pas une scene Snap : les videos d'audio2wave ne s'y appliquent pas")
                return False
            path = a2w_from_file(files[0])
            if path is None:
                scene_msg.set(f"audio2wave ne lit que des videos: {Path(files[0]).name}")
                return False
            scene_book.set_entry_a2w(index, target, path)
            scene_msg.set(f"audio2wave de '{name}' : {A2W_TARGETS[target]} = {Path(path).name}{extra}")
        if current_index(rows) == index:
            apply_entry_core(index)                        # la scene est a l'ecran : on voit tout de suite le media
        else:
            refresh_scene_bar()
        return True

    def bg_clear(index: int) -> None:
        if scene_book.set_entry_bg_video(index, None):
            scene_msg.set("Video de fond retiree : la scene garde son fond")
        refresh_scene_bar()

    def a2w_clear(index: int, key: str) -> None:
        if scene_book.set_entry_a2w(index, key, None):
            scene_msg.set(f"{A2W_TARGETS[key].capitalize()} retiree : la scene garde la sienne")
        refresh_scene_bar()

    def drop_on_button(index: int, data: str, x: int, y: int) -> None:
        """Depot sur une touche de la barre : un menu demande la destination (overlay, video interieure / exterieure)."""
        rows = scene_book.rows()
        if not 0 <= index < len(rows):
            return
        menu = tk.Menu(root, tearoff=0)
        scene = rows[index]["scene"]
        snap = scene.get("bg") == "live" and scene.get("mode") == "snap"
        menu.add_command(label="Overlay (logo / texte / video du logo)", command=lambda: drop_media(index, data, "overlay"))
        menu.add_command(label="Fond : video", command=lambda: drop_media(index, data, "bgvideo"))
        for key, label in A2W_TARGETS.items():
            menu.add_command(label=f"audio2wave : {label}" + ("" if snap else "  (scene Snap seulement)"),
                             state="normal" if snap else "disabled", command=lambda k=key: drop_media(index, data, k))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def register_drop(widget, index: int, target: str = "choose") -> None:
        """Cible de depot : `target` = `overlay` / `video` / `video2` (destination fixe) ou `choose` (menu au depot)."""
        if not dnd["ok"]:
            return
        try:
            widget.drop_target_register(dnd["files"])
            if target == "choose":
                widget.dnd_bind("<<Drop>>", lambda e, k=index: drop_on_button(k, e.data, e.x_root, e.y_root) or "copy")
            else:
                widget.dnd_bind("<<Drop>>", lambda e, k=index, tg=target: drop_media(k, e.data, tg) or "copy")
        except Exception:
            pass

    def drop_bg_video(data: str) -> bool:
        """Fichier depose sur le bloc du fond video : il devient la video du fond, et le fond passe en mode video."""
        files = [f for f in root.tk.splitlist(data) if f]
        path = a2w_from_file(files[0]) if files else None
        if path is None:
            scene_msg.set("Fond video : il faut un fichier video" + (f" ({Path(files[0]).name})" if files else ""))
            return False
        bgv_var.set(path)
        apply_bgv()
        bg_var.set("video")
        scene_msg.set(f"Fond video : {Path(path).name}")
        return True

    if dnd["ok"]:
        for w in gui_gates.leaves(bgvid_box):
            try:
                w.drop_target_register(dnd["files"])
                w.dnd_bind("<<Drop>>", lambda e: drop_bg_video(e.data) and "copy")
            except Exception:
                pass

    def register_tree(widget, index: int, target: str = "choose") -> None:
        register_drop(widget, index, target)
        for child in widget.winfo_children():
            register_tree(child, index, target)

    # ---- Vignettes des medias : calculees dans un fil (ffmpeg pour une video), affichees quand elles sont pretes
    if not hasattr(s, "thumb_cache"):
        s.thumb_cache, s.thumb_photos, s.thumb_dirty = {}, {}, False

    def thumb_photo(media: dict):
        from PIL import ImageTk
        key = json.dumps(media, sort_keys=True)
        if key in s.thumb_photos:
            return s.thumb_photos[key]
        state = s.thumb_cache.get(key)
        if state is None:
            s.thumb_cache[key] = "pending"

            def work(m=dict(media), k=key) -> None:
                s.thumb_cache[k] = make_thumb(m) or "none"
                s.thumb_dirty = True
            threading.Thread(target=work, daemon=True).start()
            return None
        if state in ("pending", "none"):
            return None
        s.thumb_photos[key] = ImageTk.PhotoImage(state)
        return s.thumb_photos[key]

    # ---- Edition d'une entree : renommer la scene, duree et exclusion du VJ
    def rename_scene_now(old: str, new: str) -> bool:
        if not scene_book.rename_scene(old, new):
            scene_msg.set("Nom de scene vide ou deja pris")
            return False
        if getattr(s, "active_scene", None) == old:
            s.active_scene = new.strip()[:scenes.MAX_NAME]
        scene_msg.set(f"Scene '{old}' renommee en '{new.strip()[:scenes.MAX_NAME]}' (dans tous les sets)")
        refresh_scene_bar()
        return True

    def rename_scene_dialog(old: str) -> None:
        new = scene_ui["ask_name"]("Renommer la scene", "Nouveau nom :", old)
        if new:
            rename_scene_now(old, new)

    def set_entry_vj(index: int, **kw) -> None:
        scene_book.set_entry_vj(index, **kw)
        if set(kw) <= {"skip", "seconds"} and panel_update_in_place(index, kw):
            return                                   # ni la barre ni le panneau ne dependent d'autre chose : aucun rechargement
        refresh_scene_bar()

    def set_vj_option(**kw) -> None:
        scene_book.set_vj(**kw)
        refresh_scene_bar()

    import types
    return types.SimpleNamespace(a2w_clear=a2w_clear, bg_clear=bg_clear, dnd=dnd, drop_bg_video=drop_bg_video, drop_media=drop_media, register_drop=register_drop, register_tree=register_tree, rename_scene_dialog=rename_scene_dialog, rename_scene_now=rename_scene_now, set_entry_vj=set_entry_vj, set_vj_option=set_vj_option, thumb_photo=thumb_photo)
