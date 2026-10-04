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
KEYS = 9                          # touches F1 a F9 : les 9 premieres entrees du set actif (pas de limite au-dela)
DEFAULT_SET = "principal"        # set qui recoit les scenes d'un ancien scenes.json
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
    """La **bibliotheque de scenes** et les **sets** (enchainements de scenes) ; fichier JSON ecrit par `_save` :
    `{"version": 2, "scenes": [...], "sets": [{"name", "entries": [{"scene", "media"}], "vj"}], "active_set": "..."}`.

    Les scenes sont des briques : un set les **reference par nom** (autant qu'on veut, plusieurs fois, dans plusieurs sets),
    chaque entree pouvant porter son propre `media` (logo / texte / video, voir `capture_media`). Modifier une scene la
    modifie dans tous les sets. Les touches F1..F9 sont celles des 9 premieres entrees du set actif. Un fichier d'avant les
    sets (`{"scenes": [...], "vj": {...}}`) est lu comme un set `principal` qui les contient toutes."""

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

    @staticmethod
    def _clean_vj(raw) -> dict:
        out = dict(VJ_DEFAULTS)
        if isinstance(raw, dict):
            if isinstance(raw.get("seconds"), (int, float)) and not isinstance(raw["seconds"], bool):
                out["seconds"] = min(max(float(raw["seconds"]), 5.0), 300.0)
            if raw.get("order") in ("seq", "random"):
                out["order"] = raw["order"]
        return out

    @staticmethod
    def _entry(e: dict, scene_name: str) -> dict:
        """Entree de set validee : `media` (overlay : logo / texte / video du logo, dict ou None), `a2w` (medias d'audio2wave :
        videos `video` interieure et `video2` exterieure du mode Snap, dict de chemins ou None), `seconds` (duree propre au VJ, 5..300, ou None = celle du set),
        `skip` (True = le VJ ne la joue pas ; on peut toujours la charger a la main)."""
        media = e.get("media")
        a2w = e.get("a2w")
        a2w = {k: a2w[k] for k in A2W_MEDIA_KEYS if isinstance(a2w.get(k), str) and a2w[k].strip()} if isinstance(a2w, dict) else {}
        sec = e.get("seconds")
        seconds = min(max(float(sec), 5.0), 300.0) if isinstance(sec, (int, float)) and not isinstance(sec, bool) else None
        return {"scene": scene_name, "media": media if isinstance(media, dict) and media else None,
                "a2w": a2w or None, "seconds": seconds, "skip": e.get("skip") is True}

    def _load(self) -> dict:
        """Contenu valide et complet (ancien format migre, entrees orphelines retirees, toujours au moins un set)."""
        raw = self._read()
        items = raw.get("scenes")
        library = [x for x in items if isinstance(x, dict) and isinstance(x.get("name"), str)] if isinstance(items, list) else []
        known = {x["name"].lower() for x in library}
        raw_sets = raw.get("sets")
        sets = []
        if isinstance(raw_sets, list):
            for item in raw_sets:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                    continue
                entries = []
                for e in item.get("entries") if isinstance(item.get("entries"), list) else []:
                    if isinstance(e, dict) and isinstance(e.get("scene"), str) and e["scene"].lower() in known:
                        entries.append(self._entry(e, next(x["name"] for x in library if x["name"].lower() == e["scene"].lower())))
                sets.append({"name": item["name"], "entries": entries, "vj": self._clean_vj(item.get("vj"))})
        if not sets:                                   # ancien format (ou fichier vide) : un set principal avec toutes les scenes
            sets = [{"name": DEFAULT_SET, "entries": [self._entry({}, x["name"]) for x in library],
                     "vj": self._clean_vj(raw.get("vj"))}]
        active = raw.get("active_set")
        if not any(x["name"] == active for x in sets):
            active = sets[0]["name"]
        return {"scenes": library, "sets": sets, "active_set": active}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {"version": 2, "scenes": data["scenes"], "sets": data["sets"], "active_set": data["active_set"]}
        self.path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _find(items: list[dict], name: str) -> int | None:
        low = name.strip().lower()
        for i, x in enumerate(items):
            if x["name"].lower() == low:
                return i
        return None

    @staticmethod
    def _active(data: dict) -> dict:
        return next(x for x in data["sets"] if x["name"] == data["active_set"])

    # ---- bibliotheque de scenes
    def list(self) -> list[dict]:
        return self._load()["scenes"]

    def names(self) -> list[str]:
        return [x["name"] for x in self.list()]

    def index_of(self, name: str) -> int | None:
        return self._find(self.list(), name)

    def get(self, name: str) -> dict | None:
        items = self.list()
        i = self._find(items, name)
        return items[i] if i is not None else None

    def put(self, scene: dict, add_to_set: bool = True) -> bool:
        """Ajoute la scene a la bibliotheque (et, si elle est nouvelle, a la fin du set actif), ou remplace celle du meme nom :
        elle change alors dans tous les sets qui l'utilisent. Pas de limite de nombre : toujours True."""
        data = self._load()
        i = self._find(data["scenes"], scene["name"])
        if i is not None:
            data["scenes"][i] = scene
        else:
            data["scenes"].append(scene)
            if add_to_set:
                self._active(data)["entries"].append(self._entry({}, scene["name"]))
        self._save(data)
        return True

    def delete(self, name: str) -> dict | None:
        """Retire la scene de la bibliotheque **et de tous les sets**."""
        data = self._load()
        i = self._find(data["scenes"], name)
        if i is None:
            return None
        gone = data["scenes"].pop(i)
        for st in data["sets"]:
            st["entries"] = [e for e in st["entries"] if e["scene"].lower() != gone["name"].lower()]
        self._save(data)
        return gone

    # ---- sets
    def set_names(self) -> list[str]:
        return [x["name"] for x in self._load()["sets"]]

    def get_set(self, name: str) -> dict | None:
        items = self._load()["sets"]
        i = self._find(items, name)
        return items[i] if i is not None else None

    def active_set_name(self) -> str:
        return self._load()["active_set"]

    def set_active(self, name: str) -> bool:
        data = self._load()
        i = self._find(data["sets"], name)
        if i is None:
            return False
        data["active_set"] = data["sets"][i]["name"]
        self._save(data)
        return True

    def new_set(self, name: str, copy_of: str | None = None) -> bool:
        """Cree un set vide (ou copie de `copy_of`). False si le nom est pris (sans tenir compte de la casse) ou vide."""
        name = name.strip()[:MAX_NAME]
        data = self._load()
        if not name or self._find(data["sets"], name) is not None:
            return False
        source = data["sets"][self._find(data["sets"], copy_of)] if copy_of and self._find(data["sets"], copy_of) is not None else None
        data["sets"].append({"name": name, "entries": json.loads(json.dumps(source["entries"])) if source else [],
                             "vj": dict(source["vj"]) if source else dict(VJ_DEFAULTS)})
        self._save(data)
        return True

    def rename_set(self, old: str, new: str) -> bool:
        new = new.strip()[:MAX_NAME]
        data = self._load()
        i = self._find(data["sets"], old)
        j = self._find(data["sets"], new) if new else None
        if i is None or not new or (j is not None and j != i):
            return False
        was_active = data["sets"][i]["name"] == data["active_set"]
        data["sets"][i]["name"] = new
        if was_active:
            data["active_set"] = new
        self._save(data)
        return True

    def delete_set(self, name: str) -> bool:
        """Supprime un set (jamais les scenes, qui restent dans la bibliotheque). Refuse de supprimer le dernier."""
        data = self._load()
        i = self._find(data["sets"], name)
        if i is None or len(data["sets"]) <= 1:
            return False
        gone = data["sets"].pop(i)
        if gone["name"] == data["active_set"]:
            data["active_set"] = data["sets"][0]["name"]
        self._save(data)
        return True

    # ---- entrees du set actif : (scene, media) dans l'ordre de passage
    def rows(self) -> list[dict]:
        """Entrees du set actif : `{"index", "name", "scene", "media"}`. Le rang donne la touche F1..F9 (`KEYS` premieres)."""
        data = self._load()
        lib = {x["name"].lower(): x for x in data["scenes"]}
        return [{"index": i, "name": e["scene"], "scene": lib[e["scene"].lower()], "media": e["media"],
                 "a2w": e["a2w"], "seconds": e["seconds"], "skip": e["skip"]} for i, e in enumerate(self._active(data)["entries"])]

    def row_names(self) -> list[str]:
        return [r["name"] for r in self.rows()]

    def add_entry(self, scene_name: str, media: dict | None = None) -> bool:
        data = self._load()
        i = self._find(data["scenes"], scene_name)
        if i is None:
            return False
        self._active(data)["entries"].append(self._entry({"media": media}, data["scenes"][i]["name"]))
        self._save(data)
        return True

    def remove_entry(self, index: int) -> bool:
        data = self._load()
        entries = self._active(data)["entries"]
        if not 0 <= index < len(entries):
            return False
        entries.pop(index)
        self._save(data)
        return True

    def move_entry(self, index: int, delta: int) -> int | None:
        """Deplace l'entree de `delta` rangs ; renvoie son nouveau rang (None si impossible)."""
        data = self._load()
        entries = self._active(data)["entries"]
        j = index + delta
        if not (0 <= index < len(entries) and 0 <= j < len(entries)):
            return None
        entries.insert(j, entries.pop(index))
        self._save(data)
        return j

    def set_entry_media(self, index: int, media: dict | None) -> bool:
        data = self._load()
        entries = self._active(data)["entries"]
        if not 0 <= index < len(entries):
            return False
        entries[index]["media"] = media or None
        self._save(data)
        return True

    def set_entry_a2w(self, index: int, key: str, path: str | None) -> bool:
        """Pose (ou retire, `path` None) la video d'audio2wave `key` (`video` ou `video2`) de l'entree."""
        data = self._load()
        entries = self._active(data)["entries"]
        if key not in A2W_MEDIA_KEYS or not 0 <= index < len(entries):
            return False
        a2w = dict(entries[index]["a2w"] or {})
        if path:
            a2w[key] = str(path)
        else:
            a2w.pop(key, None)
        entries[index]["a2w"] = a2w or None
        self._save(data)
        return True

    def set_entry_vj(self, index: int, seconds="keep", skip=None) -> bool:
        """Reglages VJ d'une entree : `seconds` (None = duree du set, sinon 5..300), `skip` (True = jamais jouee par le VJ)."""
        data = self._load()
        entries = self._active(data)["entries"]
        if not 0 <= index < len(entries):
            return False
        if seconds != "keep":
            entries[index]["seconds"] = seconds
        if skip is not None:
            entries[index]["skip"] = bool(skip)
        entries[index] = self._entry(entries[index], entries[index]["scene"])
        self._save(data)
        return True

    def rename_scene(self, old: str, new: str) -> bool:
        """Renomme une scene de la bibliotheque (les entrees de tous les sets suivent). False si le nom est vide ou pris."""
        new = new.strip()[:MAX_NAME]
        data = self._load()
        i = self._find(data["scenes"], old)
        j = self._find(data["scenes"], new) if new else None
        if i is None or not new or (j is not None and j != i):
            return False
        before = data["scenes"][i]["name"]
        data["scenes"][i]["name"] = new
        for st in data["sets"]:
            for e in st["entries"]:
                if e["scene"] == before:
                    e["scene"] = new
        self._save(data)
        return True

    # ---- reglages du VJ : propres a chaque set
    def vj(self) -> dict:
        """Reglages du mode VJ du set actif (duree par scene en secondes, ordre), valides, completes par les valeurs par defaut."""
        data = self._load()
        return dict(self._active(data)["vj"])

    def set_vj(self, **kw) -> None:
        data = self._load()
        st = self._active(data)
        st["vj"] = self._clean_vj({**st["vj"], **kw})
        self._save(data)


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


def next_entry_index(rows: list[dict], current: int | None, order: str, rng=random) -> int | None:
    """Rang de la prochaine entree que le VJ joue parmi celles qui ne sont pas exclues (`skip`) : memes regles que
    `next_scene_index` (en boucle dans l'ordre ; au hasard sans jamais rejouer la meme deux fois de suite). None si aucune."""
    cands = [i for i, r in enumerate(rows) if not r.get("skip")]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if order == "random":
        return rng.choice([i for i in cands if i != current])
    after = [i for i in cands if current is not None and i > current]
    return after[0] if after else cands[0]


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


# ---- media d'une entree de set : le logo / texte / video que cette entree pose par-dessus le look de la scene
A2W_MEDIA_KEYS = ("video", "video2")        # medias d'audio2wave (mode Snap, style pencil) : video interieure / exterieure
MEDIA_KEYS = ("logo_source", "logo_path", "logo_video", "logo_key", "text_content", "text_font", "text_color", "text_align")


def capture_media(params: dict) -> dict:
    """Le media affiche en ce moment : les reglages de la source courante seulement (image, texte ou video)."""
    source = params.get("logo_source", "image")
    keys = {"text": ("text_content", "text_font", "text_color", "text_align"),
            "video": ("logo_video", "logo_key")}.get(source, ("logo_path",))
    return {k: params[k] for k in ("logo_source",) + keys if k in params}


def media_values(media: dict | None, current: dict) -> dict:
    """Reglages a poser pour `media` (valides, seulement les cles de MEDIA_KEYS) ; {} si aucun media."""
    if not isinstance(media, dict) or not media:
        return {}
    full = gl.coerce_params({k: v for k, v in media.items() if k in MEDIA_KEYS})
    return {k: full[k] for k in MEDIA_KEYS if k in media}


def media_missing(media: dict | None) -> str | None:
    """Chemin du fichier du media qui n'existe pas (image ou video), sinon None."""
    if not isinstance(media, dict):
        return None
    source = media.get("logo_source", "image")
    path = media.get("logo_video") if source == "video" else media.get("logo_path") if source == "image" else None
    return path if path and not Path(path).exists() else None


def scene_a2w_media(scene: dict) -> dict:
    """Videos d'audio2wave que la scene a capturees elle-meme (dans l'etat de son panneau Snap) : {"video": ..., "video2": ...}."""
    over = scene.get("live_overrides") if isinstance(scene, dict) else None
    if not isinstance(over, dict):
        return {}
    return {k: str(over[k]) for k in A2W_MEDIA_KEYS if isinstance(over.get(k), (str, Path)) and str(over[k]).strip()}


def a2w_missing(a2w: dict | None) -> list[str]:
    """Videos d'audio2wave a chemin absolu qui n'existent pas (un chemin relatif est cherche par audio2wave dans son dossier
    d'assets : on ne peut pas le savoir ici)."""
    return [p for p in (a2w or {}).values() if Path(p).is_absolute() and not Path(p).exists()]


def media_label(media: dict | None) -> str:
    if not media:
        return ""
    source = media.get("logo_source", "image")
    if source == "text":
        text = str(media.get("text_content", "")).replace("\n", " / ")
        return "texte \"" + (text if len(text) <= 24 else text[:23] + ".") + "\""
    path = media.get("logo_video") if source == "video" else media.get("logo_path")
    return ("video " if source == "video" else "image ") + (Path(path).name if path else "(aucune)")


# ---- export / import d'un set : un seul fichier autonome (le set + les scenes qu'il utilise ; les medias restent des chemins)
EXPORT_FORMAT = "casual-overlay-set"
EXPORT_VERSION = 1


def export_set(book: SceneBook, set_name: str | None = None) -> dict:
    data = book._load()
    name = set_name or data["active_set"]
    i = book._find(data["sets"], name)
    if i is None:
        raise ValueError(f"set inconnu: {name}")
    st = data["sets"][i]
    used = []
    for e in st["entries"]:
        if e["scene"] not in used:
            used.append(e["scene"])
    lib = {x["name"]: x for x in data["scenes"]}
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION,
            "set": {"name": st["name"], "vj": st["vj"], "entries": json.loads(json.dumps(st["entries"]))},
            "scenes": [json.loads(json.dumps(lib[n])) for n in used]}


def parse_import(raw) -> dict:
    """Valide le contenu d'un fichier de set (texte JSON ou dict). Leve ValueError avec un message clair, sans rien modifier."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError("ce fichier n'est pas un set (JSON illisible)") from None
    if not isinstance(raw, dict) or raw.get("format") != EXPORT_FORMAT:
        raise ValueError("ce fichier n'est pas un set de casual-overlay")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("version du set illisible")
    if version > EXPORT_VERSION:
        raise ValueError(f"set d'une version plus recente (v{version}) : mets casual-overlay a jour pour l'ouvrir")
    st = raw.get("set")
    if not isinstance(st, dict) or not isinstance(st.get("name"), str) or not st["name"].strip():
        raise ValueError("set sans nom")
    scenes_in = raw.get("scenes")
    lib = [x for x in scenes_in if isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"].strip()] \
        if isinstance(scenes_in, list) else []
    known = {x["name"].lower() for x in lib}
    entries = []
    for e in st.get("entries") if isinstance(st.get("entries"), list) else []:
        if isinstance(e, dict) and isinstance(e.get("scene"), str) and e["scene"].lower() in known:
            entries.append(SceneBook._entry(e, e["scene"]))
    return {"set": {"name": st["name"].strip()[:MAX_NAME], "entries": entries, "vj": SceneBook._clean_vj(st.get("vj"))},
            "scenes": lib}


def _same(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def unique_name(name: str, taken: list[str]) -> str:
    """`name`, ou `name (2)`, `name (3)`... jamais un nom de `taken` (casse ignoree), dans la limite de MAX_NAME."""
    low = {x.lower() for x in taken}
    if name.lower() not in low:
        return name
    n = 2
    while True:
        suffix = f" ({n})"
        cand = name[:max(MAX_NAME - len(suffix), 1)] + suffix
        if cand.lower() not in low:
            return cand
        n += 1


def import_conflicts(book: SceneBook, parsed: dict) -> dict:
    """Ce qui est deja pris : `set` (le nom du set existe) et `scenes` (noms de scenes qui existent avec un autre contenu ;
    une scene strictement identique n'est pas un conflit, on la reutilise)."""
    data = book._load()
    clash = []
    for scene in parsed["scenes"]:
        i = book._find(data["scenes"], scene["name"])
        if i is not None and not _same(data["scenes"][i], scene):
            clash.append(scene["name"])
    return {"set": book._find(data["sets"], parsed["set"]["name"]) is not None, "scenes": clash}


def import_set(book: SceneBook, parsed: dict, set_mode: str = "rename", scene_mode: str = "rename") -> dict:
    """Ajoute le set importe. `set_mode` / `scene_mode` : "rename" (le nouveau prend un autre nom : `nom (2)`) ou "replace"
    (ecrase l'existant). Une scene identique a celle de la bibliotheque est reutilisee. Ecriture en une seule fois.
    Renvoie {"set", "renamed": {ancien: nouveau}, "replaced": [scenes], "added": [scenes], "missing": [fichiers de medias]}."""
    data = book._load()
    renamed, replaced, added = {}, [], []
    set_renamed = None
    mapping = {}
    for scene in parsed["scenes"]:
        scene = json.loads(json.dumps(scene))
        i = book._find(data["scenes"], scene["name"])
        if i is None:
            data["scenes"].append(scene)
            added.append(scene["name"])
            mapping[scene["name"].lower()] = scene["name"]
        elif _same(data["scenes"][i], scene):
            mapping[scene["name"].lower()] = data["scenes"][i]["name"]
        elif scene_mode == "replace":
            scene["name"] = data["scenes"][i]["name"]
            data["scenes"][i] = scene
            replaced.append(scene["name"])
            mapping[scene["name"].lower()] = scene["name"]
        else:
            orig = scene["name"]
            new = unique_name(orig, [x["name"] for x in data["scenes"]])
            renamed[orig] = new
            scene["name"] = new
            data["scenes"].append(scene)
            added.append(new)
            mapping[orig.lower()] = new
    entries = []
    for e in parsed["set"]["entries"]:
        target = mapping.get(e["scene"].lower())
        if target is not None:
            entries.append({**e, "scene": target})
    st = {"name": parsed["set"]["name"], "entries": entries, "vj": parsed["set"]["vj"]}
    j = book._find(data["sets"], st["name"])
    if j is not None and set_mode == "replace":
        st["name"] = data["sets"][j]["name"]
        data["sets"][j] = st
    elif j is not None:
        new = unique_name(st["name"], [x["name"] for x in data["sets"]])
        set_renamed = (st["name"], new)
        st["name"] = new
        data["sets"].append(st)
    else:
        data["sets"].append(st)
    book._save(data)
    missing = sorted({m for e in entries for m in [media_missing(e["media"]), *a2w_missing(e.get("a2w"))] if m})
    return {"set": st["name"], "set_renamed": set_renamed, "renamed": renamed, "replaced": replaced, "added": added,
            "missing": missing}
