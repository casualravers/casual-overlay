"""Stochastique : texture a l'interieur de la tache (#220), partie pure (stdlib seulement).

La tache est l'union des points du motif stochastique ; sa zone est texturee soit par des lignes d'oscilloscope (`wave`), soit par un pavage de
reaction-diffusion (`tiling`, la tuile de `engine/textfill.py`) balaye par une vague qui peint puis efface. Ce module fournit :
- `SweepClock` : quand la vague du pavage repart (toutes les N secondes, ou toutes les N mesures sur le temps 1) ;
- `front_times`, `sweep_tau`, `epoch_offset` : duree du front, temps local d'un pixel, decalage de lecture de la tuile (memes formules que `scene.frag`) ;
- `wave_params` : amplitude, frequence, vitesse et epaisseur des lignes de l'onde a partir des niveaux sonores.
Voir docs/plans/2026-11-26-stochastique-texture-220-design.md.
"""
from __future__ import annotations

import math
import random

from core.audio_layers import NO_KICK
from core.reaction import _zhash

EVERY_MIN, EVERY_MAX = 2.0, 30.0       # intervalle entre deux vagues (secondes)
FRONT_MIN, FRONT_MAX = 1.0, 6.0        # duree du front qui traverse l'ecran (secondes)
FRONT_SHARE = 0.4                      # part de l'intervalle prise par le front
GROWTH_MIN, GROWTH_MAX = 0.5, 1.5      # duree d'epaississement du motif derriere le front
LEVEL_MAX = 1.5                        # un niveau sonore au-dela est ramene la (les niveaux mis a l'echelle depassent un peu 1)
AMP_MAX = 24.0                         # amplitude maximale des lignes de l'onde (px pour 720 px de haut)
FREQ_BASE, FREQ_RANGE = 0.03, 0.07     # frequence d'ondulation (rad par px) : au repos, et ce que les aigus ajoutent
SPEED_BASE, SPEED_RANGE = 0.4, 3.0     # vitesse de defilement de la phase (rad par s)
WIDTH_BASE, WIDTH_RANGE = 2.0, 2.5    # demi-epaisseur des lignes (px pour 720 px de haut)
DOWNBEAT_NEW = 0.05                    # le temps depuis le dernier 1 recule d'au moins cela : un nouveau 1 est arrive (secondes)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def front_times(interval: float) -> tuple[float, float]:
    """(front, croissance) en secondes : le front met `FRONT_SHARE` de l'intervalle pour traverser l'ecran (borne), le motif epaissit ensuite sur
    le quart de ce temps (borne)."""
    front = _clamp(float(interval) * FRONT_SHARE, FRONT_MIN, FRONT_MAX)
    return front, _clamp(front * 0.25, GROWTH_MIN, GROWTH_MAX)


def sweep_tau(age: float, pos: float, front: float) -> float:
    """Temps local d'un pixel : `age` secondes depuis le depart de la vague, moins l'arrivee du front (`pos` 0 = bord de depart, 1 = bord
    d'arrivee, x `front`). Negatif = le front n'est pas encore passe. Meme formule que scene.frag."""
    return float(age) - _clamp(float(pos), 0.0, 1.0) * float(front)


def epoch_offset(epoch: int) -> tuple[float, float]:
    """Decalage de lecture de la tuile (0..1 de son cote) pour l'epoque `epoch` : un motif different a chaque vague, le meme a chaque appel."""
    rng = random.Random(int(epoch) * 7919 + 31)
    return rng.random(), rng.random()


# Front de la vague (#249) : `pos` (0 = depart, 1 = arrivee) selon la forme. Droit = projection sur la direction ; cercle / arc = distance a une origine
# (centre, ou point hors cadre du cote d'ou vient la vague) normalisee sur le cadre ; organique = la meme distance deformee par un bruit de valeur.
FRONT_SHAPES = ("straight", "circle", "arc", "organic")
FRONT_ORIGINS = ("center", "side")
ARC_FLAT = 1.0 / 40.0                  # courbure la plus faible de l'arc (1 / rayon, rayon en demi-etendues du cadre le long de la direction) : presque droit
FRONT_ROUGH_SPAN = 0.25                # deformation maximale de `pos` a irregularite 1 (part de la course du front)
FRONT_NOISE_CELL = 0.35                # taille d'une cellule du bruit organique (hauteur de l'image = 1)
FRONT_NOISE_SPREAD = 37.0              # decalage du bruit par epoque (cellules) : un autre bord a chaque vague


def _fclamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def front_geometry(shape: str, origin: str, angle: float, curve: float, aspect: float) -> tuple[float, float, float, float]:
    """(ox, oy, d0, d1) du front radial : origine en unites isotropes centrees (hauteur = 1, y vers le bas), distances mini et maxi de l'origine au cadre.
    Cercle (et organique d'origine « center ») : le centre. Arc (et organique d'origine « side ») : un point hors cadre du cote d'ou vient la
    vague (oppose a la direction), a `mult` demi-etendues du centre ; la courbure 0..1 interpole la courbure 1 / mult de ARC_FLAT a 1."""
    hx, hy = float(aspect) / 2.0, 0.5
    if shape == "circle" or (shape == "organic" and origin != "side"):
        ox = oy = 0.0
    else:
        a = math.radians(float(angle))
        vx, vy = math.cos(a), math.sin(a)
        h = hx * abs(vx) + hy * abs(vy)                                  # demi-etendue du cadre le long de la direction
        mult = 1.0 / (ARC_FLAT + _fclamp01(float(curve)) * (1.0 - ARC_FLAT))
        ox, oy = -vx * h * mult, -vy * h * mult
    nx, ny = min(max(ox, -hx), hx), min(max(oy, -hy), hy)
    d0 = math.hypot(ox - nx, oy - ny)
    d1 = max(math.hypot(ox - cx, oy - cy) for cx in (-hx, hx) for cy in (-hy, hy))
    return ox, oy, d0, max(d1, d0 + 1e-6)


def _front_noise(qx: float, qy: float) -> float:
    """Bruit de valeur 0..1 a deux octaves (replique de `front_noise` de scene.frag)."""
    def vn(x: float, y: float) -> float:
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3.0 - 2.0 * fx), fy * fy * (3.0 - 2.0 * fy)
        a = _zhash(ix, iy) + (_zhash(ix + 1, iy) - _zhash(ix, iy)) * fx
        b = _zhash(ix, iy + 1) + (_zhash(ix + 1, iy + 1) - _zhash(ix, iy + 1)) * fx
        return a + (b - a) * fy

    return 0.65 * vn(qx, qy) + 0.35 * vn(qx * 2.0 + 5.2, qy * 2.0 + 1.3)


def front_pos(shape: str, origin: str, angle: float, curve: float, rough: float, epoch: int, x: float, y: float, aspect: float) -> float:
    """`pos` 0..1 du pixel (x, y) de l'image (0..1, origine en haut a gauche) pour la forme du front. Meme formule que scene.frag."""
    a = math.radians(float(angle))
    if shape not in FRONT_SHAPES[1:]:
        dx, dy = math.cos(a), math.sin(a)
        return _fclamp01(((x - 0.5) * dx + (y - 0.5) * dy) / max(0.5 * (abs(dx) + abs(dy)), 0.001) * 0.5 + 0.5)
    ox, oy, d0, d1 = front_geometry(shape, origin, angle, curve, aspect)
    px, py = (x - 0.5) * float(aspect), y - 0.5
    pos = (math.hypot(px - ox, py - oy) - d0) / (d1 - d0)
    if shape == "organic":
        ex, ey = epoch_offset(epoch)
        n = _front_noise(px / FRONT_NOISE_CELL + ex * FRONT_NOISE_SPREAD, py / FRONT_NOISE_CELL + ey * FRONT_NOISE_SPREAD)
        pos += _fclamp01(float(rough)) * FRONT_ROUGH_SPAN * (2.0 * n - 1.0)
    return _fclamp01(pos)


def front_uniforms(shape: str, origin: str, angle: float, curve: float, rough: float, epoch: int, aspect: float) -> tuple[tuple, tuple, tuple]:
    """Uniforms de scene.frag : u_sto_front = (mode, ox, oy, d0) avec mode 0 droit / 1 radial / 2 organique ; u_sto_front2 = (1 / (d1 - d0), amplitude de
    l'irregularite, decalage du bruit x, y) ; u_sto_dir = (cos, sin) de la direction."""
    a = math.radians(float(angle))
    direction = (math.cos(a), math.sin(a))
    if shape not in FRONT_SHAPES[1:]:
        return (0.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), direction
    ox, oy, d0, d1 = front_geometry(shape, origin, angle, curve, aspect)
    ex, ey = epoch_offset(epoch)
    organic = shape == "organic"
    return ((2.0 if organic else 1.0, ox, oy, d0), (1.0 / (d1 - d0), _fclamp01(float(rough)) * FRONT_ROUGH_SPAN if organic else 0.0,
                                                     ex * FRONT_NOISE_SPREAD, ey * FRONT_NOISE_SPREAD), direction)


def wave_params(bass: float, mid: float, high: float, kick: float, react: float) -> tuple[float, float, float, float]:
    """(amplitude px, frequence rad/px, vitesse rad/s, demi-epaisseur px) des lignes de l'onde, pour 720 px de haut. Basses = amplitude, aigus = frequence
    d'ondulation, mediums = vitesse de defilement, kick = epaisseur des lignes ; `react` (reaction du fond) = 0 : lignes droites, immobiles au repos."""
    r = _clamp(float(react), 0.0, 1.0)
    b, m, h, k = (_clamp(float(v), 0.0, LEVEL_MAX) for v in (bass, mid, high, kick))
    return (AMP_MAX * r * b / LEVEL_MAX, FREQ_BASE + FREQ_RANGE * h / LEVEL_MAX, SPEED_BASE + SPEED_RANGE * r * m / LEVEL_MAX,
            WIDTH_BASE + WIDTH_RANGE * r * k / LEVEL_MAX)


class SweepClock:
    """Calendrier de la vague du pavage : `update` rend (age, epoque), l'age en secondes depuis le depart de la vague courante et l'epoque
    (compte des departs, graine du decalage de lecture de la tuile). Une vague repart toutes les `every` secondes ; en mode mesure (`sync`),
    toutes les `bars` mesures, sur le temps 1 (`since_down` = secondes depuis le dernier 1 de mesure, `NO_KICK` si inconnu : repli sur les
    secondes). Garde-fou : en mode mesure, sans nouveau 1 pendant `max(4 x bars, 8)` s (silence), elle repart quand meme. Pure : l'horloge et le
    temps depuis le 1 sont passes en arguments."""

    def __init__(self) -> None:
        self.t_start: float | None = None
        self.epoch = 0
        self.interval = 8.0                       # duree de la derniere vague mesuree (secondes) : regle la duree du front
        self.downs = 0                            # temps 1 comptes depuis le depart
        self.last_since: float | None = None

    def _restart(self, start: float) -> None:
        if self.t_start is not None:
            self.interval = max(start - self.t_start, EVERY_MIN)
            self.epoch += 1
        self.t_start = start
        self.downs = 0

    def update(self, now: float, since_down: float, every: float, sync: bool, bars: int) -> tuple[float, int]:
        now = float(now)
        every = _clamp(float(every), EVERY_MIN, EVERY_MAX)
        bars = max(int(bars), 1)
        since = float(since_down)
        known = since < NO_KICK - 1e-3
        fresh = known and self.last_since is not None and since < self.last_since - DOWNBEAT_NEW
        self.last_since = since if known else None
        if self.t_start is None:
            self.t_start = now
            return 0.0, self.epoch
        if sync and known:
            if fresh:
                self.downs += 1
                if self.downs >= bars:
                    self._restart(now - since)
            elif now - self.t_start >= max(4.0 * bars, 8.0):
                self._restart(now)
        elif now - self.t_start >= every:
            self._restart(now)
        return max(now - self.t_start, 0.0), self.epoch
