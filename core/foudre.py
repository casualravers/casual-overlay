"""Mode « Foudre » du strobe : un reseau lumineux vivant (racines, nerfs) qui pousse depuis le centre sur les kicks.

Python pur (stdlib seulement). Le dessin est fait par le moteur (`engine/render.py`, `gl_shaders/foudre.*`) ; ici : le graphe du reseau,
sa croissance (pointes qui avancent, bifurquent, se soudent aux branches voisines), son pouls (une onde qui part de la racine et
renforce les routes qu'elle traverse), sa consolidation (les routes qui alimentent beaucoup de pointes s'epaississent), son effacement
(les routes non renforcees s'eteignent puis disparaissent) et le plafond d'energie selon la phase du morceau. Repere : centre de
l'image = (0, 0), hauteur de l'image = 1 (y dans [-0,5 ; 0,5], vers le haut), largeur = `aspect`. Constantes a regler a l'oeil.
"""
from __future__ import annotations

import math
import random

STEP = 0.009                # longueur d'un pas de croissance, en hauteurs d'image
STEPS_PER_S = 30.0          # pas par seconde et par pointe (a tempo 1, energie 1)
FORK_P = 0.07               # probabilite de bifurcation par pas
JOIN_R = 0.014              # distance a laquelle une pointe se soude a une branche voisine
JOIN_P = 0.85               # probabilite de soudure quand une branche voisine est a portee
AVOID_R = 0.035             # rayon de repulsion des branches voisines (donne la taille des alveoles)
MAX_SEGMENTS = 2400
TRUNK_LIFE = (95, 145)      # duree de vie d'un tronc, en pas
SPROUT_LIFE = (14, 53)      # duree de vie d'une pousse (x energie), en pas
SPROUTS = (4, 12)           # pousses par kick : base + gain x energie
MAX_TIPS = 100
MAX_STEPS_FRAME = 60        # pas de croissance au plus par image (une image tres longue ne bloque jamais le rendu)
MIN_PUSH_S = 0.25           # delai minimal entre deux poussees de kick : 4 par seconde au plus
HOLD_PUSH_S = 0.5           # bouton maintenu : un pouls toutes les 0,5 s
MAX_DT = 0.1
KIN = 12                    # une branche qui partage un ancetre a moins de KIN generations n'est ni evitee ni soudee

PULSE_SPEED = 1.6           # vitesse de l'onde, en distance de chemin par seconde (a tempo 1)
MAX_PULSES = 4
DROP_ENERGY = 1.5           # energie de la grande onde du Drop
DROP_SPROUTS = 24           # pousses nees d'un coup au Drop
DECAY = 0.14                # perte de vigueur par seconde d'un tronc ; les fines s'eteignent jusqu'a 5 fois plus vite
QUIET_DECAY = 1.5           # x ce facteur quand l'energie est tombee (silence, ambiance)
W_FINE = 0.0012             # largeur d'une brindille, en hauteurs d'image
W_TRUNK = 0.0065            # largeur d'un tronc
DESC_REF = 40               # nombre de pointes en aval d'un tronc pleinement consolide
W_INERTIA_S = 0.5           # inertie de l'epaississement

PHASE_CEILING = {"silence": 0.0, "ambient": 0.0, "groove": 0.5, "build": 0.7, "break": 0.17, "peak": 1.0}
DEFAULT_CEILING = 0.5       # phase inconnue (Phases auto coupees)


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
    """Le reseau : noeuds relies a leur parent, pointes actives, soudures, ondes. Le temps est injecte (`update(dt, ...)`)."""

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
        self.vig: list[float] = []                # vigueur 0..1 (decroit, renforcee par les ondes)
        self.w: list[float] = []                  # epaisseur courante (tend vers sa cible avec de l'inertie)
        self.desc: list[int] = []                 # nombre de feuilles en aval (modele des tuyaux)
        self.kids: list[int] = []
        self.n_alive = 0
        self.tips: dict[int, int] = {}            # pointes actives : noeud -> pas restants
        self.joins: list[tuple[int, int]] = []    # soudures (pointe, noeud rejoint)
        self.jref: dict[int, int] = {}            # noeud rejoint -> nombre de soudures qui le visent (il ne peut pas etre elague)
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.pulse_list: list[list[float]] = []   # ondes : [rayon, energie]
        self.forks = self.pushes = 0
        self.since_push = 1e9
        self.now = 0.0
        self.acc = 0.0
        self.hi_acc = 0.0
        self.energy = 0.0
        self.max_dist = 0.0

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
        self.vig.append(1.0)
        self.w.append(W_FINE)
        self.desc.append(1)
        self.kids.append(0)
        self.n_alive += 1
        self.grid.setdefault(self._cell(x, y), []).append(i)
        if parent >= 0:
            if self.kids[parent] > 0:                 # le parent n'etait pas une feuille : une feuille de plus en amont
                a = parent
                while a >= 0:
                    self.desc[a] += 1
                    a = self.parent[a]
            self.kids[parent] += 1
        if self.dist[i] > self.max_dist:
            self.max_dist = self.dist[i]
        return i

    def _remove(self, i: int) -> None:
        """Elague la feuille i (et ses soudures)."""
        self.alive[i] = False
        self.n_alive -= 1
        self.tips.pop(i, None)
        cell = self.grid.get(self._cell(self.x[i], self.y[i]))
        if cell is not None and i in cell:
            cell.remove(i)
        for a, b in [j for j in self.joins if j[0] == i]:
            self.joins.remove((a, b))
            self.jref[b] -= 1
        p = self.parent[i]
        if p >= 0:
            self.kids[p] -= 1
            if self.kids[p] > 0:                      # le parent reste interne : une feuille de moins en amont
                a = p
                while a >= 0:
                    self.desc[a] -= 1
                    a = self.parent[a]

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
        """Place pour une branche de plus : le plafond de densite suit l'energie (pic : MAX_SEGMENTS, groove : un tiers, cassure : moins)."""
        return self.n_alive + len(self.joins) < int(MAX_SEGMENTS * (0.12 + 0.88 * self.energy ** 2))

    def _importance(self, i: int) -> float:
        return min(1.0, math.sqrt(self.desc[i] / DESC_REF))

    # ---- naissance
    def _seed(self) -> None:
        r = self.rng
        root = self._add(r.uniform(-0.02, 0.02), r.uniform(-0.02, 0.02), -1, 0.0)
        n = r.randint(6, 8)
        base = r.uniform(0.0, 2 * math.pi)
        step = 2 * math.pi / n
        for i in range(n):
            ang = base + i * step + r.uniform(-0.35, 0.35) * step
            self._grow_from(root, ang, r.randint(*TRUNK_LIFE))

    def _grow_from(self, i: int, ang: float, life: int) -> int | None:
        """Nouvelle pointe qui part du noeud i dans la direction ang."""
        nx, ny = self.x[i] + math.cos(ang) * STEP, self.y[i] + math.sin(ang) * STEP
        if not self._inside(nx, ny) or not self._room() or len(self.tips) >= MAX_TIPS:
            return None
        n = self._add(nx, ny, i, ang)
        if life > 1:
            self.tips[n] = life - 1
        return n

    def _sprouts(self, count: int, energy: float) -> None:
        """`count` pousses sur des noeuds au hasard (poids plus fort pour les pointes actives et les noeuds proches de la racine)."""
        r = self.rng
        ids = [i for i in range(len(self.x)) if self.alive[i]]
        if not ids:
            return
        weights = [3.0 if i in self.tips else (1.0 + (2.0 if self.dist[i] < 0.3 else 0.0)) for i in ids]
        for _ in range(count):
            i = r.choices(ids, weights)[0]
            ang = self.heading[i] + r.choice((-1, 1)) * math.radians(r.uniform(25, 70))
            self._grow_from(i, ang, max(3, int(r.uniform(*SPROUT_LIFE) * max(energy, 0.3))))

    def _wave(self, energy: float) -> None:
        self.pulse_list.append([0.0, energy])
        del self.pulse_list[:-MAX_PULSES]

    def _push(self, energy: float) -> None:
        """Un kick (ou un pouls du bouton maintenu) : le premier seme le reseau, tous lancent une onde et font naitre des pousses."""
        first = self.empty
        self.pushes += 1
        self.since_push = 0.0
        if first:
            self._seed()
        else:
            self._sprouts(round(SPROUTS[0] + SPROUTS[1] * energy), energy)
        self._wave(0.4 + 0.6 * energy)

    def drop(self) -> None:
        """Grande decharge : onde d'energie 1,5, pousses en masse, toutes les vigueurs au maximum."""
        self.pushes += 1
        self.since_push = 0.0
        if self.empty:
            self._seed()
        self._sprouts(DROP_SPROUTS, 1.0)
        self.vig = [1.0 if a else v for v, a in zip(self.vig, self.alive)]
        self._wave(DROP_ENERGY)

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
            self.jref[join] = self.jref.get(join, 0) + 1
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

    # ---- vie du reseau : vigueur, epaisseur, ondes, elagage
    def _live(self, dt: float, speed: float) -> None:
        """Vigueur (temps reel : le silence efface le reseau meme a tempo bas), epaisseur (integree, suit le tempo), elagage."""
        rate = DECAY * (QUIET_DECAY if self.energy <= 0.2 else 1.0)
        k = min(1.0, dt * speed / W_INERTIA_S)
        top = 0.0
        for i in range(len(self.x)):
            if not self.alive[i]:
                continue
            imp = self._importance(i)
            self.vig[i] -= rate * (1.0 + 4.0 * (1.0 - imp)) * dt
            self.w[i] += (W_FINE + (W_TRUNK - W_FINE) * imp - self.w[i]) * k
            if self.dist[i] > top:
                top = self.dist[i]
        self.max_dist = top
        for i in range(len(self.x) - 1, -1, -1):                          # feuilles d'abord : un parent n'est elague qu'une fois ses branches parties
            if self.alive[i] and self.kids[i] == 0 and self.vig[i] <= 0.0 and self.jref.get(i, 0) == 0:
                self._remove(i)

    def _compact(self) -> None:
        """Retire les noeuds elagues des listes (sinon elles grandissent sans fin et chaque boucle les parcourt) ; l'ordre est conserve : un parent precede toujours ses enfants."""
        new = {}
        for i, a in enumerate(self.alive):
            if a:
                new[i] = len(new)
        keep = list(new)
        for name in ("x", "y", "dist", "heading", "vig", "w", "desc", "kids"):
            old = getattr(self, name)
            setattr(self, name, [old[i] for i in keep])
        self.parent = [new[self.parent[i]] if self.parent[i] >= 0 else -1 for i in keep]
        self.alive = [True] * len(keep)
        self.tips = {new[i]: v for i, v in self.tips.items()}
        self.joins = [(new[a], new[b]) for a, b in self.joins]
        self.jref = {new[i]: v for i, v in self.jref.items() if v > 0}
        self.grid = {}
        for i in range(len(keep)):
            self.grid.setdefault(self._cell(self.x[i], self.y[i]), []).append(i)

    def _pulses(self, dt: float, speed: float) -> None:
        """Les ondes avancent le long des routes et renforcent les noeuds qu'elles atteignent (plus fort pour les routes tres utilisees)."""
        keep = []
        for p in self.pulse_list:
            r0, e = p
            r1 = r0 + PULSE_SPEED * dt * speed
            if r1 > r0:
                for i in range(len(self.x)):
                    if self.alive[i] and r0 <= self.dist[i] < r1:
                        self.vig[i] = min(1.0, self.vig[i] + e * self._importance(i))
            p[0] = r1
            if r1 <= self.max_dist + 0.2:
                keep.append(p)
        self.pulse_list = keep

    def update(self, dt: float, kicks: int = 0, ceiling: float = DEFAULT_CEILING, hold: bool = False, speed: float = 1.0, high: float = 0.0) -> None:
        """Avance de `dt` secondes (borne a 0,1 s). `kicks` = nouveaux kicks depuis le dernier appel (une rafale = une poussee)."""
        dt = min(dt, MAX_DT) if dt == dt and dt > 0.0 else 0.0
        speed = _unit(speed, 0.0, 4.0)
        self.energy = 1.0 if hold else _unit(ceiling)
        self.now += dt
        self.since_push += dt
        if self.energy > 0.0:
            if hold:
                if self.since_push >= HOLD_PUSH_S:
                    self._push(1.0)
            elif kicks > 0 and self.since_push >= MIN_PUSH_S:
                self._push(self.energy)
            self.hi_acc += _unit(high) * 0.3 * dt                             # les aigus font naitre des brindilles
            if self.hi_acc >= 1.0 and not self.empty:
                self.hi_acc -= 1.0
                self._sprouts(1, self.energy)
            self._grow(dt * speed, self.energy)
        self._pulses(dt, speed)
        self._live(dt, speed)
        if len(self.x) > 2 * self.n_alive + 256:
            self._compact()

    # ---- lecture
    def segments(self) -> list[tuple[float, float, float, float, float, float, float]]:
        """Segments visibles : (x0, y0, x1, y1, largeur, vigueur 0..1, distance de chemin depuis la racine).
        La vigueur d'un noeud est le max de la sienne et de celle de ses descendants : un tronc ne disparait jamais avant ses branches."""
        eff = [max(v, 0.0) for v in self.vig]
        for i in range(len(self.x) - 1, 0, -1):
            p = self.parent[i]
            if self.alive[i] and p >= 0 and eff[i] > eff[p]:
                eff[p] = eff[i]
        for a, b in self.joins:
            if eff[a] > eff[b]:
                eff[b] = eff[a]
        out = []
        for i in range(len(self.x)):
            p = self.parent[i]
            if self.alive[i] and p >= 0:
                out.append((self.x[p], self.y[p], self.x[i], self.y[i], self.w[i], min(eff[i], 1.0), self.dist[i]))
        for a, b in self.joins:
            out.append((self.x[a], self.y[a], self.x[b], self.y[b], self.w[a], min(eff[a], 1.0), self.dist[a]))
        return out

    def pulses(self) -> list[tuple[float, float]]:
        """Ondes en cours : (rayon en distance de chemin, energie)."""
        return [(r, e) for r, e in self.pulse_list]

    def stats(self) -> dict:
        return {"segments": max(self.n_alive - 1, 0) + len(self.joins), "tips": len(self.tips), "joins": len(self.joins), "forks": self.forks,
                "pushes": self.pushes, "max_dist": self.max_dist, "pulses": len(self.pulse_list), "energy": self.energy}
