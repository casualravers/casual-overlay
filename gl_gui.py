"""Fenetre de reglages tkinter de audio2wave_gl (--gui).

La partie "Live" (le visuel de fond ffmpeg) N'EST PAS reecrite ici : c'est la fenetre de
reglages d'audio2wave_live.py (`build_gui`, importee du depot audio2wave comme dependance),
avec ses presets, ses automations de courbes, ses info-bulles et son theme. Ce module :

  1. l'appelle telle quelle sur la fenetre Tk, avec ses trois evenements habituels
     (`restart_event`, `stop_event`, `finished_event`) ;
  2. la nettoie de ce qui n'a pas de sens ici (boutons Snap/Ridge, taille de fenetre) ;
  3. fait le pont entre son `restart_event` et `ProducerManager` : a chaque reglage, `build_gui`
     a deja mute les options live et positionne l'evenement, on en prend une copie et on
     remplace le producteur ffmpeg a chaud, sans toucher a la fenetre GL (aucun redemarrage
     si la commande ffmpeg est inchangee) ;
  4. ajoute a droite ses propres panneaux, dans la meme fenetre (partie OVERLAY, bandeau violet ;
     la partie LIVE a un bandeau turquoise) :
        PRESETS OVERLAY + FOND (motif genere) + EFFETS + HALO HOLOGRAPHIQUE
        LOGO               + ANALYSE AUDIO + AFFICHAGE + mesures.

Sous-ensembles sans aucune modification du depot audio2wave : tout passe par le contrat public
de `build_gui(args, width, height, status, restart_event, stop_event, finished_event, root)`.

tkinter garde le fil principal, la fenetre GL tourne dans un fil (comme run() dans les
scripts d'audio2wave): toute action qui doit s'executer dans le fil GL passe par
`session.commands`.
"""

from __future__ import annotations

import copy
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog

import audio2wave_gl as gl

REFRESH_MS = 500
METER_MS = 60
BRIDGE_MS = 100
AUTOMATION_SYNC_MS = 200
HEX_DIGITS = "0123456789abcdefABCDEF"


class Zone:
    """Endroit ou poser des widgets en grille: un Frame avec son propre compteur de lignes."""

    def __init__(self, parent, label_col: int = 0, ctrl_col: int = 1, first_row: int = 0):
        self.parent, self.label_col, self.ctrl_col = parent, label_col, ctrl_col
        self.row = first_row

    def next_row(self) -> int:
        self.row += 1
        return self.row - 1


# Couleurs des deux grandes parties de la fenetre (le theme sombre d'audio2wave reste le fond) :
# LIVE (spectre ffmpeg, fenetre d'audio2wave) en turquoise, OVERLAY (casual-overlay) en violet.
LIVE_COLOR = "#5fd4c8"
OVERLAY_COLOR = "#b392ff"
BANNER_FG = "#0b1018"

# Titres de section d'audio2wave (texte d'origine -> libelle affiche)
LIVE_SECTIONS = {"SOURCE": "SOURCE", "SPECTRE": "SPECTRE", "COULEURS": "COULEURS", "SORTIE": "SORTIE",
                 "PRESETS": "PRESETS LIVE  (audio2wave)"}


def style_band(label, a2w, text: str | None = None, color: str = LIVE_COLOR) -> None:
    """Petit titre de section -> bandeau pleine largeur : fond du theme (plus clair que la fenetre), texte
    en gras a la couleur de la partie a laquelle il appartient."""
    label.config(text=text or label.cget("text"), bg=a2w.GUI_PANEL_BG, fg=color,
                 font=("Segoe UI", 9, "bold"), anchor="w", padx=8, pady=2)
    label.grid_configure(sticky="ew", pady=(6, 3))


def tidy_live_gui(root) -> None:
    """Retire de la fenetre d'audio2wave ce qui n'a pas de sens dans casual-overlay:
    les boutons de bascule Snap/Ridge (ces modes ne sont pas des sources ici) et la taille
    de fenetre, qui devient la taille du rendu ffmpeg, fixe (la texture video est fixee au
    lancement: le pont remet toujours cette taille)."""

    import audio2wave as a2w

    def walk(widget) -> None:
        for child in widget.winfo_children():
            cls = child.winfo_class()
            if cls == "Button" and child.cget("text") in ("Snap", "Ridge"):
                child.destroy()
                continue
            if cls == "Label" and child.cget("text") in LIVE_SECTIONS:
                # Les petits titres de section d'audio2wave deviennent des bandeaux pleine largeur,
                # pour que les parties de la fenetre se distinguent d'un coup d'oeil.
                style_band(child, a2w, LIVE_SECTIONS[child.cget("text")])
            if cls == "Entry" and int(child.cget("width")) == 6:
                child.config(state="readonly")
            if cls == "Label" and child.cget("text") == "Taille fenetre":
                child.config(text="Taille du rendu")
            if cls == "Label" and child.cget("text") == "Reglages Live":
                child.config(text="Reglages casual-overlay GL")
            walk(child)

    walk(root)


SHORTCUTS_HELP = (
    "Raccourcis (fenetre de rendu active) :\n"
    "Echap : quitter\n"
    "F : fenetre / plein ecran\n"
    "H : barres de debug\n"
    "B : fond spectre / motif genere\n"
    "T : automations actives / figees\n"
    "1 a 5 : effets on/off (wobble, ripple,\n"
    "    chroma, glitch, logo)\n"
    "+ / - ou PageUp / PageDown : intensite\n"
    "    globale des effets\n"
    "Maj + 1 a 4 : meme effet sur la couche\n"
    "    du logo (delie fond et logo)\n"
    "L : lier / separer les effets du fond\n"
    "    et du logo\n"
    "C : halo holographique on/off\n"
    "Haut / Bas : sensibilite du kick\n"
    "R : recharger shaders et reglages\n"
    "P : sauver les reglages"
)


def run_gui(s, live, on_ready=None) -> None:
    """Construit la fenetre et bloque dans mainloop() jusqu'a la fin de la session.

    `on_ready(controls)` (tests): appele dans le fil tkinter, une fois la fenetre construite."""
    import audio2wave as a2w

    Tooltip = live.Tooltip
    params = s.params
    manager = s.manager
    render_w, render_h = s.render_size

    root = tk.Tk()

    # ---------------------------------------------------- 1. la GUI d'audio2wave, telle quelle
    restart_event = threading.Event()
    live_status = {"text": ""}
    live.build_gui(s.live_args, render_w, render_h, live_status, restart_event, s.stop_event,
                   s.finished_event, root=root)
    root.title("casual-overlay GL - reglages")
    tidy_live_gui(root)
    root.update_idletasks()
    cols, nrows = root.grid_size()

    # Deux grandes parties, bien distinctes : bandeaux de couleur en haut (a la place du titre
    # d'audio2wave) et gros trait vertical entre les deux.
    title = next((w for w in root.grid_slaves(row=0) if w.winfo_class() == "Label"
                  and w.cget("text") == "Reglages casual-overlay GL"), None)
    if title is not None:
        title.grid_remove()
    tk.Label(root, text="LIVE  -  spectre audio (audio2wave)", bg=LIVE_COLOR, fg=BANNER_FG,
             font=("Segoe UI", 12, "bold"), anchor="w", padx=12, pady=5).grid(
        row=0, column=0, columnspan=cols, sticky="ew", padx=(6, 0), pady=(8, 2))
    tk.Label(root, text="OVERLAY  -  fond, logo, effets (casual-overlay)", bg=OVERLAY_COLOR, fg=BANNER_FG,
             font=("Segoe UI", 12, "bold"), anchor="w", padx=12, pady=5).grid(
        row=0, column=cols + 1, columnspan=2, sticky="ew", padx=(0, 10), pady=(8, 2))
    ROW_PADX, ROW_PADY, SECTION_GAP = 6, 2, 5

    # ------------------------------------------------ 2. pont restart_event -> ProducerManager
    last = {"fullscreen": bool(s.live_args.fullscreen)}

    def push_live() -> None:
        args = s.live_args
        # La case "Plein ecran" de la GUI live commande la fenetre GL.
        if bool(args.fullscreen) != last["fullscreen"]:
            last["fullscreen"] = bool(args.fullscreen)
            s.commands.put(f"fullscreen={int(last['fullscreen'])}")
        if manager is None:
            live_status["text"] = "Mode --synthetic: les reglages du fond ffmpeg sont ignores"
            return
        new = copy.copy(args)                       # build_gui a fini de muter `args`: copie coherente
        new.size = f"{render_w}x{render_h}"         # resolution du rendu fixe
        if not new.device:
            live_status["text"] = "Choisis une entree audio"
            return
        bad = gl.invalid_colors(new, manager.args)
        if bad:
            # La GUI d'audio2wave applique chaque saisie apres 400 ms: "teal" tape lentement passe par "t".
            # ffmpeg ne refuse pas un nom inconnu (il logue et affiche du blanc): on attend un nom valide.
            live_status["text"] = f"Couleur inconnue: {', '.join(bad)} (en attente d'un nom valide)"
            return
        if live.producer_command(new) == live.producer_command(manager.args):
            live_status["text"] = "Reglages a jour"   # rien ne change cote ffmpeg (ex. plein ecran)
            return
        manager.request_restart(new)

    seen_live = {"text": None}

    def poll_bridge() -> None:
        if restart_event.is_set():
            restart_event.clear()
            push_live()
        if manager is not None:
            msg = manager.status.get("live")
            if msg and msg != seen_live["text"]:
                seen_live["text"] = msg
                live_status["text"] = msg            # affiche dans la ligne de statut d'audio2wave
        root.after(BRIDGE_MS, poll_bridge)

    # ------------------------------------------------------ 3. nos panneaux, a droite
    separator = tk.Frame(root, bg=OVERLAY_COLOR, width=3)       # frontiere LIVE | OVERLAY
    separator.grid(row=1, column=cols, rowspan=nrows - 1, sticky="ns", padx=ROW_PADX + 4)
    col_a = tk.Frame(root)
    col_a.grid(row=1, column=cols + 1, rowspan=nrows - 1, sticky="nw", padx=(0, 10))
    col_b = tk.Frame(root)
    col_b.grid(row=1, column=cols + 2, rowspan=nrows - 1, sticky="nw", padx=(0, 10))
    zone_a = Zone(col_a)
    zone_b = Zone(col_b)

    def add_label(z: Zone, text: str, r: int, tooltip: str | None = None) -> tk.Label:
        label = tk.Label(z.parent, text=text)
        label.grid(row=r, column=z.label_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        if tooltip:
            Tooltip(label, tooltip)
        return label

    def add_section_title(z: Zone, title: str) -> None:
        """Bandeau de section (meme style que les titres de la partie LIVE, en violet)."""
        band = tk.Label(z.parent, text=title.upper())
        band.grid(row=z.next_row(), column=z.label_col, columnspan=3, sticky="ew", padx=ROW_PADX)
        style_band(band, a2w, color=OVERLAY_COLOR)

    def add_separator(z: Zone, title: str | None = None) -> None:
        if title:
            add_section_title(z, title)
            return
        tk.Frame(z.parent, bg=a2w.GUI_PANEL_BG, height=1).grid(
            row=z.next_row(), column=z.label_col, columnspan=3, sticky="ew",
            padx=ROW_PADX, pady=(SECTION_GAP, SECTION_GAP))

    synced: dict[str, tk.Variable] = {}         # cle de params -> variable du widget (pour charger un preset)

    def bind_param(key: str, var: tk.Variable, conv=float) -> None:
        synced[key] = var

        def on_write(*_a) -> None:
            try:
                params[key] = conv(var.get())
            except (ValueError, tk.TclError):
                pass    # champ en cours de saisie

        var.trace_add("write", on_write)

    # ---- automations: l'editeur de courbes d'audio2wave (case "~" + bouton de courbe), pose a droite
    # du curseur (3e colonne) plutot que dessous, pour ne pas allonger la fenetre. Le MOTEUR est dans
    # audio2wave_gl (AutomationEngine, fil de rendu): ici on ne fait qu'editer l'etat (copie dans
    # params["_automation"]) et faire suivre les curseurs.
    params["_automation"] = gl.merge_automation(params.get("_automation"))
    automation = live.AutomationManager(root, Tooltip, a2w.GUI_PANEL_BG, a2w.GUI_MUTED_FG, a2w.GUI_ACCENT)
    auto_vars: dict[str, tk.Variable] = {}

    def add_auto(z: Zone, r: int, key: str, var: tk.Variable) -> None:
        label, lo, hi = gl.AUTOMATION_SPECS[key][:3]
        holder = tk.Frame(z.parent)
        holder.grid(row=r, column=2, sticky="w", padx=(0, 4))
        automation.register(holder, key, label, lo, hi)
        for widget in holder.winfo_children()[0].winfo_children():
            if widget.winfo_class() == "Button":
                widget.config(text="∿", padx=3)          # "courbe" -> compact, l'info-bulle reste
        auto_vars[key] = var

    def compact_scale(parent, lo: float, hi: float, step: float, var: tk.Variable, length: int = 120) -> tk.Frame:
        """Curseur dont la valeur s'affiche A COTE (et non au-dessus): une ligne fait ~22 px de moins,
        ce qui garde la fenetre sous la hauteur d'un ecran 1080 p meme avec le bloc motif ouvert."""
        frame = tk.Frame(parent)
        decimals = len(f"{step:.6f}".rstrip("0").split(".")[1]) if step < 1 else 0
        scale = tk.Scale(frame, from_=lo, to=hi, resolution=step, orient="horizontal", variable=var,
                         length=length, showvalue=False, width=12, sliderlength=16)
        scale.pack(side="left")
        value = tk.Label(frame, width=5, anchor="e", fg=a2w.GUI_MUTED_FG)
        value.pack(side="left", padx=(4, 0))

        def show(*_a) -> None:
            try:
                value.config(text=f"{float(var.get()):.{decimals}f}")
            except (ValueError, tk.TclError):
                pass

        var.trace_add("write", show)
        show()
        frame.scale = scale
        return frame

    def add_slider(z: Zone, label: str, lo: float, hi: float, step: float, var: tk.Variable,
                   tooltip: str | None = None, length: int = 120, auto_key: str | None = None) -> tk.Scale:
        r = z.next_row()
        add_label(z, label, r, tooltip)
        holder = compact_scale(z.parent, lo, hi, step, var, length)
        scale = holder.scale
        holder.grid(row=r, column=z.ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        if auto_key:
            add_auto(z, r, auto_key, var)
        return scale

    def param_slider(z: Zone, label: str, key: str, lo: float, hi: float, step: float,
                     tooltip: str | None = None) -> tk.DoubleVar:
        var = tk.DoubleVar(value=params[key])
        bind_param(key, var)
        add_slider(z, label, lo, hi, step, var, tooltip, auto_key=key if key in gl.AUTOMATION_SPECS else None)
        return var

    def param_radio(z: Zone, label: str, key: str, choices: tuple[tuple[str, str], ...],
                    tooltip: str | None = None) -> tk.StringVar:
        r = z.next_row()
        add_label(z, label, r, tooltip)
        var = tk.StringVar(value=params[key])
        bind_param(key, var, str)
        frame = tk.Frame(z.parent)
        frame.grid(row=r, column=z.ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        for text, value in choices:
            tk.Radiobutton(frame, text=text, variable=var, value=value).pack(side="left")
        return var

    def color_row(z: Zone, label: str, key: str, tooltip: str | None = None,
                  into: tk.Frame | None = None) -> tk.StringVar:
        """Champ hexadecimal + pastille + selecteur de couleur, ecrit params[key]. `into`: un cadre deja
        pose sur une ligne (plusieurs couleurs cote a cote), sinon une ligne a soi avec son libelle."""
        if into is None:
            r = z.next_row()
            add_label(z, label, r, tooltip)
            frame = tk.Frame(z.parent)
            frame.grid(row=r, column=z.ctrl_col, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        else:
            frame = tk.Frame(into)
            frame.pack(side="left", padx=(0, 10))
        var = tk.StringVar(value=params[key])
        synced[key] = var
        swatch = tk.Label(frame, width=3, bg=params[key])
        tk.Entry(frame, textvariable=var, width=8 if into is None else 7).pack(side="left")
        swatch.pack(side="left", padx=(8, 0))

        def on_write(*_a) -> None:
            text = var.get().strip().lstrip("#")
            # Seule une valeur complete et valide est poussee: pendant la saisie, l'ancienne reste.
            if len(text) == 6 and all(c in HEX_DIGITS for c in text):
                params[key] = "#" + text.lower()
                swatch.config(bg=params[key])

        var.trace_add("write", on_write)

        def pick() -> None:
            chosen = colorchooser.askcolor(color=params[key], title=label)
            if chosen and chosen[1]:
                var.set(chosen[1])

        pick_button = tk.Button(frame, text="...", command=pick, padx=6)
        pick_button.pack(side="left", padx=(6, 0))
        Tooltip(pick_button, "Choisir une couleur")
        return var

    # ---- colonne A : PRESETS OVERLAY (look complet: fond, logo, effets, halo, automations)
    # Meme classe (PresetStore) et memes gestes que les presets LIVE d'audio2wave, dans un fichier a part.
    add_section_title(zone_a, "Presets overlay")
    overlay_store = live.PresetStore(gl.OVERLAY_PRESETS, gl.OVERLAY_PRESETS_PATH)
    overlay_var = tk.StringVar(value="default")
    preset_msg = tk.StringVar(value="Un preset = tout le look de l'overlay.")

    load_row = tk.Frame(col_a)
    load_row.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    tk.Label(load_row, text="Charger").pack(side="left", padx=(0, 10))
    overlay_menu = tk.OptionMenu(load_row, overlay_var, "")
    a2w.style_option_menu(overlay_menu)
    overlay_menu.pack(side="left")

    def refresh_overlay_menu(select: str | None = None) -> None:
        names = sorted(overlay_store.all(), key=lambda n: (n != "default", n))
        menu = overlay_menu["menu"]
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=lambda n=name: load_overlay_preset(n))
        if select is not None:
            overlay_var.set(select)
        elif overlay_var.get() not in names:
            overlay_var.set("default")

    def load_overlay_preset(name: str) -> None:
        overlay_var.set(name)
        presets = overlay_store.all()
        if name not in presets:
            preset_msg.set(f"preset inconnu: {name}")
            return
        values = gl.overlay_preset_values(presets[name], params)
        automation_state = values.pop("_automation", None)
        for key, value in values.items():
            params[key] = value                  # lu a chaque image par le fil GL
        if automation_state is not None:
            params["_automation"] = automation_state
            automation.apply(automation_state)
        sync_widgets()
        preset_msg.set(f"preset '{name}' charge")

    def update_overlay_preset() -> None:
        name = overlay_var.get()
        if name == "default":
            preset_msg.set("'default' n'est pas modifiable (reglages d'origine)")
            return
        sync_automation_now()
        overlay_store.save_user(name, gl.capture_overlay(params))
        preset_msg.set(f"preset '{name}' mis a jour ({gl.OVERLAY_PRESETS_PATH.name})"
                       + (" - remplace le preset integre du meme nom" if name in gl.OVERLAY_PRESETS else ""))

    def delete_overlay_preset() -> None:
        name = overlay_var.get()
        if name not in overlay_store.load_user():
            preset_msg.set(f"'{name}' est un preset integre, impossible a supprimer")
            return

        def do_delete() -> None:
            overlay_store.delete_user(name)
            refresh_overlay_menu()
            preset_msg.set(f"preset '{name}' supprime")

        live.confirm_dialog(root, "Supprimer le preset",
                            f"Supprimer definitivement le preset overlay '{name}' ?\nCette action est irreversible.",
                            do_delete)

    tk.Button(load_row, text="Mettre a jour", command=update_overlay_preset).pack(side="left", padx=(8, 0))
    tk.Button(load_row, text="Supprimer", command=delete_overlay_preset).pack(side="left", padx=(6, 0))

    save_row = tk.Frame(col_a)
    save_row.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    tk.Label(save_row, text="Sauvegarder sous").pack(side="left", padx=(0, 10))
    save_name_var = tk.StringVar(value="")
    save_entry = tk.Entry(save_row, textvariable=save_name_var, width=14)
    save_entry.pack(side="left")

    def save_overlay_preset(_evt=None) -> None:
        name = save_name_var.get().strip().lower()
        if not name:
            preset_msg.set("nom de preset vide")
            return
        if name in gl.OVERLAY_PRESETS:
            preset_msg.set(f"'{name}' est un preset integre, choisis un autre nom")
            return
        sync_automation_now()
        overlay_store.save_user(name, gl.capture_overlay(params))
        refresh_overlay_menu(select=name)
        save_name_var.set("")
        preset_msg.set(f"preset '{name}' sauvegarde ({gl.OVERLAY_PRESETS_PATH.name})")

    save_entry.bind("<Return>", save_overlay_preset)
    tk.Button(save_row, text="Sauvegarder", command=save_overlay_preset).pack(side="left", padx=(8, 0))
    tk.Label(col_a, textvariable=preset_msg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=380).grid(
        row=zone_a.next_row(), column=0, columnspan=3, sticky="we", padx=ROW_PADX, pady=(0, 2))
    refresh_overlay_menu()

    # ---- colonne A : FOND (motif genere)
    add_section_title(zone_a, "Fond")
    bg_var = tk.StringVar(value=params["bg_mode"])
    r = zone_a.next_row()
    add_label(zone_a, "Fond", r, "Spectre audio = le visuel ffmpeg regle dans le panneau de gauche. "
                                 "Motif genere = degrades et damier calcules par la carte graphique (touche B).")
    bg_frame = tk.Frame(col_a)
    bg_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, value in (("Spectre audio", "live"), ("Motif genere", "pattern")):
        tk.Radiobutton(bg_frame, text=text, variable=bg_var, value=value).pack(side="left")
    bind_param("bg_mode", bg_var, str)

    pattern_box = tk.Frame(col_a)
    pattern_box.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="new")
    pattern_box.columnconfigure(2, weight=1)         # le bandeau "Motif genere" prend toute la largeur
    pz = Zone(pattern_box)
    add_section_title(pz, "Motif genere")
    param_radio(pz, "Palette", "bg_palette", (("Arc-en-ciel", "classic"), ("Duo", "duo")),
                tooltip="Arc-en-ciel: le visuel d'origine (canaux R/V/B qui defilent). "
                        "Duo: degrade lisse entre deux couleurs.")
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

    def show_bg(*_a) -> None:
        if bg_var.get() == "pattern":
            pattern_box.grid()
        else:
            pattern_box.grid_remove()

    bg_var.trace_add("write", show_bg)
    show_bg()

    # ---- colonne A : EFFETS
    add_separator(zone_a, "Effets")
    fx_on_vars: list[tk.IntVar] = []
    fx_int_vars: list[tk.DoubleVar] = []
    fx_labels = ("Wobble", "Onde de choc", "Aberration chromatique", "Glitch", "Logo (pulse/contour)")
    fx_tips = ("Ondulation de l'image, amplitude = basses.",
               "Onde de choc depuis le centre a chaque kick.",
               "Canaux R/V/B decales depuis le centre au kick.",
               "Bandes horizontales decalees sur les gros kicks / aigus.",
               "Interrupteur des reactions du logo (pulsation, tremblement, contour).")
    for i, name in enumerate(fx_labels):
        r = zone_a.next_row()
        on_var = tk.IntVar(value=int(params["fx_on"][i]))
        fx_on_vars.append(on_var)
        on_var.trace_add("write", lambda *_a, i=i, v=on_var: params["fx_on"].__setitem__(i, int(v.get())))
        cb = tk.Checkbutton(col_a, text=name, variable=on_var)
        cb.grid(row=r, column=0, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        Tooltip(cb, fx_tips[i])
        int_var = tk.DoubleVar(value=params["fx_int"][i])
        fx_int_vars.append(int_var)
        int_var.trace_add("write", lambda *_a, i=i, v=int_var: params["fx_int"].__setitem__(i, _safe(v, 1.0)))
        compact_scale(col_a, 0, 2, 0.05, int_var, 110).grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        add_auto(zone_a, r, f"fx_int{i}", int_var)

    master_var = tk.DoubleVar(value=params["master"])
    bind_param("master", master_var)
    add_slider(zone_a, "Intensite globale", 0.0, 2.0, 0.1, master_var, length=110)
    sens_var = tk.DoubleVar(value=params["sensitivity"])
    bind_param("sensitivity", sens_var)
    add_slider(zone_a, "Sensibilite kick", 0.25, 4.0, 0.05, sens_var, length=110,
               tooltip="Plus haut = kicks detectes plus facilement (aussi touches haut/bas dans la fenetre GL).")

    # ---- colonne A : EFFETS DE LA COUCHE LOGO (le fond garde les reglages ci-dessus)
    add_separator(zone_a, "Effets sur le logo")
    link_var = tk.IntVar(value=int(float(params["fx_link"]) >= 0.5))
    link_check = tk.Checkbutton(col_a, text="Memes effets que le fond", variable=link_var)
    link_check.grid(row=zone_a.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    Tooltip(link_check, "Coche: le logo subit exactement les memes deformations que le fond. Decoche: le logo a "
                        "ses propres effets (ci-dessous), ses ondes partent de son centre et son glitch tire "
                        "d'autres bandes. Touche L; Maj+1 a 4 dans la fenetre GL agissent sur le logo.")
    logo_fx_box = tk.Frame(col_a)
    logo_fx_box.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="nw")
    lz = Zone(logo_fx_box)
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
    add_separator(zone_a, "Halo holographique (logo)")
    holo_var = tk.IntVar(value=int(float(params["holo_on"]) >= 0.5))
    bind_param("holo_on", holo_var, float)
    holo_row = tk.Frame(col_a)
    holo_row.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    holo_check = tk.Checkbutton(holo_row, text="Halo holographique", variable=holo_var)
    holo_check.pack(side="left")
    holo_box = tk.Frame(col_a)            # reglages repliables: une fenetre trop haute deborde de l'ecran
    holo_box.grid(row=zone_a.next_row(), column=0, columnspan=3, sticky="nw")
    holo_box.grid_remove()

    def toggle_holo_box() -> None:
        if holo_box.winfo_manager():
            holo_box.grid_remove()
            holo_toggle.config(text="Reglages >")
        else:
            holo_box.grid()
            holo_toggle.config(text="Reglages v")

    holo_toggle = tk.Button(holo_row, text="Reglages >", command=toggle_holo_box, padx=6)
    holo_toggle.pack(side="left", padx=(8, 0))
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

    # ---- colonne B : LOGO
    add_section_title(zone_b, "Logo / texte")
    source_var = tk.StringVar(value=params["logo_source"])
    r = zone_b.next_row()
    add_label(zone_b, "Source", r, "Image PNG, texte que tu tapes, ou video animee (logo detoure): tout est "
                                   "traite comme un logo (position, opacite, pulsation, tremblement, contour).")
    source_frame = tk.Frame(col_b)
    source_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, value in (("Image", "image"), ("Texte", "text"), ("Video", "video")):
        tk.Radiobutton(source_frame, text=text, variable=source_var, value=value).pack(side="left")
    bind_param("logo_source", source_var, str)

    # Les trois blocs occupent la meme cellule; un seul est affiche.
    image_box = tk.Frame(col_b)
    text_box = tk.Frame(col_b)
    video_box = tk.Frame(col_b)
    source_row = zone_b.next_row()
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

    add_separator(zone_b, "Position")
    x_var = param_slider(zone_b, "Position X", "logo_x", 0.0, 1.0, 0.01, "Centre, 0 = gauche, 1 = droite.")
    y_var = param_slider(zone_b, "Position Y", "logo_y", 0.0, 1.0, 0.01, "Centre, 0 = haut, 1 = bas.")
    param_slider(zone_b, "Opacite", "logo_opacity", 0.0, 1.0, 0.05)
    tk.Button(col_b, text="Centrer", command=lambda: (x_var.set(0.5), y_var.set(0.5))).grid(
        row=zone_b.next_row(), column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

    add_separator(zone_b, "Reaction a l'audio")
    param_slider(zone_b, "Pulsation", "logo_pulse", 0.0, 0.5, 0.01,
                 "Zoom du logo a chaque kick (0,12 = +12 %). Jamais coupe hors cadre.")
    param_slider(zone_b, "Tremblement", "logo_jitter", 0.0, 0.03, 0.001, "Vibration du logo proportionnelle aux aigus.")
    param_slider(zone_b, "Contour lumineux", "logo_glow", 0.0, 3.0, 0.1,
                 "Intensite du halo autour du logo, module par les basses.")
    param_slider(zone_b, "Rayon du contour", "logo_glow_radius", 0.0, 3.0, 0.1)
    color_var = color_row(zone_b, "Couleur du contour", "logo_glow_color")

    # ---- colonne B : ANALYSE AUDIO + AFFICHAGE
    add_separator(zone_b, "Analyse audio (effets)")
    audio_choices = gl.AudioController.input_choices() if not s.audio.synthetic else []
    if not audio_choices:
        tk.Label(col_b, text=s.audio.name, fg=a2w.GUI_MUTED_FG, wraplength=300, justify="left").grid(
            row=zone_b.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    else:
        labels = {label: idx for idx, label in audio_choices}
        current = next((label for idx, label in audio_choices if s.audio.name in label), s.audio.name)
        audio_var = tk.StringVar(value=current)
        r = zone_b.next_row()
        add_label(zone_b, "Entree", r, "Entree sounddevice qui alimente les effets (kick, basses...). "
                                       "Independante de l'entree ffmpeg du fond.")
        menu = tk.OptionMenu(col_b, audio_var, *labels.keys())
        a2w.style_option_menu(menu)
        menu.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

        def on_audio_change(*_a) -> None:
            try:
                name = s.audio.switch(labels[audio_var.get()])
                s.status["msg"] = f"Analyse sur: {name}"
            except Exception as exc:
                s.status["msg"] = f"Entree refusee: {exc}"

        audio_var.trace_add("write", on_audio_change)

    add_separator(zone_b, "Affichage")
    for row_buttons in ((("Plein ecran", "fullscreen"), ("Barres debug", "hud")),
                        (("Recharger shaders", "reload"), ("Sauver reglages", "save"))):
        frame = tk.Frame(col_b)
        frame.grid(row=zone_b.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        for text, cmd in row_buttons:
            tk.Button(frame, text=text, command=lambda c=cmd: s.commands.put(c)).pack(side="left", padx=(0, 6))

    auto_master_var = tk.IntVar(value=int(float(params["auto_master"]) >= 0.5))
    bind_param("auto_master", auto_master_var, float)
    frame = tk.Frame(col_b)
    frame.grid(row=zone_b.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    master_check = tk.Checkbutton(frame, text="Automations actives", variable=auto_master_var)
    master_check.pack(side="left")
    Tooltip(master_check, "Interrupteur general (touche T): decoche, toutes les valeurs restent ou elles sont. "
                          "Les cases '~' a cote de chaque curseur choisissent ce qui varie, le bouton a cote "
                          "ouvre l'editeur de courbe.")

    def reset_automation() -> None:
        params["_automation"] = gl.default_automation()
        automation.apply(params["_automation"])
        s.status["msg"] = "Automations: ambiance par defaut"

    tk.Button(frame, text="Ambiance par defaut", command=reset_automation).pack(side="left", padx=(8, 0))

    shortcuts_label = tk.Label(frame, text=" (i) ", fg=a2w.GUI_ACCENT, font=a2w.GUI_FONT, cursor="question_arrow")
    shortcuts_label.pack(side="left", padx=(8, 0))
    Tooltip(shortcuts_label, SHORTCUTS_HELP)

    meter = tk.Canvas(col_b, width=260, height=58, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    meter.grid(row=zone_b.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 2))
    bars = {}
    for i, (name, color) in enumerate((("bass", "#ff5a4d"), ("mid", "#66ff73"), ("high", "#66b3ff"),
                                       ("beat", "#ffe633"))):
        y0 = 4 + i * 13
        meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        bars[name] = meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    status_label = tk.Label(col_b, text="", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=320)
    status_label.grid(row=zone_b.next_row(), column=0, columnspan=2, sticky="we", padx=ROW_PADX, pady=(4, 6))

    # ---------------------------------------------------------------- boucle
    automation.apply(params["_automation"])          # charge l'etat (sauve ou par defaut) dans l'editeur

    def sync_automation_now() -> None:
        """Editeur -> params (le moteur du fil GL lit params["_automation"]). Remplacement
        atomique du dict: le fil GL ne voit jamais un etat a moitie ecrit."""
        merged = dict(params.get("_automation") or {})
        merged.update(automation.capture())
        params["_automation"] = merged

    def sync_automation_state() -> None:
        sync_automation_now()
        root.after(AUTOMATION_SYNC_MS, sync_automation_state)

    def sync_widgets() -> None:
        """params -> widgets, apres le chargement d'un preset (les variables ecrivent params par leur
        trace, avec la meme valeur: aucun effet de bord)."""
        for key, var in synced.items():
            value = params.get(key)
            try:
                if isinstance(var, tk.StringVar):
                    var.set(str(value))
                elif isinstance(var, tk.IntVar):
                    var.set(int(float(value) >= 0.5))
                else:
                    var.set(float(value))
            except (tk.TclError, ValueError, TypeError):
                pass
        for name, variables in (("fx_on", fx_on_vars), ("fx_int", fx_int_vars),
                                ("fxl_on", fxl_on_vars), ("fxl_int", fxl_int_vars)):
            for i, var in enumerate(variables):
                var.set(params[name][i])
        link_var.set(int(float(params["fx_link"]) >= 0.5))
        holo_var.set(int(float(params["holo_on"]) >= 0.5))
        logo_var.set(params["logo_path"])
        video_var.set(params["logo_video"])
        key_var.set(params["logo_key"])
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", params["text_content"])

    def follow_automation() -> None:
        """params -> curseurs: les curseurs automatises suivent la valeur calculee par le moteur."""
        if float(params["auto_master"]) >= 0.5:
            data = params["_automation"]
            for key, var in auto_vars.items():
                if data.get(key, {}).get("enabled"):
                    try:
                        var.set(round(float(gl.get_param(params, key)), 3))
                    except (tk.TclError, ValueError):
                        pass

    def refresh() -> None:
        # Recopie les reglages que les touches de la fenetre GL ont pu changer.
        for i, v in enumerate(fx_on_vars):
            if int(params["fx_on"][i]) != v.get():
                v.set(int(params["fx_on"][i]))
        for i, v in enumerate(fxl_on_vars):
            if int(params["fxl_on"][i]) != v.get():
                v.set(int(params["fxl_on"][i]))
        for i, v in enumerate(fxl_int_vars):
            if abs(_safe(v, 0.0) - float(params["fxl_int"][i])) > 1e-6:
                v.set(float(params["fxl_int"][i]))
        if link_var.get() != int(float(params["fx_link"]) >= 0.5):
            link_var.set(int(float(params["fx_link"]) >= 0.5))
        if holo_var.get() != int(float(params["holo_on"]) >= 0.5):
            holo_var.set(int(float(params["holo_on"]) >= 0.5))
        if abs(master_var.get() - params["master"]) > 1e-6:
            master_var.set(params["master"])
        if abs(sens_var.get() - params["sensitivity"]) > 1e-6:
            sens_var.set(params["sensitivity"])
        if bg_var.get() != params["bg_mode"]:
            bg_var.set(params["bg_mode"])
        if auto_master_var.get() != int(float(params["auto_master"]) >= 0.5):
            auto_master_var.set(int(float(params["auto_master"]) >= 0.5))
        st = s.status
        parts = [f"{st.get('fps', 0):.0f} fps"] + [st[k] for k in ("logo", "msg") if st.get(k)]
        status_label.config(text="  |  ".join(parts))
        root.after(REFRESH_MS, refresh)

    def update_meter() -> None:
        follow_automation()
        state = s.audio.analyzer.latest()
        for name, item in bars.items():
            meter.coords(item, 52, meter.coords(item)[1], 52 + 200 * min(max(state[name], 0.0), 1.0),
                         meter.coords(item)[3])
        root.after(METER_MS, update_meter)

    def on_close() -> None:
        s.stop_event.set()                # le fil GL sort, positionne finished_event, leur refresh() ferme
        root.after(3000, root.destroy)    # filet de securite si le fil GL ne repond plus

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    update_meter()
    poll_bridge()
    sync_automation_state()
    if on_ready is not None:
        root.after(50, lambda: on_ready({
            "root": root, "restart_event": restart_event, "live_status": live_status, "close": on_close,
            "logo_var": logo_var, "apply_logo_path": apply_logo_path, "color_var": color_var, "x_var": x_var,
            "fx_on_vars": fx_on_vars, "fxl_on_vars": fxl_on_vars, "fxl_int_vars": fxl_int_vars,
            "link_var": link_var, "logo_fx_box": logo_fx_box, "holo_var": holo_var, "holo_box": holo_box, "overlay_var": overlay_var,
            "load_overlay_preset": load_overlay_preset, "save_name_var": save_name_var,
            "save_overlay_preset": save_overlay_preset, "update_overlay_preset": update_overlay_preset,
            "overlay_store": overlay_store, "preset_msg": preset_msg, "overlay_menu": overlay_menu,
            "fx_int_vars": fx_int_vars, "text_widget_sync": sync_widgets, "master_var": master_var, "bg_var": bg_var, "pattern_box": pattern_box,
            "bg_color1_var": bg_color1_var, "bg_color2_var": bg_color2_var,
            "automation": automation, "auto_master_var": auto_master_var, "auto_vars": auto_vars,
            "source_var": source_var, "text_widget": text_widget, "apply_text": apply_text,
            "image_box": image_box, "text_box": text_box, "font_var": font_var}))
    root.mainloop()


def _safe(var: tk.Variable, default: float) -> float:
    try:
        return float(var.get())
    except (ValueError, tk.TclError):
        return default
