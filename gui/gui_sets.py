"""Sets de scenes : dialogues (creer, dupliquer, renommer, supprimer, exporter, importer), menu d'une entree, changement de set.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import params as core_params
from core import scenes
from core import setpack
from pathlib import Path
import threading
import time
from tkinter import filedialog
from tkinter import simpledialog
import json


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import choice_dialog, keep_dialog          # import tardif : gl_gui importe ce module
    apply_entry = c.apply_entry
    ask_delete_scene = c.ask_delete_scene
    capture_scene_now = c.capture_scene_now
    live = c.live
    media_clear = c.media_clear
    media_from_current = c.media_from_current
    move_entry_now = c.move_entry_now
    refresh_scene_bar = c.refresh_scene_bar
    request_preview = c.request_preview
    remove_entry_now = c.remove_entry_now
    root = c.root
    s = c.s
    scene_book = c.scene_book
    scene_msg = c.scene_msg
    panel_state = c.panel_state
    scene_state = c.scene_state
    vj = c.vj
    vj_advance = c.vj_advance

    scene_ui = {
        "chooser": lambda title, text, buttons: choice_dialog(root, title, text, buttons),
        "ask_name": lambda title, prompt, initial="": simpledialog.askstring(title, prompt, parent=root, initialvalue=initial),
        "ask_save": lambda initial: filedialog.asksaveasfilename(
            parent=root, title="Exporter le set", initialfile=initial, defaultextension=".zip",
            filetypes=[("Archive de set avec medias (.zip)", "*.zip"), ("Set seul, sans medias (.json)", "*.json")]),
        "ask_keep": lambda defaults, fond_ok, validate, a2w_mode=None: keep_dialog(root, defaults, fond_ok, validate, a2w_mode),
        "ask_dir": lambda: filedialog.askdirectory(parent=root, title="Dossier d'assets (images, videos, animations)"),
        "ask_open": lambda: filedialog.askopenfilename(
            parent=root, title="Importer un set", filetypes=[("Set casual-overlay (.zip ou .json)", "*.zip *.json"), ("Tous les fichiers", "*.*")]),
    }

    def _after_history(done: str | None, verb: str, label: str | None) -> bool:
        if done is None:
            scene_msg.set(f"Rien a {verb}")
            return False
        panel_state["sel"].clear()
        panel_state["sel_last"] = None
        s.active_entry = None
        if getattr(s, "active_scene", None) not in (None, "Full Randomizer") and s.active_scene not in scene_book.names():
            s.active_scene = None
        scene_msg.set(("Annule" if verb == "annuler" else "Retabli") + f" : {done}")
        refresh_scene_bar()
        return True

    def undo_now() -> bool:
        """Annule la derniere modification du set / des scenes (Ctrl+Z, menu SET, bouton Annuler du panneau etendu)."""
        return _after_history(scene_book.undo(), "annuler", None)

    def redo_now() -> bool:
        return _after_history(scene_book.redo(), "retablir", None)

    def key_history(evt, redo: bool = False) -> None:
        if evt.widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox"):       # Ctrl+Z dans un champ de saisie : ce n'est pas pour nous
            return
        redo_now() if redo else undo_now()
    root.bind_all("<Control-z>", key_history)
    root.bind_all("<Control-y>", lambda e: key_history(e, True))
    root.bind_all("<Control-Z>", lambda e: key_history(e, True))

    def switch_set(name: str) -> bool:
        """Change de set. Si le VJ tournait, il **redemarre** sur le nouveau set (premiere scene, ou une au hasard)."""
        if not scene_book.set_active(name):
            scene_msg.set(f"set inconnu: {name}")
            return False
        s.active_scene = None
        s.active_entry = None
        scene_msg.set(f"Set '{scene_book.active_set_name()}' ({len(scene_book.rows())} scenes)")
        scene_state["adding"] = False
        if vj["running"]:
            vj_advance()
            if vj["running"]:
                scene_msg.set(f"Set '{scene_book.active_set_name()}' : VJ redemarre")
        refresh_scene_bar()
        return True

    def create_set(name: str, copy_of: str | None = None) -> bool:
        name = (name or "").strip()[:scenes.MAX_NAME]
        if not name:
            return False
        if not scene_book.new_set(name, copy_of=copy_of):
            scene_msg.set(f"Un set '{name}' existe deja" if scene_book.get_set(name) else "Donne un nom au set")
            return False
        return switch_set(name)

    def new_set_dialog(copy: bool = False) -> None:
        current = scene_book.active_set_name()
        name = scene_ui["ask_name"]("Nouveau set", "Nom du set :", f"{current} (copie)" if copy else "")
        if name:
            create_set(name, current if copy else None)

    def rename_set_now(new: str) -> bool:
        old = scene_book.active_set_name()
        if not scene_book.rename_set(old, new):
            scene_msg.set("Nom de set vide ou deja pris")
            return False
        scene_msg.set(f"Set '{old}' renomme en '{scene_book.active_set_name()}'")
        refresh_scene_bar()
        return True

    def rename_set_dialog() -> None:
        name = scene_ui["ask_name"]("Renommer le set", "Nouveau nom :", scene_book.active_set_name())
        if name:
            rename_set_now(name)

    def delete_set_now() -> bool:
        name = scene_book.active_set_name()
        if not scene_book.delete_set(name):
            scene_msg.set("Impossible de supprimer le dernier set")
            return False
        switch_set(scene_book.active_set_name())
        scene_msg.set(f"Set '{name}' supprime (ses scenes restent dans la bibliotheque)")
        return True

    def ask_delete_set() -> None:
        name = scene_book.active_set_name()
        if len(scene_book.set_names()) <= 1:
            scene_msg.set("Impossible de supprimer le dernier set")
            return
        live.confirm_dialog(root, "Supprimer le set", f"Supprimer le set '{name}' ?\nSes scenes restent dans la bibliotheque.",
                            delete_set_now)

    job = {"busy": False}

    def run_job(label: str, work, done) -> None:
        """Travail long (copie / extraction de medias) dans un fil ; `done(resultat, erreur)` est rappelee dans le fil Tk. La fenetre reste
        utilisable, un seul travail a la fois."""
        job["busy"] = True
        state: dict = {"fin": False, "result": None, "exc": None}

        def runner() -> None:
            try:
                state["result"] = work()
            except Exception as exc:
                state["exc"] = exc
            finally:
                state["fin"] = True

        threading.Thread(target=runner, name="archive-set", daemon=True).start()
        started = time.monotonic()

        def poll() -> None:
            if not state["fin"]:
                scene_msg.set(f"{label} en cours... {time.monotonic() - started:.0f} s")
                root.after(200, poll)
                return
            job["busy"] = False
            done(state["result"], state["exc"])
        root.after(200, poll)

    def export_set_now(path: str | None = None, wait: bool | None = None) -> bool:
        """Exporte le set actif. Une archive .zip copie les medias : depuis le menu (chemin choisi dans le dialogue) c'est fait dans un fil,
        la fenetre reste utilisable ; avec un chemin donne (tests, scripts) on attend la fin et on rend le resultat."""
        wait = path is not None if wait is None else wait
        name = scene_book.active_set_name()
        if job["busy"]:
            scene_msg.set("Une archive est deja en cours de preparation")
            return False
        path = path or scene_ui["ask_save"](f"{name}.casual-set.zip")
        if not path:
            return False
        pack = str(path).lower().endswith(".zip")
        try:
            data = scenes.export_set(scene_book, name, core_params.OVERLAY_BOOK, core_params.BACKGROUND_BOOK)
            if not pack:
                Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError) as exc:
            scene_msg.set(f"Export impossible: {exc}")
            return False

        def finish(info, exc) -> bool:
            if exc is not None:
                scene_msg.set(f"Export impossible: {exc}")
                return False
            if pack:
                note = f", {info['files']} media(s) embarque(s) ({info['bytes'] / 1048576:.1f} Mo)"
                if info["missing"]:
                    note += f" - introuvable(s), non embarque(s): {', '.join(Path(m).name for m in info['missing'][:3])}"
                scene_msg.set(f"Set '{name}' exporte ({len(data['scenes'])} scenes{note}) : {Path(path).name}")
            else:
                scene_msg.set(f"Set '{name}' exporte ({len(data['scenes'])} scenes, sans medias) : {Path(path).name}")
            return True

        if not pack:
            return finish(None, None)
        if wait:
            try:
                return finish(setpack.write_pack(path, data), None)
            except (OSError, ValueError) as exc:
                return finish(None, exc)
        scene_msg.set(f"Export de '{name}' en cours (copie des medias)...")
        run_job(f"Export de '{name}'", lambda: setpack.write_pack(path, data), finish)
        return True

    def resolve_conflict(title: str, text: str, what: str) -> str | None:
        """Nom deja pris : renommer (garder les deux) ou remplacer (avec confirmation). None = annuler."""
        choice = scene_ui["chooser"](title, text, ["Renommer (garder les deux)", "Remplacer", "Annuler"])
        if choice == "Remplacer":
            sure = scene_ui["chooser"]("Confirmer le remplacement",
                                       f"Remplacer {what} existant(es) par celui du fichier ?\nL'ancienne version sera perdue.",
                                       ["Remplacer", "Annuler"])
            return "replace" if sure == "Remplacer" else None
        return "rename" if choice and choice.startswith("Renommer") else None

    def import_set_now(path: str | None = None, wait: bool | None = None) -> bool:
        """Importe un set (JSON ou archive). Une archive est extraite dans un fil quand le chemin vient du dialogue ; avec un chemin
        donne (tests, scripts) on attend la fin et on rend le resultat."""
        wait = path is not None if wait is None else wait
        if job["busy"]:
            scene_msg.set("Une archive est deja en cours de preparation")
            return False
        path = path or scene_ui["ask_open"]()
        if not path:
            return False
        try:
            packed = setpack.is_pack(path)
            if not packed:
                parsed = scenes.parse_import(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            scene_msg.set(f"Import impossible: {exc}")
            return False
        if not packed:
            return finish_import(parsed)
        if wait:
            try:
                return finish_import(scenes.parse_import(setpack.read_pack(path, scenes.IMPORT_DIR / "media")))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                scene_msg.set(f"Import impossible: {exc}")
                return False

        def done(text, exc) -> None:
            if exc is not None:
                scene_msg.set(f"Import impossible: {exc}")
                return
            try:
                finish_import(scenes.parse_import(text))
            except ValueError as err:
                scene_msg.set(f"Import impossible: {err}")
        scene_msg.set("Import en cours (extraction des medias)...")
        run_job("Import", lambda: setpack.read_pack(path, scenes.IMPORT_DIR / "media"), done)
        return True

    def finish_import(parsed) -> bool:
        conflicts = scenes.import_conflicts(scene_book, parsed)
        set_mode = scene_mode = "rename"
        if conflicts["set"]:
            set_mode = resolve_conflict("Set deja present", f"Un set nomme '{parsed['set']['name']}' existe deja.", "le set")
        if set_mode and conflicts["scenes"]:
            scene_mode = resolve_conflict(
                "Scenes deja presentes",
                f"{len(conflicts['scenes'])} scene(s) du fichier ont le nom d'une scene differente de ta bibliotheque :\n"
                + ", ".join(conflicts["scenes"][:8]) + ("..." if len(conflicts["scenes"]) > 8 else "")
                + "\n(remplacer les change aussi dans tes autres sets).", "ces scenes")
        if not set_mode or not scene_mode:
            scene_msg.set("Import annule")
            return False
        result = scenes.import_set(scene_book, parsed, set_mode, scene_mode, core_params.OVERLAY_BOOK, core_params.BACKGROUND_BOOK)
        if result["presets_added"]:
            c.refresh_presets()
        switch_set(result["set"])
        note = ""
        if result["set_renamed"]:
            note += f" (renomme '{result['set']}')"
        if result["renamed"]:
            note += f", {len(result['renamed'])} scene(s) renommee(s)"
        if result["replaced"]:
            note += f", {len(result['replaced'])} scene(s) remplacee(s)"
        if result["presets_added"]:
            note += f", {len(result['presets_added'])} preset(s) ajoute(s)"
        if result["presets_kept"]:
            note += f" ({len(result['presets_kept'])} preset(s) de meme nom gardes tels quels)"
        if result["missing"]:
            note += f" - media introuvable: {', '.join(Path(m).name for m in result['missing'][:3])}"
        scene_msg.set(f"Set '{result['set']}' importe{note}")
        return True

    def fill_entry_menu(menu, row: dict) -> None:
        i = row["index"]
        menu.add_command(label="Charger", command=lambda: apply_entry(i))
        menu.add_command(label="Mettre a jour la scene avec l'etat actuel (dans tous les sets)",
                         command=lambda n=row["name"]: capture_scene_now(n))
        menu.add_command(label="Media de cette entree : utiliser le logo / texte actuel", command=lambda: media_from_current(i))
        if row["media"]:
            menu.add_command(label=f"Media de cette entree : retirer ({scenes.media_label(row['media'])})",
                             command=lambda: media_clear(i))
        menu.add_command(label="Mettre a jour l'apercu (capture de la projection)", command=lambda n=row["name"]: request_preview(n))
        menu.add_separator()
        if i > 0:
            menu.add_command(label="Monter", command=lambda: move_entry_now(i, -1))
        if i < len(scene_book.rows()) - 1:
            menu.add_command(label="Descendre", command=lambda: move_entry_now(i, 1))
        menu.add_command(label="Retirer du set", command=lambda: remove_entry_now(i))
        menu.add_command(label="Supprimer la scene de la bibliotheque...", command=lambda n=row["name"]: ask_delete_scene(n))

    import types
    return types.SimpleNamespace(undo_now=undo_now, redo_now=redo_now, ask_delete_set=ask_delete_set, create_set=create_set, delete_set_now=delete_set_now, export_set_now=export_set_now, fill_entry_menu=fill_entry_menu, import_set_now=import_set_now, new_set_dialog=new_set_dialog, rename_set_dialog=rename_set_dialog, rename_set_now=rename_set_now, scene_ui=scene_ui, switch_set=switch_set)
