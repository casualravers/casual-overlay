"""Mode « Foudre » du strobe : un reseau lumineux vivant (racines, nerfs) qui pousse depuis le centre sur les kicks.

Python pur (stdlib seulement). Le dessin est fait par le moteur (`engine/render.py`, `gl_shaders/foudre.*`) ; ici : le graphe du reseau,
sa croissance (pointes qui avancent, bifurquent, se soudent aux branches voisines), son pouls, sa consolidation et son effacement,
le plafond d'energie selon la phase du morceau. Repere : centre de l'image = (0, 0), hauteur de l'image = 1 (y dans [-0,5 ; 0,5],
vers le haut), largeur = `aspect`. Toutes les constantes ci-dessous sont a regler a l'oeil.
"""
from __future__ import annotations

import math
import random

STEP = 0.012                # longueur d'un pas de croissance, en hauteurs d'image
STEPS_PER_S = 18.0          # pas par seconde et par pointe (a tempo 1, energie 1)
FORK_P = 0.045              # probabilite de bifurcation par pas
JOIN_R = 0.018              # distance a laquelle une pointe se soude a une branche voisine
JOIN_P = 0.85               # probabilite de soudure quand une branche voisine est a portee
AVOID_R = 0.05              # rayon de repulsion des branches voisines (donne la taille des alveoles)
MAX_SEGMENTS = 1200
MAX_TIPS = 100
MAX_STEPS_FRAME = 60        # pas de croissance au plus par image (une image tres longue ne bloque jamais le rendu)
MIN_PUSH_S = 0.25           # delai minimal entre deux poussees de kick : 4 par seconde au plus
MAX_DT = 0.1
KIN = 12                    # une branche qui partage un ancetre a moins de KIN generations n'est ni evitee ni soudee

PHASE_CEILING = {"silence": 0.0, "ambient": 0.0, "groove": 0.5, "build": 0.7, "break": 0.17, "peak": 1.0}
DEFAULT_CEILING = 0.5       # phase inconnue (Phases auto coupees)

W_FINE = 0.0012             # largeur d'une brindille, en hauteurs d'image


def _unit(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v != v:
        return lo
    return min(max(v, lo), hi)


def phase_ceiling(name: str | None, build: float = 0.0) -> float:
    """Plafond d'energie (0..1) selon la phase du morceau ; la montee (`build`, 0..1) le releve."""
    base = PHASE_CEILING.get(name, DEFAULT_CEILING) if name else DEFAULT_CEILING
    return max(base, _unit(build))


def _angdiff(a: float, b: float) -> float:
    """Ecart angulaire de b a a, dans [-pi, pi]."""
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Lightning:
    """Le reseau : noeuds relies a leur parent, pointes actives, soudures. Le temps est injecte (`update(dt, ...)`)."""

    def __init__(self, seed: int = 1) -> None:
        self.seed0 = int(seed)
        self.episode = 0
        self.aspect, self.reach = 16 / 9, 1.0
        self._reset()

    # ---- etat
    def _reset(self) -> None:
        self.rng = random.Random(self.seed0 + self.episode * 7919)
        self.x: list[float] = []
        self.y: list[float] = []
        self.parent: list[int] = []
        self.dist: list[float] = []
        self.heading: list[float] = []
        self.alive: list[bool] = []
        self.n_alive = 0
        self.tips: dict[int, int] = {}            # pointes actives : noeud -> pas restants
        self.joins: list[tuple[int, int]] = []    # soudures (pointe, noeud rejoint)
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.forks = self.pushes = 0
        self.since_push = 1e9
        self.now = 0.0
        self.acc = 0.0
        self.energy = 0.0

    @property
    def empty(self) -> bool:
        return self.n_alive == 0

    def configure(self, aspect: float, reach: float) -> None:
        self.aspect, self.reach = _unit(aspect, 0.1, 20.0), _unit(reach, 0.05, 3.0)

    def clear(self) -> None:
        self.episode += 1
        self._reset()

    # ---- graphe
    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return int(math.floor(x / AVOID_R)), int(math.floor(y / AVOID_R))

    def _add(self, x: float, y: float, parent: int, heading: float) -> int:
        i = len(self.x)
        self.x.append(x)
        self.y.append(y)
        self.parent.append(parent)
        self.dist.append(self.dist[parent] + math.hypot(x - self.x[parent], y - self.y[parent]) if parent >= 0 else 0.0)
        self.heading.append(heading)
        self.alive.append(True)
        self.n_alive += 1
        self.grid.setdefault(self._cell(x, y), []).append(i)
        return i

    def _near(self, x: float, y: float) -> list[int]:
        ci, cj = self._cell(x, y)
        out: list[int] = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                out.extend(self.grid.get((ci + di, cj + dj), ()))
        return out

    def _kin(self, i: int) -> set[int]:
        """Le noeud et ses KIN ancetres : les parents proches ne sont ni evites ni rejoints."""
        out = set()
        while i >= 0 and len(out) <= KIN:
            out.add(i)
            i = self.parent[i]
        return out

    def _inside(self, x: float, y: float) -> bool:
        return abs(x) <= 0.5 * self.aspect * self.reach and abs(y) <= 0.5 * self.reach

    def _room(self) -> bool:
        return self.n_alive + len(self.joins) < MAX_SEGMENTS

    # ---- naissance
    def _seed(self) -> None:
        r = self.rng
        root = self._add(r.uniform(-0.02, 0.02), r.uniform(-0.02, 0.02), -1, 0.0)
        n = r.randint(6, 8)
        base = r.uniform(0.0, 2 * math.pi)
        step = 2 * math.pi / n
        for i in range(n):
            ang = base + i * step + r.uniform(-0.35, 0.35) * step
            self._grow_from(root, ang, r.randint(30, 45))

    def _grow_from(self, i: int, ang: float, life: int) -> int | None:
        """Nouvelle pointe qui part du noeud i dans la direction ang."""
        nx, ny = self.x[i] + math.cos(ang) * STEP, self.y[i] + math.sin(ang) * STEP
        if not self._inside(nx, ny) or not self._room() or len(self.tips) >= MAX_TIPS:
            return None
        n = self._add(nx, ny, i, ang)
        if life > 1:
            self.tips[n] = life - 1
        return n

    def _push(self, energy: float) -> None:
        """Un kick : le premier seme le reseau, les suivants font naitre des pousses sur des noeuds au hasard."""
        self.pushes += 1
        self.since_push = 0.0
        if self.empty:
            self._seed()
            return
        r = self.rng
        ids = [i for i in range(len(self.x)) if self.alive[i]]
        weights = [3.0 if i in self.tips else (1.0 + (2.0 if self.dist[i] < 0.3 else 0.0)) for i in ids]
        for _ in range(round(3 + 7 * energy)):
            i = r.choices(ids, weights)[0]
            ang = self.heading[i] + r.choice((-1, 1)) * math.radians(r.uniform(25, 70))
            self._grow_from(i, ang, max(2, int(r.uniform(6, 20) * max(energy, 0.2))))

    # ---- croissance
    def _advance(self, tip: int) -> None:
        r = self.rng
        left = self.tips.pop(tip)
        x, y = self.x[tip], self.y[tip]
        h = self.heading[tip]
        h += 0.08 * _angdiff(math.atan2(y, x), h)                       # biais vers l'exterieur
        h += r.uniform(-0.35, 0.35)                                       # derive
        kin = self._kin(tip)
        ax = ay = 0.0
        for j in self._near(x, y):                                        # repulsion des branches voisines (donne des alveoles regulieres)
            if not self.alive[j] or j in kin:
                continue
            d = math.hypot(x - self.x[j], y - self.y[j])
            if 1e-9 < d < AVOID_R:
                w = (AVOID_R - d) / AVOID_R
                ax += (x - self.x[j]) / d * w
                ay += (y - self.y[j]) / d * w
        if ax or ay:
            h += 0.35 * _angdiff(math.atan2(ay, ax), h) * min(1.0, math.hypot(ax, ay))
        nx, ny = x + math.cos(h) * STEP, y + math.sin(h) * STEP
        if not self._inside(nx, ny) or not self._room():
            return                                                        # la pointe s'arrete : bord du cadre ou plafond atteint
        join = None
        for j in self._near(nx, ny):                                      # soudure : une branche voisine a portee
            if self.alive[j] and j not in kin and math.hypot(nx - self.x[j], ny - self.y[j]) < JOIN_R and not (self._kin(j) & kin):
                join = j
                break
        n = self._add(nx, ny, tip, h)
        if join is not None and r.random() < JOIN_P:
            self.joins.append((n, join))
            return
        if left > 1:
            self.tips[n] = left - 1
        if r.random() < FORK_P:                                           # bifurcation
            self.forks += 1
            self._grow_from(n, h + r.choice((-1, 1)) * math.radians(r.uniform(25, 60)), int(left * 0.7))

    def _grow(self, dt: float, energy: float) -> None:
        self.acc += dt * STEPS_PER_S * (0.6 + 0.4 * energy)
        steps = int(self.acc)
        self.acc -= steps
        for _ in range(min(steps, MAX_STEPS_FRAME)):
            for tip in list(self.tips):
                self._advance(tip)

    def update(self, dt: float, kicks: int = 0, ceiling: float = DEFAULT_CEILING, hold: bool = False, speed: float = 1.0, high: float = 0.0) -> None:
        """Avance de `dt` secondes (borne a 0,1 s). `kicks` = nouveaux kicks depuis le dernier appel (une rafale = une poussee)."""
        dt = min(dt, MAX_DT) if dt == dt and dt > 0.0 else 0.0
        speed = _unit(speed, 0.0, 4.0)
        self.energy = 1.0 if hold else _unit(ceiling)
        self.now += dt
        self.since_push += dt
        if kicks > 0 and self.energy > 0.0 and self.since_push >= MIN_PUSH_S:
            self._push(self.energy)
        if self.energy > 0.0:
            self._grow(dt * speed, self.energy)

    # ---- lecture
    def segments(self) -> list[tuple[float, float, float, float, float, float, float]]:
        """Segments visibles : (x0, y0, x1, y1, largeur, vigueur 0..1, distance de chemin depuis la racine)."""
        out = []
        for i in range(len(self.x)):
            p = self.parent[i]
            if self.alive[i] and p >= 0:
                out.append((self.x[p], self.y[p], self.x[i], self.y[i], W_FINE, 1.0, self.dist[i]))
        for a, b in self.joins:
            out.append((self.x[a], self.y[a], self.x[b], self.y[b], W_FINE, 1.0, self.dist[a]))
        return out

    def stats(self) -> dict:
        return {"segments": max(self.n_alive - 1, 0) + len(self.joins), "tips": len(self.tips), "joins": len(self.joins), "forks": self.forks,
                "pushes": self.pushes, "max_dist": max((d for d, a in zip(self.dist, self.alive) if a), default=0.0)}
