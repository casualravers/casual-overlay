"""Mode « Foudre » du strobe : un arbre de lumiere fractal qui part du centre sur chaque kick.

Python pur (stdlib seulement). Le dessin est fait par le moteur (`engine/render.py`, `gl_shaders/foudre.*`) ; ici : generation de l'arbre
(`build_tree`), plafond de charge selon la phase du morceau (`phase_ceiling`) et machine d'etat (`Lightning` : charge, generations,
croissance, decroissance). Repere : centre de l'image = (0, 0), hauteur de l'image = 1 (y dans [-0,5 ; 0,5], vers le haut),
largeur = `aspect`.
"""
from __future__ import annotations

import math
import random
from typing import NamedTuple

MAX_DEPTH = 6               # generations 0..5
MAX_SEGMENTS = 300
MIN_PUSH_S = 0.25           # delai minimal entre deux poussees : 4 par seconde au plus
GROW_S = 0.25               # duree de croissance d'une generation (a tempo 1)
FLASH_S = 0.15              # duree de l'eclat qui suit une poussee
HALF_LIFE_S = 2.5           # demi-vie de la charge
CLEAR_S = 4.0               # sans poussee depuis ce delai, l'arbre s'efface
GAIN = 0.4                  # part de l'ecart au plafond gagnee a chaque poussee
TRUNK_WIDTH = 0.006         # largeur d'un tronc, en hauteurs d'image
FADE_STEP = 0.12            # la generation g n'est visible que si la charge depasse FADE_STEP * g
FADE_SPAN = 0.1             # ... avec un fondu de cette largeur
MAX_DT = 0.1

PHASE_CEILING = {"silence": 0.0, "ambient": 0.0, "groove": 0.5, "build": 0.7, "break": 0.17, "peak": 1.0}
DEFAULT_CEILING = 0.5       # phase inconnue (Phases auto coupees)
_LEVELS = (3, 2, 2, 1, 1, 1)    # zigzag par generation : 8, 4, 4, 2, 2, 2 sous-segments


class Seg(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    gen: int
    width: float
    k: int                  # rang du sous-segment dans sa branche
    n: int                  # nombre de sous-segments de la branche
    branch: int


def _unit(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v != v:
        return lo
    return min(max(v, lo), hi)


def phase_ceiling(name: str | None, build: float = 0.0) -> float:
    """Plafond de charge (0..1) selon la phase du morceau ; la montee (`build`, 0..1) le releve."""
    base = PHASE_CEILING.get(name, DEFAULT_CEILING) if name else DEFAULT_CEILING
    return max(base, _unit(build))


def _edge_dist(ang: float, hx: float, hy: float) -> float:
    """Distance du centre au bord du cadre dans la direction `ang`."""
    c, s = abs(math.cos(ang)), abs(math.sin(ang))
    return min(hx / c if c > 1e-9 else math.inf, hy / s if s > 1e-9 else math.inf)


def _zigzag(p0, p1, levels: int, rng: random.Random, amp: float = 0.12) -> list:
    """Ligne brisee de p0 a p1 par deplacement du point milieu (2**levels sous-segments)."""
    if levels <= 0:
        return [p0, p1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    off = rng.uniform(-amp, amp) * length
    nx, ny = (-dy / length, dx / length) if length > 1e-12 else (0.0, 0.0)
    mid = ((p0[0] + p1[0]) / 2 + nx * off, (p0[1] + p1[1]) / 2 + ny * off)
    left = _zigzag(p0, mid, levels - 1, rng, amp * 0.8)
    right = _zigzag(mid, p1, levels - 1, rng, amp * 0.8)
    return left[:-1] + right


def build_tree(seed: int, aspect: float, reach: float = 1.0) -> list[Seg]:
    """Arbre complet (toutes les generations) pour une graine ; `reach` 1 = jusqu'au bord de l'image."""
    rng = random.Random(seed)
    aspect = max(_unit(aspect, 0.1, 20.0), 0.1)
    reach = _unit(reach, 0.05, 3.0)
    hx, hy = 0.5 * aspect, 0.5
    inside = reach <= 1.0

    def fit(pt):
        return (min(max(pt[0], -hx), hx), min(max(pt[1], -hy), hy)) if inside else pt

    def path(p0, p1, levels):
        return [p0] + [fit(p) for p in _zigzag(p0, p1, levels, rng)[1:]]

    count = rng.randint(5, 7)
    base = rng.uniform(0.0, 2 * math.pi)
    step = 2 * math.pi / count
    layer = []                                           # branches de la generation : (points, largeur)
    for i in range(count):
        ang = base + i * step + rng.uniform(-0.35, 0.35) * step
        length = _edge_dist(ang, hx, hy) * reach * rng.uniform(0.6, 1.0)
        end = (math.cos(ang) * length, math.sin(ang) * length)
        layer.append((path((0.0, 0.0), end, _LEVELS[0]), TRUNK_WIDTH))
    segs: list[Seg] = []
    bid = 0
    for gen in range(MAX_DEPTH):
        nxt = []
        for pts, width in layer:
            n = len(pts) - 1
            if len(segs) + n > MAX_SEGMENTS:
                return segs
            for k in range(n):
                segs.append(Seg(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], gen, width, k, n, bid))
            bid += 1
            if gen + 1 >= MAX_DEPTH:
                continue
            total = sum(math.hypot(pts[j + 1][0] - pts[j][0], pts[j + 1][1] - pts[j][1]) for j in range(n))
            for _ in range(1 + (rng.random() < 0.4)):
                lo, hi = max(1, int(0.25 * n)), max(1, min(n - 1, int(0.9 * n)))
                j = rng.randint(lo, hi)
                a, b = pts[j - 1], pts[min(j + 1, n)]
                ang = math.atan2(b[1] - a[1], b[0] - a[0]) + rng.choice((-1, 1)) * math.radians(rng.uniform(20, 50))
                length = total * rng.uniform(0.6, 0.8)
                end = (pts[j][0] + math.cos(ang) * length, pts[j][1] + math.sin(ang) * length)
                nxt.append((path(pts[j], end, _LEVELS[gen + 1]), width * 0.65))
        layer = nxt
    return segs


class Lightning:
    """Etat d'une foudre : charge, generations poussees, croissance. Le temps est injecte (`update(dt, ...)`)."""

    def __init__(self, seed: int = 1) -> None:
        self.seed0 = int(seed)
        self.episode = 0
        self.aspect, self.reach = 16 / 9, 1.0
        self.gens = 0
        self.charge = 0.0
        self.now = 0.0
        self.since_push = 1e9
        self.since_flash = 1e9                  # depuis le dernier eclat (une poussee du bouton maintenu n'en relance pas : pas de pulsation)
        self.grow = [0.0] * MAX_DEPTH           # progression de la croissance de chaque generation (integree : un tempo qui change ne fait pas rentrer l'arbre)
        self._tree: list[Seg] = []
        self._key = None

    # ---- etat
    @property
    def empty(self) -> bool:
        return self.gens == 0

    @property
    def tree_seed(self) -> int:
        return self.seed0 + self.episode * 7919

    def configure(self, aspect: float, reach: float) -> None:
        self.aspect, self.reach = _unit(aspect, 0.1, 20.0), _unit(reach, 0.05, 3.0)

    def clear(self) -> None:
        self.gens, self.charge, self.since_push, self.since_flash = 0, 0.0, 1e9, 1e9
        self.episode += 1

    def drop(self) -> None:
        """Grande decharge : pleine profondeur, charge au maximum, les generations partent l'une apres l'autre."""
        self.gens, self.charge, self.since_push, self.since_flash = MAX_DEPTH, 1.0, 0.0, 0.0
        self.grow = [-g * 0.12 / GROW_S for g in range(MAX_DEPTH)]

    def _push(self, ceiling: float, flash: bool = True) -> None:
        cap = min(MAX_DEPTH, max(1, round(ceiling * MAX_DEPTH)))
        if self.gens < cap:
            self.grow[self.gens] = 0.0
            self.gens += 1
        self.charge = min(1.0, self.charge + GAIN * max(ceiling - self.charge, 0.0))
        self.since_push = 0.0
        if flash:
            self.since_flash = 0.0

    def update(self, dt: float, kicks: int = 0, ceiling: float = DEFAULT_CEILING, hold: bool = False, speed: float = 1.0) -> None:
        """Avance de `dt` secondes (borne a 0,1 s). `kicks` = nouveaux kicks depuis le dernier appel (une rafale = une poussee)."""
        dt = min(dt, MAX_DT) if dt == dt and dt > 0.0 else 0.0
        speed = _unit(speed, 0.0, 4.0)
        ceiling = 1.0 if hold else _unit(ceiling)
        self.now += dt
        self.since_push += dt
        self.since_flash += dt
        for g in range(self.gens):
            self.grow[g] += dt * speed / GROW_S
        if not hold:
            self.charge *= 0.5 ** (dt / HALF_LIFE_S)
            if self.gens and (self.since_push > CLEAR_S or self.charge < 0.02):
                self.clear()
        if hold:
            if self.since_push >= GROW_S:
                self._push(1.0, flash=False)
            self.charge = 1.0
        elif kicks > 0 and ceiling > 0.0 and self.since_push >= MIN_PUSH_S:
            self._push(ceiling)

    # ---- dessin
    def _segments(self) -> list[Seg]:
        key = (self.tree_seed, round(self.aspect, 6), round(self.reach, 6))
        if key != self._key:
            self._tree, self._key = build_tree(self.tree_seed, self.aspect, self.reach), key
        return self._tree

    def draw_list(self, bass: float = 0.0, high: float = 0.0, t: float = 0.0) -> list[tuple[float, float, float, float, float, float]]:
        """Segments visibles (x0, y0, x1, y1, largeur, intensite 0..1) : croissance, eclat, braise, respiration (basses), scintillement (aigus)."""
        if not self.gens:
            return []
        bass, high = _unit(bass), _unit(high)
        flash = max(0.0, 1.0 - self.since_flash / FLASH_S) ** 2
        level = 0.25 + 0.5 * self.charge
        level += (1.0 - level) * flash
        out = []
        for s in self._segments():
            if s.gen >= self.gens:
                continue
            fade = _unit((self.charge - FADE_STEP * s.gen) / FADE_SPAN)
            frac = _unit(self.grow[s.gen]) * s.n - s.k
            if fade <= 0.0 or frac <= 0.0:
                continue
            f = min(frac, 1.0)
            inten = level * fade * min(1.0 + 0.25 * bass, 1.5)
            if s.gen >= 3:
                inten *= 0.7 + 0.3 * high * (0.5 + 0.5 * math.sin(40.0 * t + 13.0 * len(out)))
            inten = min(inten, 1.0)
            if inten <= 0.001:
                continue
            out.append((s.x0, s.y0, s.x0 + (s.x1 - s.x0) * f, s.y0 + (s.y1 - s.y0) * f, s.width * (1.0 + 0.3 * bass), inten))
        return out
