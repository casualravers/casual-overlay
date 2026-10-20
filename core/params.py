"""Reglages : valeurs par defaut, validation, presets (overlay et fond), capture / application, lecture et ecriture de gl_params.json.

Stdlib seulement. La table des reglages est dans core/params_schema.py ; ici vivent le chargement, les presets et les instantanes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core import params_schema
from core.automation import AUTOMATION_SPECS, merge_automation, set_param

ROOT_DIR = Path(__file__).resolve().parent.parent       # dossier du programme (asset/, gl_shaders/...)
DEFAULT_LOGO_NAME = "Casual Ravers - Kit_Sigle - Blanc.png"


def default_logo(script_dir: Path) -> Path:
    """Logo par defaut : `asset/images/` (rangement actuel des assets), sinon `asset/` (ancien emplacement)."""
    for sub in ("images", ""):
        path = script_dir / "asset" / sub / DEFAULT_LOGO_NAME
        if path.is_file():
            return path
    return script_dir / "asset" / "images" / DEFAULT_LOGO_NAME


DEFAULT_LOGO = default_logo(ROOT_DIR)

PARAMS_PATH = Path.home() / ".audio2wave" / "gl_params.json"

FX_NAMES = ["wobble", "ripple", "chroma", "glitch", "logo"]
# Les reglages sont decrits UNE fois, dans params_schema.py (defaut, plage, enum, portee, doc) ; tout le reste en est derive.
DEFAULT_PARAMS = params_schema.defaults(str(DEFAULT_LOGO))

# Portees des reglages (voir params_schema.py) : salle (Mode VJ, relus a chaque image, jamais captures par les presets ni les scenes),
# etats du moment (ni sauves ni recharges), contenu d'une memoire de masters.
ROOM_KEYS = params_schema.ROOM_KEYS
TRANSIENT_KEYS = params_schema.TRANSIENT_KEYS
MEMORY_KEYS = params_schema.MEMORY_KEYS


# --- Parametres en direct -------------------------------------------------------------

def coerce_params(saved: dict, base: dict | None = None) -> dict:
    """Reglages valides tires de `saved` (fichier sauve ou preset) par-dessus `base` (defaut: les valeurs
    par defaut) : cle inconnue, type, longueur de liste ou valeur d'enum incorrects = ignores, nombre hors plage ramene dans la plage
    (params_schema.validate). `_automation` est
    fusionne avec l'ambiance par defaut (merge_automation) s'il est present."""
    params = json.loads(json.dumps(base if base is not None else DEFAULT_PARAMS))
    for key in DEFAULT_PARAMS:
        if key not in saved:
            continue
        ok, value = params_schema.validate(key, saved[key])      # type, longueur, enum ; un nombre hors plage est ramene dedans
        if ok:
            params[key] = value
    if isinstance(saved.get("_automation"), dict):
        params["_automation"] = merge_automation(saved["_automation"])
    return params


# Presets propres a l'overlay : memes gestes que les presets live d'audio2wave, mais dans leurs PROPRES fichiers et
# avec une mecanique modifiable (PresetBook). Deux jeux decorreles, comme les deux parties de la GUI :
#  - OVERLAY (logo, effets, halo, fonte, cellules, automations correspondantes) : aucune cle `bg_*` ;
#  - FOND (source Audio2wave / motif genere et ses reglages, automations `bg_*`).
# Un preset = des valeurs posees par-dessus les reglages par defaut ; "sensibilite" en est exclue (elle depend du
# micro et de la salle, pas du look).
OVERLAY_PRESETS_PATH = Path.home() / ".audio2wave" / "overlay_presets.json"
BACKGROUND_PRESETS_PATH = Path.home() / ".audio2wave" / "background_presets.json"
OVERLAY_PRESET_EXCLUDED = params_schema.OVERLAY_PRESET_EXCLUDED     # sensibilite + reglages de salle : ni presets ni scenes n'y touchent
BG_KEYS = params_schema.BG_KEYS                                     # portee « fond » (bg_master est un reglage de SALLE, pas du fond)
BG_AUTOMATION_KEYS = tuple(k for k in AUTOMATION_SPECS if params_schema.is_bg(k))
# Un seul preset integre : `default` (le point de depart). Les autres sont ceux de l'utilisateur, tous traites pareil.
OVERLAY_PRESETS: dict[str, dict] = {"default": {}}
BACKGROUND_PRESETS: dict[str, dict] = {"default": {}}

FOLDER_SEP = "/"
RECENT_MAX = 8


def split_folder(name: str) -> tuple[str, str]:
    """`club/neon` -> (`club`, `neon`) ; un nom sans `/` est a la racine (dossier vide)."""
    folder, sep, leaf = name.partition(FOLDER_SEP)
    return (folder.strip(), leaf.strip()) if sep and folder.strip() and leaf.strip() else ("", name)


def normalize_preset_name(raw: str) -> str | None:
    """Nom de preset valide (minuscules, au plus un dossier `dossier/nom`, espaces autour du `/` retires), ou None s'il est vide / invalide."""
    raw = (raw or "").strip().lower()
    parts = [x.strip() for x in raw.split(FOLDER_SEP)]
    if not raw or raw.startswith("_") or len(parts) > 2 or not all(parts):
        return None
    return FOLDER_SEP.join(parts)


def menu_layout(names: list[str], favorites: list[str], recents: list[str], needle: str = "") -> dict:
    """Organisation d'un menu de presets : `favorites`, `recents` (existants seulement), `folders` (dossier -> noms, tries) et `root`
    (noms sans dossier). `default` est toujours le premier de la racine. Avec `needle`, une liste plate `found` des noms qui le contiennent."""
    have = set(names)
    needle = needle.strip().lower()
    if needle:
        return {"found": [n for n in names if needle in n.lower()], "favorites": [], "recents": [], "folders": {}, "root": []}
    folders: dict[str, list[str]] = {}
    root: list[str] = []
    for n in names:
        folder, _leaf = split_folder(n)
        (folders.setdefault(folder, []) if folder else root).append(n)
    root.sort(key=lambda n: (n != "default", n.lower()))
    return {"found": None, "favorites": [n for n in favorites if n in have],
            "recents": [n for n in recents if n in have and n not in favorites][:RECENT_MAX],
            "folders": {f: sorted(v, key=str.lower) for f, v in sorted(folders.items(), key=lambda kv: kv[0].lower())}, "root": root}


def _close(a, b, tol: float = 1e-3) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    return a == b


def _undriven(snap: dict, automation) -> dict:
    """Copie de `snap` ou les reglages pilotes par une courbe d'automation allumee sont neutralises (ils bougent tout seuls)."""
    out = json.loads(json.dumps(snap))
    if isinstance(automation, dict) and float(out.get("auto_master", 1.0)) >= 0.5:
        for key, entry in automation.items():
            if key in AUTOMATION_SPECS and isinstance(entry, dict) and entry.get("enabled"):
                try:
                    set_param(out, key, 0.0)
                except (KeyError, IndexError, TypeError):
                    pass
    return out


def differs_from(snap: dict, baseline: dict, automation=None) -> bool:
    """Vrai si l'etat `snap` (capture courante) s'ecarte de `baseline` (capture faite juste apres le chargement d'un preset),
    en ignorant les reglages que les automations font bouger et le bruit d'arrondi des curseurs."""
    return not _close(_undriven(snap, automation), _undriven(baseline, automation))


class PresetBook:
    """Presets (integre `default` + ceux de l'utilisateur, JSON `path`), comme PresetStore d'audio2wave_live mais **tout est modifiable**.
    `default` reste toujours present et toujours **en tete** : le supprimer revient a sa version d'origine (vide).
    Un nom peut contenir `/` pour ranger un preset dans un dossier (`club/neon`). Les cles reservees du fichier (commencant par `_`)
    gardent les favoris (`_favorites`) et les derniers charges (`_recent`)."""

    FAVORITES = "_favorites"
    RECENT = "_recent"

    def __init__(self, builtin: dict[str, dict], path_getter):
        self.builtin = builtin
        self._path_getter = path_getter        # le chemin est relu a chaque appel (les tests le remplacent)

    @property
    def path(self) -> Path:
        return Path(self._path_getter())

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def load_user(self) -> dict[str, dict]:
        return {k: v for k, v in self._read().items() if not k.startswith("_") and isinstance(v, dict)}

    def all(self) -> dict[str, dict]:
        """Tous les presets, `default` en tete puis ordre alphabetique."""
        merged = dict(self.builtin)
        merged.update(self.load_user())
        merged.setdefault("default", {})
        names = ["default"] + sorted((n for n in merged if n != "default"), key=str.lower)
        return {n: merged[n] for n in names}

    def names(self) -> list[str]:
        return list(self.all())

    def _list(self, key: str) -> list[str]:
        value = self._read().get(key)
        return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []

    def favorites(self) -> list[str]:
        return [n for n in self._list(self.FAVORITES) if n in self.all()]

    def recents(self) -> list[str]:
        return [n for n in self._list(self.RECENT) if n in self.all()]

    def toggle_favorite(self, name: str) -> bool:
        """Ajoute / retire `name` des favoris ; rend vrai s'il en est un maintenant."""
        data = self._read()
        favs = self._list(self.FAVORITES)
        on = name not in favs
        favs = [n for n in favs if n != name] + ([name] if on else [])
        data[self.FAVORITES] = favs
        self._write(data)
        return on

    def touch(self, name: str) -> None:
        """Note `name` comme le dernier preset charge (en tete des recents)."""
        if name not in self.all():
            return
        data = self._read()
        data[self.RECENT] = ([name] + [n for n in self._list(self.RECENT) if n != name])[:RECENT_MAX * 2]
        self._write(data)

    def save(self, name: str, overrides: dict) -> None:
        data = self._read()
        data[name] = overrides
        self._write(data)

    def rename(self, old: str, new: str) -> bool:
        """Renomme (ou range dans un dossier : `club/neon`) un preset d'utilisateur ; les favoris et les recents suivent.
        False si `old` n'est pas un preset d'utilisateur (`default` ne bouge pas), si `new` est invalide ou deja pris."""
        new = normalize_preset_name(new)
        data = self._read()
        if new is None or old == "default" or new == "default" or old not in self.load_user() or (new != old and new in self.all()):
            return False
        if new != old:
            data[new] = data.pop(old)
            for key in (self.FAVORITES, self.RECENT):
                if key in data:
                    data[key] = [new if n == old else n for n in self._list(key)]
            self._write(data)
        return True

    def delete(self, name: str) -> str | None:
        """'reset' (default : retour a l'original), 'deleted', ou None si le nom n'existe pas."""
        data = self._read()
        if name == "default":
            if "default" not in data:
                return "reset"
            del data["default"]
            self._write(data)
            return "reset"
        if name not in self.all():
            return None
        data.pop(name, None)
        for key in (self.FAVORITES, self.RECENT):
            if key in data:
                data[key] = [n for n in self._list(key) if n != name]
        self._write(data)
        return "deleted"


OVERLAY_BOOK = PresetBook(OVERLAY_PRESETS, lambda: OVERLAY_PRESETS_PATH)
BACKGROUND_BOOK = PresetBook(BACKGROUND_PRESETS, lambda: BACKGROUND_PRESETS_PATH)


def _split_automation(state, current: dict, want_bg: bool) -> dict:
    """Etat d'automation : les entrees `bg_*` (want_bg) ou toutes les autres, venant de `state` ; le reste reste celui de
    `current`."""
    merged = merge_automation(current.get("_automation"))
    for key, entry in merge_automation(state).items():
        if params_schema.is_bg(key) == want_bg:
            merged[key] = entry
    return merged


def capture_overlay(params: dict) -> dict:
    """Instantane des reglages de l'overlay pour un preset (sans le fond ni ce qui depend du materiel)."""
    snap = json.loads(json.dumps({k: v for k, v in params.items()
                                  if (k in DEFAULT_PARAMS or k == "_automation") and k not in BG_KEYS}))
    for key in OVERLAY_PRESET_EXCLUDED:
        snap.pop(key, None)
    if "_automation" in snap:
        snap["_automation"] = {k: v for k, v in snap["_automation"].items() if k not in BG_AUTOMATION_KEYS}
    return snap


def capture_background(params: dict) -> dict:
    """Instantane des reglages du fond (source, motif, automations `bg_*`)."""
    snap = json.loads(json.dumps({k: params[k] for k in BG_KEYS if k in params}))
    if "_automation" in params:
        snap["_automation"] = json.loads(json.dumps({k: v for k, v in params["_automation"].items()
                                                     if k in BG_AUTOMATION_KEYS}))
    return snap


def overlay_preset_values(overrides: dict, current: dict) -> dict:
    """Reglages a appliquer pour un preset overlay : valeurs par defaut + `overrides` (valides), sauf ce qui est exclu
    ou appartient au fond (garde la valeur courante, meme si un ancien preset contenait des `bg_*`). L'automation
    n'est touchee que si le preset en contient une, et jamais pour les reglages du fond."""
    values = coerce_params(overrides)
    for key in OVERLAY_PRESET_EXCLUDED + BG_KEYS:
        values[key] = current.get(key, DEFAULT_PARAMS[key])
    if "_automation" in overrides:
        values["_automation"] = _split_automation(overrides["_automation"], current, want_bg=False)
    else:
        values.pop("_automation", None)
    return values


def background_preset_values(overrides: dict, current: dict) -> dict:
    """Reglages a appliquer pour un preset de fond : seulement les cles `bg_*` (defaut + preset) et, si le preset en
    contient, les automations `bg_*`. Tout le reste (logo, effets, sensibilite) garde sa valeur courante."""
    full = coerce_params(overrides)
    values = {k: full[k] for k in BG_KEYS}
    if "_automation" in overrides:
        values["_automation"] = _split_automation(overrides["_automation"], current, want_bg=True)
    return values


def load_params() -> dict:
    params = json.loads(json.dumps(DEFAULT_PARAMS))
    try:
        saved = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        params = coerce_params({k: v for k, v in saved.items() if k not in TRANSIENT_KEYS})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"Reglages ignores ({PARAMS_PATH}): {exc}", file=sys.stderr)
    return params


def save_params(params: dict) -> None:
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    saved = {k: v for k, v in params.items() if k not in TRANSIENT_KEYS}          # idle, montee, strobe arme : etats du moment
    PARAMS_PATH.write_text(json.dumps(saved, indent=2), encoding="utf-8")
