#!/usr/bin/env python3
"""Sphere: le spectre du son projete sur une sphere, vu de face.

Cinquieme variante, a cote de audio2wave.py, audio2wave_live.py, audio2wave_snap.py et audio2wave_ridge.py. Les frequences partent
du CENTRE de la sphere vue de face (graves au milieu, aigus vers le bord) et deforment sa surface vers l'exterieur ; l'angle autour du
centre decale l'historique (un bout du passe par direction), ce qui donne un relief en spirale plutot que des anneaux identiques.
La taille de la sphere suit l'intensite du morceau (et pulse au kick).

Ce mode n'existe que dans casual-overlay : il est dessine en GL (engine/sphere_mode.py), il n'a ni fenetre ffplay ni mode ligne de
commande. Ce module ne porte que ses options (`parse_args`), ses presets et sa fenetre de reglages (`build_gui`), sur le modele de
audio2wave_ridge.py (memes options hote : `parent`, `embedded`, `compact`, `presets_first`, `on_change`).
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Callable

try:
    import tkinter as tk
except ImportError:
    tk = None

from audio2wave import (
    GUI_ACCENT, GUI_FONT_HEADING, GUI_FONT_MONO, GUI_FONT_SMALL, GUI_MUTED_FG, GUI_PANEL_BG,
    style_gui, style_option_menu,
)
from audio2wave_live import (
    AUTOMATE_TICK_MS, AutomationManager, PresetStore, Tooltip, confirm_dialog, list_audio_devices,
)
from audio2wave_snap import probe_color  # noqa: F401  (utilise par l'hote pour lire une couleur nommee)
from common import GuiController, move_rows_to_top, watch_args

STYLES = ("wire", "surface", "points")
STYLE_LABELS = {"wire": "Fil de fer", "surface": "Surface eclairee", "points": "Nuage de points"}

PRESETS: dict[str, dict[str, object]] = {
    "default": {},
    "neon": dict(style="wire", colors="0x39c9ff,0xff3df2", relief=0.8, history=0.8, size_react=0.6),
    "metal": dict(style="surface", colors="0x6b7280,0xf4f4f5", relief=0.6, history=0.4, smooth=0.5),
    "poussiere": dict(style="points", colors="0xfbbf24,0xf43f5e", relief=0.9, history=1.2, point_size=2),
}
PRESET_ALIASES: dict[str, str] = {}
USER_PRESETS_PATH = Path.home() / ".audio2wave" / "sphere_presets.json"
preset_store = PresetStore(PRESETS, USER_PRESETS_PATH, PRESET_ALIASES)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """`argv` : arguments a analyser (sans le nom du programme), `None` = sys.argv[1:]."""
    p = argparse.ArgumentParser(description="Le spectre du son projete sur une sphere (dessine en GL par casual-overlay).")
    p.add_argument("-d", "--device", help="Nom exact de l'entree audio (DirectShow)")
    p.add_argument("--preset", type=preset_store.resolve, default=None, help="Charge un jeu d'options nomme")
    p.add_argument("--size", default=None, help="Resolution WIDTHxHEIGHT (fixee par l'hote)")
    p.add_argument("--fullscreen", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--gui", action="store_true", help="Sans objet ici (l'hote affiche la fenetre de reglages)")
    p.add_argument("--style", choices=STYLES, default="wire", help="wire (fil de fer), surface (eclairee) ou points")
    p.add_argument("--bands", type=int, default=32, help="Nombre de bandes de frequences (8 a 64)")
    p.add_argument("--detail", type=int, default=48, help="Finesse du maillage, en rangees de latitude (16 a 96)")
    p.add_argument("--relief", type=float, default=0.7, help="Hauteur des deformations (0 a 1,5)")
    p.add_argument("--size-react", dest="size_react", type=float, default=0.6,
                   help="Part de la taille de la sphere qui suit l'intensite du morceau (0 = taille fixe, 1 = tres sensible)")
    p.add_argument("--pulse", type=float, default=0.4, help="Pulsation de la sphere entiere aux graves / kicks (0 a 1)")
    p.add_argument("--shape", type=float, default=0.0,
                   help="Forme (0 a 5) : 0 sphere, 1 tore, 2 noeud de trefle, 3 ressort, 4 fleur, 5 bouteille de Klein ; entre deux, les formes se melangent")
    p.add_argument("--history", type=float, default=0.6, help="Profondeur de l'historique, en secondes (0 = anneaux identiques)")
    p.add_argument("--smooth", type=float, default=0.4, help="Lissage du relachement (0 a 1)")
    p.add_argument("--range", dest="db_range", type=float, default=40.0, help="Dynamique affichee, en dB (20 a 70)")
    p.add_argument("--tilt", type=float, default=20.0, help="Inclinaison de la camera, en degres (-60 a 60)")
    p.add_argument("--spin", type=float, default=0.0, help="Rotation autour de la verticale, en degres par seconde (0 = camera fixe)")
    p.add_argument("--colors", default="0x39c9ff,0xff3df2", help="Couleurs du grave a l'aigu / du calme au fort (une a trois)")
    p.add_argument("--bg-color", default="black", help="Couleur de fond")
    p.add_argument("--line-width", type=int, default=1, help="Epaisseur des lignes (selon le pilote graphique)")
    p.add_argument("--point-size", dest="point_size", type=int, default=2, help="Taille des points du nuage, en pixels")
    p.add_argument("--loglevel", default="warning")
    args = p.parse_args(argv)
    if args.preset:
        p.set_defaults(**preset_store.all()[args.preset])
        args = p.parse_args(argv)
    args.stereo = False
    args.split_channels = False
    args.interval = 1.0                        # sans objet (pas de photos) : lu par le pont de l'hote
    return args


def build_gui(args: argparse.Namespace, size: tuple[int, int], status: dict,
              stop_event: threading.Event, finished_event: threading.Event,
              root: "tk.Tk | None" = None,
              on_switch_mode: Callable[[str], None] | None = None,
              *, parent: object | None = None,
              on_change: Callable[[argparse.Namespace], None] | None = None,
              embedded: bool = False, presets_first: bool = False,
              compact: bool = False) -> "GuiController":
    """Fenetre de reglages en direct (meme principe que `audio2wave_ridge.build_gui`) : ne touche qu'aux attributs de `args`, que le rendu
    relit a chaque image. Renvoie un `GuiController`."""
    host_on_change = on_change
    if root is None and parent is not None:
        root = parent
    if root is None:
        root = tk.Tk()
    else:
        for child in list(root.winfo_children()):
            child.destroy()
    if not embedded:
        root.title("audio2wave sphere - reglages")
        root.resizable(False, False)
    style_gui(root)

    widgets: dict = {"root": root, "section_titles": [], "separators": [], "presets": []}
    commit_callbacks: list[Callable[[], None]] = []

    def bind_commit(entry: object, handler: Callable[..., None]) -> None:
        entry.bind("<Return>", handler)
        entry.bind("<FocusOut>", handler)
        commit_callbacks.append(handler)

    refresh_after_id: dict[str, str | None] = {"id": None}
    controls: dict[str, Callable[[object], None]] = {}
    automation = AutomationManager(root, Tooltip, GUI_PANEL_BG, GUI_MUTED_FG, GUI_ACCENT)

    ROW_PADX = 6 if compact else 11
    ROW_PADY = 2 if compact else 4
    SECTION_GAP = 4 if compact else 7
    LEFT_LABEL_COL, LEFT_CTRL_COL = 0, 1
    SPACER_COL = 2
    RIGHT_LABEL_COL, RIGHT_CTRL_COL = 3, 4
    TOTAL_COLUMNS = 5
    rows = {"left": 1, "right": 1}

    def next_row(panel: str) -> int:
        rows[panel] += 1
        return rows[panel] - 1

    def cols(panel: str) -> tuple[int, int]:
        return (RIGHT_LABEL_COL, RIGHT_CTRL_COL) if panel == "right" else (LEFT_LABEL_COL, LEFT_CTRL_COL)

    def add_label(text: str, r: int, column: int, tooltip: str | None = None) -> "tk.Label":
        label = tk.Label(root, text=text)
        label.grid(row=r, column=column, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        if tooltip:
            Tooltip(label, tooltip)
        return label

    def add_section_title(panel: str, title: str) -> None:
        label_col, _ = cols(panel)
        title_label = tk.Label(root, text=title.upper(), font=GUI_FONT_SMALL, fg=GUI_MUTED_FG)
        title_label.grid(row=next_row(panel), column=label_col, columnspan=2, sticky="w", padx=ROW_PADX, pady=(0, 2))
        widgets["section_titles"].append(title_label)

    def add_separator(panel: str, title: str | None = None) -> None:
        label_col, _ = cols(panel)
        line = tk.Frame(root, bg=GUI_PANEL_BG, height=1)
        line.grid(row=next_row(panel), column=label_col, columnspan=2, sticky="ew",
                  padx=ROW_PADX, pady=(SECTION_GAP, SECTION_GAP if title is None else 4))
        widgets["separators"].append(line)
        if title:
            add_section_title(panel, title)

    tk.Label(root, text="Reglages Sphere", font=GUI_FONT_HEADING, fg=GUI_ACCENT,
             ).grid(row=0, column=0, columnspan=TOTAL_COLUMNS, sticky="w", padx=ROW_PADX, pady=(10, SECTION_GAP))

    def request_switch(mode_name: str) -> None:
        if on_switch_mode is None:
            status["text"] = "Bascule de mode indisponible dans ce contexte"
            return
        if refresh_after_id["id"] is not None:
            root.after_cancel(refresh_after_id["id"])
            refresh_after_id["id"] = None
        status_label.config(text=f"Bascule vers {mode_name}...")
        on_switch_mode(mode_name)

    # ============================= PANNEAU GAUCHE =============================
    add_section_title("left", "Source")
    device_var = tk.StringVar(value=args.device or "")

    def on_device_change(value: object = None) -> None:
        if value is not None:
            device_var.set(value)
        args.device = device_var.get()

    controls["device"] = on_device_change
    r = next_row("left")
    add_label("Entree audio", r, LEFT_LABEL_COL)
    device_frame = tk.Frame(root)
    device_frame.grid(row=r, column=LEFT_CTRL_COL, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    device_menu = tk.OptionMenu(device_frame, device_var, device_var.get())
    style_option_menu(device_menu)
    device_menu.pack(side="left")

    def refresh_devices() -> None:
        names = list_audio_devices()
        if args.device and args.device not in names:
            names = [args.device] + names
        menu = device_menu["menu"]
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=lambda n=name: on_device_change(n))
        status["text"] = f"{len(names)} entree(s) audio detectee(s)"

    tk.Button(device_frame, text="Actualiser", command=refresh_devices).pack(side="left", padx=(8, 0))
    tk.Button(device_frame, text="Snap", command=lambda: request_switch("snap")).pack(side="left", padx=(8, 0))
    tk.Button(device_frame, text="Ridge", command=lambda: request_switch("ridge")).pack(side="left", padx=(4, 0))
    tk.Button(device_frame, text="Live", command=lambda: request_switch("live")).pack(side="left", padx=(4, 0))

    def add_slider(label: str, attr: str, lo: float, hi: float, step: float, panel: str = "left",
                   tooltip: str | None = None, automatable: bool = False) -> None:
        label_col, ctrl_col = cols(panel)
        r = next_row(panel)
        add_label(label, r, label_col, tooltip=tooltip)
        var = tk.DoubleVar(value=getattr(args, attr))
        is_int = step >= 1

        def on_change_(_value: object = None) -> None:
            setattr(args, attr, int(var.get()) if is_int else round(var.get(), 3))

        holder = root
        if automatable:
            holder = tk.Frame(root)
            holder.grid(row=r, column=ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        scale = tk.Scale(holder, from_=lo, to=hi, resolution=step, orient="horizontal",
                         variable=var, length=170, showvalue=True, command=on_change_)
        widgets[attr] = scale
        if automatable:
            scale.pack(side="top", anchor="w")
        else:
            scale.grid(row=r, column=ctrl_col, padx=ROW_PADX, pady=ROW_PADY)

        def set_value(value: object) -> None:
            if value is None:
                return
            var.set(value)
            on_change_()

        controls[attr] = set_value
        if automatable:
            automation.register(holder, attr, label, lo, hi)

    # style : trois boutons radio (la variable est exposee a l'hote comme chez Snap)
    add_section_title("left", "Forme")
    style_var = tk.StringVar(value=args.style)
    widgets["style_var"] = style_var

    def on_style_change(value: object = None) -> None:
        if value is not None:
            style_var.set(value)
        args.style = style_var.get()

    controls["style"] = on_style_change
    r = next_row("left")
    add_label("Style", r, LEFT_LABEL_COL, tooltip="Fil de fer, surface eclairee ou nuage de points.")
    style_frame = tk.Frame(root)
    style_frame.grid(row=r, column=LEFT_CTRL_COL, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for key in STYLES:
        tk.Radiobutton(style_frame, text=STYLE_LABELS[key], value=key, variable=style_var,
                       command=on_style_change).pack(side="left", padx=(0, 6))

    add_slider("Forme", "shape", 0.0, 5.0, 0.01,
               tooltip="0 sphere, 1 tore, 2 noeud de trefle, 3 ressort, 4 fleur, 5 bouteille de Klein. Entre deux, les formes se melangent (une courbe "
                       "d'automation fait morpher toute seule).", automatable=True)
    add_slider("Relief", "relief", 0.0, 1.5, 0.01, tooltip="Hauteur des deformations dues aux frequences.", automatable=True)
    add_slider("Taille selon l'intensite", "size_react", 0.0, 1.0, 0.01,
               tooltip="La sphere grossit quand le morceau est fort et rapetisse quand il est calme. 0 = taille fixe.",
               automatable=True)
    add_slider("Pulsation (graves)", "pulse", 0.0, 1.0, 0.01, tooltip="La sphere entiere pulse sur les graves et les kicks.",
               automatable=True)
    add_slider("Historique (s)", "history", 0.0, 2.0, 0.05,
               tooltip="Profondeur du passe repartie autour du centre. 0 = anneaux identiques.", automatable=True)
    add_slider("Lissage", "smooth", 0.0, 1.0, 0.01, tooltip="Retombee des deformations (0 = nerveux, 1 = tres doux).",
               automatable=True)

    # ============================= PANNEAU DROIT ==============================
    add_section_title("right", "Maillage et camera")
    add_slider("Bandes", "bands", 8, 64, 1, panel="right", tooltip="Nombre de bandes de frequences (echelle logarithmique).")
    add_slider("Finesse du maillage", "detail", 16, 96, 1, panel="right",
               tooltip="Rangees de latitude de la sphere. Plus fin = plus joli et un peu plus lourd.")
    add_slider("Dynamique (dB)", "db_range", 20, 70, 1, panel="right",
               tooltip="Ecart entre le plus fort et le plus faible niveau affiche.")
    add_slider("Inclinaison (deg)", "tilt", -60, 60, 1, panel="right", tooltip="Angle de la camera (fixe).", automatable=True)
    add_slider("Rotation (deg/s)", "spin", -90, 90, 1, panel="right", tooltip="0 = camera fixe.", automatable=True)
    add_slider("Epaisseur des lignes", "line_width", 1, 6, 1, panel="right", tooltip="Selon le pilote graphique.")
    add_slider("Taille des points", "point_size", 1, 8, 1, panel="right", tooltip="Nuage de points.")

    add_separator("right", "Couleurs")

    def add_color_entry(label: str, attr: str, tip: str) -> None:
        r = next_row("right")
        add_label(label, r, RIGHT_LABEL_COL, tooltip=tip)
        var = tk.StringVar(value=getattr(args, attr))

        def apply(_evt=None) -> None:
            setattr(args, attr, var.get().strip())

        entry = tk.Entry(root, textvariable=var, width=20)
        entry.grid(row=r, column=RIGHT_CTRL_COL, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        widgets[attr] = entry
        bind_commit(entry, apply)

        def set_value(value: object) -> None:
            var.set(value or "")
            apply()

        controls[attr] = set_value

    add_color_entry("Couleurs", "colors", "Une a trois couleurs separees par des virgules : du calme au fort.")
    add_color_entry("Couleur de fond", "bg_color", "Couleur du fond derriere la sphere.")
    tk.Label(root, text="Valider une couleur : Entree ou clic ailleurs", fg=GUI_MUTED_FG,
             ).grid(row=next_row("right"), column=RIGHT_LABEL_COL, columnspan=2, sticky="w", padx=ROW_PADX)

    # =========================== SECTION PARTAGEE ===========================
    row_shared = max(rows["left"], rows["right"])

    def next_shared_row() -> int:
        nonlocal row_shared
        row_shared += 1
        return row_shared - 1

    tk.Frame(root, bg=GUI_PANEL_BG, width=1).grid(
        row=1, column=SPACER_COL, rowspan=row_shared - 1, sticky="ns", padx=ROW_PADX + 4)
    tk.Frame(root, bg=GUI_PANEL_BG, height=1).grid(
        row=next_shared_row(), column=0, columnspan=TOTAL_COLUMNS, sticky="ew", padx=ROW_PADX, pady=(SECTION_GAP, 0))
    tk.Label(root, text="PRESETS", font=GUI_FONT_SMALL, fg=GUI_MUTED_FG,
             ).grid(row=next_shared_row(), column=0, columnspan=TOTAL_COLUMNS, sticky="w", padx=ROW_PADX, pady=(0, 2))
    presets_block_first_row = row_shared - 2

    def capture_overrides() -> dict:
        overrides = {attr: getattr(args, attr) for attr in controls}
        overrides[AutomationManager.AUTOMATION_KEY] = automation.capture()
        return overrides

    def apply_preset(overrides: dict) -> list[str]:
        skipped = [key for key in overrides if key != AutomationManager.AUTOMATION_KEY and key not in controls]
        for key, value in overrides.items():
            if key != AutomationManager.AUTOMATION_KEY and key in controls:
                controls[key](value)
        automation_data = overrides.get(AutomationManager.AUTOMATION_KEY)
        if isinstance(automation_data, dict):
            automation.apply(automation_data)
        return skipped

    def on_load_preset(name: str) -> None:
        presets = preset_store.all()
        if name not in presets:
            status["text"] = f"preset inconnu: {name}"
            return
        skipped = apply_preset(presets[name])
        text = f"preset '{name}' charge"
        if skipped:
            text += f" (ignore: {', '.join(skipped)})"
        status["text"] = text

    def select_preset(name: str) -> None:
        preset_var.set(name)
        on_load_preset(name)

    def refresh_preset_menu(select: str | None = None) -> None:
        names = sorted(preset_store.all())
        menu = preset_menu["menu"]
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=lambda n=name: select_preset(n))
        if select is not None:
            preset_var.set(select)
        elif names and preset_var.get() not in names:
            preset_var.set(names[0])

    preset_var = tk.StringVar(value=args.preset or "default")
    r = next_shared_row()
    tk.Label(root, text="Charger").grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    preset_frame = tk.Frame(root)
    preset_frame.grid(row=r, column=1, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    preset_menu = tk.OptionMenu(preset_frame, preset_var, "")
    style_option_menu(preset_menu)
    preset_menu.pack(side="left")
    widgets["preset_menu"] = preset_menu

    def on_update_preset() -> None:
        name = preset_var.get()
        if not name:
            status["text"] = "aucun preset selectionne"
            return
        if name == "default":
            status["text"] = "'default' n'est pas modifiable"
            return
        preset_store.save_user(name, capture_overrides())
        text = f"preset '{name}' mis a jour ({USER_PRESETS_PATH})"
        if name in PRESETS:
            text += " -- remplace desormais le preset integre du meme nom sur cette machine"
        status["text"] = text

    tk.Button(preset_frame, text="Mettre a jour", command=on_update_preset).pack(side="left", padx=(8, 0))

    def on_delete_preset() -> None:
        name = preset_var.get()
        if not name:
            status["text"] = "aucun preset selectionne"
            return
        if name not in preset_store.load_user():
            status["text"] = f"'{name}' est un preset integre, impossible a supprimer"
            return

        def do_delete() -> None:
            preset_store.delete_user(name)
            refresh_preset_menu()
            status["text"] = f"preset '{name}' supprime"

        confirm_dialog(root, "Supprimer le preset",
                       f"Supprimer definitivement le preset '{name}' ?\nCette action est irreversible.", do_delete)

    tk.Button(preset_frame, text="Supprimer", command=on_delete_preset).pack(side="left", padx=(8, 0))
    refresh_preset_menu()

    save_name_var = tk.StringVar(value="")
    r = next_shared_row()
    tk.Label(root, text="Sauvegarder sous").grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    save_preset_frame = tk.Frame(root)
    save_preset_frame.grid(row=r, column=1, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    save_name_entry = tk.Entry(save_preset_frame, textvariable=save_name_var, width=14)
    save_name_entry.pack(side="left")
    widgets["save_name"] = save_name_entry
    presets_block_last_row = r

    def on_save_preset(_evt: object = None) -> None:
        from preset_registry import normalize_name
        name = normalize_name(save_name_var.get())
        if not name:
            status["text"] = "nom de preset vide"
            return
        if name in PRESETS:
            status["text"] = f"'{name}' est un preset integre, choisis un autre nom"
            return
        preset_store.save_user(name, capture_overrides())
        refresh_preset_menu(select=name)
        save_name_var.set("")
        status["text"] = f"preset '{name}' sauvegarde ({USER_PRESETS_PATH})"

    save_name_entry.bind("<Return>", on_save_preset)
    tk.Button(save_preset_frame, text="Sauvegarder", command=on_save_preset).pack(side="left", padx=(8, 0))

    tk.Frame(root, bg=GUI_PANEL_BG, height=1).grid(
        row=next_shared_row(), column=0, columnspan=TOTAL_COLUMNS, sticky="ew", padx=ROW_PADX, pady=(SECTION_GAP, 0))
    status_label = tk.Label(root, text="", justify="left", anchor="w", fg=GUI_ACCENT, font=GUI_FONT_MONO)
    status_label.grid(row=next_shared_row(), column=0, columnspan=TOTAL_COLUMNS, sticky="w", padx=ROW_PADX, pady=(10, 10))

    root.after(AUTOMATE_TICK_MS, lambda: automation.tick(controls, finished_event))
    widgets["status"] = status_label

    def refresh() -> None:
        status_label.config(text=status.get("text", ""))
        if finished_event.is_set():
            if not embedded:
                root.destroy()
            return
        refresh_after_id["id"] = root.after(200, refresh)

    if not embedded:
        root.protocol("WM_DELETE_WINDOW", stop_event.set)
    refresh()

    block_first = presets_block_first_row
    if presets_first:
        move_rows_to_top(root, presets_block_first_row, presets_block_last_row)
        block_first = 1
    block_last = block_first + (presets_block_last_row - presets_block_first_row)
    widgets["presets"] = [w for w in root.grid_slaves() if block_first <= int(w.grid_info()["row"]) <= block_last]

    def commit_now() -> None:
        for handler in commit_callbacks:
            handler()

    controller = GuiController("sphere", args, widgets, capture_overrides, apply_preset, commit_now)
    if host_on_change is not None:
        controller.on_change = host_on_change
    watch_args(args, root, lambda a: controller.on_change(a) if controller.on_change else None)
    return controller
