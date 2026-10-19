"""Reglages : valeurs par defaut, validation, presets (overlay et fond), capture / application, lecture et ecriture de gl_params.json.

Stdlib seulement. La table des reglages est dans core/params_schema.py ; ici vivent le chargement, les presets et les instantanes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core import params_schema
from core.automation import AUTOMATION_SPECS, merge_automation

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
OVERLAY_PRESETS: dict[str, dict] = {
    "default": {},
    # logo net, effets doux : le decorrele fond/logo dans son usage le plus simple
    "sobre": {"fx_link": 0.0, "fxl_on": [0, 0, 0, 0], "fx_on": [1, 1, 0, 0, 1], "fx_int": [0.7, 0.7, 1.0, 1.0, 1.0],
              "master": 0.8, "logo_pulse": 0.06, "logo_jitter": 0.0, "logo_glow": 0.5,
              "holo_on": 1.0, "holo_intensity": 0.8, "holo_warp": 0.7},
    # halo holographique ample et contour magenta
    "neon": {"holo_on": 1.0, "holo_intensity": 1.5, "holo_reach": 1.8, "holo_react": 0.5, "logo_glow": 1.4,
             "logo_glow_color": "#ff3df2", "fx_link": 0.0, "fxl_on": [0, 0, 1, 1], "fxl_int": [1.0, 1.0, 0.8, 0.6]},
    # tout a fond, tout reagit
    "chaos": {"master": 1.3, "fx_int": [1.6, 1.4, 1.5, 1.3, 1.4], "logo_pulse": 0.25, "logo_jitter": 0.012,
              "holo_react": 1.0, "fx_link": 0.0, "fxl_on": [1, 1, 1, 1], "fxl_int": [1.4, 1.2, 1.4, 1.2]},
}
BACKGROUND_PRESETS: dict[str, dict] = {
    "default": {},
    "sobre": {"bg_react": 0.4},
    # fond sombre duo
    "neon": {"bg_mode": "pattern", "bg_palette": "duo", "bg_color1": "#14003a", "bg_color2": "#00e5ff",
             "bg_checker": 0.08, "bg_angle": 35.0},
    "chaos": {"bg_react": 1.6, "bg_speed": 2.2, "bg_flip": 4.0},
}


class PresetBook:
    """Presets integres (`builtin`, dans le code) + presets utilisateur (JSON `path`), comme PresetStore d'audio2wave_live
    mais **tout est modifiable** : mettre a jour un integre en enregistre une version utilisateur qui le remplace, le
    supprimer le masque (liste reservee `_deleted` du meme fichier), `restore_builtins()` rend les integres d'origine.
    `default` reste toujours present et toujours **en tete** : le supprimer revient a sa version d'origine."""

    DELETED = "_deleted"

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
        return {k: v for k, v in self._read().items() if k != self.DELETED and isinstance(v, dict)}

    def deleted(self) -> list[str]:
        value = self._read().get(self.DELETED)
        return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []

    def all(self) -> dict[str, dict]:
        """Tous les presets, `default` en tete puis ordre alphabetique."""
        hidden = set(self.deleted())
        merged = {n: v for n, v in self.builtin.items() if n not in hidden}
        merged.update(self.load_user())
        merged.setdefault("default", self.builtin.get("default", {}))
        names = ["default"] + sorted(n for n in merged if n != "default")
        return {n: merged[n] for n in names}

    def names(self) -> list[str]:
        return list(self.all())

    def save(self, name: str, overrides: dict) -> None:
        data = self._read()
        data[name] = overrides
        data[self.DELETED] = [n for n in self.deleted() if n != name]
        self._write(data)

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
        if name in self.builtin:
            data[self.DELETED] = sorted(set(self.deleted()) | {name})
        self._write(data)
        return "deleted"

    def is_modified(self, name: str) -> bool:
        return name in self.builtin and name in self.load_user()

    def restore_builtins(self) -> None:
        data = {k: v for k, v in self._read().items() if k != self.DELETED and k not in self.builtin}
        self._write(data)


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
