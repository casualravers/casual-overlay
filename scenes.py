"""Scenes : un clic (ou F1 a F9) change tout le look d'un coup.

Une scene = (fond audio2wave OU fond genere) + overlay :
  - `bg` = "live" : le fond est le visuel d'audio2wave ; la scene retient le mode (live / snap / ridge) et l'etat de son
    panneau (`live_overrides`, meme forme qu'un preset d'audio2wave) **dans scenes.json** : jamais dans les presets
    d'audio2wave, qui sont autre chose (un preset est une boite de reglages, une scene un look complet) ;
  - `bg` = "pattern" : le fond est le motif genere ; la scene retient son reglage (`capture_background`), et le mode
    d'audio2wave n'est pas touche (le motif remplace le visuel, il ne le configure pas) ;
  - dans les deux cas, le reglage overlay (`capture_overlay` : logo, effets, halo, fonte, cellules, noise, automations).

Ce module est pur (aucune fenetre) : stockage ordonne, capture et valeurs a appliquer. La GUI (gl_gui.py) fait le reste.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import audio2wave_gl as gl

SCENES_PATH = Path.home() / ".audio2wave" / "scenes.json"
MAX_SCENES = 9                    # F1 a F9
MAX_NAME = 24
VJ_SECONDS = (10, 20, 30, 60, 120, 300)          # durees proposees dans le menu du VJ
VJ_DEFAULTS = {"seconds": 30, "order": "seq"}    # "seq" = dans l'ordre, "random" = au hasard (jamais la meme deux fois)


# Reglages d'audio2wave qu'une scene ne retient ni n'applique jamais : l'entree audio reste celle qui est active, et la
# fenetre de rendu garde son etat (plein ecran, taille) : une scene change le look, pas l'installation.
SCENE_EXCLUDED = ("device", "fullscreen", "size")
TEMP_LIVE_PRESET = "scene-tmp"      # nom d'un preset d'audio2wave le temps d'une capture (retire tout de suite)


def live_preset_name(name: str) -> str:
    """Nom que portaient les presets d'audio2wave crees par les premieres versions des scenes (migration)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "scene"
    return f"scene-{slug}"


class SceneBook:
    """Les scenes, dans l'ordre de creation (le rang donne la touche F1..F9) ; fichier JSON `{"scenes": [...]}`."""

    def __init__(self, path_getter) -> None:
        self._path_getter = path_getter            # relu a chaque appel (les tests remplacent le chemin)

    @property
    def path(self) -> Path:
        return Path(self._path_getter())

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def vj(self) -> dict:
        """Reglages du mode VJ (duree par scene en secondes, ordre), valides, completes par les valeurs par defaut."""
        raw = self._read().get("vj")
        out = dict(VJ_DEFAULTS)
        if isinstance(raw, dict):
            if isinstance(raw.get("seconds"), (int, float)) and not isinstance(raw["seconds"], bool):
                out["seconds"] = min(max(float(raw["seconds"]), 5.0), 300.0)
            if raw.get("order") in ("seq", "random"):
                out["order"] = raw["order"]
        return out

    def set_vj(self, **kw) -> None:
        data = self._read()
        data["vj"] = {**self.vj(), **kw}
        data.setdefault("scenes", [])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self) -> list[dict]:
        items = self._read().get("scenes")
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict) and isinstance(x.get("name"), str)][:MAX_SCENES]

    def _write(self, items: list[dict]) -> None:
        data = self._read()
        data["scenes"] = items                     # les reglages du VJ sont gardes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def names(self) -> list[str]:
        return [x["name"] for x in self.list()]

    def index_of(self, name: str) -> int | None:
        low = name.strip().lower()
        for i, x in enumerate(self.list()):
            if x["name"].lower() == low:
                return i
        return None

    def get(self, name: str) -> dict | None:
        i = self.index_of(name)
        return self.list()[i] if i is not None else None

    def put(self, scene: dict) -> bool:
        """Ajoute la scene, ou remplace celle du meme nom (meme rang). False si la liste est pleine."""
        items = self.list()
        i = self.index_of(scene["name"])
        if i is not None:
            items[i] = scene
        elif len(items) >= MAX_SCENES:
            return False
        else:
            items.append(scene)
        self._write(items)
        return True

    def delete(self, name: str) -> dict | None:
        i = self.index_of(name)
        if i is None:
            return None
        items = self.list()
        gone = items.pop(i)
        self._write(items)
        return gone


def next_scene_index(count: int, current: int | None, order: str, rng=random) -> int | None:
    """Rang de la scene suivante du VJ : `seq` = la suivante (en boucle, la premiere si aucune n'est active), `random` = une
    autre au hasard (jamais la meme deux fois de suite). None s'il n'y a pas de scene."""
    if count <= 0:
        return None
    if count == 1:
        return 0
    if order == "random":
        pick = rng.randrange(count - 1) if current is not None else rng.randrange(count)
        return pick + 1 if (current is not None and pick >= current) else pick
    return 0 if current is None else (current + 1) % count


def strip_live_overrides(overrides: dict | None) -> dict | None:
    """`overrides` sans les reglages exclus des scenes (voir SCENE_EXCLUDED)."""
    if not isinstance(overrides, dict):
        return overrides
    return {k: v for k, v in overrides.items() if k not in SCENE_EXCLUDED}


def capture_scene(name: str, params: dict, mode: str, live_overrides: dict | None) -> dict:
    """Instantane de l'etat courant. `live_overrides` : etat du panneau d'audio2wave (fond audio2wave seulement)."""
    pattern = params.get("bg_mode") == "pattern"
    return {
        "name": name,
        "bg": "pattern" if pattern else "live",
        "mode": None if pattern else mode,
        "live_overrides": None if pattern else strip_live_overrides(live_overrides),
        "fond": gl.capture_background(params) if pattern else None,
        "overlay": gl.capture_overlay(params),
    }


def scene_values(scene: dict, current: dict) -> dict:
    """Reglages overlay a poser pour la scene : le reglage overlay (jamais le fond) + soit le motif genere, soit
    seulement `bg_mode = live` (le visuel d'audio2wave est regle a part, par son propre preset). Meme forme que
    `gl.overlay_preset_values` (`_automation` complet si la scene en a)."""
    values = gl.overlay_preset_values(scene.get("overlay") or {}, current)
    if scene.get("bg") == "pattern" and isinstance(scene.get("fond"), dict):
        bg = gl.background_preset_values(scene["fond"], current)
        bg_auto = bg.pop("_automation", None)
        values.update(bg)
        if bg_auto is not None:
            merged = gl.merge_automation(values.get("_automation", current.get("_automation")))
            for key in gl.BG_AUTOMATION_KEYS:
                merged[key] = bg_auto[key]
            values["_automation"] = merged
        values["bg_mode"] = "pattern"
    else:
        values["bg_mode"] = "live"
    return values
