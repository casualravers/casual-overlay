"""**Full Randomizer** (ordre VJ `alea`) : tire une scene temporaire (fond + overlay + media) a chaque changement du VJ.

Tout est pur (aucun Tk, aucun fichier ecrit, graine injectable) : testable sans fenetre. Le VJ (gl_gui) appelle `draw_scene` et
applique la scene rendue avec le meme chemin que les scenes enregistrees ; « Garder cette scene » l'enregistre telle quelle.

- **Deux banques d'assets**, chacune avec autant de dossiers que l'on veut : **FOND** (videos plein cadre, pour le fond) et **OVERLAY**
  (images, animations et videos, pour le logo). C'est le dossier qui classe, **jamais l'extension** (un .mov peut etre une animation
  a fond transparent comme une video) : pour qu'une video serve aux deux, on declare son dossier dans les deux banques.
  `scan_bank(dossiers, "fond" | "overlay")` -> `Asset(path, kind, category)` ; la categorie est le premier sous-dossier sous le
  dossier declare ; `kind` (`image` / `video`) dit seulement quelle source du logo utiliser (le fond n'accepte que des videos).
- **Fond** (une seule source par tirage, parmi `bg_sources` cochees ET disponibles) : `preset` (preset de fond genere), `a2w` (preset
  d'audio2wave du mode courant : pas de changement de mode), `video` (video de la banque), `params` (**config aleatoire** : soit un fond
  genere aux valeurs tirees dans les plages des curseurs, soit un **visuel audio2wave** dans un mode (Live / Snap / Ridge, parmi `modes`)
  et un preset de ce mode tires au hasard : changer de mode reconstruit la fenetre, environ 1 s, masque en partie par le flash).
- **Overlay** : une base, **un preset overlay** (`overlay_preset`) ou **une config entierement nouvelle** (`params` : logo, contour,
  effets, halo / fonte / cellules, noise tires au hasard, sans preset) ; des effets du logo allumes au hasard par-dessus un preset
  (`fx`, au plus `MAX_COSTLY` effets couteux a la fois : halo / fonte / cellules, pour ne pas declencher le garde-fou GPU) ; un media
  de la banque (`media`). Fond et overlay « config aleatoire » peuvent etre enregistres en presets (voir la GUI).
- **Verrous** par couche (`lock_bg`, `lock_media`, `lock_fx`) : la couche gardee reprend l'etat courant.
- **Overlay toujours centre et statique** : tout tirage pose `logo_x` = `logo_y` = 0,5 et coupe l'automation de position (un overlay
  decentre ou qui se promene ne rend jamais bien), meme pour un preset qui le decale et meme avec le verrou des effets.
- Jamais deux fois de suite le meme fond / media / preset overlay quand une alternative existe.
"""
from __future__ import annotations

import colorsys
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp")
VIDEO_EXT = (".gif", ".webm", ".mp4", ".mov", ".mkv", ".avi", ".webp", ".apng")
ROOT_CATEGORY = "(racine)"        # categorie des fichiers poses directement dans le dossier declare
MAX_FILES = 5000                 # garde-fou : un dossier enorme ne bloque pas la fenetre
MAX_DEPTH = 4
CENTER = {"logo_x": 0.5, "logo_y": 0.5}      # l'overlay du Full Randomizer est toujours centre
MAX_COSTLY = 2                   # halo + fonte + cellules : jamais les trois ensemble
BG_SOURCES = ("preset", "a2w", "video", "params")
A2W_MODES = ("live", "snap", "ridge")
OVERLAY_PARTS = ("overlay_preset", "params", "fx", "media", "text")
DEFAULT_TEXT = "CASUAL RAVERS"      # texte tire comme logo quand « texte » est coche (modifiable dans le panneau)
MAX_TEXT = 200
DEFAULTS = {
    "folders_fond": [],          # dossiers de la banque FOND (chemins)
    "folders_overlay": [],       # dossiers de la banque OVERLAY
    "categories_fond": [],       # sous-dossiers retenus dans chaque banque ([] = tous)
    "categories_overlay": [],
    "bg_sources": list(BG_SOURCES),
    "overlay": list(OVERLAY_PARTS),
    "text": DEFAULT_TEXT,
    "modes": list(A2W_MODES),    # modes audio2wave que la config aleatoire du fond peut tirer
    "lock_bg": False, "lock_media": False, "lock_fx": False,
}
MEDIA_KEYS = ("logo_source", "logo_path", "logo_video", "logo_key", "text_content", "text_font", "text_color", "text_align")
ACID_COLORS = ("#a6ff00", "#00e5ff", "#ff3df2", "#ffd400", "#ff5a36")
GLOW_COLORS = ("#3b82f6", "#ff3df2", "#00e5ff", "#a6ff00", "#ffffff", "#ffd400", "#b392ff")


@dataclass(frozen=True)
class Asset:
    path: str
    kind: str                    # "image" | "video"
    category: str                # premier sous-dossier (ROOT_CATEGORY = a la racine du dossier declare)


def clean_settings(raw) -> dict:
    """Reglages du hasard valides, completes par les valeurs par defaut (jamais d'exception sur un fichier abime)."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    if not isinstance(raw, dict):
        return out
    for key in ("folders_fond", "folders_overlay", "categories_fond", "categories_overlay"):
        if isinstance(raw.get(key), list):
            out[key] = [str(x) for x in raw[key] if isinstance(x, str) and x.strip()][:50]
    for key, allowed in (("bg_sources", BG_SOURCES), ("overlay", OVERLAY_PARTS), ("modes", A2W_MODES)):
        if isinstance(raw.get(key), list):
            out[key] = [x for x in allowed if x in raw[key]]
    if isinstance(raw.get("text"), str):
        out["text"] = raw["text"].replace("\r", "").strip()[:MAX_TEXT]           # vide = pas de texte tire
    for key in ("lock_bg", "lock_media", "lock_fx"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    return out


def asset_kind(path: str, bank: str = "overlay") -> str | None:
    """`image` ou `video` d'apres l'extension (c'est tout ce qu'elle dit : voir l'en-tete), None si le fichier n'est pas retenu :
    la banque FOND n'accepte que des videos."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image" if bank == "overlay" else None
    if ext in VIDEO_EXT:
        return "video"
    return None


def scan_bank(folders: list[str], bank: str = "overlay", max_files: int = MAX_FILES) -> list[Asset]:
    """Les fichiers retenus des dossiers d'une banque (`fond` ou `overlay`, sous-dossiers compris, `MAX_DEPTH` niveaux), tries :
    meme dossier = meme banque. Un dossier introuvable est ignore."""
    found: list[Asset] = []
    seen: set[str] = set()
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            rel = Path(current).relative_to(root)
            if len(rel.parts) >= MAX_DEPTH:
                dirs[:] = []
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.startswith("."):
                    continue
                kind = asset_kind(name, bank)
                path = str(Path(current) / name)
                if kind is None or path in seen:
                    continue
                seen.add(path)
                found.append(Asset(path, kind, rel.parts[0] if rel.parts else ROOT_CATEGORY))
                if len(found) >= max_files:
                    return found
    return found


def categories(bank: list[Asset]) -> list[str]:
    return sorted({a.category for a in bank})


def usable(bank: list[Asset], categories: list[str] | None, kind: str | None = None) -> list[Asset]:
    """Les assets retenus par les categories cochees ([] = toutes), du type voulu, dont le fichier existe encore."""
    wanted = set(categories or [])
    return [a for a in bank if (not wanted or a.category in wanted) and (kind is None or a.kind == kind)
            and os.path.isfile(a.path)]


def media_for(asset: Asset) -> dict:
    """Reglages du logo pour un asset : image -> source image ; video / animation -> source video (sans detourage)."""
    if asset.kind == "image":
        return {"logo_source": "image", "logo_path": asset.path}
    return {"logo_source": "video", "logo_video": asset.path, "logo_key": ""}


NAME_WORDS_A = ("Neon", "Acide", "Vapeur", "Cobalt", "Turbo", "Solaire", "Plasma", "Chrome", "Onyx", "Cosmique", "Fievre", "Rubis",
                "Glacial", "Sauvage", "Electrique", "Opale", "Furtif", "Magnetique", "Abyssal", "Laser")
NAME_WORDS_B = ("Panthere", "Orage", "Comete", "Mirage", "Tigre", "Cyclone", "Dragon", "Prisme", "Rafale", "Vortex", "Serpent", "Aurore",
                "Meteore", "Tornade", "Faucon", "Eclipse", "Nebuleuse", "Pulsar", "Tempete", "Nova")


def random_name(rng: random.Random, taken) -> str:
    """Nom aleatoire (`Neon Comete`) pour une scene, jamais un nom de `taken` (casse ignoree) : `Neon Comete 2` en dernier recours."""
    used = {str(x).lower() for x in taken}
    for _ in range(50):
        name = f"{rng.choice(NAME_WORDS_A)} {rng.choice(NAME_WORDS_B)}"
        if name.lower() not in used:
            return name
    base = f"{rng.choice(NAME_WORDS_A)} {rng.choice(NAME_WORDS_B)}"
    n = 2
    while f"{base} {n}".lower() in used:
        n += 1
    return f"{base} {n}"


def text_media(rng: random.Random, text: str) -> dict:
    """Le texte comme logo : source texte, contenu donne, couleur tiree (lisible sur un fond sombre), centre ; la police reste celle du moment."""
    return {"logo_source": "text", "text_content": text, "text_color": rng.choice(GLOW_COLORS), "text_align": "center"}


def _pick(rng: random.Random, items: list, avoid=None, key=lambda x: x):
    """Un element au hasard, pas celui de `avoid` s'il y en a un autre."""
    if not items:
        return None
    others = [x for x in items if key(x) != avoid]
    return rng.choice(others or items)


def random_background_params(rng: random.Random) -> dict:
    """Fond genere aux valeurs aleatoires, dans les plages des curseurs (jamais la palette banc de test)."""
    palette = rng.choice(("classic", "duo", "duo"))
    values = {"bg_palette": palette, "bg_hue": round(rng.random(), 3), "bg_angle": round(rng.uniform(0, 180), 1),
              "bg_speed": round(rng.uniform(0.4, 1.8), 2), "bg_tile": round(rng.uniform(50, 150), 1),
              "bg_checker": round(rng.uniform(0.06, 0.32), 3), "bg_flip": round(rng.uniform(0.5, 3.5), 2),
              "bg_react": round(rng.uniform(0.3, 1.4), 2)}
    if palette == "duo":
        h = rng.random()
        for key, hue in (("bg_color1", h), ("bg_color2", (h + rng.uniform(0.15, 0.55)) % 1.0)):
            r, g, b = colorsys.hsv_to_rgb(hue, rng.uniform(0.55, 1.0), rng.uniform(0.35, 1.0) if key == "bg_color1" else 1.0)
            values[key] = "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    return values


def random_effects(rng: random.Random) -> dict:
    """Effets du logo allumes au hasard (au plus MAX_COSTLY couteux) avec des reglages moderes. Les effets non tires sont coupes
    explicitement : le tirage ne depend pas de l'etat d'avant."""
    out: dict = {"holo_on": 0.0, "melt_on": 0.0, "cell_on": 0.0}
    weights = {"holo": 0.6, "melt": 0.3, "cell": 0.3}
    chosen = [n for n, w in weights.items() if rng.random() < w]
    rng.shuffle(chosen)
    for name in chosen[:MAX_COSTLY]:
        if name == "holo":
            out.update(holo_on=1.0, holo_intensity=round(rng.uniform(0.6, 1.6), 2), holo_reach=round(rng.uniform(1.0, 1.8), 2),
                       holo_warp=round(rng.uniform(0.5, 1.4), 2), holo_dust=round(rng.uniform(0.5, 1.5), 2),
                       holo_speed=round(rng.uniform(0.08, 0.3), 3))
        elif name == "melt":
            out.update(melt_on=1.0, melt_depth=round(rng.uniform(0.5, 0.95), 2), melt_period=round(rng.uniform(8, 20), 1),
                       melt_scale=round(rng.uniform(0.7, 1.6), 2), melt_color=rng.choice(ACID_COLORS))
        else:
            out.update(cell_on=1.0, cell_amount=round(rng.uniform(0.35, 0.85), 2), cell_fusion=round(rng.uniform(0.7, 1.1), 2),
                       cell_scale=round(rng.uniform(0.8, 1.5), 2), cell_speed=round(rng.uniform(0.15, 0.5), 2),
                       cell_flat=round(rng.uniform(0.6, 0.95), 2))
    fx_on = [1 if rng.random() < 0.7 else 0 for _ in range(5)]
    out.update(fx_on=fx_on, fx_int=[round(rng.uniform(0.5, 1.3), 2) for _ in range(5)],
               noise_amount=round(rng.uniform(0.4, 0.9), 2),
               noise_fx=[1 if rng.random() < 0.35 else 0 for _ in range(5)],
               logo_glow_color=rng.choice(GLOW_COLORS), logo_glow=round(rng.uniform(0.5, 1.5), 2))
    return out


def random_overlay_params(rng: random.Random) -> dict:
    """Overlay entierement nouveau : taille et contour du logo, effets du fond ET de la couche logo (liee ou non), halo / fonte /
    cellules (au plus MAX_COSTLY), noise. Reglages moderes et limites a des valeurs sures. Toujours centre (`CENTER`). Ni media, ni
    automations (celles du moment restent)."""
    out = random_effects(rng)
    link = 1.0 if rng.random() < 0.5 else 0.0
    out.update(
        fx_link=link, fxl_on=[1 if rng.random() < 0.6 else 0 for _ in range(4)],
        fxl_int=[round(rng.uniform(0.5, 1.3), 2) for _ in range(4)],
        master=round(rng.uniform(0.8, 1.3), 2),
        logo_scale=round(rng.uniform(0.28, 0.45), 3), text_scale=round(rng.uniform(0.12, 0.2), 3),
        logo_opacity=round(rng.uniform(0.8, 1.0), 2), logo_pulse=round(rng.uniform(0.04, 0.28), 3),
        logo_jitter=round(rng.uniform(0.0, 0.01), 4), logo_glow_radius=round(rng.uniform(0.6, 1.6), 2),
        holo_react=round(rng.choice((0.0, 0.0, 0.4, 1.0)), 2), melt_react=0.0, cell_react=0.0)
    out.update(CENTER)
    return out


def centered(overlay: dict, current_overlay: dict | None) -> dict:
    """`overlay` centre et statique : position au centre, automation de position coupee (celle de l'overlay, sinon celle du moment ;
    rien n'est ajoute si aucune n'est allumee, pour ne pas reinitialiser les autres automations)."""
    out = dict(overlay)
    out.update(CENTER)
    auto = out.get("_automation") or (current_overlay or {}).get("_automation")
    if isinstance(auto, dict) and any(isinstance(auto.get(k), dict) and auto[k].get("enabled") for k in CENTER):
        auto = {k: dict(v) if isinstance(v, dict) else v for k, v in auto.items()}
        for key in CENTER:
            if isinstance(auto.get(key), dict):
                auto[key]["enabled"] = False
        out["_automation"] = auto
    return out


@dataclass
class Context:
    """Ce dont le tirage a besoin (rempli par la GUI ; tout est injectable pour les tests)."""
    bank_fond: list = field(default_factory=list)             # [Asset] banque FOND (videos)
    bank_overlay: list = field(default_factory=list)          # [Asset] banque OVERLAY (images, animations, videos)
    bg_presets: dict = field(default_factory=dict)            # nom -> preset de fond
    overlay_presets: dict = field(default_factory=dict)       # nom -> preset overlay
    a2w_presets: dict = field(default_factory=dict)           # nom -> etat du panneau d'audio2wave du mode courant
    a2w_modes: dict = field(default_factory=dict)             # mode -> {nom -> etat} pour chaque mode (config aleatoire du fond)
    mode: str = "live"                                        # mode audio2wave courant
    current_scene: dict | None = None                         # etat courant (capture_scene) : sert aux verrous
    last: dict = field(default_factory=dict)                  # enregistrement du tirage precedent


def fond_saveable(scene: dict | None) -> bool:
    """Le fond de la scene peut-il devenir un preset de fond ? Un fond genere, ou une video qui existe. Pas un fond audio2wave
    (il a ses propres presets) ni un fond « video » sans fichier choisi (ecran noir : rien a enregistrer)."""
    scene = scene or {}
    fond = scene.get("fond")
    if scene.get("bg") not in ("pattern", "video") or not isinstance(fond, dict):
        return False
    if scene["bg"] == "video":
        path = str(fond.get("bg_video") or "")
        return bool(path) and Path(path).is_file()
    return True


def draw_scene(rng: random.Random, settings: dict, ctx: Context) -> tuple[dict, str, dict]:
    """Une scene temporaire `(scene, resume, enregistrement)` : `scene` au format des scenes enregistrees (nom `Full Randomizer`),
    `resume` = une ligne pour le message de la GUI, `enregistrement` = a rendre dans `ctx.last` au tirage suivant."""
    settings = clean_settings(settings)
    last, cur = ctx.last or {}, ctx.current_scene or {}
    notes: list[str] = []
    record: dict = {}

    # ------------------------------------------------------------------ fond
    scene: dict
    if settings["lock_bg"] and cur:
        scene = {"bg": cur.get("bg", "live"), "mode": cur.get("mode"), "live_overrides": None,
                 "fond": cur.get("fond")}
        record["bg"] = last.get("bg")
        notes.append("fond verrouille")
    else:
        videos = usable(ctx.bank_fond, settings["categories_fond"])
        options = {}
        if "preset" in settings["bg_sources"] and ctx.bg_presets:
            options["preset"] = sorted(ctx.bg_presets)
        if "a2w" in settings["bg_sources"] and ctx.a2w_presets:
            options["a2w"] = sorted(n for n, v in ctx.a2w_presets.items() if v)      # `default` (vide) ne change rien
        if "video" in settings["bg_sources"] and videos:
            options["video"] = videos
        gen_ok = "params" in settings["bg_sources"]
        a2w_choices = [(m, n) for m in settings["modes"] for n in sorted(ctx.a2w_modes.get(m) or {})]
        if gen_ok:
            options["params"] = [None]
        options = {k: v for k, v in options.items() if v}
        if not options:
            scene = {"bg": "live", "mode": ctx.mode, "live_overrides": None, "fond": None}
            notes.append("fond: aucune source disponible")
        else:
            kind = _pick(rng, sorted(options), avoid=(last.get("bg") or "").split(":")[0] if len(options) > 1 else None)
            if kind == "preset":
                name = _pick(rng, options["preset"], avoid=(last.get("bg") or "")[7:] if last.get("bg", "").startswith("preset:") else None)
                preset = ctx.bg_presets[name]
                if preset.get("bg_mode") == "live":                       # preset de fond « audio2wave » : on ne touche pas au panneau
                    scene = {"bg": "live", "mode": ctx.mode, "live_overrides": None, "fond": None}
                else:
                    scene = {"bg": "pattern", "mode": None, "live_overrides": None, "fond": dict(preset, bg_mode="pattern")}
                record["bg"] = f"preset:{name}"
                notes.append(f"fond: preset {name}")
            elif kind == "a2w":
                name = _pick(rng, options["a2w"], avoid=(last.get("bg") or "")[4:] if last.get("bg", "").startswith("a2w:") else None)
                scene = {"bg": "live", "mode": ctx.mode, "live_overrides": dict(ctx.a2w_presets[name]), "fond": None}
                record["bg"] = f"a2w:{name}"
                notes.append(f"fond: audio2wave {name}")
            elif kind == "video":
                asset = _pick(rng, options["video"], avoid=(last.get("bg") or "")[6:] if last.get("bg", "").startswith("video:") else None,
                              key=lambda a: a.path)
                fit = rng.choice(("cover", "cover", "stretch"))
                scene = {"bg": "video", "mode": None, "live_overrides": None,
                         "fond": {"bg_mode": "video", "bg_video": asset.path, "bg_video_fit": fit}}
                record["bg"] = f"video:{asset.path}"
                record["bg_video"] = asset.path
                notes.append(f"fond: video {Path(asset.path).name}")
            else:
                # config aleatoire : un fond genere tout neuf, ou un visuel audio2wave (mode + preset de ce mode au hasard)
                subs = ["gen"] + (["a2w"] if a2w_choices else [])
                sub = _pick(rng, subs, avoid=(last.get("bg") or "").split(":")[1] if len(subs) > 1 and (last.get("bg") or "").startswith("params:") else None)
                if sub == "a2w":
                    mode, name = _pick(rng, a2w_choices, avoid=tuple((last.get("bg") or "").split(":")[2:4])
                                       if (last.get("bg") or "").startswith("params:a2w:") else None, key=tuple)
                    overrides = dict(ctx.a2w_modes[mode][name]) or None
                    scene = {"bg": "live", "mode": mode, "live_overrides": overrides, "fond": None}
                    record["bg"] = f"params:a2w:{mode}:{name}"
                    notes.append(f"fond: audio2wave {mode} / {name}")
                else:
                    scene = {"bg": "pattern", "mode": None, "live_overrides": None,
                             "fond": dict(random_background_params(rng), bg_mode="pattern")}
                    record["bg"] = "params:gen"
                    notes.append("fond: aleatoire")

    # ------------------------------------------------------------------ overlay
    overlay: dict = {}
    if settings["lock_fx"] and cur.get("overlay") is not None:
        overlay = dict(cur["overlay"])
        notes.append("effets verrouilles")
    else:
        parts = settings["overlay"]
        bases = (["preset"] if "overlay_preset" in parts and ctx.overlay_presets else []) + (["params"] if "params" in parts else [])
        base = _pick(rng, bases, avoid=(last.get("overlay") or "").split(":")[0] if len(bases) > 1 else None)
        fx: dict | None = None
        if base == "preset":
            name = _pick(rng, sorted(ctx.overlay_presets), avoid=(last.get("overlay") or "")[7:]
                         if (last.get("overlay") or "").startswith("preset:") else None)
            overlay = dict(ctx.overlay_presets[name])
            record["overlay"] = f"preset:{name}"
            notes.append(f"overlay: preset {name}")
        elif base == "params":
            overlay = random_overlay_params(rng)
            fx = overlay
            record["overlay"] = "params"
            notes.append("overlay: config aleatoire")
        if base != "params" and "fx" in parts:
            fx = random_effects(rng)
            overlay.update(fx)
        if fx is not None:
            on = [lb for key, lb in (("holo_on", "halo"), ("melt_on", "fonte"), ("cell_on", "cellules")) if fx[key]]
            notes.append("effets: " + (", ".join(on) if on else "sans effet couteux"))
    pool = usable(ctx.bank_overlay, settings["categories_overlay"])
    current_media = {k: v for k, v in (cur.get("overlay") or {}).items() if k in MEDIA_KEYS}
    if settings["lock_media"]:
        media = current_media
        notes.append("media verrouille")
    else:
        # une video deja choisie pour le fond n'est pas aussi le logo (sauf si c'est la seule)
        assets = ([a for a in pool if a.path != record.get("bg_video")] or pool) if "media" in settings["overlay"] else []
        choices = [("asset", a) for a in assets] + ([("text", settings["text"])] if "text" in settings["overlay"] and settings["text"].strip() else [])
        picked = _pick(rng, choices, avoid=last.get("media"), key=lambda c: c[1].path if c[0] == "asset" else "text") if choices else None
        if picked is None:
            media = current_media                 # pas de media a tirer : celui d'avant reste (un preset remettrait le logo par defaut)
            if "media" in settings["overlay"] or "text" in settings["overlay"]:
                notes.append("media: banque vide")
        elif picked[0] == "text":
            media = text_media(rng, settings["text"])
            record["media"] = "text"
            notes.append(f"media: texte {settings['text'].strip().splitlines()[0][:30]}")
        else:
            media = media_for(picked[1])
            record["media"] = picked[1].path
            notes.append(f"media: {Path(picked[1].path).name}")
    overlay.update(media)
    scene.update({"name": "Full Randomizer", "overlay": centered(overlay, cur.get("overlay"))})
    return scene, "  |  ".join(notes), record
