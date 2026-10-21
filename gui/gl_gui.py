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

Tout passe par l'API hote de la copie interne d'audio2wave (`common.API_VERSION` >= 2,
voir `require_audio2wave_api`).

tkinter garde le fil principal ; la fenetre GL (`Projection`, ouverte par le bouton) tourne dans un fil
(comme run() dans les scripts d'audio2wave): toute action qui doit s'executer dans le fil GL passe par
`session.commands`.
"""

from __future__ import annotations

import copy
import os
import json
import random
import sys
import threading
import time
import traceback
import types
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, ttk

import audio2wave_gl as gl
from engine import diagnostics
import skin
import theme
import gui_fond
import gui_effects
import gui_logo
import gui_bar
import gui_palette
import gui_presets
import gui_remote
import gui_stage
import gui_audio
import gui_panel
import gui_random
import gui_drop
import gui_sets
import gui_vj
import gui_colors
import gui_gates
from core import controls as core_controls
from core import params as core_params
from core import safety, scenes, sessionlog, uistats

STATS = uistats.Stats(log=sessionlog.event)           # duree des operations de l'interface et gels du fil Tk (rapport : menu « reglages VJ »)
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
LIVE_MAX_HEIGHT = 600           # hauteur maxi du panneau d'audio2wave avant d'afficher un ascenseur
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
        where = Path(getattr(live, "__file__", ".")).resolve().parent if live is not None else "sources/audio2wave"
        raise SystemExit("Le depot audio2wave est trop ancien pour cette version de casual-overlay : il manque "
                         + " et ".join(missing) + f".\nMets a jour audio2wave ({where}) : {AUDIO2WAVE_REQUIRED}.")


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
    from engine import native_modes

    old = s.mode
    if new == old:
        return None
    if not hasattr(s, "base_reader"):
        s.base_reader = s.reader                  # le flux d'origine (ffmpeg, ou le flux de test en --synthetic) : on y revient en mode Live
    native = native_modes.available() and not os.environ.get("CASUAL_MODES_PYTHON")      # dessin en GL ; CASUAL_MODES_PYTHON=1 : rendus Python d'audio2wave
    if s.manager is None and not native:
        return "Les modes Snap et Ridge ne sont pas disponibles en --synthetic"
    old_src = s.pysrc
    device = (old_src.args.device if old_src is not None else s.live_args.device) or ""
    if new == "ridge" and not device and not (native and s.audio.synthetic):
        return "Ridge a besoin d'une entree audio : choisis-la d'abord dans le mode Live ou Snap"
    src = None
    if new != "live":
        try:
            mod = py_modes.load_mode(Path(live.__file__).resolve().parent, new)
            args = s.mode_args.get(new)
            if args is None:
                args = s.mode_args[new] = py_modes.make_args(mod, new, device, s.render_size)
            args.device = device or None
            if native:
                src = native_modes.NativeModeSource(new, mod, args, s.render_size, s.audio)
            else:
                src = py_modes.PyModeSource(new, mod, args, s.render_size, gl.FrameReader)
            src.start()
        except Exception as exc:
            return f"{new}: demarrage impossible ({exc})"
    if old_src is not None:
        old_src.stop()
    elif old == "live" and s.manager is not None:
        s.manager.suspend()
    s.mode, s.pysrc = new, src
    if new == "live":
        if device:
            s.live_args.device = device
        if s.manager is not None:
            s.reader = s.manager.reader
            resumed = copy.copy(s.live_args)
            resumed.size = f"{s.render_size[0]}x{s.render_size[1]}"
            s.manager.resume(resumed)
        else:
            s.reader = s.base_reader
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
    import audio2wave as a2w                 # (palette d'audio2wave : pas un import de module, le dossier d'audio2wave est deja sur sys.path)
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
    "P : sauver les reglages\n"
    "\n"
    "Mode VJ :\n"
    "X : PANIQUE (coupe effets, strobe, montee,\n"
    "    pause du VJ, image propre)\n"
    "S : strobe (maintenu)\n"
    "K : verrou de scene\n"
    "Retour arriere : scene precedente\n"
    ". (point) : tap tempo\n"
    ", (virgule) : Temps 1 (le temps qui tombe est le 1 de la mesure)\n"
    "/ ou ? : cette aide"
)


class Late:
    """Renvoi tardif d'un objet de `build_window` defini APRES le bloc qui s'en sert (un bloc extrait dans un module `gui_*` recoit ses
    dependances a l'installation) : chaque acces est relu au moment de l'appel, comme le ferait la fermeture d'origine."""

    def __init__(self, get):
        object.__setattr__(self, "_get", get)

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def __getitem__(self, key):
        return self._get()[key]

    def __setitem__(self, key, value):
        self._get()[key] = value

    def __contains__(self, key):
        return key in self._get()

    def __iter__(self):
        return iter(self._get())

    def __len__(self):
        return len(self._get())

    def __call__(self, *a, **k):
        return self._get()(*a, **k)

    def __bool__(self):
        return bool(self._get())

DEVICE_CACHE = {"on": False, "t": 0.0, "value": None}
DEVICE_CACHE_S = 60.0


def _cached_devices(orig):
    def inner():
        now = time.monotonic()
        if DEVICE_CACHE["on"] and DEVICE_CACHE["value"] is not None and now - DEVICE_CACHE["t"] < DEVICE_CACHE_S:
            return list(DEVICE_CACHE["value"])
        value = orig()
        DEVICE_CACHE.update(t=now, value=list(value))
        return value
    inner._cached = True
    return inner


def install_device_cache(modules=None) -> None:
    """La liste des entrees audio (un ffmpeg lance a chaque construction du panneau Snap / Ridge / Live, ~300 ms) est gardee 60 s pendant
    la reconstruction de la fenetre ; hors reconstruction (bouton « actualiser » des panneaux) elle est toujours relue."""
    if modules is None:
        modules = [sys.modules.get(n) for n in ("common", "audio2wave_live", "audio2wave_snap", "audio2wave_ridge")]
    for mod in modules:
        fn = getattr(mod, "list_audio_devices", None) if mod is not None else None
        if fn is not None and not getattr(fn, "_cached", False):
            setattr(mod, "list_audio_devices", _cached_devices(fn))


def grab_window(root):
    """Image de la fenetre de reglages telle qu'elle est (None si impossible ou desactive : CASUAL_NO_COVER=1)."""
    try:
        if os.environ.get("CASUAL_NO_COVER") or not root.winfo_ismapped():
            return None
        from PIL import ImageGrab
        x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
        if w < 50 or h < 50:
            return None
        return ImageGrab.grab(bbox=(x, y, x + w, y + h))
    except Exception:
        return None


def show_cover(root, image):
    """Pose l'ancienne image par-dessus la fenetre pendant qu'elle est reconstruite : l'utilisateur ne voit plus les widgets se
    detruire et se recreer (clignotement), puis `drop_cover` la retire une fois la nouvelle fenetre prete."""
    if image is None:
        return None
    try:
        from PIL import ImageTk
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        w, h = image.size
        top.geometry(f"{w}x{h}+{root.winfo_rootx()}+{root.winfo_rooty()}")
        photo = ImageTk.PhotoImage(image)
        label = tk.Label(top, image=photo, bd=0, highlightthickness=0)
        label.image = photo
        label.pack()
        top.update_idletasks()
        return top
    except Exception:
        return None


def drop_cover(root, cover, delay_ms: int = 350) -> None:
    """Retire l'image figee : apres un court delai, pour que la nouvelle fenetre ait fini de se mettre en page."""
    if cover is None:
        return

    def gone() -> None:
        try:
            cover.destroy()
        except tk.TclError:
            pass
    root.after(delay_ms, gone)


def run_gui(s, live, on_ready=None) -> None:
    """Cree la fenetre Tk, la construit pour le mode courant et bloque dans mainloop() jusqu'a la fin.

    `on_ready(controls)` (tests): appele dans le fil tkinter, une fois la fenetre construite."""
    require_audio2wave_api(live)
    root = tk.Tk()

    def report(exc, val, tb) -> None:                     # une erreur de callback ne doit pas passer inapercue : journal + console
        sessionlog.error("Erreur dans l'interface (callback Tk)", val)
        traceback.print_exception(exc, val, tb)

    root.report_callback_exception = report
    build_window(s, live, root, s.mode, on_ready)
    watch = uistats.StallWatch(period_ms=50.0)

    def stall_watch() -> None:
        late = watch.beat(time.perf_counter())
        if late:
            STATS.stall(late)
        root.after(50, stall_watch)

    root.after(50, stall_watch)
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
                with STATS.timed(getattr(fn, "__name__", "after")):
                    fn()
        root.after(ms, guarded)

    # Un seul ordonnanceur pour tous les rafraichissements periodiques (voyants, mesures, statut...) : une minuterie Tk au lieu
    # d'une par rafraichissement, chaque tache a sa periode, une erreur dans l'une ne tue pas les autres.
    sched = uistats.Scheduler(STATS, on_error=lambda name, exc: None if isinstance(exc, tk.TclError) else sessionlog.error(f"Erreur dans la tache d'interface {name}", exc))   # (TclError : fenetre en cours de fermeture)

    def ui_every(name: str, period_ms: float, fn, first_ms: float = 0.0) -> None:
        sched.add(name, period_ms, fn, first_ms=first_ms, now=time.perf_counter())

    def ui_remove(name: str) -> None:
        sched.remove(name)

    def ui_pump() -> None:
        sched.run_due(time.perf_counter())
        if alive["ok"]:
            try:
                after(int(sched.next_delay_ms(time.perf_counter())), ui_pump)
            except tk.TclError:
                pass

    after(10, ui_pump)

    # Le VJ enchaine des scenes de modes differents (panneaux de largeurs differentes) : la fenetre garde la taille qu'elle a.
    keep_size = None
    if (getattr(s, "vj_state", None) or {}).get("running") and root.winfo_ismapped():
        keep_size = (root.winfo_width(), root.winfo_height())
    install_device_cache()
    DEVICE_CACHE["on"] = True
    snapshot = grab_window(root)
    with STATS.timed("build.destruction"):
        for child in list(root.winfo_children()):
            child.destroy()
    cover = show_cover(root, snapshot)
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

    stage_ref = {"open": lambda: None}          # ouvre le mode scene (rempli en fin de construction, le menu est fait avant)
    controls_ref = {"f": None}          # fabrique du dictionnaire de controles donne a `on_ready` (rempli en fin de construction)

    def request_mode(new_mode: str) -> None:
        """Changement de source (Live / Snap / Ridge) : seul le panneau d'audio2wave (a gauche) est reconstruit ; la barre des scenes,
        la partie OVERLAY, le panneau etendu et l'espace de controle restent en place (plus de reconstruction de toute la fenetre)."""
        sync_automation_now()
        message = activate_mode(s, live, new_mode)
        if message:
            s.status["msg"] = message
        swap_live_panel()

    t_build = time.perf_counter()
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
    mode_ref = {"v": mode}              # mode du panneau affiche (change quand on bascule Live / Snap / Ridge)

    def build_live_panel(m: str) -> None:
        """Construit (ou reconstruit) le panneau d'audio2wave du mode `m` dans `host`."""
        for child in list(host.winfo_children()):
            child.destroy()
        # Un cadre neuf par panneau : ses minuteries (`after` d'audio2wave, liees a ce cadre) sont supprimees avec lui quand on le detruit,
        # sinon la boucle d'un ancien panneau continuerait sur des widgets disparus.
        pf = tk.Frame(host)
        pf.pack(fill="both", expand=True)
        panel["frame"] = pf
        if m == "live":
            panel["ctrl"] = live.build_gui(s.live_args, render_w, render_h, live_status, restart_event, s.stop_event,
                                           s.finished_event, on_switch_mode=on_switch, **PANEL_OPTIONS, parent=pf)
        else:
            src = s.pysrc
            never = threading.Event()     # leur refresh() ferme la fenetre quand cet evenement est positionne: jamais ici
            if m == "snap":
                panel["ctrl"] = src.module.build_gui(src.args, s.render_size, src.status, src.capture_state, src.stop_event, never,
                                                     on_switch_mode=on_switch, **PANEL_OPTIONS, parent=pf)
            else:
                panel["ctrl"] = src.module.build_gui(src.args, s.render_size, src.status, src.stop_event, never,
                                                     on_switch_mode=on_switch, **PANEL_OPTIONS, parent=pf)
        widgets = panel["ctrl"].widgets
        tidy_mode_gui(pf, m, widgets)
        gui_colors.add_color_helpers(pf, a2w.GUI_ACCENT, a2w.GUI_ACCENT_FG, widgets, m)      # pastilles + selecteur sur les champs couleur
        gui_gates.install_mode_gates(pf, m, a2w.GUI_MUTED_FG, widgets)     # grise ce qui n'a pas de sens dans ce mode

    build_live_panel(mode)
    root.title("casual-overlay GL - reglages")
    STATS.record("build.panneau_audio2wave", (time.perf_counter() - t_build) * 1000.0)
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

    def swap_live_panel() -> None:
        """Reconstruit seulement le panneau d'audio2wave pour `s.mode` (ce que faisait `build_window` en entier : ~1,5 s de plus)."""
        t0 = time.perf_counter()
        new_mode = s.mode
        old_w = max(host.winfo_reqwidth(), pattern_holder.winfo_reqwidth())
        keep = None
        if (getattr(s, "vj_state", None) or {}).get("running") and root.winfo_ismapped():
            keep = (root.winfo_width(), root.winfo_height())            # le VJ enchaine des modes : la fenetre garde sa taille
        migrate_scene_presets(live)                                     # scenes d'anciennes versions : leur preset passe dans scenes.json (avant leur panneau)
        mode_ref["v"] = new_mode
        canvas.itemconfigure(host_item, state="hidden")                 # le panneau neuf se construit sans etre peint a moitie
        DEVICE_CACHE["on"] = True
        try:
            build_live_panel(new_mode)
            skin.apply(host)
        finally:
            DEVICE_CACHE["on"] = False
        host.update_idletasks()
        canvas.itemconfigure(host_item, state="hidden" if bg_state["mode"] != "live" else "normal")
        canvas.yview_moveto(0)
        if bg_state["mode"] == "live":
            banner_live.config(text=MODE_BANNERS[new_mode])
        fit()
        new_w = max(host.winfo_reqwidth(), pattern_holder.winfo_reqwidth())
        layout["wide_need"] = max(layout["wide_need"] + new_w - old_w, 0)       # largeur cote a cote : les panneaux n'ont pas la meme largeur
        on_page_config()
        if keep is not None:
            root.update_idletasks()
            root.geometry(f"{keep[0]}x{keep[1]}")
        pending = getattr(s, "pending_live_overrides", None)
        if pending:                                  # scene qui a change de mode : on charge son etat dans le panneau neuf
            s.pending_live_overrides = None
            after(150, lambda: apply_live_overrides(pending) or scene_msg.set("panneau audio2wave de la scene introuvable"))
        STATS.record("mode.panneau", (time.perf_counter() - t0) * 1000.0)
        if on_ready is not None and controls_ref["f"] is not None:
            root.after(50, lambda: on_ready(controls_ref["f"]()))

    def under(widget, container) -> bool:
        if container is None:
            return False
        w, c = str(widget), str(container)
        return w == c or w.startswith(c + ".")

    def on_wheel(e) -> None:
        step = -1 if e.delta > 0 else 1
        pcv = panel_state["cv"]                           # liste des scenes du panneau etendu : elle defile sous la souris
        strip = proj_ui.get("strip")                      # bande des touches de scene (barre du haut) : defile en largeur
        if e.state & 0x1:                                 # Maj + molette : defilement horizontal de ce qui est sous la souris
            if under(e.widget, strip):
                strip.xview_scroll(step * 2, "units")
            elif under(e.widget, pcv):
                pcv.xview_scroll(step * 2, "units")
            return
        if under(e.widget, strip):
            strip.xview_scroll(step * 2, "units")
        elif under(e.widget, pcv):
            pcv.yview_scroll(step, "units")
        elif under(e.widget, canvas) and active_panel().winfo_reqheight() > visible_height() + 1:
            canvas.yview_scroll(step, "units")            # le panneau d'audio2wave defile d'abord...
        elif under(e.widget, right_cv) and right_bar.winfo_ismapped():
            right_cv.yview_scroll(step, "units")          # ...la colonne OVERLAY a son propre ascenseur...
        elif page_bar.winfo_ismapped():
            page_canvas.yview_scroll(step, "units")       # ...sinon c'est la page

    canvas.bind("<Configure>", update_scrollbar)
    host.bind("<Configure>", fit)
    pattern_holder.bind("<Configure>", fit)
    root.bind_all("<MouseWheel>", on_wheel)
    host.update_idletasks()
    fit()

    # Deux grandes parties, bien distinctes : bandeaux de couleur en haut et gros trait vertical entre les deux.
    banner_live = tk.Label(page, text=MODE_BANNERS[mode_ref["v"]])
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
                s.status["msg"] = f"Reglages sauves ({core_params.PARAMS_PATH.name})"
            elif name in ("reload", "hud", "fullscreen"):
                s.status["msg"] = "Disponible quand la projection est ouverte"
            return
        if name == "fullscreen":
            # Le bouton demande un ETAT (comme la case Plein ecran), pas une bascule : sur l'ecran des reglages il est refuse avec un
            # message au lieu de recouvrir la fenetre de reglages (seule la touche F, dans la fenetre de rendu, bascule sans garde).
            window = getattr(s, "window", None)
            name = f"fullscreen={0 if window is not None and window.fullscreen else 1}"
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
        if mode_ref["v"] != "live":
            return
        if restart_event.is_set():
            restart_event.clear()
            push_live()
        if manager is not None:
            msg = manager.status.get("live")
            if msg and msg != seen_live["text"]:
                seen_live["text"] = msg
                live_status["text"] = msg            # affiche dans la ligne de statut d'audio2wave

    # ------------------------------------------------------ 3. nos panneaux, a droite
    separator = tk.Frame(page, bg=OVERLAY_COLOR, width=3)       # frontiere LIVE | OVERLAY (mise en page cote a cote)
    # Partie OVERLAY: les presets restent toujours visibles en haut, le reste est range en ONGLETS (Effets,
    # Logo, Aura...). Une seule colonne de reglages a la fois: la fenetre fait environ la moitie de la
    # hauteur qu'avec des colonnes empilees (qui depassait la hauteur d'un ecran des qu'un bloc s'ouvrait).
    # La colonne OVERLAY a SON ascenseur (canvas defilant) : a cote de LIVE, l'ascenseur de droite ne defile que l'overlay.
    right_wrap = tk.Frame(page)
    right_cv = tk.Canvas(right_wrap, highlightthickness=0, bd=0, width=10, height=10, bg=a2w.GUI_BG, yscrollincrement=24)
    right_bar = skin.scrollbar(right_wrap, command=right_cv.yview)
    right_cv.configure(yscrollcommand=right_bar.set)
    right_cv.pack(side="left", fill="both", expand=True)
    right = tk.Frame(right_cv)
    right_item = right_cv.create_window((0, 0), window=right, anchor="nw")

    def right_fit(_e=None) -> None:
        """Taille voulue du canvas = celle de la colonne (plafonnee a cote de LIVE) ; ascenseur seulement si elle est plus haute que la place."""
        w, h = right.winfo_reqwidth(), right.winfo_reqheight()
        vis_w, vis_h = right_cv.winfo_width(), right_cv.winfo_height()
        right_cv.configure(width=w, height=min(h, cap_now()), scrollregion=(0, 0, max(w, vis_w), h))
        right_cv.itemconfigure(right_item, width=max(w, vis_w), height=max(h, vis_h if vis_h > 50 else 0))
        if h > vis_h + 1 and vis_h > 50:
            if not right_bar.winfo_ismapped():
                right_bar.pack(side="right", fill="y")
        elif right_bar.winfo_ismapped():
            right_bar.pack_forget()
            right_cv.yview_moveto(0)

    right.bind("<Configure>", right_fit)
    right_cv.bind("<Configure>", right_fit)
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
    # « Affichage et analyse » (entree d'analyse, plein ecran, diagnostic, mesures...) est un reglage de la projection, pas de l'overlay :
    # onglet « Affichage » de l'espace de controle global du Mode VJ. Le contenu est construit ici (parent persistant : la barre) puis
    # pose dans l'onglet a sa premiere selection (`pack(in_=...)`, comme le panneau lui-meme).
    tab_aff = tk.Frame(scene_top, bg=a2w.GUI_PANEL_BG)

    def toggle_aff(flag: bool | None = None) -> None:
        """Ouvre le panneau etendu sur l'onglet Affichage (True / bascule) ou le referme (False)."""
        tabs = vj_ui.get("tabs")
        shown = bool(tab_aff.winfo_ismapped())
        want = (not shown) if flag is None else bool(flag)
        if not want:
            set_expanded(False)
            return
        set_expanded(True)
        master_box()
        tabs = vj_ui.get("tabs")
        if tabs is None:
            return
        nb = tabs[0]
        for t in nb.tabs():
            if nb.tab(t, "text") == "Affichage":
                nb.select(t)
                break
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
    gui_presets_ctl = gui_presets.install(types.SimpleNamespace(
        ROW_PADX=ROW_PADX,
        ROW_PADY=ROW_PADY,
        Tooltip=Tooltip,
        a2w=a2w,
        add_section_title=add_section_title,
        automation=automation,
        live=live,
        params=params,
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        root=root,
        scene_book=Late(lambda: scene_book),
        scene_ui=Late(lambda: scene_ui),
        sync_automation_now=lambda *a, **k: sync_automation_now(*a, **k),
        sync_widgets=lambda *a, **k: sync_widgets(*a, **k),
        ui_every=ui_every))
    make_preset_bar = gui_presets_ctl.make_preset_bar

    overlay_presets = make_preset_bar(preset_bar, "Presets overlay", gl.OVERLAY_BOOK, gl.capture_overlay,
                                      gl.overlay_preset_values,
                                      "Un preset = le look du logo et des effets (le fond a ses propres presets).")
    overlay_store, overlay_var, preset_msg = overlay_presets["book"], overlay_presets["var"], overlay_presets["msg"]
    overlay_menu, save_name_var = overlay_presets["menu"], overlay_presets["save_var"]
    gui_fond_ctl = gui_fond.install(types.SimpleNamespace(
        ROW_PADX=ROW_PADX,
        ROW_PADY=ROW_PADY,
        Tooltip=Tooltip,
        a2w=a2w,
        add_label=add_label,
        add_section_title=add_section_title,
        banner_live=banner_live,
        bg_state=bg_state,
        bind_param=bind_param,
        canvas=canvas,
        color_row=color_row,
        fit=fit,
        host_item=host_item,
        make_preset_bar=make_preset_bar,
        mode_ref=mode_ref,
        overlay_presets=overlay_presets,
        param_radio=param_radio,
        param_slider=param_slider,
        params=params,
        pattern_holder=pattern_holder,
        pattern_item=pattern_item,
        tab_fond=tab_fond,
        zone_f=zone_f))
    apply_bgv = gui_fond_ctl.apply_bgv
    bg_color1_var = gui_fond_ctl.bg_color1_var
    bg_color2_var = gui_fond_ctl.bg_color2_var
    bg_presets = gui_fond_ctl.bg_presets
    bg_var = gui_fond_ctl.bg_var
    bgv_status = gui_fond_ctl.bgv_status
    bgv_var = gui_fond_ctl.bgv_var
    bgvid_box = gui_fond_ctl.bgvid_box
    load_overlay_preset = gui_fond_ctl.load_overlay_preset
    pattern_box = gui_fond_ctl.pattern_box
    save_overlay_preset = gui_fond_ctl.save_overlay_preset
    update_overlay_preset = gui_fond_ctl.update_overlay_preset
    cap = max(360, cap - tab_fond.winfo_reqheight())     # le choix du fond prend de la place au-dessus du panneau
    fit()

    # ---- colonne A : EFFETS
    gui_effects_ctl = gui_effects.install(types.SimpleNamespace(
        ROW_PADX=ROW_PADX,
        ROW_PADY=ROW_PADY,
        SECTION_GAP=SECTION_GAP,
        Tooltip=Tooltip,
        a2w=a2w,
        add_auto=add_auto,
        add_separator=add_separator,
        add_slider=add_slider,
        bind_param=bind_param,
        color_row=color_row,
        compact_scale=compact_scale,
        param_slider=param_slider,
        params=params,
        tab_aura=tab_aura,
        tab_cell=tab_cell,
        tab_fx=tab_fx,
        tab_melt=tab_melt,
        tab_noise=tab_noise,
        zone_aura=zone_aura,
        zone_cell=zone_cell,
        zone_fx=zone_fx,
        zone_melt=zone_melt,
        zone_noise=zone_noise))
    cell_row = gui_effects_ctl.cell_row
    cell_var = gui_effects_ctl.cell_var
    fx_int_vars = gui_effects_ctl.fx_int_vars
    fx_on_vars = gui_effects_ctl.fx_on_vars
    fxl_int_vars = gui_effects_ctl.fxl_int_vars
    fxl_on_vars = gui_effects_ctl.fxl_on_vars
    holo_box = gui_effects_ctl.holo_box
    holo_var = gui_effects_ctl.holo_var
    link_var = gui_effects_ctl.link_var
    logo_fx_box = gui_effects_ctl.logo_fx_box
    master_var = gui_effects_ctl.master_var
    melt_row = gui_effects_ctl.melt_row
    melt_var = gui_effects_ctl.melt_var
    noise_bars = gui_effects_ctl.noise_bars
    noise_fx_vars = gui_effects_ctl.noise_fx_vars
    noise_meter = gui_effects_ctl.noise_meter
    sens_var = gui_effects_ctl.sens_var
    tk.Label(tab_noise, text="Indicateur : ce que le noise envoie aux effets coches.", fg=a2w.GUI_MUTED_FG,
             anchor="w").grid(row=zone_noise.next_row(), column=0, columnspan=3, sticky="w", padx=ROW_PADX)

    # ---- colonne B : LOGO
    gui_logo_ctl = gui_logo.install(types.SimpleNamespace(
        ROW_PADX=ROW_PADX,
        ROW_PADY=ROW_PADY,
        Tooltip=Tooltip,
        a2w=a2w,
        add_label=add_label,
        add_section_title=add_section_title,
        add_separator=add_separator,
        add_slider=add_slider,
        bind_param=bind_param,
        color_row=color_row,
        param_radio=param_radio,
        param_slider=param_slider,
        params=params,
        tab_logo=tab_logo,
        ui_every=ui_every,
        zone_aura=zone_aura,
        zone_logo=zone_logo))
    apply_logo_path = gui_logo_ctl.apply_logo_path
    apply_text = gui_logo_ctl.apply_text
    apply_video_path = gui_logo_ctl.apply_video_path
    font_var = gui_logo_ctl.font_var
    image_box = gui_logo_ctl.image_box
    key_var = gui_logo_ctl.key_var
    logo_var = gui_logo_ctl.logo_var
    source_var = gui_logo_ctl.source_var
    text_box = gui_logo_ctl.text_box
    text_widget = gui_logo_ctl.text_widget
    video_var = gui_logo_ctl.video_var
    x_var = gui_logo_ctl.x_var
    color_var = color_row(zone_aura, "Couleur du contour", "logo_glow_color")

    # ---- colonne B : ANALYSE AUDIO + AFFICHAGE
    gui_audio_ctl = gui_audio.install(types.SimpleNamespace(
        ROW_PADX=ROW_PADX,
        ROW_PADY=ROW_PADY,
        SECTION_GAP=SECTION_GAP,
        Tooltip=Tooltip,
        a2w=a2w,
        add_label=add_label,
        add_separator=add_separator,
        automation=automation,
        bind_param=bind_param,
        params=params,
        root=root,
        s=s,
        send=send,
        tab_aff=tab_aff,
        zone_aff=zone_aff))
    auto_master_var = gui_audio_ctl.auto_master_var
    remote_box = gui_audio_ctl.remote_box
    bars = gui_audio_ctl.bars
    diag_btn = gui_audio_ctl.diag_btn
    diagnostic_report = gui_audio_ctl.diagnostic_report
    meter = gui_audio_ctl.meter
    status_label = gui_audio_ctl.status_label

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
        shown = {"overlay": overlay_var.get(), "fond": bg_presets["var"].get()}
        scene = scenes.capture_scene(name, params, s.mode, overrides, presets=shown)
        scene_book.put(scene)                          # nouvelle : bibliotheque + fin du set actif ; sinon mise a jour partout
        s.active_scene = scene["name"]
        s.active_entry = current_index(scene_book.rows())
        scene_msg.set(f"Scene '{name}' enregistree" + note)
        refresh_scene_bar()
        panel_ops.request_preview(scene["name"])      # apercu = ce qu'on voit a l'ecran au moment de l'enregistrement

    # ---- Mode VJ : enchaine les scenes du set actif toutes les N secondes, avec un flash court a chaque changement.
    vj = getattr(s, "vj_state", None)
    if vj is None:
        vj = s.vj_state = {"running": False, "t_next": 0.0, "bar": None}
    vj_ui: dict = {}
    vj_auto: dict = {"on": False}                   # vrai pendant que le VJ (pas la main de l'operateur) pose une scene : securite GPU

    def beat_count() -> int:
        """Kicks detectes depuis le lancement (compteur croissant de l'analyse audio) ; 0 si l'analyse n'est pas disponible."""
        try:
            return int(s.audio.analyzer.latest().get("beats", 0))
        except Exception:
            return 0

    def rhythm_source():
        """BPM et drop detectes par l'analyse audio (`core.rhythm.Rhythm`), ou None si l'analyse n'est pas disponible."""
        try:
            return s.audio.analyzer.rhythm
        except Exception:
            return None

    def vj_arm(own: float | None = None) -> None:
        """Arme le minuteur du prochain changement : N secondes, ou N mesures de kicks si le set est « en rythme ». Une duree propre
        a l'entree (`own`, en secondes) l'emporte et reste en secondes."""
        st = scene_book.vj()
        now = time.monotonic()
        vj["grid_due"] = None
        grid = getattr(rhythm_source(), "grid", None)
        if st["unit"] == "bars" and not own and grid is not None and grid.active:
            # grille du temps donnee (« Temps 1 ») : N mesures plus tard, sur le 1 le plus proche (la duree reste ~N x 4 temps)
            due = grid.nearest_downbeat(now + st["bars"] * grid.BEATS_PER_BAR * grid.period)
            vj["bar"] = None
            vj["grid_due"] = due
            vj["t_next"] = due + 2.0 * grid.BEATS_PER_BAR * grid.period       # garde-fou si la grille se perd en route
        elif st["unit"] == "bars" and not own:
            rhythm = rhythm_source()
            vj["bar"] = core_controls.BarTimer(st["bars"]).arm(beat_count(), now, float(params.get("tempo", 1.0)),
                                                               getattr(rhythm, "bpm", None))
            vj["t_next"] = vj["bar"].deadline                 # garde-fou si les kicks ne viennent pas
        else:
            vj["bar"] = None
            vj["t_next"] = now + (own or st["seconds"])

    def vj_due() -> bool:
        now = time.monotonic()
        bar = vj.get("bar")
        due = vj.get("grid_due")
        if due is not None:
            return now >= due or now >= vj["t_next"]
        return now >= vj["t_next"] or (bar is not None and bar.due(beat_count(), now))

    def vj_period_text() -> str:
        st = scene_book.vj()
        return f"{st['bars']} mesure(s) (4 kicks chacune)" if st["unit"] == "bars" else f"{st['seconds']:.0f} s"

    def vj_remaining() -> float:
        return max(vj["t_next"] - time.monotonic(), 0.0)

    def vj_remaining_text() -> str:
        """Ce qui reste avant la scene suivante : « 12 s », ou « mesure 3/8 » en rythme."""
        bar = vj.get("bar")
        if vj.get("grid_due") is not None:
            return f"sur le 1 dans {max(vj['grid_due'] - time.monotonic(), 0.0):.0f} s"
        return f"mesure {bar.label(beat_count())}" if bar is not None else f"{vj_remaining():.0f} s"

    def update_vj_button() -> None:
        btn = vj_ui.get("toggle")
        if btn is None:
            return
        try:
            if vj["running"]:
                btn.config(text=f"\u25a0 VJ  {vj_remaining_text()}", bg=SCENE_COLOR, fg=BANNER_FG)
            else:
                btn.config(text="\u25b6 VJ", bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        except tk.TclError:
            vj_ui.clear()

    panel_state: dict = {"sig": None, "rows": {}, "cv": None, "summary": None, "dur": {}, "name_w": 18}     # etat du panneau etendu (partage avec le Full Randomizer)

    # ---- Full Randomizer : une scene temporaire tiree dans les presets, la banque d'assets et les valeurs aleatoires
    gui_random_ctl = gui_random.install(types.SimpleNamespace(
        after=after,
        apply_scene_dict=lambda *a, **k: apply_scene_dict(*a, **k),
        bg_presets=bg_presets,
        capture_live_overrides=capture_live_overrides,
        current_index=current_index,
        live=live,
        overlay_presets=overlay_presets,
        panel_state=panel_state,
        params=params,
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        rename_scene_dialog=Late(lambda: rename_scene_dialog),
        s=s,
        save_live_preset=save_live_preset,
        scene_book=scene_book,
        scene_locked=lambda *a, **k: scene_locked(*a, **k),
        scene_msg=scene_msg,
        scene_ui=Late(lambda: scene_ui),
        sync_automation_now=sync_automation_now,
        vj=vj,
        vj_arm=vj_arm))
    add_random_scene = gui_random_ctl.add_random_scene
    add_random_scenes = gui_random_ctl.add_random_scenes
    duplicate_last_scene = gui_random_ctl.duplicate_last_scene
    random_bank = gui_random_ctl.random_bank
    random_draw = gui_random_ctl.random_draw
    random_draw_now = gui_random_ctl.random_draw_now
    random_keep = gui_random_ctl.random_keep
    random_keep_check = gui_random_ctl.random_keep_check
    random_save = gui_random_ctl.random_save
    rnd = gui_random_ctl.rnd
    set_random_option = gui_random_ctl.set_random_option

    def vj_advance() -> None:
        """Entree suivante du set (ordre choisi) + flash, et le minuteur repart. Ordre `alea` : une scene tiree au hasard."""
        if scene_locked():
            return
        rows = scene_book.rows()
        if scene_book.vj()["order"] == "alea":
            s.commands.put("flash")
            vj_auto["on"] = True
            try:
                random_draw()
            finally:
                vj_auto["on"] = False
            vj_arm()
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
        vj_auto["on"] = True
        try:
            apply_entry_core(index)
        finally:
            vj_auto["on"] = False
        vj_arm(rows[index]["seconds"])                     # duree propre a l'entree (en secondes) sinon celle du set
        update_vj_button()

    def scene_locked(say: bool = True) -> bool:
        """Verrou de scene : la scene courante est gelee (ni VJ, ni touche, ni F1..F9, ni tirage) ; les masters restent actifs."""
        locked = bool(getattr(s, "scene_lock", False))
        if locked and say:
            scene_msg.set("Scene verrouillee (bouton Verrou ou touche K pour deverrouiller)")
        return locked

    def update_lock_button() -> None:
        btn = vj_ui.get("lock")
        if btn is None:
            return
        try:
            on = bool(getattr(s, "scene_lock", False))
            btn.config(text="Verrou ●" if on else "Verrou", bg=SCENE_COLOR if on else a2w.GUI_BG, fg=BANNER_FG if on else a2w.GUI_FG)
        except tk.TclError:
            vj_ui.pop("lock", None)

    def toggle_scene_lock() -> None:
        """Bouton Verrou / touche K : gele la scene (le VJ se met en pause) ; deverrouiller relance le VJ s'il tournait avant."""
        sessionlog.event("Verrou de scene : " + ("retire" if getattr(s, "scene_lock", False) else "pose"))
        if getattr(s, "scene_lock", False):
            s.scene_lock = False
            if getattr(s, "lock_resume", False):
                vj["running"] = True
                vj_arm()
            s.lock_resume = False
            scene_msg.set("Scene deverrouillee" + (" : VJ relance" if vj["running"] else ""))
        else:
            s.lock_resume = bool(vj["running"])
            vj["running"] = False
            s.scene_lock = True
            scene_msg.set("Scene verrouillee : le VJ est en pause, les masters restent actifs")
        update_vj_button()
        update_lock_button()
        refresh_scene_bar()                                 # l'indicateur « Verrou » de la barre apparait / disparait

    def vj_toggle() -> None:
        if not vj["running"] and scene_locked():
            return
        sessionlog.event("VJ : " + ("pause" if vj["running"] else "lecture"))
        if vj["running"]:
            vj["running"] = False
            scene_msg.set("VJ en pause")
        elif scene_book.vj()["order"] != "alea" and sum(1 for r in scene_book.rows() if not r["skip"]) < 2:
            scene_msg.set("Il faut au moins 2 scenes actives dans le set pour enchainer")
        else:
            vj["running"] = True
            vj_arm()
            scene_msg.set(f"VJ: une scene toutes les {vj_period_text()}"
                          + (" (Full Randomizer)" if scene_book.vj()["order"] == "alea" else ""))
        update_vj_button()

    def vj_tick() -> None:
        """Minuteur du VJ (250 ms). Chaque etape est isolee et la suivante est TOUJOURS programmee : une erreur (affichee sur la console)
        ne doit jamais tuer le minuteur, sinon Play ne ferait plus rien et le VJ ne passerait plus jamais a la scene suivante."""
        try:
            if vj["running"] and vj_due():
                vj_advance()
            elif vj["running"] and vj.get("grid_due") is not None and not vj.get("grid_job"):
                wait = vj["grid_due"] - time.monotonic()
                if 0.0 < wait <= 0.3:                      # le minuteur bat toutes les 250 ms : le dernier bout est programme au plus juste
                    vj["grid_job"] = True

                    def on_downbeat() -> None:
                        vj["grid_job"] = False
                        if vj["running"] and vj_due():
                            vj_advance()
                    after(max(int(wait * 1000), 1), on_downbeat)
        except Exception:
            traceback.print_exc()
            vj["t_next"] = time.monotonic() + max(float(scene_book.vj()["seconds"]), 5.0)     # pas de boucle d'erreurs a 4 Hz
        try:
            if getattr(s, "thumb_dirty", False) and time.monotonic() - panel_state.get("thumb_at", 0.0) >= 0.8:
                s.thumb_dirty = False                      # une vignette est prete (calculee dans un fil) : au plus un rafraichissement
                panel_state["thumb_at"] = time.monotonic() # toutes les 0,8 s, les vignettes arrivent en rafale
                refresh_scene_panel()
            update_vj_button()
            update_master_panel()
        except Exception:
            traceback.print_exc()
        finally:
            after(250, vj_tick)

    def apply_scene(name: str, index: int | None = None) -> None:
        """Choix a la main (par nom, ou par rang dans le set) : met le VJ en pause (on ne se bat pas avec le minuteur)."""
        if scene_locked():
            return
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

    def show_scene_presets(scene: dict) -> None:
        """Reaffiche dans les menus de presets overlay / fond ceux que la scene avait choisis, s'ils existent encore (les reglages
        eux-memes viennent de la scene, pas du preset : un preset modifie depuis ne la change pas)."""
        wanted = scenes.clean_presets(scene.get("presets"))
        for key, bar in (("overlay", overlay_presets), ("fond", bg_presets)):
            name = wanted.get(key)
            if name and name in bar["book"].names():
                bar["select"](name)

    def apply_scene_dict(scene: dict, media: dict | None = None, index: int | None = None, a2w: dict | None = None,
                         bgv: str | None = None) -> None:
        """Applique une scene (de la bibliotheque, ou temporaire : tirage du VJ aleatoire). `bgv` : video de fond propre a l'entree."""
        record_history(scene, media, index, a2w, bgv)
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
        if vj_auto["on"] and float(params.get("gpu_cap", 1.0)) >= 0.5:
            # securite GPU : la scene posee par le VJ est allegee si, avec ses effets, l'image ne tiendrait plus dans le budget
            values, lightened = safety.cap_values(values, params, float(s.status.get("gpu_ms", 0.0)), float(s.status.get("gpu_budget_ms", 16.7)),
                                                  getattr(s, "render_size", None))
            if lightened:
                note += f" (allegee pour le GPU : {', '.join(lightened)} coupe(s))"
                sessionlog.event(f"Securite GPU : scene '{scene.get('name')}' allegee ({', '.join(lightened)})")
        apply_values(values)
        show_scene_presets(scene)
        s.active_scene = scene["name"]
        s.active_entry = index
        try:
            panel_ops.schedule_preview(scene["name"])      # premier passage de la scene sans apercu : capture de la projection
        except NameError:
            pass
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

    def move_entry_to(index: int, target: int) -> None:
        """Glisser-deposer d'une ligne du panneau : l'entree `index` prend le rang `target`."""
        move_entry_now(index, target - index)

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
            if index == "lock":
                toggle_scene_lock()
                continue
            if index == "back":
                back_scene()
                continue
            if index == "panic":
                do_panic(from_gl=True)
                continue
            if index == "tap":
                do_tap()
                continue
            if index == "one":
                do_one()
                continue
            if index == "help":
                scene_ui["help"]()
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
    gui_sets_ctl = gui_sets.install(types.SimpleNamespace(
        apply_entry=apply_entry,
        ask_delete_scene=ask_delete_scene,
        capture_scene_now=capture_scene_now,
        live=live,
        media_clear=media_clear,
        media_from_current=media_from_current,
        move_entry_now=move_entry_now,
        move_entry_to=move_entry_to,
        panel_state=panel_state,
        refresh_presets=lambda: (overlay_presets["refresh"](), bg_presets["refresh"]()),
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        remove_entry_now=remove_entry_now,
        request_preview=lambda name: panel_ops.request_preview(name),
        root=root,
        s=s,
        scene_book=scene_book,
        scene_msg=scene_msg,
        scene_state=scene_state,
        vj=vj,
        vj_advance=vj_advance))
    ask_delete_set = gui_sets_ctl.ask_delete_set
    undo_now = gui_sets_ctl.undo_now
    redo_now = gui_sets_ctl.redo_now
    create_set = gui_sets_ctl.create_set
    delete_set_now = gui_sets_ctl.delete_set_now
    export_set_now = gui_sets_ctl.export_set_now
    fill_entry_menu = gui_sets_ctl.fill_entry_menu
    import_set_now = gui_sets_ctl.import_set_now
    new_set_dialog = gui_sets_ctl.new_set_dialog
    rename_set_dialog = gui_sets_ctl.rename_set_dialog
    rename_set_now = gui_sets_ctl.rename_set_now
    scene_ui = gui_sets_ctl.scene_ui
    switch_set = gui_sets_ctl.switch_set

    # ---- Glisser-deposer d'un fichier sur une scene (touche de la barre ou ligne du panneau etendu) : tkdnd via tkinterdnd2
    # (facultatif : sans lui, tout marche sauf le glisser-deposer).
    gui_drop_ctl = gui_drop.install(types.SimpleNamespace(
        apply_bgv=apply_bgv,
        apply_entry_core=apply_entry_core,
        bg_var=bg_var,
        bgv_var=bgv_var,
        bgvid_box=bgvid_box,
        current_index=current_index,
        panel_update_in_place=lambda *a, **k: panel_update_in_place(*a, **k),
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        root=root,
        s=s,
        scene_book=scene_book,
        scene_msg=scene_msg,
        scene_ui=scene_ui))
    a2w_clear = gui_drop_ctl.a2w_clear
    bg_clear = gui_drop_ctl.bg_clear
    dnd = gui_drop_ctl.dnd
    drop_bg_video = gui_drop_ctl.drop_bg_video
    drop_media = gui_drop_ctl.drop_media
    register_drop = gui_drop_ctl.register_drop
    register_tree = gui_drop_ctl.register_tree
    rename_scene_dialog = gui_drop_ctl.rename_scene_dialog
    rename_scene_now = gui_drop_ctl.rename_scene_now
    set_entry_vj = gui_drop_ctl.set_entry_vj
    set_vj_option = gui_drop_ctl.set_vj_option
    thumb_photo = gui_drop_ctl.thumb_photo

    # ---- Panneau etendu : le set en detail (media lie, duree et participation au VJ de chaque entree, ordre, renommer)
    gui_panel_ctl = gui_panel.install(types.SimpleNamespace(
        Tooltip=Tooltip,
        a2w=a2w,
        tab_aff=tab_aff,
        a2w_clear=a2w_clear,
        add_random_scene=add_random_scene,
        add_random_scenes=add_random_scenes,
        apply_entry=apply_entry,
        apply_scene=apply_scene,
        bg_clear=bg_clear,
        current_index=current_index,
        dnd=dnd,
        duplicate_last_scene=duplicate_last_scene,
        master_box=Late(lambda: master_box),
        media_clear=media_clear,
        move_entry_now=move_entry_now,
        move_entry_to=move_entry_to,
        panel_state=panel_state,
        redo_now=lambda: redo_now(),
        undo_now=lambda: undo_now(),
        random_bank=random_bank,
        random_draw_now=random_draw_now,
        random_keep=random_keep,
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        register_drop=register_drop,
        register_tree=register_tree,
        remove_entry_now=remove_entry_now,
        rename_scene_dialog=rename_scene_dialog,
        ask_delete_scene=ask_delete_scene,
        rnd=rnd,
        root=root,
        s=s,
        scene_book=scene_book,
        scene_msg=scene_msg,
        scene_panel=scene_panel,
        scene_ui=scene_ui,
        set_entry_vj=set_entry_vj,
        set_random_option=set_random_option,
        set_vj_option=set_vj_option,
        thumb_photo=thumb_photo))
    panel_update_in_place = gui_panel_ctl.panel_update_in_place
    refresh_scene_panel = STATS.watched("refresh_scene_panel")(gui_panel_ctl.refresh_scene_panel)
    set_expanded = gui_panel_ctl.set_expanded
    panel_ops = gui_panel_ctl.ops

    def proj_text() -> str:
        return "Arreter la projection" if projection is not None and projection.running() else "Demarrer la projection"

    def toggle_projection() -> None:
        if projection is None:
            return
        now = time.monotonic()
        if now - proj_ui.get("t", 0.0) < 0.6:          # double declenchement (double clic, Espace sur le bouton) : ignore
            return
        proj_ui["t"] = now
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

    master_book = scenes.MasterBook(lambda: scenes.MASTERS_PATH)
    history_ui: dict = {"replay": False, "list": None}
    gui_vj_ctl = gui_vj.install(types.SimpleNamespace(
        Tooltip=Tooltip,
        a2w=a2w,
        after=after,
        ui_every=ui_every,
        ui_remove=ui_remove,
        apply_scene_dict=apply_scene_dict,
        history_ui=history_ui,
        master_book=master_book,
        params=params,
        projection=projection,
        refresh_scene_bar=lambda *a, **k: refresh_scene_bar(*a, **k),
        rhythm_source=rhythm_source,
        root=root,
        s=s,
        scene_book=scene_book,
        scene_locked=scene_locked,
        scene_msg=scene_msg,
        scene_top=scene_top,
        scene_ui=scene_ui,
        sync_widgets=sync_widgets,
        tab_aff=tab_aff,
        toggle_scene_lock=toggle_scene_lock,
        update_lock_button=update_lock_button,
        update_vj_button=update_vj_button,
        vj=vj,
        vj_advance=vj_advance,
        vj_remaining=vj_remaining,
        vj_remaining_text=vj_remaining_text,
        vj_toggle=vj_toggle,
        vj_ui=vj_ui))
    autosave_tick = gui_vj_ctl.autosave_tick
    back_scene = gui_vj_ctl.back_scene
    console_window = gui_vj_ctl.console_window
    do_drop = gui_vj_ctl.do_drop
    do_panic = gui_vj_ctl.do_panic
    do_tap = gui_vj_ctl.do_tap
    do_one = gui_vj_ctl.do_one
    vj_voyants = gui_vj_ctl.voyants
    sync_master_panel = gui_vj_ctl.sync_master_panel
    detected_bpm = gui_vj_ctl.detected_bpm
    help_window = gui_vj_ctl.help_window
    master_box = gui_vj_ctl.master_box
    record_history = gui_vj_ctl.record_history
    start_build = gui_vj_ctl.start_build
    rhythm_step = gui_vj_ctl.rhythm_step
    status_text = gui_vj_ctl.status_text
    update_master_panel = gui_vj_ctl.update_master_panel

    gui_bar_ctl = gui_bar.install(types.SimpleNamespace(
        Tooltip=Tooltip,
        a2w=a2w,
        add_entry_now=add_entry_now,
        after=after,
        apply_entry=apply_entry,
        ask_delete_set=ask_delete_set,
        cancel_adding=cancel_adding,
        console_window=console_window,
        current_index=current_index,
        do_panic=do_panic,
        export_set_now=export_set_now,
        fill_entry_menu=fill_entry_menu,
        help_window=help_window,
        import_set_now=import_set_now,
        new_scene_from_entry=new_scene_from_entry,
        new_set_dialog=new_set_dialog,
        proj_text=proj_text,
        proj_ui=proj_ui,
        projection=projection,
        redo_now=redo_now,
        refresh_scene_panel=refresh_scene_panel,
        register_drop=register_drop,
        rename_set_dialog=rename_set_dialog,
        root=root,
        s=s,
        scene_bar=scene_bar,
        scene_book=scene_book,
        scene_entry_var=scene_entry_var,
        scene_msg=scene_msg,
        scene_state=scene_state,
        scene_top=scene_top,
        set_expanded=set_expanded,
        set_vj_option=set_vj_option,
        stage_ref=stage_ref,
        start_adding=start_adding,
        switch_set=switch_set,
        toggle_projection=toggle_projection,
        toggle_scene_lock=toggle_scene_lock,
        undo_now=undo_now,
        update_vj_button=update_vj_button,
        vj_advance=vj_advance,
        vj_toggle=vj_toggle,
        vj_ui=vj_ui))
    bar_signature = gui_bar_ctl.bar_signature
    refresh_scene_bar = gui_bar_ctl.refresh_scene_bar

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
        parts = [f"{st.get('fps', 0):.0f} fps"] + [st[k] for k in ("perf", "logo", "doodles", "msg") if st.get(k)]
        status_label.config(text="  |  ".join(parts))

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

    def on_close() -> None:
        s.stop_event.set()                # le fil GL (s'il est ouvert) sort ; finished_event : leur refresh() ferme leurs panneaux
        s.finished_event.set()
        root.after(3000, root.destroy)    # filet de securite si le fil GL ne repond plus

    # ---- Mise en page adaptative : cote a cote quand la largeur le permet, sinon LIVE au-dessus de OVERLAY
    def set_layout(name: str) -> None:
        if layout["name"] == name and live_wrap.winfo_manager():
            return
        layout["name"] = name
        for w in (banner_live, banner_overlay, live_wrap, separator, right_wrap):
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
            right_wrap.grid(row=1, column=2, sticky="nsew", padx=(0, 10))
            page.rowconfigure(1, weight=1)
            page.columnconfigure(2, weight=1)             # la largeur en plus va a l'overlay
        else:
            banner_live.grid(row=0, column=0, columnspan=3, sticky="ew", padx=(6, 10), pady=(8, 2))
            live_wrap.grid(row=1, column=0, columnspan=3, sticky="nw", padx=(6, 0))
            banner_overlay.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(6, 10), pady=(8, 2))
            right_wrap.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=(6, 10))
            page.rowconfigure(3, weight=1)
            page.columnconfigure(0, weight=1)
        fit()
        right_fit()

    def on_page_config(_e=None) -> None:
        """Page = canvas : largeur du canvas pour le contenu, hauteur au moins celle du contenu (au-dela, ascenseur)."""
        w, h = page_canvas.winfo_width(), page_canvas.winfo_height()
        want_w, want_h = page.winfo_reqwidth(), page.winfo_reqheight()
        # taille voulue de la fenetre tant que l'utilisateur ne l'a pas redimensionnee
        page_canvas.configure(width=want_w, height=min(want_h, root.winfo_screenheight() - 100))
        if w > 50:
            # Cote a cote, la page ne defile pas : LIVE et OVERLAY ont chacun leur ascenseur et se partagent la hauteur de la fenetre.
            # Empilee, c'est la page qui defile.
            fixed_h = layout["name"] == "wide"
            page_canvas.itemconfigure(page_item, width=max(w, want_w if layout["name"] == "stack" else 1), height=h if fixed_h else max(h, want_h))
            page_canvas.configure(scrollregion=(0, 0, max(w, want_w), h if fixed_h else max(h, want_h)))
            before = layout["name"]
            # Tant que la fenetre n'a pas pris sa taille (premiers instants), une largeur transitoire plus etroite que la
            # taille voulue ne doit pas faire passer en mise en page empilee.
            set_layout("wide" if (w >= layout["wide_need"] or not layout["settled"]) else "stack")
            if layout["name"] != before:
                root.after_idle(on_page_config)          # les tailles voulues ont change: on recalcule la hauteur de la page
        if want_h > h + 1 and w > 50 and layout["name"] != "wide":
            page_bar.grid(row=1, column=1, sticky="ns")
        else:
            page_bar.grid_remove()
            page_canvas.yview_moveto(0)

    skin.zone(right, OVERLAY_COLOR)
    t_skin = time.perf_counter()
    skin.apply(root)                   # avant de mesurer la largeur naturelle : les images changent la taille des widgets
    STATS.record("build.habillage_global", (time.perf_counter() - t_skin) * 1000.0)
    set_layout("wide")
    page.update_idletasks()
    right_fit()
    page.update_idletasks()
    # largeur naturelle cote a cote : en dessous, on empile. L'ascenseur du panneau d'audio2wave (17 px) apparait ou disparait
    # selon la hauteur de la fenetre : il ne compte pas, sinon une fenetre a sa taille naturelle SANS ascenseur etait jugee trop
    # etroite et passait en mise en page empilee alors que tout tenait.
    layout["wide_need"] = page.winfo_reqwidth() - (vbar.winfo_reqwidth() if vbar.winfo_ismapped() else 0)
    page.bind("<Configure>", on_page_config)
    page_canvas.bind("<Configure>", on_page_config)
    on_page_config()
    after(400, lambda: (layout.__setitem__("settled", True), on_page_config()))

    def open_panel_by_default() -> None:
        """Au premier affichage le Mode VJ est etendu (la liste des scenes et l'espace de controle sont la, pas caches) ; une seule fois par session."""
        if os.environ.get("CASUAL_NO_EXPAND") or getattr(s, "expand_default_done", False):
            return
        s.expand_default_done = True
        set_expanded(True)

    after(900, open_panel_by_default)

    def watch_page() -> None:
        """La taille VOULUE de la page change sans que la page change de taille (section « Affichage et analyse » qu'on ouvre, onglet plus haut,
        panneau etendu) : aucun <Configure>, donc ni ascenseur ni zone de defilement. On la surveille."""
        want = (page.winfo_reqwidth(), page.winfo_reqheight())
        if want != layout.get("want"):
            layout["want"] = want
            on_page_config()
        after(300, watch_page)

    after(300, watch_page)

    stage_ctl = gui_stage.install(types.SimpleNamespace(
        a2w=a2w,
        apply_entry=apply_entry,
        back_scene=back_scene,
        current_index=current_index,
        do_panic=do_panic,
        params=params,
        root=root,
        s=s,
        scene_book=scene_book,
        status_text=status_text,
        toggle_scene_lock=toggle_scene_lock,
        ui_every=ui_every,
        ui_remove=ui_remove,
        vj=vj,
        vj_advance=vj_advance,
        vj_toggle=vj_toggle))
    remote_ctl = gui_remote.install(types.SimpleNamespace(
        a2w=a2w, Tooltip=Tooltip, apply_entry=apply_entry, back_scene=back_scene, current_index=current_index, detected_bpm=detected_bpm,
        do_drop=do_drop, do_one=do_one, master_book=master_book, params=params, projection=projection, remote_box=remote_box, root=root, s=s,
        scene_book=scene_book, scene_msg=scene_msg, skin=skin, start_build=start_build, sync_master_panel=sync_master_panel, sync_widgets=sync_widgets,
        toggle_scene_lock=toggle_scene_lock, ui_every=ui_every, vj=vj, vj_advance=vj_advance, vj_remaining_text=vj_remaining_text,
        vj_toggle=vj_toggle, vj_voyants=vj_voyants))
    stage_ref["open"] = stage_ctl.open
    palette_ctl = gui_palette.install(types.SimpleNamespace(
        open_stage=stage_ctl.open,
        a2w=a2w,
        apply_entry=apply_entry,
        back_scene=back_scene,
        bg_presets=bg_presets,
        console_window=console_window,
        do_drop=do_drop,
        do_one=do_one,
        do_panic=do_panic,
        do_tap=do_tap,
        help_window=help_window,
        overlay_presets=overlay_presets,
        params=params,
        root=root,
        s=s,
        scene_book=scene_book,
        scene_msg=scene_msg,
        set_expanded=set_expanded,
        start_build=start_build,
        switch_set=switch_set,
        toggle_projection=toggle_projection,
        toggle_scene_lock=toggle_scene_lock,
        vj_advance=vj_advance,
        vj_toggle=vj_toggle))
    root.protocol("WM_DELETE_WINDOW", on_close)
    ui_every("refresh", REFRESH_MS, refresh)
    ui_every("meter", METER_MS, update_meter)
    ui_every("bridge", BRIDGE_MS, poll_bridge)
    sync_automation_state()
    if keep_size is not None:
        root.update_idletasks()
        root.geometry(f"{keep_size[0]}x{keep_size[1]}")        # fixe la taille : plus de redimensionnement automatique
    DEVICE_CACHE["on"] = False
    drop_cover(root, cover)
    pending = getattr(s, "pending_live_overrides", None)
    if pending:                                  # scene qui a change de mode : on charge son etat dans le panneau neuf
        s.pending_live_overrides = None
        after(150, lambda: apply_live_overrides(pending) or scene_msg.set("panneau audio2wave de la scene introuvable"))
    controls_ref["f"] = lambda: ({
            "root": root, "live_host": panel["frame"], "switch": request_mode, "restart_event": restart_event, "live_status": live_status, "close": on_close, "panel": panel, "projection": {"toggle": toggle_projection, "button": lambda: proj_ui["btn"], "send": send},
            "logo_var": logo_var, "video_var": video_var, "apply_logo_path": apply_logo_path, "color_var": color_var, "x_var": x_var,
            "fx_on_vars": fx_on_vars, "fxl_on_vars": fxl_on_vars, "fxl_int_vars": fxl_int_vars,
            "link_var": link_var, "logo_fx_box": logo_fx_box, "holo_var": holo_var, "holo_box": holo_box, "notebook": notebook, "aff_toggle": toggle_aff, "aff_frame": tab_aff, "tabs": (tab_fond, tab_fx, tab_logo, tab_aura, tab_melt, tab_cell, tab_aff, tab_noise), "noise_fx_vars": noise_fx_vars, "melt_var": melt_var, "cell_var": cell_var, "gates": gates, "palette_var": palette_var, "layout": layout, "page_bar": page_bar, "right_bar": right_bar, "right_cv": right_cv, "page_canvas": page_canvas,
            "live_wrap": live_wrap, "right_panel": right_wrap, "overlay_var": overlay_var,
            "load_overlay_preset": load_overlay_preset, "save_name_var": save_name_var,
            "save_overlay_preset": save_overlay_preset, "update_overlay_preset": update_overlay_preset,
            "overlay_store": overlay_store, "preset_msg": preset_msg, "overlay_menu": overlay_menu,
            "overlay_presets": overlay_presets, "bg_presets": bg_presets, "scene_book": scene_book, "scene_bar": scene_bar,
            "vj": {"master_box": master_box, "back": back_scene, "build": start_build, "drop": do_drop, "memories": master_book, "state": vj, "rhythm_step": rhythm_step, "toggle": vj_toggle, "advance": vj_advance, "lock": toggle_scene_lock, "apply": apply_entry, "tick": vj_tick, "remaining": vj_remaining, "remaining_text": vj_remaining_text, "arm": vj_arm, "panic": do_panic, "tap": do_tap, "one": do_one, "status": status_text, "console": console_window, "help": help_window, "autosave": autosave_tick},
            "random": {"draw": random_draw, "draw_now": random_draw_now, "keep": random_keep, "save": random_save, "check": random_keep_check, "bank": random_bank,
                       "option": set_random_option, "state": rnd},
            "scenes": {"capture": capture_scene_now, "apply": apply_scene, "apply_entry": apply_entry, "delete": delete_scene_now,
                       "poll": poll_scene_requests, "msg": scene_msg, "start_adding": start_adding, "entry_var": scene_entry_var,
                       "new_from_entry": new_scene_from_entry, "switch_set": switch_set, "create_set": create_set,
                       "rename_set": rename_set_now, "delete_set": delete_set_now, "export": export_set_now,
                       "import": import_set_now, "ui": scene_ui, "expand": set_expanded, "panel": scene_panel, "refresh_panel": refresh_scene_panel, "panel_state": panel_state,
                       "drop": drop_media, "a2w_clear": a2w_clear, "bg_clear": bg_clear, "add_random": add_random_scene, "add_random5": add_random_scenes, "duplicate": duplicate_last_scene, "flush": flush_pending_inputs, "dnd": dnd, "entry_vj": set_entry_vj, "rename_scene": rename_scene_now,
                       "thumb": thumb_photo, "add_entry": add_entry_now, "remove_entry": remove_entry_now,
                       "move_entry": move_entry_now, "move_to": move_entry_to, "apply_auto": lambda name: (vj_auto.__setitem__("on", True), apply_scene_core(name), vj_auto.__setitem__("on", False)), "undo": lambda: undo_now(), "redo": lambda: redo_now(), "panel_ops": panel_ops, "strip": lambda: proj_ui.get("strip"), "media_current": media_from_current, "media_clear": media_clear},
            "fx_int_vars": fx_int_vars, "text_widget_sync": sync_widgets, "master_var": master_var, "bg_var": bg_var, "diag_report": diagnostic_report, "diag_btn": diag_btn, "pattern_box": pattern_box, "bgvid_box": bgvid_box, "bgv_var": bgv_var, "drop_bg_video": drop_bg_video,
            "bg_color1_var": bg_color1_var, "bg_color2_var": bg_color2_var,
            "automation": automation, "auto_master_var": auto_master_var, "auto_vars": auto_vars,
            "source_var": source_var, "text_widget": text_widget, "apply_text": apply_text,
            "palette": palette_ctl, "stage": stage_ctl, "remote": remote_ctl, "sched_tick": gui_logo_ctl.sched_tick, "sched_var": gui_logo_ctl.sched_var, "sched_text": gui_logo_ctl.sched_text, "apply_sched": gui_logo_ctl.apply_sched,
            "image_box": image_box, "text_box": text_box, "font_var": font_var})
    if on_ready is not None:
        root.after(50, lambda: on_ready(controls_ref["f"]()))


build_window = STATS.watched("build_window")(build_window)


def _safe(var: tk.Variable, default: float) -> float:
    try:
        return float(var.get())
    except (ValueError, tk.TclError):
        return default
