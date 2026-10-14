"""Fenetre de reglages tkinter de audio2wave_gl (--gui).

La partie "Live" (le visuel de fond ffmpeg) N'EST PAS reecrite ici : c'est la fenetre de
reglages d'audio2wave_live.py (`build_gui`, importee du depot audio2wave comme dependance),
avec ses presets, ses automations de courbes, ses info-bulles et son theme. Ce module :

  1. l'appelle sur un conteneur dedie de la fenetre Tk avec l'API hote d'audio2wave (`parent`, `embedded`,
     `compact`, `presets_first`) et garde le `GuiController` qu'elle renvoie (`capture_state`, `apply_state`,
     `commit_pending`, `widgets`) : on ne fouille plus ses widgets pour lire ou charger un etat ;
  2. habille ce qu'elle a de propre (titres de section en bandeaux, taille du rendu figee) ; les boutons
     Live / Snap / Ridge changent la source du fond (modes Snap et Ridge d'audio2wave via py_modes.py,
     `start_headless`, fenetre reconstruite) ;
  3. fait le pont entre son `restart_event` et `ProducerManager` : a chaque reglage, `build_gui`
     a deja mute les options live et positionne l'evenement (apres son anti-rebond : `on_change` se
     declencherait a chaque ecriture, sans lui), on en prend une copie et on remplace le producteur ffmpeg
     a chaud, sans toucher a la fenetre GL (aucun redemarrage si la commande ffmpeg est inchangee) ;
  4. ajoute a droite ses propres panneaux, dans la meme fenetre (partie OVERLAY, bandeau violet ;
     la partie LIVE a un bandeau bleu) :
        PRESETS OVERLAY + FOND (motif genere) + EFFETS + HALO HOLOGRAPHIQUE
        LOGO               + ANALYSE AUDIO + AFFICHAGE + mesures.

Rien n'est modifie dans le depot audio2wave : tout passe par son API hote (`common.API_VERSION` >= 2,
voir `require_audio2wave_api`).

tkinter garde le fil principal ; la fenetre GL (`Projection`, ouverte par le bouton) tourne dans un fil
(comme run() dans les scripts d'audio2wave): toute action qui doit s'executer dans le fil GL passe par
`session.commands`.
"""

from __future__ import annotations

import copy
import json
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, simpledialog, ttk

import audio2wave_gl as gl
import diagnostics
import randomizer
import skin
import theme
import gui_colors
import gui_gates
import scenes

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
LIVE_COLOR = theme.LIVE
OVERLAY_COLOR = "#b392ff"
SCENE_COLOR = "#ffcf6b"          # barre des scenes (ambre)
BANNER_FG = "#0b1018"

# Titres de section d'audio2wave (texte d'origine -> libelle affiche)
# Options de mise en page des trois `build_gui` d'audio2wave (API hote) : ni titre / redimensionnement / `protocol` (`embedded`),
# marges serrees (`compact`), bloc des presets en tete (`presets_first`).
PANEL_OPTIONS = {"embedded": True, "compact": True, "presets_first": True}

LIVE_SECTIONS = {"SOURCE": "SOURCE", "SPECTRE": "SPECTRE", "COULEURS": "COULEURS", "SORTIE": "SORTIE",
                 "PRESETS": "PRESETS LIVE  (audio2wave)"}


def style_band(label, a2w, text: str | None = None, color: str = LIVE_COLOR) -> None:
    """Petit titre de section -> bandeau pleine largeur : fond du theme (plus clair que la fenetre), texte
    en gras a la couleur de la partie a laquelle il appartient."""
    label.config(text=text or label.cget("text"))
    skin.title(label, color)
    label.grid_configure(sticky="ew", pady=(8, 3))


MIN_WIN_W, MIN_WIN_H = 940, 420   # en dessous, le panneau d'audio2wave seul ne tient plus
LIVE_MAX_HEIGHT = 600            # hauteur maxi du panneau d'audio2wave avant d'afficher un ascenseur
MODE_BANNERS = {"live": "LIVE  -  spectre audio (audio2wave)",
                "snap": "SNAP  -  photo de l'onde (audio2wave)",
                "ridge": "RIDGE  -  vagues empilees (audio2wave)"}


AUDIO2WAVE_API_MIN = 2           # `common.API_VERSION` exige (API hote : GuiController, parent / embedded..., start_headless, snapshot)
AUDIO2WAVE_REQUIRED = "branche add-snap-waveform-photo, commit 86e0458 ou plus recent"


def audio2wave_api_missing(live=None, common=None) -> list[str]:
    """Liste vide si le depot audio2wave expose l'API hote attendue, sinon ce qui manque (une phrase). On compare `common.API_VERSION`
    a `AUDIO2WAVE_API_MIN` au lieu de tester chaque nom (`hasattr`) : c'est audio2wave qui garantit les noms (`check_contract.py`).
    `live` n'est plus utilise (gardee pour les appelants) ; `common` s'injecte pour les tests."""
    if common is None:
        try:
            import common
        except ImportError:
            return ["le module common d'audio2wave (introuvable)"]
    version = getattr(common, "API_VERSION", None)
    if not isinstance(version, int) or version < AUDIO2WAVE_API_MIN:
        found = f"version {version}" if isinstance(version, int) else "pas de common.API_VERSION"
        return [f"common.API_VERSION >= {AUDIO2WAVE_API_MIN} ({found})"]
    return []


def require_audio2wave_api(live=None, common=None) -> None:
    """Arrete le programme avec un message clair si le depot audio2wave est trop ancien (au lieu d'un AttributeError obscur)."""
    missing = audio2wave_api_missing(live, common)
    if missing:
        where = Path(getattr(live, "__file__", ".")).resolve().parent if live is not None else "depot voisin"
        raise SystemExit("Le depot audio2wave est trop ancien pour cette version de casual-overlay : il manque "
                         + " et ".join(missing) + f".\nMets a jour le depot audio2wave ({where}) : git pull, {AUDIO2WAVE_REQUIRED}.")


def apply_a2w_palette() -> None:
    """Accent d'audio2wave (`GUI_ACCENT`, turquoise) -> bleu du theme, par le point d'extension `common.set_gui_accent` (met a jour
    tous les modules audio2wave deja importes ; Snap et Ridge, importes plus tard, lisent la valeur de `common`). A appeler avant
    de construire leurs widgets (leur `style_gui`, leurs labels et curseurs la lisent a la creation)."""
    import common
    common.set_gui_accent(theme.LIVE, theme.FG)


def install_curve_skin(live) -> None:
    """Fenetre « courbe » (editeur de variation automatique d'audio2wave) : habillee comme le reste a son ouverture (accent du
    gestionnaire : violet cote overlay, bleu cote LIVE) par le crochet de classe `AutomationManager.default_on_editor_open`,
    `(manager, fenetre, attr, canvas)`, appele une fois la fenetre construite. Idempotent (le crochet est simplement reaffecte)."""
    def on_editor_open(manager, win, attr: str, canvas) -> None:
        skin.zone(win, manager.accent)
        if canvas is not None:
            canvas.configure(bg=theme.TROUGH, highlightbackground=theme.LINE)
            canvas.pack_configure(padx=18, pady=(18, 14))
        stack = [win]
        while stack:                                  # leurs Label n'ont pas le fond du panneau (palette de la fenetre)
            node = stack.pop()
            for child in node.winfo_children():
                if child.winfo_class() == "Label":
                    child.configure(bg=str(node.cget("bg")))
                stack.append(child)
        skin.apply(win)

    # Crochet de classe : atteint aussi les gestionnaires crees par les build_gui d'audio2wave.
    live.AutomationManager.default_on_editor_open = on_editor_open


def tidy_mode_gui(host, mode: str, widgets: dict) -> None:
    """Retire ou renomme, dans le panneau d'un mode d'audio2wave, ce qui n'a pas de sens ici.

    Tous les modes : le titre ("Reglages Live"...) disparait (un bandeau le remplace) et les titres de
    section (`widgets["section_titles"]`, plus le bandeau des presets) deviennent des bandeaux. Live seulement : la taille de fenetre devient la taille du rendu, figee
    (la texture video est fixee au lancement ; Snap et Ridge n'ont pas ce champ, leur taille est fixee
    par `py_modes.make_args`). Les boutons de bascule Live / Snap / Ridge sont GARDES : ils pilotent
    `on_switch_mode`, donc le changement de source."""

    import audio2wave as a2w

    titles = list(widgets.get("section_titles", []))
    titles += [w for w in widgets.get("presets", []) if w.winfo_class() == "Label" and str(w.cget("text")).upper() == "PRESETS"
               and w not in titles]
    for label in titles:
        text = str(label.cget("text"))
        if mode == "live":
            style_band(label, a2w, LIVE_SECTIONS.get(text, text))
        else:
            style_band(label, a2w, f"PRESETS {mode.upper()}  (audio2wave)" if text.upper() == "PRESETS" else text.upper())
    for child in gui_gates.leaves(host):          # le titre de la fenetre ("Reglages Live"...) : pas de section_titles pour lui
        if child.winfo_class() == "Label" and child.cget("text") in ("Reglages Live", "Reglages Snap", "Reglages Ridge"):
            child.grid_remove()
        elif child.winfo_class() == "Label" and child.cget("text") == "Taille fenetre":
            child.config(text="Taille du rendu")
    # Ligne "Taille fenetre" (champs largeur / hauteur + Plein ecran) : taille du rendu figee, plein ecran gere par notre
    # fenetre GL. On cible la LIGNE (et non "tous les champs de 6 caracteres" : le crossover de Snap en a aussi).
    size_label = gui_gates.find(host, "Label", "Taille du rendu")
    for w in (gui_gates.row_widgets(host, size_label) if size_label is not None else []):
        for x in gui_gates.leaves(w):
            if x.winfo_class() == "Entry":
                x.config(state="readonly")
            elif mode != "live" and x.winfo_class() == "Checkbutton" and x.cget("text") == "Plein ecran":
                x.config(state="disabled")


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
            src = py_modes.PyModeSource(new, mod, args, s.render_size, gl.FrameReader)
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


def mode_store(live, mode: str):
    """Magasin de presets du mode d'audio2wave `mode` ("live", "snap" ou "ridge") : `preset_registry.ModeStore`
    (user/all/save/delete/override_default), le meme acces unifie que le Mode VJ de Snap."""
    import preset_registry
    import py_modes
    module = live if mode == "live" else py_modes.load_mode(Path(live.__file__).resolve().parent, mode)
    return preset_registry.store_for(mode, module)


def migrate_scene_presets(live) -> None:
    """Les premieres scenes rangeaient l'etat d'audio2wave dans leurs presets (`scene-<nom>`) : on le ramene dans
    scenes.json et on les retire des listes d'audio2wave (a faire avant de construire leur panneau)."""
    book = scenes.SceneBook(lambda: scenes.SCENES_PATH)
    for scene in book.list():
        name = scene.get("live_preset")
        if not name:
            continue
        overrides = None
        if scene.get("mode"):
            try:
                store = mode_store(live, scene["mode"])
                overrides = store.user().get(name)
                if overrides is not None:
                    store.delete(name)
            except Exception:
                overrides = None
        scene.pop("live_preset", None)
        if overrides is not None and not scene.get("live_overrides"):
            scene["live_overrides"] = scenes.strip_live_overrides(overrides)
        book.put(scene)


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp")
VIDEO_EXT = (".gif", ".webm", ".mp4", ".mov", ".mkv", ".avi", ".webp", ".apng")
THUMB_W, THUMB_H = 56, 34


def media_from_file(path: str) -> dict | None:
    """Media d'entree pour un fichier depose : image (logo) ou video / animation ; None si le format n'est pas reconnu."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return {"logo_source": "image", "logo_path": str(Path(path))}
    if ext in VIDEO_EXT:
        return {"logo_source": "video", "logo_video": str(Path(path)), "logo_key": ""}
    return None


def a2w_from_file(path: str) -> str | None:
    """Video d'audio2wave (Snap : video interieure / exterieure) pour un fichier depose ; None si ce n'est pas une video."""
    return str(Path(path)) if Path(path).suffix.lower() in VIDEO_EXT + (".m4v",) else None


def _has_content(img) -> bool:
    """Une image qui montre quelque chose : pas entierement transparente ni quasi noire."""
    rgba = img.resize((16, 16))
    alpha = rgba.getchannel("A").getextrema()[1]
    lum = rgba.convert("L").getextrema()[1]
    return alpha > 16 and lum > 24


def make_thumb(media: dict):
    """Vignette PIL (THUMB_W x THUMB_H, fond sombre) d'un media image ou video, ou None. Sans Tk : appelable dans un fil.
    Une video : premiere image (ffmpeg, 6 s maximum) ; texte : pas de vignette."""
    import io
    import shutil
    import subprocess

    from PIL import Image
    source = media.get("logo_source", "image")
    path = media.get("logo_video") if source == "video" else media.get("logo_path") if source == "image" else None
    if not path or not Path(path).exists():
        return None
    img = None
    try:
        if source == "image" or Path(path).suffix.lower() == ".gif":
            img = Image.open(path)
            img.seek(0)
            img = img.convert("RGBA")
        else:
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                codecs = ([["-c:v", "libvpx-vp9"]] if Path(path).suffix.lower() == ".webm" else []) + [[]]
                fallback = None
                for at in ("0", "0.6", "1.5"):             # la 1re image d'une animation est souvent vide (fond transparent, noir)
                    for codec in codecs:                   # webm VP9 : le decodeur natif ignore l'alpha
                        out = subprocess.run([ffmpeg, "-v", "error", *codec, "-ss", at, "-i", path, "-frames:v", "1", "-f", "image2pipe",
                                              "-vcodec", "png", "-"], capture_output=True, timeout=6,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if out.returncode == 0 and out.stdout:
                            frame = Image.open(io.BytesIO(out.stdout)).convert("RGBA")
                            fallback = fallback or frame
                            if _has_content(frame):
                                img = frame
                            break
                    if img is not None:
                        break
                img = img or fallback
        if img is None:
            return None
        img.thumbnail((THUMB_W, THUMB_H))
        back = Image.new("RGBA", (THUMB_W, THUMB_H), (28, 28, 34, 255))
        back.alpha_composite(img, ((THUMB_W - img.width) // 2, (THUMB_H - img.height) // 2))
        return back.convert("RGB")
    except Exception:
        return None


def choice_dialog(parent, title: str, text: str, buttons: list[str]) -> str | None:
    """Petite fenetre modale : un texte et des boutons ; renvoie le libelle du bouton choisi (None si on ferme)."""
    import audio2wave as a2w                 # (palette d'audio2wave : pas un import de module, le depot voisin est deja sur sys.path)
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=a2w.GUI_BG)
    top.transient(parent.winfo_toplevel())
    top.resizable(False, False)
    answer: list[str | None] = [None]
    tk.Label(top, text=text, bg=a2w.GUI_BG, fg=a2w.GUI_FG, justify="left", wraplength=420, padx=18, pady=14).pack()
    row = tk.Frame(top, bg=a2w.GUI_BG)
    row.pack(pady=(0, 14))

    def pick(label: str) -> None:
        answer[0] = label
        top.destroy()
    for label in buttons:
        tk.Button(row, text=label, command=lambda lb=label: pick(lb), padx=10).pack(side="left", padx=5)
    top.protocol("WM_DELETE_WINDOW", top.destroy)
    top.bind("<Escape>", lambda _e: top.destroy())
    skin.apply(top)
    try:
        top.grab_set()
    except tk.TclError:
        pass
    parent.wait_window(top)
    return answer[0]


def keep_dialog(parent, defaults: dict, fond_ok: bool, validate, a2w_mode: str | None = None) -> dict | None:
    """Modale « Garder ce tirage » : les noms du preset de fond, du preset overlay, du preset audio2wave (seulement si le fond du
    tirage est un visuel audio2wave : `a2w_mode` = son mode) et de la scene, un champ vide = ne pas l'enregistrer.
    `validate(noms)` rend un message d'erreur (la fenetre reste ouverte) ou None. Rend `{"fond", "overlay", "a2w", "scene"}` ou None."""
    import audio2wave as a2w
    top = tk.Toplevel(parent)
    top.title("Garder ce tirage")
    top.configure(bg=a2w.GUI_BG)
    top.transient(parent.winfo_toplevel())
    top.resizable(False, False)
    answer: list = [None]
    tk.Label(top, text="Enregistre ce tirage. Laisse un champ vide pour ne pas l'enregistrer.", bg=a2w.GUI_BG, fg=a2w.GUI_FG,
             justify="left", padx=18, pady=12).pack(anchor="w")
    form = tk.Frame(top, bg=a2w.GUI_BG)
    form.pack(padx=18)
    vars_: dict[str, tk.StringVar] = {}
    entries: dict[str, tk.Entry] = {}
    fields = [("fond", "Preset fond"), ("overlay", "Preset overlay")]
    if a2w_mode:
        fields.append(("a2w", f"Preset {a2w_mode.capitalize()}"))
    fields.append(("scene", "Scene"))
    for row, (key, label) in enumerate(fields):
        tk.Label(form, text=label, bg=a2w.GUI_BG, fg=a2w.GUI_FG, anchor="w", width=14).grid(row=row, column=0, sticky="w", pady=4)
        var = tk.StringVar(value=defaults.get(key, "") if (key != "fond" or fond_ok) else "")
        entry = tk.Entry(form, textvariable=var, width=28, state="normal" if (key != "fond" or fond_ok) else "disabled")
        entry.grid(row=row, column=1, pady=4, padx=(6, 0))
        vars_[key], entries[key] = var, entry
    if not fond_ok:
        why = ("Fond audio2wave : son reglage s'enregistre dans le champ Preset " + a2w_mode.capitalize() + " (presets d'audio2wave)."
               if a2w_mode else "Ce tirage n'a pas de fond genere ni de video choisie : pas de preset de fond.")
        tk.Label(top, text=why, bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG, padx=18, wraplength=380, justify="left").pack(anchor="w")
    error = tk.Label(top, text="", bg=a2w.GUI_BG, fg="#ff8a8a", padx=18, anchor="w", justify="left", wraplength=360)
    error.pack(anchor="w")
    row = tk.Frame(top, bg=a2w.GUI_BG)
    row.pack(pady=(8, 14))

    def ok(_e=None) -> None:
        names = {k: vars_[k].get().strip() if k in vars_ else "" for k in ("fond", "overlay", "a2w", "scene")}
        message = validate(names)
        if message:
            error.config(text=message)
            return
        answer[0] = names
        top.destroy()
    tk.Button(row, text="Enregistrer", command=ok).pack(side="left", padx=5)
    tk.Button(row, text="Annuler", command=top.destroy).pack(side="left", padx=5)
    top.protocol("WM_DELETE_WINDOW", top.destroy)
    top.bind("<Escape>", lambda _e: top.destroy())
    top.bind("<Return>", ok)
    skin.apply(top)
    first = entries["fond"] if fond_ok else entries["overlay"]
    first.focus_set()
    try:
        top.grab_set()
    except tk.TclError:
        pass
    parent.wait_window(top)
    return answer[0]


SHORTCUTS_HELP = (
    "Raccourcis (fenetre de rendu active) :\n"
    "Echap : quitter\n"
    "F : fenetre / plein ecran\n"
    "F1 a F9 : scenes (avec --gui)\n"
    "Espace : VJ lecture / pause\n"
    "Fleche droite : scene suivante (VJ)\n"
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
    "M : fonte acide du logo on/off\n"
    "V : cellules du logo on/off\n"
    "Haut / Bas : sensibilite du kick\n"
    "R : recharger shaders et reglages\n"
    "P : sauver les reglages"
)


def run_gui(s, live, on_ready=None) -> None:
    """Cree la fenetre Tk, la construit pour le mode courant et bloque dans mainloop() jusqu'a la fin.

    `on_ready(controls)` (tests): appele dans le fil tkinter, une fois la fenetre construite."""
    require_audio2wave_api(live)
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

    # Le VJ enchaine des scenes de modes differents (panneaux de largeurs differentes) : la fenetre garde la taille qu'elle a.
    keep_size = None
    if (getattr(s, "vj_state", None) or {}).get("running") and root.winfo_ismapped():
        keep_size = (root.winfo_width(), root.winfo_height())
    for child in list(root.winfo_children()):
        child.destroy()
    apply_a2w_palette()
    install_curve_skin(live)
    live.style_gui(root)
    root.title("casual-overlay GL - reglages")
    root.resizable(True, True)
    root.rowconfigure(1, weight=1)          # ligne 0 = barre des scenes, ligne 1 = la page
    root.columnconfigure(0, weight=1)
    root.minsize(MIN_WIN_W, MIN_WIN_H)

    # Barre des scenes (remplie plus bas par `refresh_scene_bar`) : posee tout de suite, avant la page, pour que la
    # geometrie de la fenetre se fixe dans le meme ordre qu'avant (sinon elle s'ouvrait a la largeur minimale, empilee).
    scene_top = tk.Frame(root, bg=a2w.GUI_PANEL_BG)           # la barre, et dessous le panneau etendu (si ouvert)
    scene_top.grid(row=0, column=0, columnspan=2, sticky="ew")
    skin.zone(scene_top, SCENE_COLOR)
    scene_bar = tk.Frame(scene_top, bg=a2w.GUI_PANEL_BG)
    scene_bar.pack(fill="x")
    scene_panel = tk.Frame(scene_top, bg=a2w.GUI_BG)

    # Page: tout le contenu vit dans un canvas defilant (ascenseur vertical + molette) : jamais de contenu coupe quand la
    # fenetre est petite, et au-dela de la taille voulue le contenu s'etire. La mise en page (cote a cote / empilee)
    # s'adapte a la largeur : voir `set_layout` plus bas.
    page_canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg=a2w.GUI_BG, yscrollincrement=24)
    page_bar = skin.scrollbar(root, command=page_canvas.yview)
    page_canvas.configure(yscrollcommand=page_bar.set)
    page_canvas.grid(row=1, column=0, sticky="nsew")
    page = tk.Frame(page_canvas)
    page_item = page_canvas.create_window((0, 0), window=page, anchor="nw")
    layout = {"name": "wide", "wide_need": 0, "settled": False}

    def on_switch(new_mode: str) -> None:
        root.after(30, lambda: request_mode(new_mode))     # hors du callback du bouton qu'on va detruire

    def request_mode(new_mode: str) -> None:
        sync_automation_now()
        message = activate_mode(s, live, new_mode)
        alive["ok"] = False
        if message:
            s.status["msg"] = message
        build_window(s, live, root, s.mode, on_ready)

    migrate_scene_presets(live)          # presets `scene-<nom>` des premieres versions -> scenes.json
    # ---------------------------------------------------- 1. la GUI du mode d'audio2wave, telle quelle
    live_wrap = tk.Frame(page)
    canvas = tk.Canvas(live_wrap, highlightthickness=0, bd=0, width=10, height=10, bg=a2w.GUI_BG,
                       yscrollincrement=24)
    vbar = skin.scrollbar(live_wrap, command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    host = tk.Frame(canvas)             # conteneur DEDIE du panneau d'audio2wave (`parent=` de leur build_gui)
    host_item = canvas.create_window((0, 0), window=host, anchor="nw")
    # Le fond est EXCLUSIF : soit le visuel d'audio2wave (son panneau, `host`), soit le fond genere (`pattern_holder`). Les deux
    # sont des fenetres du meme canvas, une seule est affichee (voir `show_bg`) ; le choix est en haut, hors du canvas.
    pattern_holder = tk.Frame(canvas)
    pattern_item = canvas.create_window((0, 0), window=pattern_holder, anchor="nw", state="hidden")
    bg_state = {"mode": "live"}

    def active_panel() -> tk.Frame:
        return host if bg_state["mode"] == "live" else pattern_holder      # fond genere ET fond video partagent le meme bloc

    restart_event = threading.Event()
    live_status = {"text": ""}
    panel = {"ctrl": None}              # `common.GuiController` du panneau d'audio2wave : capture_state / apply_state / commit_pending / widgets
    if mode == "live":
        panel["ctrl"] = live.build_gui(s.live_args, render_w, render_h, live_status, restart_event, s.stop_event,
                                       s.finished_event, on_switch_mode=on_switch, **PANEL_OPTIONS, parent=host)
    else:
        src = s.pysrc
        never = threading.Event()     # leur refresh() ferme la fenetre quand cet evenement est positionne: jamais ici
        if mode == "snap":
            panel["ctrl"] = src.module.build_gui(src.args, s.render_size, src.status, src.capture_state, src.stop_event, never,
                                                 on_switch_mode=on_switch, **PANEL_OPTIONS, parent=host)
        else:
            panel["ctrl"] = src.module.build_gui(src.args, s.render_size, src.status, src.stop_event, never,
                                                 on_switch_mode=on_switch, **PANEL_OPTIONS, parent=host)
    root.title("casual-overlay GL - reglages")
    widgets = panel["ctrl"].widgets
    tidy_mode_gui(host, mode, widgets)
    gui_colors.add_color_helpers(host, a2w.GUI_ACCENT, a2w.GUI_ACCENT_FG, widgets, mode)      # pastilles + selecteur sur les champs couleur
    gui_gates.install_mode_gates(host, mode, a2w.GUI_MUTED_FG, widgets)     # grise ce qui n'a pas de sens dans ce mode
    cap = max(420, min(LIVE_MAX_HEIGHT, root.winfo_screenheight() - 200))

    def cap_now() -> int:
        """Plafond de hauteur du panneau d'audio2wave : a cote de l'overlay il est plafonne (ascenseur propre) ; empile, il
        prend toute sa hauteur et c'est la page qui defile."""
        return cap if layout["name"] == "wide" else 10 ** 5

    def visible_height() -> int:
        """Hauteur reellement offerte au panneau (la fenetre est redimensionnable) ; avant l'affichage, la taille voulue."""
        h = canvas.winfo_height()
        return h if h > 50 else min(host.winfo_reqheight(), cap_now())

    def update_scrollbar(_e=None) -> None:
        if active_panel().winfo_reqheight() > visible_height() + 1:
            if not vbar.winfo_ismapped():
                vbar.pack(side="right", fill="y")
        else:
            if vbar.winfo_ismapped():
                vbar.pack_forget()
            canvas.yview_moveto(0)

    def fit(_e=None) -> None:
        """Taille voulue du canvas = celle du panneau (plafonnee a l'ouverture) ; ensuite c'est la fenetre qui decide,
        et un ascenseur apparait des que le panneau est plus haut que la place disponible."""
        w = max(host.winfo_reqwidth(), pattern_holder.winfo_reqwidth())      # largeur et hauteur stables d'un fond a l'autre
        h = active_panel().winfo_reqheight()
        canvas.configure(width=w, height=min(host.winfo_reqheight(), cap_now()), scrollregion=(0, 0, w, h))
        update_scrollbar()

    def on_wheel(e) -> None:
        step = -1 if e.delta > 0 else 1
        pcv = panel_state["cv"]                           # liste des scenes du panneau etendu : elle defile sous la souris
        if pcv is not None and (str(e.widget) == str(pcv) or str(e.widget).startswith(str(pcv) + ".")):
            pcv.yview_scroll(step, "units")
        elif str(e.widget).startswith(str(canvas)) and active_panel().winfo_reqheight() > visible_height() + 1:
            canvas.yview_scroll(step, "units")            # le panneau d'audio2wave defile d'abord...
        elif page_bar.winfo_ismapped():
            page_canvas.yview_scroll(step, "units")       # ...sinon c'est la page

    canvas.bind("<Configure>", update_scrollbar)
    host.bind("<Configure>", fit)
    pattern_holder.bind("<Configure>", fit)
    root.bind_all("<MouseWheel>", on_wheel)
    host.update_idletasks()
    fit()

    # Deux grandes parties, bien distinctes : bandeaux de couleur en haut et gros trait vertical entre les deux.
    banner_live = tk.Label(page, text=MODE_BANNERS[mode])
    skin.title(banner_live, LIVE_COLOR, kicker=False)
    banner_overlay = tk.Label(page, text="OVERLAY  -  logo, effets (casual-overlay)")
    skin.title(banner_overlay, OVERLAY_COLOR, kicker=False)
    ROW_PADX, ROW_PADY, SECTION_GAP = 6, 2, 5

    projection = getattr(s, "projection", None)           # avec --gui : la fenetre de rendu s'ouvre au bouton
    s.gui_point_fn = lambda: (root.winfo_rootx() + root.winfo_width() // 2, root.winfo_rooty() + root.winfo_height() // 2)
    proj_ui: dict = {"btn": None}

    def send(name: str) -> None:
        """Commande pour le fil GL ; projection fermee : l'enregistrement se fait ici, le reste attend l'ouverture."""
        if projection is not None and not projection.running():
            if name == "save":
                gl.save_params(params)
                s.status["msg"] = f"Reglages sauves ({gl.PARAMS_PATH.name})"
            elif name in ("reload", "hud", "fullscreen"):
                s.status["msg"] = "Disponible quand la projection est ouverte"
            return
        s.commands.put(name)

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
    separator = tk.Frame(page, bg=OVERLAY_COLOR, width=3)       # frontiere LIVE | OVERLAY (mise en page cote a cote)
    # Partie OVERLAY: les presets restent toujours visibles en haut, le reste est range en ONGLETS (Effets,
    # Logo, Aura...). Une seule colonne de reglages a la fois: la fenetre fait environ la moitie de la
    # hauteur qu'avec des colonnes empilees (qui depassait la hauteur d'un ecran des qu'un bloc s'ouvrait).
    right = tk.Frame(page)
    preset_bar = tk.Frame(right)
    preset_bar.pack(fill="x")
    style = ttk.Style(root)
    style.theme_use("clam")
    edge = {"bordercolor": a2w.GUI_BG, "lightcolor": a2w.GUI_BG, "darkcolor": a2w.GUI_BG}   # pas de filets clairs
    style.configure("TNotebook", background=a2w.GUI_BG, borderwidth=0, tabmargins=(0, 0, 0, 0), **edge)
    style.configure("TNotebook.Tab", background=a2w.GUI_BG, foreground=a2w.GUI_MUTED_FG,
                    padding=(14, 6), borderwidth=0, font=("Segoe UI", 9, "bold"), **edge)
    style.map("TNotebook.Tab", background=[("selected", theme.CARD), ("active", theme.PANEL)],
              foreground=[("selected", OVERLAY_COLOR), ("active", a2w.GUI_FG)])
    notebook = ttk.Notebook(right)
    notebook.pack(fill="both", expand=True, pady=(6, 0))

    def new_tab(title: str) -> tk.Frame:
        frame = tk.Frame(notebook)
        notebook.add(frame, text=title)
        return frame

    # Le fond est un reglage de la partie LIVE (c'est la source du visuel d'audio2wave) : ce bloc est pose sous le
    # panneau du mode, dans le meme canvas defilant, et non dans les onglets OVERLAY.
    tab_fond = tk.Frame(live_wrap)                 # en-tete : le choix Audio2wave / Fond genere (toujours visible)
    tab_fond.pack(side="top", fill="x", before=canvas)
    tab_fx, tab_logo = new_tab("Effets"), new_tab("Logo")
    tab_aura, tab_melt = new_tab("Aura du logo"), new_tab("Fonte du logo")
    tab_cell, tab_noise = new_tab("Cellules"), new_tab("Noise")
    # « Affichage et analyse » (entree d'analyse, plein ecran, automations, mesures...) n'a rien a voir avec les effets du logo :
    # section repliable SOUS les onglets, pas un onglet.
    aff_bar = tk.Frame(right)
    aff_bar.pack(fill="x", pady=(6, 0))
    tab_aff = tk.Frame(right)

    def aff_text() -> str:
        return "AFFICHAGE ET ANALYSE  " + ("▾" if getattr(s, "aff_open", False) else "▸")

    def toggle_aff(flag: bool | None = None) -> None:
        want = (not getattr(s, "aff_open", False)) if flag is None else bool(flag)
        if want == bool(getattr(s, "aff_open", False)) and aff_toggle.cget("text") == aff_text():
            return
        s.aff_open = want
        if want:
            tab_aff.pack(fill="x", after=aff_bar)
        else:
            tab_aff.pack_forget()
        aff_toggle.configure(text=aff_text())

    aff_toggle = tk.Button(aff_bar, text=aff_text(), anchor="w", padx=8, command=toggle_aff)
    aff_toggle.pack(side="left", fill="x", expand=True)
    if getattr(s, "aff_open", False):
        tab_aff.pack(fill="x", after=aff_bar)
    Tooltip(aff_toggle, "Entree d'analyse des effets, plein ecran, barres de debug, sauvegarde, automations, mesures et diagnostic.")
    zone_p, zone_f, zone_fx = Zone(preset_bar), Zone(tab_fond), Zone(tab_fx)
    zone_logo, zone_aura, zone_melt = Zone(tab_logo), Zone(tab_aura), Zone(tab_melt)
    zone_cell, zone_noise, zone_aff = Zone(tab_cell), Zone(tab_noise), Zone(tab_aff)

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
    automation = live.AutomationManager(root, Tooltip, a2w.GUI_PANEL_BG, a2w.GUI_MUTED_FG, OVERLAY_COLOR)
    auto_vars: dict[str, tk.Variable] = {}

    def add_auto(z: Zone, r: int, key: str, var: tk.Variable) -> None:
        label, lo, hi = gl.AUTOMATION_SPECS[key][:3]
        holder = tk.Frame(z.parent)
        holder.grid(row=r, column=2, sticky="w", padx=(0, 4))
        automation.register(holder, key, label, lo, hi)
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

    # ---- PRESETS : deux jeux decorreles (overlay = logo + effets, fond = source + motif), meme interface.
    # Tout est modifiable, y compris les integres ; `default` reste le premier de la liste (voir gl.PresetBook).
    def make_preset_bar(parent, title: str, book, capture, values_of, hint: str) -> dict:
        z = Zone(parent)
        add_section_title(z, title)
        name_var = tk.StringVar(value="default")
        msg = tk.StringVar(value=hint)

        load_row = tk.Frame(parent)
        load_row.grid(row=z.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        tk.Label(load_row, text="Charger").pack(side="left", padx=(0, 10))
        menu_widget = tk.OptionMenu(load_row, name_var, "")
        a2w.style_option_menu(menu_widget)
        menu_widget.pack(side="left")

        def refresh_menu(select: str | None = None) -> None:
            names = book.names()                       # `default` toujours en tete
            menu = menu_widget["menu"]
            menu.delete(0, "end")
            for n in names:
                menu.add_command(label=n, command=lambda n=n: load_preset(n))
            if select is not None:
                name_var.set(select)
            elif name_var.get() not in names:
                name_var.set("default")

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
            msg.set(f"preset '{name}' charge")

        def update_preset() -> None:
            name = name_var.get()
            sync_automation_now()
            book.save(name, capture(params))
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

            question = (f"Remettre le preset '{name}' a sa version d'origine ?" if name == "default"
                        else f"Supprimer definitivement le preset '{name}' ?\nCette action est irreversible.")
            live.confirm_dialog(root, "Supprimer le preset", question, do_delete)

        tk.Button(load_row, text="Mettre a jour", command=update_preset).pack(side="left", padx=(8, 0))
        tk.Button(load_row, text="Supprimer", command=delete_preset).pack(side="left", padx=(6, 0))

        save_row = tk.Frame(parent)
        save_row.grid(row=z.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
        tk.Label(save_row, text="Sauvegarder sous").pack(side="left", padx=(0, 10))
        save_name_var = tk.StringVar(value="")
        save_entry = tk.Entry(save_row, textvariable=save_name_var, width=14)
        save_entry.pack(side="left")

        def save_preset(_evt=None) -> None:
            name = save_name_var.get().strip().lower()
            if not name or name.startswith("_"):
                msg.set("nom de preset vide ou invalide")
                return
            existed = name in book.all()
            sync_automation_now()
            book.save(name, capture(params))
            refresh_menu(select=name)
            save_name_var.set("")
            msg.set(f"preset '{name}' " + ("remplace" if existed else "sauvegarde") + f" ({book.path.name})")

        def restore_presets() -> None:
            book.restore_builtins()
            refresh_menu()
            msg.set("presets integres d'origine restaures (les tiens sont gardes)")

        save_entry.bind("<Return>", save_preset)
        tk.Button(save_row, text="Sauvegarder", command=save_preset).pack(side="left", padx=(8, 0))
        restore_button = tk.Button(save_row, text="Restaurer", command=restore_presets)
        restore_button.pack(side="left", padx=(6, 0))
        Tooltip(restore_button, "Rend les presets integres (default, sobre, neon, chaos) a leur version d'origine, "
                                "meme supprimes ou modifies. Tes propres presets ne sont pas touches.")
        tk.Label(parent, textvariable=msg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=380).grid(
            row=z.next_row(), column=0, columnspan=3, sticky="we", padx=ROW_PADX, pady=(0, 2))
        refresh_menu()
        return {"var": name_var, "msg": msg, "menu": menu_widget, "book": book, "load": load_preset,
                "update": update_preset, "delete": delete_preset, "save": save_preset, "save_var": save_name_var,
                "restore": restore_presets, "refresh": refresh_menu}

    overlay_presets = make_preset_bar(preset_bar, "Presets overlay", gl.OVERLAY_BOOK, gl.capture_overlay,
                                      gl.overlay_preset_values,
                                      "Un preset = le look du logo et des effets (le fond a ses propres presets).")
    overlay_store, overlay_var, preset_msg = overlay_presets["book"], overlay_presets["var"], overlay_presets["msg"]
    overlay_menu, save_name_var = overlay_presets["menu"], overlay_presets["save_var"]
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
                                 "Un preset de fond = la source et le fond genere (ni le logo, ni les effets).")

    pattern_box = tk.Frame(pattern_holder)
    pattern_box.pack(fill="x")
    pattern_box.columnconfigure(2, weight=1)         # le bandeau "Fond genere" prend toute la largeur
    pz = Zone(pattern_box)
    add_section_title(pz, "Fond genere")
    param_radio(pz, "Palette", "bg_palette", (("Arc-en-ciel", "classic"), ("Duo", "duo"), ("Banc de test", "test")),
                tooltip="Arc-en-ciel: le visuel d'origine (canaux R/V/B qui defilent). "
                        "Duo: degrade lisse entre deux couleurs. "
                        "Banc de test: copie EXACTE du motif de test (carreaux de 80 px, 30 images/s) ; "
                        "les autres reglages du motif ne s'y appliquent pas.")
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
                                 "video": "LIVE  -  fond video (casual-overlay)"}.get(bg_state["mode"], MODE_BANNERS[mode]))
        fit()

    bg_var.trace_add("write", show_bg)
    show_bg()
    tab_fond.update_idletasks()
    cap = max(360, cap - tab_fond.winfo_reqheight())     # le choix du fond prend de la place au-dessus du panneau
    fit()

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
    tk.Label(tab_noise, text="Indicateur : ce que le noise envoie aux effets coches.", fg=a2w.GUI_MUTED_FG,
             anchor="w").grid(row=zone_noise.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX)

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
            tk.Button(frame, text=text, command=lambda c=cmd: send(c)).pack(side="left", padx=(0, 6))

    def diagnostic_report() -> str:
        """Rapport du diagnostic : verifications refaites maintenant, sauf OpenGL (resultat du demarrage : on n'ouvre pas un
        second contexte GL pendant que le rendu tourne)."""
        a = getattr(s, "args", None)
        startup = getattr(a, "diag_checks", None) or []
        checks = diagnostics.run_checks(Path(getattr(a, "a2w_dir", gl.DEFAULT_A2W_DIR)), bool(getattr(a, "synthetic", False)),
                                        True, opengl=False)
        gl_check = next((x for x in startup if x.name == "OpenGL"), None)
        if gl_check is not None:
            checks.insert(next((i for i, x in enumerate(checks) if x.name == "Micro"), len(checks)), gl_check)
        return diagnostics.format_report(checks)

    diag_btn = tk.Button(tab_aff, text="Diagnostic",
                         command=lambda: choice_dialog(root, "Diagnostic", diagnostic_report(), ["Fermer"]))
    diag_btn.grid(row=zone_aff.next_row(), column=0, columnspan=2, sticky="w", padx=ROW_PADX, pady=ROW_PADY)
    Tooltip(diag_btn, "Verifie ce PC : ffmpeg, OpenGL, micro, modules Python (glisser-deposer...), audio2wave, dossier des reglages.")

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

    # ---- Reglages incompatibles grises : un effet coupe, ou une palette qui ignore ces curseurs
    gates = gui_gates.Gates(a2w.GUI_MUTED_FG)
    gates.add([holo_box], lambda: holo_var.get() == 1)
    gates.add(gui_gates.rows_after(tab_melt, melt_row), lambda: melt_var.get() == 1)
    gates.add(gui_gates.rows_after(tab_cell, cell_row), lambda: cell_var.get() == 1)
    palette_var = synced["bg_palette"]
    pal_rows = lambda *labels: [w for lb in labels for w in gui_gates.row_of_label(pattern_box, lb, panels=False)]
    gates.add(pal_rows("Couleurs 1 / 2", "Angle du degrade"), lambda: palette_var.get() == "duo")
    gates.add(pal_rows("Teinte", "Vitesse", "Taille des carreaux", "Contraste du damier", "Cadence du damier",
                       "Reaction a l'audio"), lambda: palette_var.get() != "test")
    for var in (holo_var, melt_var, cell_var, palette_var):
        var.trace_add("write", lambda *_a: gates.refresh())
    gates.refresh()

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
        trace, avec la meme valeur: aucun effet de bord). Ecrire une variable peut griser un groupe de curseurs
        (`Gates`), et Tk rend alors a un curseur la valeur PERIMEE de sa variable, qui reecrit `params` par la trace :
        on part donc d'un instantane, on passe deux fois, et on remet l'instantane dans `params` a la fin."""
        snapshot = {key: params.get(key) for key in synced}
        for _pass in range(2):
            for key, var in synced.items():
                value = snapshot[key]
                try:
                    if isinstance(var, tk.StringVar):
                        var.set(str(value))
                    elif isinstance(var, tk.IntVar):
                        var.set(int(float(value) >= 0.5))
                    else:
                        var.set(float(value))
                except (tk.TclError, ValueError, TypeError):
                    pass
        for key, value in snapshot.items():
            if value is not None:
                params[key] = value
        for name, variables in (("fx_on", fx_on_vars), ("fx_int", fx_int_vars),
                                ("fxl_on", fxl_on_vars), ("fxl_int", fxl_int_vars),
                                ("noise_fx", noise_fx_vars)):
            for i, var in enumerate(variables):
                var.set(params[name][i])
        link_var.set(int(float(params["fx_link"]) >= 0.5))
        holo_var.set(int(float(params["holo_on"]) >= 0.5))
        melt_var.set(int(float(params["melt_on"]) >= 0.5))
        cell_var.set(int(float(params["cell_on"]) >= 0.5))
        logo_var.set(params["logo_path"])
        video_var.set(params["logo_video"])
        bgv_var.set(params["bg_video"])
        key_var.set(params["logo_key"])
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", params["text_content"])

    # ---- SCENES : une rangee de touches toujours visible en haut (un clic = tout le look change ; F1..F9 dans la fenetre de
    # rendu). Une scene = (fond audio2wave OU motif genere) + overlay, voir scenes.py.
    scene_book = scenes.SceneBook(lambda: scenes.SCENES_PATH)
    scene_msg = tk.StringVar(value="")
    if not getattr(s, "diag_shown", False):       # une fois : ce que le diagnostic de demarrage a signale
        s.diag_shown = True
        scene_msg.set(diagnostics.summary_line(getattr(getattr(s, "args", None), "diag_checks", None) or []))
    scene_state = {"adding": False}
    scene_entry_var = tk.StringVar(value="")

    def apply_values(values: dict) -> None:
        values = dict(values)
        automation_state = values.pop("_automation", None)
        for key, value in values.items():
            params[key] = value                      # lu a chaque image par le fil GL
        if automation_state is not None:
            params["_automation"] = automation_state
            automation.apply(automation_state)
        sync_widgets()

    def apply_live_overrides(overrides: dict) -> bool:
        """Applique l'etat `overrides` au panneau d'audio2wave courant (`GuiController.apply_state` : comme charger un preset, sans
        clic de menu ni ecriture dans leurs presets ; en live il relance aussi le producteur). Faux si le panneau manque."""
        ctrl = panel["ctrl"]
        if ctrl is None:
            return False
        skipped = ctrl.apply_state(scenes.strip_live_overrides(overrides))      # ni l'entree audio, ni le plein ecran / la taille
        if skipped:
            print(f"Etat audio2wave: cles ignorees par le panneau {s.mode}: {', '.join(sorted(skipped))}", file=sys.stderr)
        return True

    def capture_live_overrides() -> dict | None:
        """Etat du panneau d'audio2wave courant, automations comprises (`GuiController.capture_state`, qui valide d'abord ce qui est
        en saisie) : rien n'est ecrit dans leurs presets, aucun widget n'est actionne."""
        ctrl = panel["ctrl"]
        return None if ctrl is None else json.loads(json.dumps(ctrl.capture_state()))

    def save_live_preset(name: str) -> str | None:
        """Enregistre l'etat du panneau d'audio2wave courant comme preset `name` par leur champ « Sauvegarder sous » (le widget nomme
        `widgets["save_name"]` + Entree : leur menu se met a jour, ce que `ModeStore.save` seul ne fait pas). Rend le nom reellement
        enregistre (`preset_registry.normalize_name`), ou None si le panneau l'a refuse (nom vide ou preset integre)."""
        import preset_registry
        ctrl = panel["ctrl"]
        stored = preset_registry.normalize_name(name)
        if ctrl is None or not stored:
            return None
        store = mode_store(live, s.mode)
        entry = ctrl.widgets.get("save_name")
        if entry is not None and entry.winfo_viewable():
            entry.delete(0, "end")
            entry.insert(0, name)
            entry.focus_force()                         # un evenement clavier n'arrive qu'au widget qui a le focus
            entry.event_generate("<Return>")
            if stored in store.user():
                return stored
        # Panneau masque (fond genere affiche) ou Entree sans effet : on ecrit par le magasin (leur menu ne le montre qu'a la
        # prochaine construction du panneau ; besoin cote audio2wave : un rafraichissement du menu par le controleur).
        try:
            return store.save(name, ctrl.capture_state())
        except Exception:
            return None

    def current_index(rows: list[dict]) -> int | None:
        """Rang (dans le set actif) de la scene active ; le nom fait foi (une scene peut etre deplacee ou retiree)."""
        active = getattr(s, "active_scene", None)
        if not active:
            return None
        i = getattr(s, "active_entry", None)
        if i is not None and 0 <= i < len(rows) and rows[i]["name"].lower() == active.lower():
            return i
        for row in rows:
            if row["name"].lower() == active.lower():
                return row["index"]
        return None

    def flush_pending_inputs() -> None:
        """Valide ce qui a ete tape sans Entree : un champ de fichier ne s'applique qu'a Entree / perte du focus, et cliquer un
        menu (+ Scene) ne fait pas perdre le focus : sans cela la scene capturait l'ancien media."""
        for fn in (apply_logo_path, apply_video_path, apply_bgv, apply_text):
            try:
                fn()
            except tk.TclError:
                pass
        if panel["ctrl"] is not None:
            panel["ctrl"].commit_pending()          # leurs champs de fichier / texte (video de Snap...) et leur redemarrage differe

    def capture_scene_now(name: str) -> None:
        flush_pending_inputs()
        sync_automation_now()
        overrides = None
        note = ""
        if params["bg_mode"] not in ("pattern", "video"):
            overrides = capture_live_overrides()
            if overrides is None:
                note = " (reglages du panneau audio2wave non retenus)"
        scene = scenes.capture_scene(name, params, s.mode, overrides)
        scene_book.put(scene)                          # nouvelle : bibliotheque + fin du set actif ; sinon mise a jour partout
        s.active_scene = scene["name"]
        s.active_entry = current_index(scene_book.rows())
        scene_msg.set(f"Scene '{name}' enregistree" + note)
        refresh_scene_bar()

    # ---- Mode VJ : enchaine les scenes du set actif toutes les N secondes, avec un flash court a chaque changement.
    vj = getattr(s, "vj_state", None)
    if vj is None:
        vj = s.vj_state = {"running": False, "t_next": 0.0}
    vj_ui: dict = {}

    def vj_remaining() -> float:
        return max(vj["t_next"] - time.monotonic(), 0.0)

    def update_vj_button() -> None:
        btn = vj_ui.get("toggle")
        if btn is None:
            return
        try:
            if vj["running"]:
                btn.config(text=f"\u25a0 VJ  {vj_remaining():.0f} s", bg=SCENE_COLOR, fg=BANNER_FG)
            else:
                btn.config(text="\u25b6 VJ", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        except tk.TclError:
            vj_ui.clear()

    # ---- Full Randomizer : une scene temporaire tiree dans les presets, la banque d'assets et les valeurs aleatoires
    rnd = getattr(s, "rnd", None)
    if rnd is None:
        rnd = s.rnd = {"last": {}, "scene": None, "info": "", "bank": {"fond": [], "overlay": []}, "bank_key": None,
                       "rng": random.Random()}

    def random_bank(force: bool = False) -> dict:
        """Les deux banques d'assets du set, `{"fond": [...], "overlay": [...]}` (rescannees seulement si les dossiers changent
        ou si `force`)."""
        rs = scene_book.vj()["random"]
        key = (tuple(rs["folders_fond"]), tuple(rs["folders_overlay"]))
        if force or rnd["bank_key"] != key:
            rnd["bank"] = {"fond": randomizer.scan_bank(list(key[0]), "fond"), "overlay": randomizer.scan_bank(list(key[1]), "overlay")}
            rnd["bank_key"] = key
        return rnd["bank"]

    def random_scene() -> tuple[dict, str, dict]:
        """Tire une scene (sans l'appliquer) avec les reglages du hasard du set actif."""
        sync_automation_now()
        settings = scene_book.vj()["random"]
        try:
            a2w_presets = mode_store(live, s.mode).all()
        except Exception:
            a2w_presets = {}
        a2w_modes: dict = {}
        if "params" in settings["bg_sources"]:               # config aleatoire : tous les modes autorises (charges seulement ici)
            for mode in settings["modes"]:
                try:
                    a2w_modes[mode] = a2w_presets if mode == s.mode else mode_store(live, mode).all()
                except Exception:
                    a2w_modes[mode] = {}
        banks = random_bank()
        ctx = randomizer.Context(bank_fond=banks["fond"], bank_overlay=banks["overlay"], bg_presets=gl.BACKGROUND_BOOK.all(),
                                 overlay_presets=gl.OVERLAY_BOOK.all(), a2w_presets=a2w_presets, a2w_modes=a2w_modes, mode=s.mode,
                                 current_scene=scenes.capture_scene("Full Randomizer", params, s.mode, None), last=rnd["last"])
        return randomizer.draw_scene(rnd["rng"], settings, ctx)

    def random_draw() -> bool:
        """Tire une scene et l'applique (le VJ ajoute le flash et relance le minuteur)."""
        scene, info, record = random_scene()
        rnd["last"], rnd["scene"], rnd["info"] = record, scene, info
        apply_scene_dict(scene)
        s.active_scene, s.active_entry = "Full Randomizer", None
        scene_msg.set("Full Randomizer : " + info)
        return True

    def add_random_scene() -> str:
        """Bouton « + » : une scene tiree au hasard, sous un nom aleatoire, ajoutee a la fin du set actif. Aucun preset n'est enregistre
        et rien n'est applique (le spectacle en cours n'est pas perturbe). Rend le nom de la scene."""
        scene, info, record = random_scene()
        rnd["last"] = record
        name = randomizer.random_name(rnd["rng"], scene_book.names())
        scene_book.put(dict(scene, name=name))
        scene_msg.set(f"Scene aleatoire '{name}' ajoutee au set (aucun preset enregistre) : {info}")
        refresh_scene_bar()
        return name

    def random_draw_now() -> None:
        """Bouton « Tirer maintenant » : un tirage tout de suite (le VJ, s'il tourne, repart pour une duree pleine)."""
        s.commands.put("flash")
        random_draw()
        if vj["running"]:
            vj["t_next"] = time.monotonic() + scene_book.vj()["seconds"]
        refresh_scene_bar()

    def keep_a2w_mode(scene: dict | None) -> str | None:
        """Mode d'audio2wave dont le panneau est le fond de ce tirage (None : le fond n'est pas un visuel audio2wave)."""
        if scene is not None and scene.get("bg") == "live" and (scene.get("mode") in (None, s.mode)):
            return s.mode
        return None

    def random_keep_check(names: dict) -> str | None:
        """Message d'erreur pour les noms demandes (None = tout va bien)."""
        scene = rnd["scene"]
        if scene is None:
            return "Aucun tirage a garder : 'Tirer maintenant' d'abord"
        names = {k: (names.get(k) or "").strip()[:scenes.MAX_NAME] for k in ("fond", "overlay", "a2w", "scene")}
        if not any(names.values()):
            return "Donne au moins un nom (preset fond, preset overlay, preset audio2wave ou scene)"
        if names["fond"] and not randomizer.fond_saveable(scene):
            return "Ce tirage n'a pas de fond genere ni de video choisie : pas de preset de fond"
        if names["a2w"] and keep_a2w_mode(scene) is None:
            return "Ce tirage n'a pas de fond audio2wave : pas de preset audio2wave"
        try:
            a2w_taken = list(mode_store(live, s.mode).all())
        except Exception:
            a2w_taken = []
        for key, taken, label in (("fond", gl.BACKGROUND_BOOK.names(), "preset de fond"),
                                  ("overlay", gl.OVERLAY_BOOK.names(), "preset overlay"),
                                  ("a2w", a2w_taken, f"preset {s.mode} d'audio2wave"), ("scene", scene_book.names(), "scene")):
            name = names[key]
            if name and (name.lower() == "default" or name.lower() in (x.lower() for x in taken)):
                return f"{label} '{name}' existe deja : choisis un autre nom"
        return None

    def random_save(names: dict) -> str | None:
        """Enregistre le dernier tirage : preset de fond, preset overlay et / ou scene (un nom vide = pas enregistre). Rend un message
        d'erreur (rien n'est ecrit) ou None."""
        error = random_keep_check(names)
        if error:
            return error
        scene = rnd["scene"]
        names = {k: (names.get(k) or "").strip()[:scenes.MAX_NAME] for k in ("fond", "overlay", "a2w", "scene")}
        import preset_registry
        names["a2w"] = preset_registry.normalize_name(names["a2w"])          # la regle des trois fenetres d'audio2wave
        saved = []
        if keep_a2w_mode(scene) is not None and (names["a2w"] or names["scene"]):
            # Fond audio2wave : ce qui est a l'ecran est l'etat reel de leur panneau (le tirage, mais aussi ce qui a pu etre retouche
            # depuis, et un tirage « fond verrouille » ou preset de fond live n'a pas d'etat propre) : c'est lui qu'on retient.
            if names["a2w"]:
                stored = save_live_preset(names["a2w"])
                if stored is None:
                    return f"Preset {s.mode} d'audio2wave non enregistre : leur panneau l'a refuse (rien n'a ete ecrit)"
                saved.append(f"preset {s.mode} '{stored}'")
            state = capture_live_overrides()
            if state is not None:
                scene = dict(scene, mode=s.mode, live_overrides=scenes.strip_live_overrides(state))
        if names["fond"]:
            gl.BACKGROUND_BOOK.save(names["fond"], json.loads(json.dumps(scene["fond"])))
            bg_presets["refresh"]()
            saved.append(f"preset fond '{names['fond']}'")
        if names["overlay"]:
            gl.OVERLAY_BOOK.save(names["overlay"], json.loads(json.dumps(scene["overlay"])))
            overlay_presets["refresh"]()
            saved.append(f"preset overlay '{names['overlay']}'")
        if names["scene"]:
            scene_book.put(dict(scene, name=names["scene"]))
            s.active_scene = names["scene"]
            s.active_entry = current_index(scene_book.rows())
            saved.append(f"scene '{names['scene']}' (ajoutee au set)")
        scene_msg.set("Enregistre : " + ", ".join(saved))
        refresh_scene_bar()
        return None

    def random_keep() -> None:
        """Bouton « Garder ce tirage... » : une modale propose les noms du preset de fond, du preset overlay et de la scene."""
        scene = rnd["scene"]
        if scene is None:
            scene_msg.set("Aucun tirage a garder : 'Tirer maintenant' d'abord")
            return
        fond_ok = randomizer.fond_saveable(scene)
        a2w_mode = keep_a2w_mode(scene)
        defaults = {"fond": scenes.unique_name("Tirage fond", gl.BACKGROUND_BOOK.names()),
                    "overlay": scenes.unique_name("Tirage overlay", gl.OVERLAY_BOOK.names()),
                    "scene": scenes.unique_name("Tirage", scene_book.names())}
        if a2w_mode:
            try:
                defaults["a2w"] = scenes.unique_name(f"Tirage {a2w_mode}", list(mode_store(live, a2w_mode).all()))
            except Exception:
                defaults["a2w"] = f"Tirage {a2w_mode}"
        names = scene_ui["ask_keep"](defaults, fond_ok, random_keep_check, a2w_mode)
        if names is None:
            return
        error = random_save(names)
        if error:
            scene_msg.set(error)

    def set_random_option(refresh: bool = False, **kw) -> None:
        current = scene_book.vj()["random"]
        current.update(kw)
        scene_book.set_vj(random=current)
        if refresh:
            refresh_scene_bar()

    def vj_advance() -> None:
        """Entree suivante du set (ordre choisi) + flash, et le minuteur repart. Ordre `alea` : une scene tiree au hasard."""
        rows = scene_book.rows()
        if scene_book.vj()["order"] == "alea":
            s.commands.put("flash")
            random_draw()
            vj["t_next"] = time.monotonic() + scene_book.vj()["seconds"]
            update_vj_button()
            return
        if sum(1 for r in rows if not r["skip"]) < 2:
            vj["running"] = False
            scene_msg.set("Il faut au moins 2 scenes actives dans le set pour enchainer")
            update_vj_button()
            return
        settings = scene_book.vj()
        index = scenes.next_entry_index(rows, current_index(rows), settings["order"], random)
        s.commands.put("flash")
        apply_entry_core(index)
        vj["t_next"] = time.monotonic() + (rows[index]["seconds"] or settings["seconds"])     # duree propre a l'entree
        update_vj_button()

    def vj_toggle() -> None:
        if vj["running"]:
            vj["running"] = False
            scene_msg.set("VJ en pause")
        elif scene_book.vj()["order"] != "alea" and sum(1 for r in scene_book.rows() if not r["skip"]) < 2:
            scene_msg.set("Il faut au moins 2 scenes actives dans le set pour enchainer")
        else:
            vj["running"] = True
            vj["t_next"] = time.monotonic() + scene_book.vj()["seconds"]
            scene_msg.set(f"VJ: une scene toutes les {scene_book.vj()['seconds']:.0f} s"
                          + (" (Full Randomizer)" if scene_book.vj()["order"] == "alea" else ""))
        update_vj_button()

    def vj_tick() -> None:
        if vj["running"] and time.monotonic() >= vj["t_next"]:
            vj_advance()
        if getattr(s, "thumb_dirty", False) and time.monotonic() - panel_state.get("thumb_at", 0.0) >= 0.8:
            s.thumb_dirty = False                          # une vignette est prete (calculee dans un fil) : au plus un rafraichissement
            panel_state["thumb_at"] = time.monotonic()     # toutes les 0,8 s, les vignettes arrivent en rafale
            refresh_scene_panel()
        update_vj_button()
        after(250, vj_tick)

    def apply_scene(name: str, index: int | None = None) -> None:
        """Choix a la main (par nom, ou par rang dans le set) : met le VJ en pause (on ne se bat pas avec le minuteur)."""
        rows = scene_book.rows()
        if index is None:
            index = next((r["index"] for r in rows if r["name"].lower() == name.lower()), None)
        paused = vj["running"]
        vj["running"] = False
        if index is not None and index < len(rows):
            apply_entry_core(index)
        else:
            apply_scene_core(name)                         # scene de la bibliotheque absente du set : sans media
        if paused:
            scene_msg.set(scene_msg.get() + "  (VJ en pause)")
        update_vj_button()

    def apply_entry(index: int) -> None:
        rows = scene_book.rows()
        if 0 <= index < len(rows):
            apply_scene(rows[index]["name"], index)

    def apply_entry_core(index: int) -> None:
        row = scene_book.rows()[index]
        apply_scene_core(row["name"], row["media"], index, row["a2w"], row.get("bg_video"))

    def apply_scene_core(name: str, media: dict | None = None, index: int | None = None, a2w: dict | None = None,
                         bgv: str | None = None) -> None:
        scene = scene_book.get(name)
        if scene is None:
            scene_msg.set(f"scene inconnue: {name}")
            return
        apply_scene_dict(scene, media, index, a2w, bgv)

    def apply_scene_dict(scene: dict, media: dict | None = None, index: int | None = None, a2w: dict | None = None,
                         bgv: str | None = None) -> None:
        """Applique une scene (de la bibliotheque, ou temporaire : tirage du VJ aleatoire). `bgv` : video de fond propre a l'entree."""
        sync_automation_now()
        note = ""
        if bgv:
            if scenes.bg_video_missing(bgv):
                note = f" (video de fond introuvable: {Path(bgv).name})"      # la scene garde son propre fond
            else:
                scene = scenes.scene_with_bg_video(scene, bgv)
        values = scenes.scene_values(scene, params)
        if media:
            missing = scenes.media_missing(media)
            if missing:
                note = f" (media introuvable: {Path(missing).name})"      # la scene garde son propre media
            else:
                values.update(scenes.media_values(media, params))
        apply_values(values)
        s.active_scene = scene["name"]
        s.active_entry = index
        scene_msg.set(f"Scene '{scene['name']}'" + (f" + {scenes.media_label(media)}" if media and not note else "") + note)
        if scene["bg"] == "live" and scene.get("mode"):
            overrides = scene.get("live_overrides")
            if a2w:                                        # videos d'audio2wave propres a l'entree : posees sur l'etat de la scene
                if scene["mode"] == "snap":
                    gone = scenes.a2w_missing(a2w)
                    overrides = {**(overrides or {}), **{k: v for k, v in a2w.items() if v not in gone}}
                    if gone:
                        note = f" (video audio2wave introuvable: {Path(gone[0]).name})"
                        scene_msg.set(scene_msg.get() + note)
                else:
                    scene_msg.set(scene_msg.get() + " (videos audio2wave: scene Snap seulement)")
            if scene["mode"] != s.mode:
                s.pending_live_overrides = overrides
                root.after(30, lambda: request_mode(scene["mode"]))      # reconstruit la fenetre, puis charge l'etat
                return
            if overrides and not apply_live_overrides(overrides):
                scene_msg.set(f"Scene '{scene['name']}' (panneau audio2wave introuvable)")
        refresh_scene_bar()

    def delete_scene_now(name: str) -> None:
        scene_book.delete(name)                            # bibliotheque ET tous les sets
        if getattr(s, "active_scene", None) == name:
            s.active_scene = None
            s.active_entry = None
        scene_msg.set(f"Scene '{name}' supprimee")
        refresh_scene_bar()

    def ask_delete_scene(name: str) -> None:
        live.confirm_dialog(root, "Supprimer la scene",
                            f"Supprimer la scene '{name}' de la bibliotheque ?\nElle disparait aussi de tous les sets qui "
                            "l'utilisent. Cette action est irreversible.", lambda: delete_scene_now(name))

    def remove_entry_now(index: int) -> None:
        rows = scene_book.rows()
        if 0 <= index < len(rows):
            name = rows[index]["name"]
            scene_book.remove_entry(index)
            s.active_entry = None
            scene_msg.set(f"'{name}' retiree du set (la scene reste dans la bibliotheque)")
            refresh_scene_bar()

    def move_entry_now(index: int, delta: int) -> None:
        if scene_book.move_entry(index, delta) is not None:
            s.active_entry = None
        refresh_scene_bar()

    def media_from_current(index: int) -> None:
        media = scenes.capture_media(params)
        if scene_book.set_entry_media(index, media):
            scene_msg.set(f"Media de cette entree : {scenes.media_label(media)}")
        refresh_scene_bar()

    def media_clear(index: int) -> None:
        if scene_book.set_entry_media(index, None):
            scene_msg.set("Media retire : la scene garde le sien")
        refresh_scene_bar()

    def add_entry_now(name: str) -> None:
        if scene_book.add_entry(name):
            scene_msg.set(f"'{name}' ajoutee au set '{scene_book.active_set_name()}'")
        refresh_scene_bar()

    def poll_scene_requests() -> None:
        """F1..F9 de la fenetre de rendu (fil GL) -> l'entree de ce rang dans le set actif."""
        queue_ = getattr(s, "scene_requests", None)
        while queue_ is not None and not queue_.empty():
            index = queue_.get_nowait()
            if index == "vj":
                vj_toggle()
                continue
            if index == "vjnext":
                vj_advance()
                continue
            if 0 <= index < len(scene_book.rows()):
                apply_entry(index)
            else:
                scene_msg.set(f"F{index + 1}: aucune scene a ce rang")

    def new_scene_from_entry(_evt=None) -> None:
        name = scene_entry_var.get().strip()[:scenes.MAX_NAME]
        if not name:
            scene_msg.set("Donne un nom a la scene")
            return
        if scene_book.index_of(name) is not None:
            scene_msg.set(f"'{name}' existe deja dans la bibliotheque : '+ Scene' > Ajouter, ou clic droit > Mettre a jour")
            return
        scene_state["adding"] = False
        capture_scene_now(name)
        refresh_scene_bar()

    def start_adding() -> None:
        scene_state["adding"] = True
        scene_entry_var.set(scenes.unique_name(f"Scene {len(scene_book.names()) + 1}", scene_book.names()))
        refresh_scene_bar()

    def cancel_adding(_evt=None) -> None:
        scene_state["adding"] = False
        refresh_scene_bar()

    # ---- SETS : un enchainement de scenes (de la bibliotheque) enregistre sous un nom, exportable / importable.
    # Les dialogues sont dans `scene_ui` pour que les tests les remplacent (jamais de fenetre bloquante).
    scene_ui = {
        "chooser": lambda title, text, buttons: choice_dialog(root, title, text, buttons),
        "ask_name": lambda title, prompt, initial="": simpledialog.askstring(title, prompt, parent=root, initialvalue=initial),
        "ask_save": lambda initial: filedialog.asksaveasfilename(
            parent=root, title="Exporter le set", initialfile=initial, defaultextension=".json",
            filetypes=[("Set casual-overlay", "*.json"), ("Tous les fichiers", "*.*")]),
        "ask_keep": lambda defaults, fond_ok, validate, a2w_mode=None: keep_dialog(root, defaults, fond_ok, validate, a2w_mode),
        "ask_dir": lambda: filedialog.askdirectory(parent=root, title="Dossier d'assets (images, videos, animations)"),
        "ask_open": lambda: filedialog.askopenfilename(
            parent=root, title="Importer un set", filetypes=[("Set casual-overlay", "*.json"), ("Tous les fichiers", "*.*")]),
    }

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

    def export_set_now(path: str | None = None) -> bool:
        name = scene_book.active_set_name()
        path = path or scene_ui["ask_save"](f"{name}.set.json")
        if not path:
            return False
        try:
            data = scenes.export_set(scene_book, name)
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError) as exc:
            scene_msg.set(f"Export impossible: {exc}")
            return False
        scene_msg.set(f"Set '{name}' exporte ({len(data['scenes'])} scenes) : {Path(path).name}")
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

    def import_set_now(path: str | None = None) -> bool:
        path = path or scene_ui["ask_open"]()
        if not path:
            return False
        try:
            parsed = scenes.parse_import(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            scene_msg.set(f"Import impossible: {exc}")
            return False
        except ValueError as exc:
            scene_msg.set(f"Import impossible: {exc}")
            return False
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
        result = scenes.import_set(scene_book, parsed, set_mode, scene_mode)
        switch_set(result["set"])
        note = ""
        if result["set_renamed"]:
            note += f" (renomme '{result['set']}')"
        if result["renamed"]:
            note += f", {len(result['renamed'])} scene(s) renommee(s)"
        if result["replaced"]:
            note += f", {len(result['replaced'])} scene(s) remplacee(s)"
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
        menu.add_separator()
        if i > 0:
            menu.add_command(label="Monter", command=lambda: move_entry_now(i, -1))
        if i < len(scene_book.rows()) - 1:
            menu.add_command(label="Descendre", command=lambda: move_entry_now(i, 1))
        menu.add_command(label="Retirer du set", command=lambda: remove_entry_now(i))
        menu.add_command(label="Supprimer la scene de la bibliotheque...", command=lambda n=row["name"]: ask_delete_scene(n))

    # ---- Glisser-deposer d'un fichier sur une scene (touche de la barre ou ligne du panneau etendu) : tkdnd via tkinterdnd2
    # (facultatif : sans lui, tout marche sauf le glisser-deposer).
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

    # ---- Panneau etendu : le set en detail (media lie, duree et participation au VJ de chaque entree, ordre, renommer)
    PANEL_BODY_H = 200
    PANEL_BODY_MIN = 80
    NAME_MIN, NAME_MAX = 14, 70                     # largeur (caracteres) du bouton du nom : la plus longue entree, bornee
    DUR_W = 13
    BTN_W = {"▲": 3, "▼": 3, "Renommer": 9, "Retirer": 8}
    BOLD = ("Segoe UI", 9, "bold")

    def row_bg(active: bool) -> str:
        return "#3b3320" if active else a2w.GUI_BG

    def card_bg(active: bool) -> str:
        return "#4a3f26" if active else a2w.GUI_PANEL_BG

    def dur_label(row: dict) -> str:
        return "duree " + ("du set" if row["seconds"] is None else f"{row['seconds']:.0f} s") + " \u25be"

    def build_panel_row(parent, row: dict, active: bool) -> dict:
        """Une ligne du panneau ; rend ses references (`frame`, `num`, `name`, `dur`, `cards`, `active`) pour la repeindre sur place."""
        bg = row_bg(active)
        i = row["index"]
        refs: dict = {"index": i, "cards": [], "active": active}
        fr = tk.Frame(parent, bg=bg)
        fr.pack(fill="x", pady=1, padx=2)
        refs["frame"] = fr
        refs["num"] = tk.Label(fr, text=str(i + 1), width=3, bg=bg, fg=SCENE_COLOR, font=BOLD)
        refs["num"].pack(side="left")
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
        n = len(scene_book.rows())
        for text, cmd, ok in (("\u25b2", lambda k=i: move_entry_now(k, -1), i > 0),
                              ("\u25bc", lambda k=i: move_entry_now(k, 1), i < n - 1),
                              ("Renommer", lambda nm=row["name"]: rename_scene_dialog(nm), True),
                              ("Retirer", lambda k=i: remove_entry_now(k), True)):
            b = tk.Button(fr, text=text, command=cmd, state="normal" if ok else "disabled", relief="flat", bd=0, padx=6, width=BTN_W[text],
                          cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, disabledforeground=a2w.GUI_MUTED_FG,
                          activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            b._skin_stretch = True                         # meme hauteur que la ligne (et que le bouton du nom)
            b.pack(side="left", fill="y", padx=1, pady=2)
        register_drop(fr, i)
        register_drop(name_btn, i)
        return refs

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
        banks = random_bank()

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
            tk.Label(fr, text=count if bank else ("vide : ajoute un dossier" if not rs[fkey] else "aucun fichier trouve"),
                     bg=bg, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
            if which == "overlay":
                scan_btn = tk.Button(fr, text="Rescanner", command=lambda: (random_bank(True), refresh_scene_panel(force=True)), relief="flat",
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

    panel_state: dict = {"sig": None, "rows": {}, "cv": None, "summary": None, "dur": {}, "name_w": 18}

    def panel_summary(rows: list[dict], settings: dict) -> str:
        played = [r for r in rows if not r["skip"]]
        secs = settings["seconds"]
        loop = sum(r["seconds"] or secs for r in played)
        if settings["order"] == "alea":
            return f"Full Randomizer : un tirage toutes les {secs:.0f} s"
        return (f"{len(played)}/{len(rows)} scenes dans le VJ, boucle ~ "
                + (f"{loop / 60:.1f} min" if loop >= 90 else f"{loop:.0f} s"))

    def panel_signature(rows: list[dict], settings: dict) -> str:
        """Tout ce qui, hors la scene active, change ce que le panneau affiche : s'il est identique, on ne reconstruit rien."""
        return json.dumps([settings, rows, rnd["info"] if settings["order"] == "alea" else "",
                           [len(b) for b in rnd["bank"].values()], sum(1 for v in getattr(s, "thumb_cache", {}).values() if v != "pending"), dnd["ok"],
                           scene_book.active_set_name()], sort_keys=True, default=str)

    def refresh_scene_panel(force: bool = False) -> None:
        """Construit / met a jour le panneau etendu. Rien n'est reconstruit si seul l'ordre d'affichage actif a change (la ligne
        active est alors repeinte sur place) ; une reconstruction garde la position de l'ascenseur et rehabille les widgets."""
        if not getattr(s, "scenes_expanded", False):
            for child in scene_panel.winfo_children():
                child.destroy()
            scene_panel.pack_forget()
            panel_state.update(sig=None, rows={}, cv=None, summary=None, dur={})
            return
        rows = scene_book.rows()
        settings = scene_book.vj()
        current = current_index(rows)
        sig = panel_signature(rows, settings)
        if not force and sig == panel_state["sig"] and scene_panel.winfo_children():
            for refs in panel_state["rows"].values():
                restyle_row(refs, refs["index"] == current)
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
        panel_state.update(sig=sig, rows={}, cv=None, summary=None, dur={})
        scene_panel.pack(fill="x")
        head = tk.Frame(scene_panel, bg=a2w.GUI_PANEL_BG)
        head.pack(fill="x")
        tk.Label(head, text=f" SET  {scene_book.active_set_name()}", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, font=BOLD).pack(
            side="left", padx=(6, 6), pady=4)
        plus = tk.Button(head, text="+", command=add_random_scene, relief="flat", bd=0, padx=8, cursor="hand2", bg=SCENE_COLOR,
                         fg=BANNER_FG, font=BOLD, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        plus.pack(side="left", padx=(0, 12), pady=2)
        Tooltip(plus, "Ajoute au set une scene tiree au hasard (reglages du Full Randomizer), sous un nom aleatoire. "
                      "Aucun preset n'est enregistre et la scene n'est pas appliquee.")
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
        default = tk.Menubutton(head, text=f"duree par defaut : {secs:.0f} s \u25be", relief="flat", bd=0, padx=8, cursor="hand2",
                                bg=a2w.GUI_BG, fg=a2w.GUI_FG, activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        default.pack(side="left", padx=2)
        dm = tk.Menu(default, tearoff=0)
        for value in scenes.VJ_SECONDS:
            dm.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min",
                           command=lambda v=value: set_vj_option(seconds=float(v)))
        default["menu"] = dm
        summary = tk.StringVar(value=panel_summary(rows, settings))
        panel_state["summary"] = summary
        tk.Label(head, textvariable=summary, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
        tk.Label(head, text="glisser-deposer d'un fichier sur une zone (overlay / audio2wave) = lier ce media"
                 if dnd["ok"] else "glisser-deposer indisponible (pip install tkinterdnd2)", bg=a2w.GUI_PANEL_BG,
                 fg=a2w.GUI_MUTED_FG).pack(side="right", padx=10)
        body = tk.Frame(scene_panel, bg=a2w.GUI_BG, height=getattr(s, "panel_body_h", PANEL_BODY_H))
        body.pack(fill="x")
        body.pack_propagate(False)
        grip = tk.Label(scene_panel, text="⋯", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, cursor="sb_v_double_arrow", pady=0)
        grip.pack(fill="x")
        Tooltip(grip, "Glisse vers le haut ou le bas pour agrandir ou reduire la liste (la fenetre suit).")
        drag: dict = {}

        def grip_press(e) -> None:
            drag.update(y=e.y_root, h=int(body.cget("height")))

        def grip_move(e) -> None:
            if not drag:
                return
            want = max(PANEL_BODY_MIN, drag["h"] + e.y_root - drag["y"])
            old = int(body.cget("height"))
            delta = want - old
            want = min(want, max(root.winfo_screenheight() - 200, PANEL_BODY_MIN))
            delta = want - old
            if delta == 0:
                return
            # la fenetre suit tant qu'elle tient dans l'ecran ; au-dela la liste grandit quand meme (la page du dessous se reduit,
            # elle defile) et ce « trop-plein » est rendu en premier quand on reduit
            overflow = getattr(s, "panel_overflow", 0)
            if delta > 0:
                win = min(delta, max(root.winfo_screenheight() - 80 - root.winfo_height(), 0))
                overflow += delta - win
            else:
                take = min(overflow, -delta)
                overflow -= take
                win = delta + take
            s.panel_overflow = overflow
            body.configure(height=old + delta)
            s.panel_body_h = old + delta
            if win:
                root.geometry(f"{root.winfo_width()}x{max(MIN_WIN_H, root.winfo_height() + win)}")
                s.scene_panel_h = getattr(s, "scene_panel_h", 0) + win       # a la fermeture, la fenetre rend exactement ce qu'elle a pris

        grip.bind("<ButtonPress-1>", grip_press)
        grip.bind("<B1-Motion>", grip_move)
        grip.bind("<ButtonRelease-1>", lambda e: drag.clear())
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
            for row in rows:
                refs = build_panel_row(inner, row, row["index"] == current)
                panel_state["rows"][row["index"]] = refs
                panel_state["dur"][row["index"]] = refs["dur"]
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

    def proj_text() -> str:
        return "Arreter la projection" if projection is not None and projection.running() else "Demarrer la projection"

    def toggle_projection() -> None:
        if projection is None:
            return
        if projection.running():
            projection.stop()
            scene_msg.set("Projection fermee")
        else:
            scene_msg.set(projection.start())
        refresh_proj_button()

    def refresh_proj_button() -> None:
        btn = proj_ui["btn"]
        if btn is None:
            return
        try:
            if btn.cget("text") != proj_text():
                btn.configure(text=proj_text())
        except tk.TclError:
            proj_ui["btn"] = None

    def refresh_scene_bar() -> None:
        for child in scene_bar.winfo_children():
            child.destroy()
        proj_ui["btn"] = None
        if projection is not None:
            btn = tk.Button(scene_bar, text=proj_text(), command=toggle_projection, padx=10)
            btn.pack(side="left", padx=(8, 4), pady=5)
            proj_ui["btn"] = btn
            Tooltip(btn, "Ouvre la fenetre de projection : en fenetre, sur un autre ecran que les reglages s'il y en a un "
                         "(plein ecran : case Plein ecran ou touche F). Echap dans cette fenetre la ferme sans fermer les reglages.")
        scenes_title = tk.Label(scene_bar, text="SCENES")
        skin.title(scenes_title, SCENE_COLOR)
        scenes_title.pack(side="left", padx=(8, 6), pady=5)
        # Mode VJ (a droite) : reglages, scene suivante, lecture / pause. Pose AVANT les touches des scenes : en pack, le
        # premier arrive est le premier servi, donc 9 scenes aux noms longs ne repoussent jamais le bouton hors de la fenetre.
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
        secs_var = tk.IntVar(value=int(scene_book.vj()["seconds"]))
        for label, value in (("Dans l'ordre", "seq"), ("Au hasard (jamais la meme deux fois)", "random"),
                             ("Full Randomizer (banques d'assets)", "alea")):
            menu.add_radiobutton(label=label, variable=order_var, value=value,
                                 command=lambda: set_vj_option(order=order_var.get()))
        menu.add_separator()
        for value in scenes.VJ_SECONDS:
            label = f"Toutes les {value} s" if value < 60 else f"Toutes les {value // 60} min"
            menu.add_radiobutton(label=label, variable=secs_var, value=value,
                                 command=lambda: set_vj_option(seconds=float(secs_var.get())))
        opts["menu"] = menu
        Tooltip(opts, "Ordre et duree du VJ : propres a chaque set.")
        nxt = tk.Button(scene_bar, text="\u25b6\u25b6", command=vj_advance, relief="flat", bd=0, padx=8, pady=3,
                        cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG, activebackground=SCENE_COLOR,
                        activeforeground=BANNER_FG, font=("Segoe UI", 9, "bold"))
        nxt.pack(side="right", padx=2, pady=5)
        Tooltip(nxt, "Scene suivante tout de suite, avec un flash (fleche droite dans la fenetre de rendu).")
        toggle = tk.Button(scene_bar, text="\u25b6 VJ", command=vj_toggle, relief="flat", bd=0, padx=12, pady=3,
                           cursor="hand2", font=("Segoe UI", 9, "bold"), bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                           activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        toggle.pack(side="right", padx=2, pady=5)
        Tooltip(toggle, "Mode VJ : enchaine les scenes du set tout seul (Espace dans la fenetre de rendu), avec un flash court "
                        "a chaque changement. Le chiffre est le temps avant la prochaine scene. Choisir une scene a la main "
                        "met le VJ en pause ; changer de set le redemarre. Ordre et duree : 'reglages VJ'.")
        vj_ui["toggle"] = toggle
        update_vj_button()
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
        set_menu.add_command(label="Exporter ce set (fichier)...", command=export_set_now)
        set_menu.add_command(label="Importer un set (fichier)...", command=import_set_now)
        set_btn["menu"] = set_menu
        Tooltip(set_btn, "Set = un enchainement de scenes (les memes scenes peuvent servir dans plusieurs sets, chacune avec son "
                         "propre media). Changer de set change la rangee, F1..F9 et le VJ (qui redemarre). "
                         "Exporter / importer : un seul fichier a partager.")
        rows = scene_book.rows()
        current = current_index(rows)
        for row in rows[:scenes.KEYS]:
            i = row["index"]
            name = row["name"]
            on = i == current
            btn = tk.Button(scene_bar, text=f"{i + 1}  {name if len(name) <= 14 else name[:13] + '.'}"
                            + (" \u25cf" if row["media"] else ""), command=lambda k=i: apply_entry(k), relief="flat", bd=0,
                            padx=12, pady=3, cursor="hand2", font=("Segoe UI", 9, "bold"),
                            bg=SCENE_COLOR if on else a2w.GUI_BG, fg=BANNER_FG if on else a2w.GUI_FG,
                            activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            btn.pack(side="left", padx=2, pady=5)
            register_drop(btn, i)                                      # un fichier depose sur la touche = media de la scene
            menu = tk.Menu(btn, tearoff=0)
            fill_entry_menu(menu, row)
            btn.bind("<Button-3>", lambda e, m=menu: m.tk_popup(e.x_root, e.y_root))
            scene = row["scene"]
            Tooltip(btn, f"{name} - clic: charger la scene (F{i + 1} dans la fenetre de rendu). "
                         f"{ {'pattern': 'Fond: fond genere. ', 'video': 'Fond: video. '}.get(scene['bg']) or 'Fond: audio2wave (' + str(scene.get('mode')) + '). '}"
                         + (f"Media: {scenes.media_label(row['media'])}. " if row["media"] else "")
                         + "Glisse un fichier (image / video) dessus pour lui lier un media. "
                         + "Clic droit: mettre a jour, media, deplacer, retirer, supprimer.")
        if len(rows) > scenes.KEYS:                    # au-dela de F9 : un menu (le VJ les joue toutes)
            more = tk.Menubutton(scene_bar, text=f"+{len(rows) - scenes.KEYS} \u25be", relief="flat", bd=0, padx=8, pady=3,
                                 cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, activebackground=a2w.GUI_BG,
                                 activeforeground=a2w.GUI_FG, font=("Segoe UI", 9, "bold"))
            more.pack(side="left", padx=2, pady=5)
            more_menu = tk.Menu(more, tearoff=0)
            for row in rows[scenes.KEYS:]:
                sub = tk.Menu(more_menu, tearoff=0)
                fill_entry_menu(sub, row)
                more_menu.add_cascade(label=f"{row['index'] + 1}  {row['name']}" + (" \u25cf" if row["media"] else ""), menu=sub)
            more["menu"] = more_menu
            Tooltip(more, "Les scenes du set au-dela de F9 : accessibles ici et par le VJ.")
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
        tk.Label(scene_bar, textvariable=scene_msg, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="e").pack(
            side="right", padx=10)
        refresh_scene_panel()
        skin.apply(scene_top)

    refresh_scene_bar()
    after(250, vj_tick)

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
        poll_scene_requests()
        refresh_proj_button()
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
        if cell_var.get() != int(float(params["cell_on"]) >= 0.5):
            cell_var.set(int(float(params["cell_on"]) >= 0.5))
        if melt_var.get() != int(float(params["melt_on"]) >= 0.5):
            melt_var.set(int(float(params["melt_on"]) >= 0.5))
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
        parts = [f"{st.get('fps', 0):.0f} fps"] + [st[k] for k in ("perf", "logo", "msg") if st.get(k)]
        status_label.config(text="  |  ".join(parts))
        after(REFRESH_MS, refresh)

    def update_meter() -> None:
        follow_automation()
        state = s.audio.analyzer.latest()
        for name, item in bars.items():
            meter.coords(item, 52, meter.coords(item)[1], 52 + 200 * min(max(state[name], 0.0), 1.0),
                         meter.coords(item)[3])
        bgv_status.set(s.status.get("bgvideo", ""))
        nz_swell, nz_hit = s.status.get("noise", (0.0, 0.0))
        for name, level in (("houle", nz_swell), ("coup", nz_hit)):
            item = noise_bars[name]
            noise_meter.coords(item, 52, noise_meter.coords(item)[1], 52 + 200 * min(max(level, 0.0), 1.0),
                               noise_meter.coords(item)[3])
        after(METER_MS, update_meter)

    def on_close() -> None:
        s.stop_event.set()                # le fil GL (s'il est ouvert) sort ; finished_event : leur refresh() ferme leurs panneaux
        s.finished_event.set()
        root.after(3000, root.destroy)    # filet de securite si le fil GL ne repond plus

    # ---- Mise en page adaptative : cote a cote quand la largeur le permet, sinon LIVE au-dessus de OVERLAY
    def set_layout(name: str) -> None:
        if layout["name"] == name and live_wrap.winfo_manager():
            return
        layout["name"] = name
        for w in (banner_live, banner_overlay, live_wrap, separator, right):
            w.grid_forget()
        for i in range(4):
            page.rowconfigure(i, weight=0)
        for i in range(3):
            page.columnconfigure(i, weight=0)
        if name == "wide":
            banner_live.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=(8, 2))
            banner_overlay.grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=(8, 2))
            live_wrap.grid(row=1, column=0, sticky="nsw", padx=(6, 0))
            separator.grid(row=1, column=1, sticky="ns", padx=ROW_PADX + 4)
            right.grid(row=1, column=2, sticky="nsew", padx=(0, 10))
            page.rowconfigure(1, weight=1)
            page.columnconfigure(2, weight=1)             # la largeur en plus va a l'overlay
        else:
            banner_live.grid(row=0, column=0, columnspan=3, sticky="ew", padx=(6, 10), pady=(8, 2))
            live_wrap.grid(row=1, column=0, columnspan=3, sticky="nw", padx=(6, 0))
            banner_overlay.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(6, 10), pady=(8, 2))
            right.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=(6, 10))
            page.rowconfigure(3, weight=1)
            page.columnconfigure(0, weight=1)
        fit()

    def on_page_config(_e=None) -> None:
        """Page = canvas : largeur du canvas pour le contenu, hauteur au moins celle du contenu (au-dela, ascenseur)."""
        w, h = page_canvas.winfo_width(), page_canvas.winfo_height()
        want_w, want_h = page.winfo_reqwidth(), page.winfo_reqheight()
        # taille voulue de la fenetre tant que l'utilisateur ne l'a pas redimensionnee
        page_canvas.configure(width=want_w, height=min(want_h, root.winfo_screenheight() - 100))
        if w > 50:
            page_canvas.itemconfigure(page_item, width=max(w, want_w if layout["name"] == "stack" else 1), height=max(h, want_h))
            page_canvas.configure(scrollregion=(0, 0, max(w, want_w), max(h, want_h)))
            before = layout["name"]
            # Tant que la fenetre n'a pas pris sa taille (premiers instants), une largeur transitoire plus etroite que la
            # taille voulue ne doit pas faire passer en mise en page empilee.
            set_layout("wide" if (w >= layout["wide_need"] or not layout["settled"]) else "stack")
            if layout["name"] != before:
                root.after_idle(on_page_config)          # les tailles voulues ont change: on recalcule la hauteur de la page
        if want_h > h + 1 and w > 50:
            page_bar.grid(row=1, column=1, sticky="ns")
        else:
            page_bar.grid_remove()
            page_canvas.yview_moveto(0)

    skin.zone(right, OVERLAY_COLOR)
    skin.apply(root)                   # avant de mesurer la largeur naturelle : les images changent la taille des widgets
    set_layout("wide")
    page.update_idletasks()
    # largeur naturelle cote a cote : en dessous, on empile. L'ascenseur du panneau d'audio2wave (17 px) apparait ou disparait
    # selon la hauteur de la fenetre : il ne compte pas, sinon une fenetre a sa taille naturelle SANS ascenseur etait jugee trop
    # etroite et passait en mise en page empilee alors que tout tenait.
    layout["wide_need"] = page.winfo_reqwidth() - (vbar.winfo_reqwidth() if vbar.winfo_ismapped() else 0)
    page.bind("<Configure>", on_page_config)
    page_canvas.bind("<Configure>", on_page_config)
    on_page_config()
    after(400, lambda: (layout.__setitem__("settled", True), on_page_config()))

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    update_meter()
    if mode == "live":
        poll_bridge()
    sync_automation_state()
    if keep_size is not None:
        root.update_idletasks()
        root.geometry(f"{keep_size[0]}x{keep_size[1]}")        # fixe la taille : plus de redimensionnement automatique
    pending = getattr(s, "pending_live_overrides", None)
    if pending:                                  # scene qui a change de mode : on charge son etat dans le panneau neuf
        s.pending_live_overrides = None
        after(150, lambda: apply_live_overrides(pending) or scene_msg.set("panneau audio2wave de la scene introuvable"))
    if on_ready is not None:
        root.after(50, lambda: on_ready({
            "root": root, "live_host": host, "switch": request_mode, "restart_event": restart_event, "live_status": live_status, "close": on_close, "panel": panel, "projection": {"toggle": toggle_projection, "button": lambda: proj_ui["btn"], "send": send},
            "logo_var": logo_var, "video_var": video_var, "apply_logo_path": apply_logo_path, "color_var": color_var, "x_var": x_var,
            "fx_on_vars": fx_on_vars, "fxl_on_vars": fxl_on_vars, "fxl_int_vars": fxl_int_vars,
            "link_var": link_var, "logo_fx_box": logo_fx_box, "holo_var": holo_var, "holo_box": holo_box, "notebook": notebook, "aff_toggle": toggle_aff, "aff_frame": tab_aff, "tabs": (tab_fond, tab_fx, tab_logo, tab_aura, tab_melt, tab_cell, tab_aff, tab_noise), "noise_fx_vars": noise_fx_vars, "melt_var": melt_var, "cell_var": cell_var, "gates": gates, "palette_var": palette_var, "layout": layout, "page_bar": page_bar, "page_canvas": page_canvas,
            "live_wrap": live_wrap, "right_panel": right, "overlay_var": overlay_var,
            "load_overlay_preset": load_overlay_preset, "save_name_var": save_name_var,
            "save_overlay_preset": save_overlay_preset, "update_overlay_preset": update_overlay_preset,
            "overlay_store": overlay_store, "preset_msg": preset_msg, "overlay_menu": overlay_menu,
            "overlay_presets": overlay_presets, "bg_presets": bg_presets, "scene_book": scene_book, "scene_bar": scene_bar,
            "vj": {"state": vj, "toggle": vj_toggle, "advance": vj_advance, "tick": vj_tick, "remaining": vj_remaining},
            "random": {"draw": random_draw, "draw_now": random_draw_now, "keep": random_keep, "save": random_save, "check": random_keep_check, "bank": random_bank,
                       "option": set_random_option, "state": rnd},
            "scenes": {"capture": capture_scene_now, "apply": apply_scene, "apply_entry": apply_entry, "delete": delete_scene_now,
                       "poll": poll_scene_requests, "msg": scene_msg, "start_adding": start_adding, "entry_var": scene_entry_var,
                       "new_from_entry": new_scene_from_entry, "switch_set": switch_set, "create_set": create_set,
                       "rename_set": rename_set_now, "delete_set": delete_set_now, "export": export_set_now,
                       "import": import_set_now, "ui": scene_ui, "expand": set_expanded, "panel": scene_panel, "refresh_panel": refresh_scene_panel, "panel_state": panel_state,
                       "drop": drop_media, "a2w_clear": a2w_clear, "bg_clear": bg_clear, "add_random": add_random_scene, "flush": flush_pending_inputs, "dnd": dnd, "entry_vj": set_entry_vj, "rename_scene": rename_scene_now,
                       "thumb": thumb_photo, "add_entry": add_entry_now, "remove_entry": remove_entry_now,
                       "move_entry": move_entry_now, "media_current": media_from_current, "media_clear": media_clear},
            "fx_int_vars": fx_int_vars, "text_widget_sync": sync_widgets, "master_var": master_var, "bg_var": bg_var, "diag_report": diagnostic_report, "diag_btn": diag_btn, "pattern_box": pattern_box, "bgvid_box": bgvid_box, "bgv_var": bgv_var, "drop_bg_video": drop_bg_video,
            "bg_color1_var": bg_color1_var, "bg_color2_var": bg_color2_var,
            "automation": automation, "auto_master_var": auto_master_var, "auto_vars": auto_vars,
            "source_var": source_var, "text_widget": text_widget, "apply_text": apply_text,
            "image_box": image_box, "text_box": text_box, "font_var": font_var}))


def _safe(var: tk.Variable, default: float) -> float:
    try:
        return float(var.get())
    except (ValueError, tk.TclError):
        return default
