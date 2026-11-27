"""Automations de reglages : courbes bouclees, moteur (une valeur = fonction pure du temps), liaison fond / logo des effets.

Stdlib seulement. Le moteur tourne dans le fil de rendu ; la GUI n'en est que l'editeur.
"""
from __future__ import annotations

import math
import random

from core.controls import effective_tempo

MIN_PERIOD_S = 0.3              # periode de courbe la plus courte (curseur « Vitesse » de l'editeur)

# --- Automations de reglages ------------------------------------------------------------
# Meme principe que "Variation automatique" d'audio2wave (voir son CLAUDE.md): chaque reglage
# automatise suit sa PROPRE courbe (AUTOMATION_POINTS points de controle boucles, interpoles
# lineairement) a sa PROPRE vitesse, et la valeur est une fonction pure du temps. Le moteur
# tourne ici, dans le fil de rendu (precision a l'image, et ca marche aussi sans --gui) ;
# la GUI reprend l'editeur de courbes d'audio2wave et ne fait que lire/ecrire
# params["_automation"].
AUTOMATION_POINTS = 12       # meme valeur que AUTOMATE_CURVE_POINTS d'audio2wave (compatibilite des editeurs)

# cle de params -> (libelle, min, max, courbe, periode en s, active par defaut). Min/max = plage
# de la courbe = TOUTE la plage du curseur de la GUI : le bas de l'editeur = 0 % du curseur, le haut = 100 %.
# Periodes volontairement premieres entre elles (pas de multiple commun) pour que
# l'ensemble ne se repete jamais a l'identique. Actives par defaut: ce qui donne de la vie sans
# rien deplacer (teinte, angle, carreaux, intensites d'effets); position, taille et opacite du
# logo sont proposees mais coupees (une marque ne doit pas se promener sans qu'on l'ait voulu).
AUTOMATION_SPECS: dict[str, tuple] = {
    "bg_hue":           ("Teinte", 0.0, 1.0, "sinus", 79, True),
    "bg_angle":         ("Angle du degrade", 0.0, 360.0, "sinus", 53, True),
    "bg_speed":         ("Vitesse", 0.0, 4.0, "sinus", 37, True),
    "bg_tile":          ("Taille des carreaux", 20.0, 300.0, "sinus", 29, True),
    "bg_checker":       ("Contraste du damier", 0.0, 1.0, "sinus", 23, True),
    "bg_flip":          ("Cadence du damier", 0.0, 8.0, "triangle", 47, True),
    # Palette labyrinthe : ni taille par defaut (recopie de l'etat), ni symetrie (brutal).
    "bg_rd_shape":      ("Forme du labyrinthe", 0.0, 1.0, "sinus", 73, True),
    "bg_rd_offset":     ("Ecart des couches", 0.0, 0.05, "sinus", 89, True),
    "bg_rd_mix":        ("Variete des zones", 0.0, 1.0, "triangle", 97, True),
    "bg_rd_layers":     ("Epaisseur des couches", 0.0, 1.0, "sinus", 101, True),
    "bg_rd_free":       ("Liberte de la symetrie", 0.0, 1.0, "sinus", 103, True),
    "bg_rd_void":       ("Vide du labyrinthe", 0.0, 1.0, "sinus", 107, True),
    # Masque d'expansion (#178) : coupes par defaut (un masque qui bouge tout seul change l'image : a activer expres)
    "bg_rd_mask_size":  ("Masque : taille", 0.1, 0.9, "sinus", 131, False),
    "bg_rd_mask_angle": ("Masque : angle", 0.0, 360.0, "sinus", 137, False),
    "bg_rd_mask_soft":  ("Masque : douceur", 0.0, 1.0, "sinus", 139, False),
    "bg_rd_mask_drift": ("Masque : derive", 0.0, 2.0, "sinus", 149, False),
    "bg_rd_scale":      ("Taille du motif", 0.3, 2.0, "sinus", 127, False),     # coupee par defaut : chaque changement de taille floute un peu le motif
    "logo_x":           ("Position X", 0.0, 1.0, "sinus", 43, False),
    "logo_y":           ("Position Y", 0.0, 1.0, "sinus", 31, False),
    "logo_scale":       ("Taille (image)", 0.05, 0.95, "sinus", 13, False),
    "text_scale":       ("Taille du texte", 0.03, 0.6, "sinus", 13, False),
    "logo_opacity":     ("Opacite", 0.0, 1.0, "sinus", 19, False),
    "logo_pulse":       ("Pulsation", 0.0, 0.5, "sinus", 21, True),
    "logo_glow":        ("Contour lumineux", 0.0, 3.0, "sinus", 11, True),
    "logo_glow_radius": ("Rayon du contour", 0.0, 3.0, "sinus", 17, True),
    # Halo holographique (#247) : coupes par defaut (un look existant ne bouge pas tout seul)
    "holo_intensity":   ("Halo : intensite", 0.0, 3.0, "sinus", 109, False),
    "holo_reach":       ("Halo : portee", 0.3, 3.0, "sinus", 113, False),
    "holo_warp":        ("Halo : deformation", 0.0, 3.0, "sinus", 131, False),
    "holo_dust":        ("Halo : poussiere", 0.0, 3.0, "sinus", 137, False),
    "holo_speed":       ("Halo : vitesse", 0.0, 1.0, "sinus", 139, False),
    "fx_int0":          ("Intensite wobble", 0.0, 2.0, "sinus", 19, True),
    "fx_int1":          ("Intensite onde de choc", 0.0, 2.0, "sinus", 41, True),
    "fx_int2":          ("Intensite aberration", 0.0, 2.0, "sinus", 7, True),
    "fx_int3":          ("Intensite glitch", 0.0, 2.0, "aleatoire", 61, True),
    "fx_int4":          ("Intensite logo", 0.0, 2.0, "sinus", 59, True),
    # Effets de la couche logo (utiles seulement quand fx_link = 0). Periodes differentes de celles
    # du fond: les deux couches ne respirent pas en phase.
    "fxl_int0":         ("Logo: intensite wobble", 0.0, 2.0, "sinus", 67, True),
    "fxl_int1":         ("Logo: intensite onde de choc", 0.0, 2.0, "sinus", 31, True),
    "fxl_int2":         ("Logo: intensite aberration", 0.0, 2.0, "sinus", 71, True),
    "fxl_int3":         ("Logo: intensite glitch", 0.0, 2.0, "aleatoire", 83, True),
}

# Anciennes plages des courbes (avant la version 2, quand une courbe ne couvrait qu'une partie du curseur). Elles servent a deux choses :
#  * les courbes PAR DEFAUT restent dans ces plages (zones ou le motif reste vivant : rien ne change pour qui ne touche a rien) ;
#  * une courbe sauvee avant la version 2 (presets, scenes, sets, reglages) est convertie a la lecture, a l'identique (memes valeurs).
DEFAULT_RANGES: dict[str, tuple[float, float]] = {
    "bg_hue": (0.0, 0.5), "bg_angle": (20.0, 160.0), "bg_speed": (0.4, 1.8), "bg_tile": (50.0, 150.0), "bg_checker": (0.06, 0.32),
    "bg_flip": (0.5, 3.5), "bg_rd_shape": (0.3, 0.9), "bg_rd_offset": (0.004, 0.030), "bg_rd_mix": (0.2, 0.9), "bg_rd_layers": (0.1, 0.8),
    "bg_rd_free": (0.2, 0.8), "bg_rd_void": (0.15, 0.6), "bg_rd_scale": (0.6, 1.6),
    "bg_rd_mask_size": (0.3, 0.7), "bg_rd_mask_angle": (0.0, 360.0), "bg_rd_mask_soft": (0.1, 0.8), "bg_rd_mask_drift": (0.1, 1.2), "logo_x": (0.35, 0.65), "logo_y": (0.42, 0.58),
    "logo_scale": (0.30, 0.42), "text_scale": (0.12, 0.20), "logo_opacity": (0.6, 1.0), "logo_pulse": (0.05, 0.30), "logo_glow": (0.4, 1.5),
    "logo_glow_radius": (0.6, 1.6), "fx_int0": (0.4, 1.2), "fx_int1": (0.4, 1.3), "fx_int2": (0.3, 1.2), "fx_int3": (0.15, 1.0),
    "holo_intensity": (0.5, 1.8), "holo_reach": (0.8, 1.8), "holo_warp": (0.4, 1.6), "holo_dust": (0.4, 1.6), "holo_speed": (0.05, 0.4),
    "fx_int4": (0.6, 1.2), "fxl_int0": (0.4, 1.2), "fxl_int1": (0.4, 1.3), "fxl_int2": (0.3, 1.2), "fxl_int3": (0.15, 1.0),
}
CURVE_VERSION = 2            # marque « v » de chaque entree : 2 = points sur toute la plage du curseur ; absente = ancienne plage (a convertir)


def rescale_points(points, src: tuple[float, float], dst: tuple[float, float]) -> list[float]:
    """Points 0..1 exprimes sur la plage `src` -> les memes valeurs exprimees sur `dst` (bornes a 0..1)."""
    (a, b), (c, d) = src, dst
    span = (d - c) or 1.0
    return [min(max((a + (b - a) * float(p) - c) / span, 0.0), 1.0) for p in points]


def legacy_to_full(key: str, points) -> list[float]:
    """Points d'une courbe d'avant la version 2 (plage `DEFAULT_RANGES`) -> points sur toute la plage du curseur."""
    spec = AUTOMATION_SPECS[key]
    return rescale_points(points, DEFAULT_RANGES.get(key, (spec[1], spec[2])), (spec[1], spec[2]))

FX_EFFECTS = 4               # wobble, ripple, chroma, glitch: les effets qui se deforment par couche


def curve_points(name: str, n: int = AUTOMATION_POINTS, seed: int = 7) -> list[float]:
    """Courbe de depart (valeurs 0..1, bouclee): memes formes que les presets d'audio2wave."""
    if name == "triangle":
        return [2 * (i / n) if i / n <= 0.5 else 2 * (1 - i / n) for i in range(n)]
    if name == "carre":
        return [1.0 if i / n < 0.5 else 0.0 for i in range(n)]
    if name == "dents":
        return [i / n for i in range(n)]
    if name == "aleatoire":
        rng = random.Random(seed)
        return [rng.random() for _ in range(n)]
    return [0.5 + 0.5 * math.sin(2 * math.pi * i / n) for i in range(n)]


def default_automation() -> dict:
    """Etat initial {cle: {"enabled", "points", "period", "v"}}: celui de AUTOMATION_SPECS ; la forme de depart tient dans
    l'ancienne plage (`DEFAULT_RANGES`), exprimee sur toute la plage du curseur."""
    return {key: {"enabled": bool(spec[5]), "points": legacy_to_full(key, curve_points(spec[3], seed=i)), "period": float(spec[4]),
                  "v": CURVE_VERSION}
            for i, (key, spec) in enumerate(AUTOMATION_SPECS.items())}


def merge_automation(saved) -> dict:
    """Etat sauve valide, complete par les valeurs par defaut (cle absente ou entree invalide). Une courbe sans la marque
    `v` = `CURVE_VERSION` (sauvee avant la version 2) est convertie de son ancienne plage a toute la plage du curseur :
    elle donne exactement les memes valeurs. Toute entree rendue porte la marque (jamais deux conversions)."""
    merged = default_automation()
    if isinstance(saved, dict):
        for key, entry in saved.items():
            if key not in merged or not isinstance(entry, dict):
                continue
            points = entry.get("points")
            if isinstance(points, list) and len(points) == AUTOMATION_POINTS \
                    and all(isinstance(v, (int, float)) for v in points):
                clean = [min(max(float(v), 0.0), 1.0) for v in points]
                merged[key]["points"] = clean if entry.get("v") == CURVE_VERSION else legacy_to_full(key, clean)
            if isinstance(entry.get("period"), (int, float)) and entry["period"] > 0:
                merged[key]["period"] = float(entry["period"])
            if "enabled" in entry:
                merged[key]["enabled"] = bool(entry["enabled"])
    return merged


def _list_key(key: str):
    """'fx_int2' -> ('fx_int', 2), 'fxl_int0' -> ('fxl_int', 0), sinon None (reglage scalaire)."""
    for prefix in ("fx_int", "fxl_int"):
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            return prefix, int(key[len(prefix):])
    return None


def get_param(params: dict, key: str):
    lk = _list_key(key)
    return params[lk[0]][lk[1]] if lk else params[key]


def set_param(params: dict, key: str, value: float) -> None:
    lk = _list_key(key)
    if lk:
        params[lk[0]][lk[1]] = value
    else:
        params[key] = value


def set_fx_link(params: dict, linked: bool) -> None:
    """Lie ou delie les effets du logo et du fond. En deliant, les reglages du logo partent de ceux du
    fond (rien ne saute a l'ecran)."""
    if not linked and float(params["fx_link"]) >= 0.5:
        for i in range(FX_EFFECTS):
            params["fxl_on"][i] = params["fx_on"][i]
            params["fxl_int"][i] = params["fx_int"][i]
    params["fx_link"] = 1.0 if linked else 0.0


class AutomationEngine:
    """Applique les automations a `params` (fil de rendu, une fois par image). Une automation
    repart du debut de sa courbe chaque fois qu'elle est (re)activee ; `auto_master` = 0 fige
    toutes les valeurs la ou elles sont (sans toucher aux interrupteurs individuels)."""

    def __init__(self):
        self._start: dict[str, float] = {}
        self._last: float | None = None
        self._clock = 0.0                  # temps des courbes : avance au rythme du tempo (params["tempo"])

    def step(self, params: dict, now: float) -> None:
        gap = 0.0 if self._last is None else max(now - self._last, 0.0)
        self._last = now
        self._clock += gap * effective_tempo(params)
        now = self._clock
        data = params.get("_automation")
        if not isinstance(data, dict):
            return
        if float(params.get("auto_master", 1.0)) < 0.5:
            self._start.clear()            # a la reprise, chaque courbe repart de son debut
            return
        for key, entry in data.items():
            spec = AUTOMATION_SPECS.get(key)
            if spec is None or not entry.get("enabled"):
                self._start.pop(key, None)
                continue
            points = entry["points"]
            n = len(points)
            start = self._start.setdefault(key, now)
            pos = ((now - start) / max(MIN_PERIOD_S, float(entry["period"])) % 1.0) * n
            i0 = int(pos) % n
            frac = pos - int(pos)
            v = points[i0] + (points[(i0 + 1) % n] - points[i0]) * frac
            set_param(params, key, spec[1] + (spec[2] - spec[1]) * v)
