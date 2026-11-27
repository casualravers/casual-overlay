"""Halo holographique (#240), partie pure (stdlib seulement) : champ de distance au contour du sujet.

Le halo ne se construit plus avec un flou de l'alpha a 7 echelles (dont les lignes d'egale valeur lointaines sont des cercles / ellipses, et que
l'intensite met en avant) mais avec la **distance au contour** de la couche du logo (jump flooding a basse resolution, `gl_shaders/holo_jfa.frag`
puis `holo.frag`) : les anneaux sont des copies du contour, qui s'arrondissent doucement loin du sujet. Ce module fournit les formules, repliquees
en GLSL (`holo.frag`, `post.frag`) : portee, arrondi, `v`, plafond de la lumiere lointaine, passes du jump flooding.
Distances en hauteurs d'ecran. Voir docs/plans/2026-11-26-halo-contours-240-design.md.
"""
from __future__ import annotations

import math

SEED_ALPHA = 0.03          # alpha (moyenne 4 x 4 pixels) au-dessus duquel un texel est une graine : un trait fin de 1 px en est une
RANGE_K = 0.9              # portee = RANGE_K x holo_reach x racine(largeur x hauteur du logo)
RANGE_MIN, RANGE_MAX = 0.10, 0.60
ASPECT_CAP = 3.0          # rapport largeur / hauteur au-dela duquel la portee ne grandit plus (texte large, bandeau)
ROUND_START, ROUND_END = 0.2, 2.7    # arrondi : nul jusqu'a ROUND_START x portee, total a ROUND_END x portee
CIRC_K = 0.9               # part de la distance au cercle quand l'arrondi est total (continuite avec la taille du sujet)
CURVE = 1.0                # courbe de v (1 = lineaire : les franges restent espacees regulierement)
CAP_NEAR = 0.85            # v au-dessus duquel toute l'intensite s'applique (en dessous, elle est plafonnee a 1 de plus en plus)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _smoothstep(a: float, b: float, x: float) -> float:
    t = _clamp((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def halo_range(reach: float, width: float, height: float) -> float:
    """Portee du halo (hauteurs d'ecran) : `holo_reach` x RANGE_K x racine de l'aire du logo (largeur et hauteur en hauteurs d'ecran), bornee.
    La racine de l'aire, avec un rapport largeur / hauteur plafonne a ASPECT_CAP : un texte tres large n'a pas une portee proportionnelle a sa largeur
    (retour d'essai : une ligne de texte plein cadre donnait un halo d'une demi-hauteur d'ecran de part et d'autre)."""
    w, h = max(float(width), 0.0), max(float(height), 0.0)
    w, h = min(w, ASPECT_CAP * h), min(h, ASPECT_CAP * w)
    area = w * h
    return _clamp(max(float(reach), 0.0) * RANGE_K * math.sqrt(area), RANGE_MIN, RANGE_MAX)


def round_blend(d: float, dmax: float) -> float:
    """Part d'arrondi (0 = distance exacte au contour, 1 = distance au cercle) a la distance `d` du contour, pour la portee `dmax`."""
    return _smoothstep(ROUND_START * dmax, ROUND_END * dmax, d)


def ring_distance(d: float, dcirc: float, dmax: float) -> float:
    """Distance qui range les anneaux : `d` (au contour) pres du sujet, `CIRC_K x dcirc` (au cercle du sujet) loin, fondu entre les deux."""
    w = round_blend(d, dmax)
    return d * (1.0 - w) + CIRC_K * w * dcirc


def halo_v(s: float, dmax: float) -> float:
    """Champ `v` lu par le post-traitement : 1 contre le sujet, 0 a la portee et au-dela, decroissant."""
    return (1.0 - _clamp(float(s) / max(float(dmax), 1e-9), 0.0, 1.0)) ** CURVE


def light_gain(intensity: float, v: float) -> float:
    """Gain de lumiere effectif en un point de champ `v` : l'intensite jusqu'a 1 ; au-dela, le surplus ne s'applique que pres du sujet
    (`v` >= CAP_NEAR : tout), il disparait vers la portee. A forte intensite les anneaux proches montent, les lointains restent a 1."""
    base = min(float(intensity), 1.0)
    return base + (float(intensity) - base) * _smoothstep(0.0, CAP_NEAR, v)


# Irregularites des anneaux (#256) : bruit 3D (position, v, temps) ajoute a v dans holo.frag. Memes constantes que le shader.
WOBBLE_AMP = 0.11          # decalage de v a l'irregularite 1 (environ 40 % de la periode des anneaux : 2 pi / 24 = 0,26)
WOBBLE_FREQ = 4.0         # ondulations par hauteur d'ecran (basse frequence : pas de grain)
WOBBLE_RING_K = 9.0        # v sur le 3e axe du bruit : chaque anneau voit une tranche differente, donc sa propre irregularite
WOBBLE_TIME = 0.35         # unites de bruit par seconde a la vitesse 1 (temps integre cote CPU : Renderer._holo_wt)
WOBBLE_FAR = 0.12          # le masque monte de 0 (portee) a 1 a v = WOBBLE_FAR
WOBBLE_NEAR = (0.80, 0.97)  # puis redescend vers 0 pres du sujet : son contour reste fidele (#240)


def wobble_mask(v: float) -> float:
    """Part de l'irregularite appliquee a `v` : nulle a la portee (pas de bord franc) et contre le sujet (contour intact), pleine entre les deux."""
    return _smoothstep(0.0, WOBBLE_FAR, v) * (1.0 - _smoothstep(WOBBLE_NEAR[0], WOBBLE_NEAR[1], v))


def wobble_v(v: float, amount: float, noise: float) -> float:
    """`v` avec ses irregularites : `v` + `amount` (0..1) x WOBBLE_AMP x masque x `noise` (-1..1), dans 0..1.
    `amount` a 0, negatif ou NaN, ou `noise` NaN : `v` tel quel (rendu d'avant)."""
    v, amount, noise = float(v), float(amount), float(noise)
    if not (math.isfinite(amount) and math.isfinite(noise)) or amount <= 0.0:
        return v
    return _clamp(v + min(amount, 1.0) * WOBBLE_AMP * wobble_mask(v) * _clamp(noise, -1.0, 1.0), 0.0, 1.0)


# Reaction audio par reglage (#247) : facteur K de chaque reglage et borne haute (plage du curseur ; None = la vitesse n'est pas bornee).
REACT_K = {"holo_intensity": 1.0, "holo_reach": 0.5, "holo_warp": 1.0, "holo_dust": 1.0, "holo_speed": 3.0, "holo_wobble": 1.0}
REACT_MAX = {"holo_intensity": 3.0, "holo_reach": 3.0, "holo_warp": 3.0, "holo_dust": 3.0, "holo_speed": None, "holo_wobble": 1.0}
REACT_KEYS = tuple(REACT_K)


def react_value(key: str, base: float, level: float) -> float:
    """Valeur effective d'un reglage du halo : `base` x (1 + `level` x K), bornee a la plage du curseur (sauf la vitesse).
    `level` = niveau du son de sa ligne de routage, DOSAGE COMPRIS (la pastille a droite du curseur, #259) ; nul, negatif ou NaN : `base` tel quel."""
    base = float(base)
    level = float(level)
    if not math.isfinite(level) or level <= 0.0:
        return base
    value = base * (1.0 + level * REACT_K[key])
    top = REACT_MAX[key]
    return value if top is None else min(value, max(top, base))


def jfa_steps(width: int, height: int) -> list[int]:
    """Pas (en texels) des passes de jump flooding pour une texture `width` x `height` : puissances de deux decroissantes jusqu'a 1, plus une
    passe de correction a 1 (rattrape les erreurs d'un texel du jump flooding classique)."""
    m = max(int(width), int(height), 1)
    if m <= 1:
        return [1]
    k = 1 << max(math.ceil(math.log2(m)) - 1, 0)
    steps = []
    while k >= 1:
        steps.append(k)
        k //= 2
    return steps + [1]
