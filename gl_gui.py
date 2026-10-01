"""Fenetre de reglages tkinter de audio2wave_gl (--gui).

La partie "Live" (le visuel de fond ffmpeg) N'EST PAS reecrite ici : c'est la fenetre de
reglages d'audio2wave_live.py (`build_gui`, importee du depot audio2wave comme dependance),
avec ses presets, ses automations de courbes, ses info-bulles et son theme. Ce module :

  1. l'appelle telle quelle sur la fenetre Tk, avec ses trois evenements habituels
     (`restart_event`, `stop_event`, `finished_event`) ;
  2. la nettoie de ce qui n'a pas de sens ici (taille de fenetre) ; les boutons Live / Snap / Ridge
     changent la source du fond (modes Snap et Ridge d'audio2wave via py_modes.py, fenetre reconstruite) ;
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
from tkinter import colorchooser, filedialog, ttk

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


class HostFrame(tk.Frame):
    """Cadre qui tient le role de `root` pour les `build_gui` d'audio2wave (Live, Snap, Ridge).

    Ils appellent `root.title / resizable / protocol` (sans objet ici : la vraie fenetre est geree par
    `build_window`), vident `root.winfo_children()` puis y posent leurs widgets en grille. Comme ce cadre
    est dans un canvas defilant, un panneau plus haut que l'ecran (Snap : ~950 px) reste utilisable."""

    def title(self, *_a, **_k) -> str:
        return ""

    def resizable(self, *_a, **_k) -> None:
        return None

    def protocol(self, *_a, **_k) -> None:
        return None


LIVE_MAX_HEIGHT = 640            # hauteur maxi du panneau d'audio2wave avant d'afficher un ascenseur
MODE_BANNERS = {"live": "LIVE  -  spectre audio (audio2wave)",
                "snap": "SNAP  -  photo de l'onde (audio2wave)",
                "ridge": "RIDGE  -  vagues empilees (audio2wave)"}


def tidy_mode_gui(host, mode: str) -> None:
    """Retire ou renomme, dans la fenetre d'un mode d'audio2wave, ce qui n'a pas de sens ici.

    Tous les modes : le titre ("Reglages Live"...) disparait (un bandeau le remplace) et les titres de
    section deviennent des bandeaux. Live seulement : la taille de fenetre devient la taille du rendu, figee
    (la texture video est fixee au lancement ; Snap et Ridge n'ont pas ce champ, leur taille est fixee
    par `py_modes.make_args`). Les boutons de bascule Live / Snap / Ridge sont GARDES : ils pilotent
    `on_switch_mode`, donc le changement de source."""

    import audio2wave as a2w

    def walk(widget) -> None:
        for child in widget.winfo_children():
            cls = child.winfo_class()
            if cls == "Label":
                text = child.cget("text")
                span = int(child.grid_info().get("columnspan", 1) or 1) if child.winfo_manager() == "grid" else 1
                if text in ("Reglages Live", "Reglages Snap", "Reglages Ridge"):
                    child.grid_remove()
                elif mode == "live" and text in LIVE_SECTIONS:
                    # Les petits titres de section d'audio2wave deviennent des bandeaux pleine largeur,
                    # pour que les parties de la fenetre se distinguent d'un coup d'oeil.
                    style_band(child, a2w, LIVE_SECTIONS[text])
                elif mode != "live" and ((text.isupper() and span >= 2) or text == "Presets"):
                    # Titres de section de Snap / Ridge (un titre couvre au moins deux colonnes ; 'BPM', 1 colonne,
                    # est un libelle de curseur).
                    style_band(child, a2w, f"PRESETS {mode.upper()}  (audio2wave)" if text.upper() == "PRESETS"
                               else text.upper())
                elif text == "Taille fenetre":
                    child.config(text="Taille du rendu")
            elif cls == "Entry" and int(child.cget("width")) == 6:
                child.config(state="readonly")
            elif mode != "live" and cls == "Checkbutton" and child.cget("text") == "Plein ecran":
                child.config(state="disabled")      # l'affichage est gere par notre fenetre GL
            walk(child)

    walk(host)


def activate_mode(s, live, new: str) -> str | None:
    """Fait de `new` ("live", "snap" ou "ridge") la source du fond. Renvoie un message si c'est
    impossible (le mode courant reste alors en place), sinon None.

    Live = ffmpeg (`s.manager`, suspendu pendant les autres modes) ; Snap / Ridge = `py_modes.PyModeSource`
    (leur propre fil de rendu, images captees par un faux ffplay). `s.reader` pointe vers le flux du mode
    actif, que le fil GL relit a chaque image. L'entree audio suit : celle choisie dans un mode devient
    celle des autres. Les options de chaque mode sont gardees (`s.mode_args`) : revenir a Snap retrouve ses
    reglages."""
    import py_modes

    old = s.mode
    if new == old:
        return None
    if s.manager is None:
        return "Les modes Snap et Ridge ne sont pas disponibles en --synthetic"
    old_src = s.pysrc
    device = (old_src.args.device if old_src is not None else s.live_args.device) or ""
    if new == "ridge" and not device:
        return "Ridge a besoin d'une entree audio : choisis-la d'abord dans le mode Live ou Snap"
    src = None
    if new != "live":
        try:
            mod = py_modes.load_mode(Path(live.__file__).resolve().parent, new)
            args = s.mode_args.get(new)
            if args is None:
                args = s.mode_args[new] = py_modes.make_args(mod, new, device, s.render_size)
            args.device = device or None
            src = py_modes.PyModeSource(new, mod, args, s.render_size, gl.FrameReader,
                                        capture_factory=getattr(s, "capture_factory", None))
            src.start()
        except Exception as exc:
            return f"{new}: demarrage impossible ({exc})"
    if old_src is not None:
        old_src.stop()
    elif old == "live":
        s.manager.suspend()
    s.mode, s.pysrc = new, src
    if new == "live":
        if device:
            s.live_args.device = device
        s.reader = s.manager.reader
        resumed = copy.copy(s.live_args)
        resumed.size = f"{s.render_size[0]}x{s.render_size[1]}"
        s.manager.resume(resumed)
    else:
        s.reader = src.reader
    return None


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
    """Cree la fenetre Tk, la construit pour le mode courant et bloque dans mainloop() jusqu'a la fin.

    `on_ready(controls)` (tests): appele dans le fil tkinter, une fois la fenetre construite."""
    root = tk.Tk()
    build_window(s, live, root, s.mode, on_ready)
    root.mainloop()


def build_window(s, live, root, mode: str, on_ready=None) -> None:
    """(Re)construit tout le contenu de `root` pour le mode `mode` : le panneau d'audio2wave de ce mode
    (a gauche, dans un cadre defilant) et les panneaux de casual-overlay (a droite). Appelee a chaque
    changement de source : l'etat (reglages overlay, automations) vit dans `s.params`, les widgets
    le relisent a leur creation."""
    import audio2wave as a2w

    Tooltip = live.Tooltip
    params = s.params
    manager = s.manager
    render_w, render_h = s.render_size
    alive = {"ok": True}

    def after(ms: int, fn) -> None:
        """root.after() qui s'arrete tout seul quand la fenetre est reconstruite (changement de mode)."""
        def guarded() -> None:
            if alive["ok"]:
                fn()
        root.after(ms, guarded)

    for child in list(root.winfo_children()):
        child.destroy()
    live.style_gui(root)
    root.title("casual-overlay GL - reglages")
    root.resizable(False, False)

    def on_switch(new_mode: str) -> None:
        root.after(30, lambda: request_mode(new_mode))     # hors du callback du bouton qu'on va detruire

    def request_mode(new_mode: str) -> None:
        sync_automation_now()
        message = activate_mode(s, live, new_mode)
        alive["ok"] = False
        if message:
            s.status["msg"] = message
        build_window(s, live, root, s.mode, on_ready)

    # ---------------------------------------------------- 1. la GUI du mode d'audio2wave, telle quelle
    live_wrap = tk.Frame(root)
    live_wrap.grid(row=1, column=0, sticky="nw", padx=(6, 0))
    canvas = tk.Canvas(live_wrap, highlightthickness=0, bd=0, width=10, height=10, bg=a2w.GUI_BG,
                       yscrollincrement=24)
    vbar = tk.Scrollbar(live_wrap, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side="left")
    host = HostFrame(canvas)
    canvas.create_window((0, 0), window=host, anchor="nw")

    restart_event = threading.Event()
    live_status = {"text": ""}
    if mode == "live":
        live.build_gui(s.live_args, render_w, render_h, live_status, restart_event, s.stop_event,
                       s.finished_event, root=host, on_switch_mode=on_switch)
    else:
        src = s.pysrc
        never = threading.Event()     # leur refresh() ferme la fenetre quand cet evenement est positionne: jamais ici
        if mode == "snap":
            src.module.build_gui(src.args, s.render_size, src.status, src.capture_state, src.stop_event, never,
                                 root=host, on_switch_mode=on_switch)
        else:
            src.module.build_gui(src.args, s.render_size, src.status, src.stop_event, never,
                                 root=host, on_switch_mode=on_switch)
    root.title("casual-overlay GL - reglages")
    tidy_mode_gui(host, mode)

    # Le panneau d'audio2wave est a l'aise (lignes de 4 a 7 px de marge): on resserre ses marges
    # verticales pour gagner de la hauteur, sans toucher a ses widgets ni a son code.
    for w in host.winfo_children():
        info = w.grid_info()
        if not info:
            continue
        pady = info["pady"]
        values = [pady] if isinstance(pady, (int, str)) else list(pady)
        tight = tuple(max(1, round(float(v) * 0.35)) for v in values)
        w.grid_configure(pady=tight[0] if len(tight) == 1 else tight)

    cap = max(420, min(LIVE_MAX_HEIGHT, root.winfo_screenheight() - 200))

    def fit(_e=None) -> None:
        """Le canvas prend la taille du panneau, plafonnee : au-dela, un ascenseur apparait."""
        w, h = host.winfo_reqwidth(), host.winfo_reqheight()
        canvas.configure(width=w, height=min(h, cap), scrollregion=(0, 0, w, h))
        if h > cap:
            if not vbar.winfo_ismapped():
                vbar.pack(side="right", fill="y")
        else:
            vbar.pack_forget()
            canvas.yview_moveto(0)

    def on_wheel(e) -> None:
        if str(e.widget).startswith(str(canvas)) and host.winfo_reqheight() > cap:
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    host.bind("<Configure>", fit)
    root.bind_all("<MouseWheel>", on_wheel)
    host.update_idletasks()
    fit()

    # Deux grandes parties, bien distinctes : bandeaux de couleur en haut et gros trait vertical entre les deux.
    tk.Label(root, text=MODE_BANNERS[mode], bg=LIVE_COLOR, fg=BANNER_FG,
             font=("Segoe UI", 12, "bold"), anchor="w", padx=12, pady=5).grid(
        row=0, column=0, sticky="ew", padx=(6, 0), pady=(8, 2))
    tk.Label(root, text="OVERLAY  -  fond, logo, effets (casual-overlay)", bg=OVERLAY_COLOR, fg=BANNER_FG,
             font=("Segoe UI", 12, "bold"), anchor="w", padx=12, pady=5).grid(
        row=0, column=2, sticky="ew", padx=(0, 10), pady=(8, 2))
    ROW_PADX, ROW_PADY, SECTION_GAP = 6, 2, 5

    # ------------------------------------------------ 2. pont restart_event -> ProducerManager (mode Live)
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
        after(BRIDGE_MS, poll_bridge)

    # ------------------------------------------------------ 3. nos panneaux, a droite
    separator = tk.Frame(root, bg=OVERLAY_COLOR, width=3)       # frontiere LIVE | OVERLAY
    separator.grid(row=1, column=1, sticky="ns", padx=ROW_PADX + 4)
    # Partie OVERLAY: les presets restent toujours visibles en haut, le reste est range en ONGLETS (Fond, Effets,
    # Logo, Aura, Affichage). Une seule colonne de reglages a la fois: la fenetre fait environ la moitie de la
    # hauteur qu'avec des colonnes empilees (qui depassait la hauteur d'un ecran des qu'un bloc s'ouvrait).
    right = tk.Frame(root)
    right.grid(row=1, column=2, sticky="nsew", padx=(0, 10))
    preset_bar = tk.Frame(right)
    preset_bar.pack(fill="x")
    style = ttk.Style(root)
    style.theme_use("clam")
    edge = {"bordercolor": a2w.GUI_BG, "lightcolor": a2w.GUI_BG, "darkcolor": a2w.GUI_BG}   # pas de filets clairs
    style.configure("TNotebook", background=a2w.GUI_BG, borderwidth=0, tabmargins=(0, 0, 0, 0), **edge)
    style.configure("TNotebook.Tab", background=a2w.GUI_PANEL_BG, foreground=a2w.GUI_MUTED_FG,
                    padding=(14, 5), borderwidth=0, font=("Segoe UI", 9, "bold"), **edge)
    style.map("TNotebook.Tab", background=[("selected", OVERLAY_COLOR)], foreground=[("selected", BANNER_FG)])
    notebook = ttk.Notebook(right)
    notebook.pack(fill="both", expand=True, pady=(6, 0))

    def new_tab(title: str) -> tk.Frame:
        frame = tk.Frame(notebook)
        notebook.add(frame, text=title)
        return frame

    tab_fond, tab_fx, tab_logo = new_tab("Fond"), new_tab("Effets"), new_tab("Logo")
    tab_aura, tab_aff = new_tab("Aura du logo"), new_tab("Affichage")
    zone_p, zone_f, zone_fx = Zone(preset_bar), Zone(tab_fond), Zone(tab_fx)
    zone_logo, zone_aura, zone_aff = Zone(tab_logo), Zone(tab_aura), Zone(tab_aff)

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
    add_section_title(zone_p, "Presets overlay")
    overlay_store = live.PresetStore(gl.OVERLAY_PRESETS, gl.OVERLAY_PRESETS_PATH)
    overlay_var = tk.StringVar(value="default")
    preset_msg = tk.StringVar(value="Un preset = tout le look de l'overlay.")

    load_row = tk.Frame(preset_bar)
    load_row.grid(row=zone_p.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
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

    save_row = tk.Frame(preset_bar)
    save_row.grid(row=zone_p.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
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
    tk.Label(preset_bar, textvariable=preset_msg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=380).grid(
        row=zone_p.next_row(), column=0, columnspan=3, sticky="we", padx=ROW_PADX, pady=(0, 2))
    refresh_overlay_menu()

    # ---- colonne A : FOND (motif genere)
    add_section_title(zone_f, "Fond")
    bg_var = tk.StringVar(value=params["bg_mode"])
    r = zone_f.next_row()
    add_label(zone_f, "Fond", r, "Audio2wave = le visuel du mode choisi dans le panneau de gauche (Live, Snap ou "
                                 "Ridge). Motif genere = degrades et damier calcules par la carte graphique (touche B).")
    bg_frame = tk.Frame(tab_fond)
    bg_frame.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    for text, value in (("Audio2wave", "live"), ("Motif genere", "pattern")):
        tk.Radiobutton(bg_frame, text=text, variable=bg_var, value=value).pack(side="left")
    bind_param("bg_mode", bg_var, str)

    pattern_box = tk.Frame(tab_fond)
    pattern_box.grid(row=zone_f.next_row(), column=0, columnspan=3, sticky="new")
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
    add_separator(zone_fx, "Effets")
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

    # ---- colonne B : LOGO
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
    color_var = color_row(zone_aura, "Couleur du contour", "logo_glow_color")

    # ---- colonne B : ANALYSE AUDIO + AFFICHAGE
    add_separator(zone_aff, "Analyse audio (effets)")
    audio_choices = gl.AudioController.input_choices() if not s.audio.synthetic else []
    if not audio_choices:
        tk.Label(tab_aff, text=s.audio.name, fg=a2w.GUI_MUTED_FG, wraplength=300, justify="left").grid(
            row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    else:
        labels = {label: idx for idx, label in audio_choices}
        current = next((label for idx, label in audio_choices if s.audio.name in label), s.audio.name)
        audio_var = tk.StringVar(value=current)
        r = zone_aff.next_row()
        add_label(zone_aff, "Entree", r, "Entree sounddevice qui alimente les effets (kick, basses...). "
                                       "Independante de l'entree ffmpeg du fond.")
        menu = tk.OptionMenu(tab_aff, audio_var, *labels.keys())
        a2w.style_option_menu(menu)
        menu.grid(row=r, column=1, sticky="w", padx=ROW_PADX, pady=ROW_PADY)

        def on_audio_change(*_a) -> None:
            try:
                name = s.audio.switch(labels[audio_var.get()])
                s.status["msg"] = f"Analyse sur: {name}"
            except Exception as exc:
                s.status["msg"] = f"Entree refusee: {exc}"

        audio_var.trace_add("write", on_audio_change)

    add_separator(zone_aff, "Affichage")
    for row_buttons in ((("Plein ecran", "fullscreen"), ("Barres debug", "hud")),
                        (("Recharger shaders", "reload"), ("Sauver reglages", "save"))):
        frame = tk.Frame(tab_aff)
        frame.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        for text, cmd in row_buttons:
            tk.Button(frame, text=text, command=lambda c=cmd: s.commands.put(c)).pack(side="left", padx=(0, 6))

    auto_master_var = tk.IntVar(value=int(float(params["auto_master"]) >= 0.5))
    bind_param("auto_master", auto_master_var, float)
    frame = tk.Frame(tab_aff)
    frame.grid(row=zone_aff.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
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

    meter = tk.Canvas(tab_aff, width=260, height=58, bg=a2w.GUI_PANEL_BG, highlightthickness=0)
    meter.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=(SECTION_GAP, 2))
    bars = {}
    for i, (name, color) in enumerate((("bass", "#ff5a4d"), ("mid", "#66ff73"), ("high", "#66b3ff"),
                                       ("beat", "#ffe633"))):
        y0 = 4 + i * 13
        meter.create_text(4, y0 + 5, text=name, anchor="w", fill=a2w.GUI_MUTED_FG, font=a2w.GUI_FONT_SMALL)
        bars[name] = meter.create_rectangle(52, y0, 52, y0 + 10, fill=color, width=0)

    status_label = tk.Label(tab_aff, text="", fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=320)
    status_label.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="we", padx=ROW_PADX, pady=(4, 6))

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
        after(AUTOMATION_SYNC_MS, sync_automation_state)

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
        if s.finished_event.is_set():              # le fil GL est sorti: on ferme la fenetre
            root.destroy()
            return
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
        after(REFRESH_MS, refresh)

    def update_meter() -> None:
        follow_automation()
        state = s.audio.analyzer.latest()
        for name, item in bars.items():
            meter.coords(item, 52, meter.coords(item)[1], 52 + 200 * min(max(state[name], 0.0), 1.0),
                         meter.coords(item)[3])
        after(METER_MS, update_meter)

    def on_close() -> None:
        s.stop_event.set()                # le fil GL sort, positionne finished_event, leur refresh() ferme
        root.after(3000, root.destroy)    # filet de securite si le fil GL ne repond plus

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    update_meter()
    if mode == "live":
        poll_bridge()
    sync_automation_state()
    if on_ready is not None:
        root.after(50, lambda: on_ready({
            "root": root, "live_host": host, "switch": request_mode, "restart_event": restart_event, "live_status": live_status, "close": on_close,
            "logo_var": logo_var, "apply_logo_path": apply_logo_path, "color_var": color_var, "x_var": x_var,
            "fx_on_vars": fx_on_vars, "fxl_on_vars": fxl_on_vars, "fxl_int_vars": fxl_int_vars,
            "link_var": link_var, "logo_fx_box": logo_fx_box, "holo_var": holo_var, "holo_box": holo_box, "notebook": notebook, "tabs": (tab_fond, tab_fx, tab_logo, tab_aura, tab_aff), "overlay_var": overlay_var,
            "load_overlay_preset": load_overlay_preset, "save_name_var": save_name_var,
            "save_overlay_preset": save_overlay_preset, "update_overlay_preset": update_overlay_preset,
            "overlay_store": overlay_store, "preset_msg": preset_msg, "overlay_menu": overlay_menu,
            "fx_int_vars": fx_int_vars, "text_widget_sync": sync_widgets, "master_var": master_var, "bg_var": bg_var, "pattern_box": pattern_box,
            "bg_color1_var": bg_color1_var, "bg_color2_var": bg_color2_var,
            "automation": automation, "auto_master_var": auto_master_var, "auto_vars": auto_vars,
            "source_var": source_var, "text_widget": text_widget, "apply_text": apply_text,
            "image_box": image_box, "text_box": text_box, "font_var": font_var}))


def _safe(var: tk.Variable, default: float) -> float:
    try:
        return float(var.get())
    except (ValueError, tk.TclError):
        return default
