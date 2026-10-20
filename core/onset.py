"""Detecteur d'attaques (kick, frappes aigues) : une energie de bande nettement au-dessus d'une ligne de base glissante.

Python pur (stdlib seulement, temps injecte). C'est la logique de `FeatureExtractor._detect_kick` (constantes reglees par balayage sur signaux
synthetiques, voir CLAUDE.md), rendue parametrable pour servir a plusieurs usages : les frappes des aigus (charleys, claps), et des kicks a sensibilite
propre a chaque couche. `sensitivity` > 1 abaisse les seuils ; il est donne a chaque appel (la GUI le change en route).
"""
from __future__ import annotations

import math
from collections import deque


def _coef(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau)


class WideRise:
    """Critere de MONTEE RAPIDE sur une bande large (20-400 Hz) : l'energie vient de plus que tripler depuis son creux des 120 dernieres ms.

    Rattrape deux cas que le critere principal (energie des basses au-dessus d'une ligne de base lente) rate :
      * un haut-parleur qui ne rend pas les basses (telephone, petite enceinte) : le kick ne reste que dans les mediums graves, que cette bande contient ;
      * un kick sature a longue queue : la ligne de base lente monte et le pic ne la depasse plus assez, mais le creux juste avant l'attaque est profond.
    Les faux declenchements sur du bruit sont evites par la bande large (plus de cases FFT, donc moins de fluctuations), le rapport minimal et l'ecart-type."""

    RISE = 3.0               # l'energie doit depasser (1 + RISE / sensibilite) fois son creux recent
    K = 1.5                  # ... et la ligne de base de plus de K / sensibilite ecarts-types
    LOOK_S = 0.12
    MIN_ENERGY = 0.0002

    def __init__(self) -> None:
        self.hist: deque[tuple[float, float]] = deque()
        self.baseline: float | None = None
        self.dev2 = 0.0
        self.prev = 0.0

    def check(self, e: float, dt: float, now: float, sensitivity: float, last_hit: float, ready: bool, refractory: float) -> bool:
        s = max(sensitivity, 0.05)
        while self.hist and now - self.hist[0][0] > self.LOOK_S:
            self.hist.popleft()
        lo = min((v for _, v in self.hist), default=None)
        self.hist.append((now, e))
        if self.baseline is None:
            self.baseline = e
        base = self.baseline
        std = math.sqrt(self.dev2)
        hit = (ready and lo is not None and now - last_hit > refractory and e > self.MIN_ENERGY and e > self.prev
               and e > lo * (1.0 + self.RISE / s) and e > base and (e - base) > self.K / s * std)
        if now - last_hit > 0.3 and dt > 0:
            self.dev2 += ((e - base) ** 2 - self.dev2) * _coef(dt, 1.0)
        if dt > 0:
            self.baseline = base + (e - base) * _coef(dt, 0.8)
        self.prev = e
        return hit


class OnsetDetector:
    def __init__(self, min_energy: float, refractory: float, ratio_extra: float = 1.2, k: float = 2.0, k_settle: float = 3.5,
                 settle_s: float = 1.5, warmup_s: float = 0.5, rise_tau: float = 0.8, fall_tau: float = 0.8, dev_tau: float = 1.0) -> None:
        self.min_energy, self.refractory = min_energy, refractory
        self.ratio_extra, self.k, self.k_settle = ratio_extra, k, k_settle
        self.settle_s, self.warmup_s = settle_s, warmup_s
        self.rise_tau, self.fall_tau, self.dev_tau = rise_tau, fall_tau, dev_tau
        self.baseline: float | None = None
        self.dev2 = 0.0
        self.prev = 0.0
        self.last = -1e9
        self.elapsed = 0.0
        self.wide = WideRise()

    def update(self, b: float, dt: float, now: float, sensitivity: float = 1.0, wide: float | None = None) -> bool:
        """Energie `b` de la bande sur `dt` secondes ; vrai si une attaque vient de partir. `wide` : energie 20-400 Hz (critere de montee rapide en plus)."""
        self.elapsed += dt
        s = max(sensitivity, 0.05)
        ratio = 1.0 + self.ratio_extra / s
        k = (self.k if self.elapsed > self.settle_s else self.k_settle) / s
        if self.baseline is None:
            self.baseline = b
        base = self.baseline
        dev = b - base
        std = math.sqrt(self.dev2)
        hit = (self.elapsed > self.warmup_s and now - self.last > self.refractory and b > self.min_energy
               and b > self.prev and b > base * ratio and dev > k * std)
        if hit:
            self.last = now
        if now - self.last > 0.3 and dt > 0:                 # statistiques hors attaques : l'ecart-type decrit le bruit ambiant
            self.dev2 += (dev * dev - self.dev2) * _coef(dt, self.dev_tau)
        if dt > 0:
            self.baseline = base + (b - base) * _coef(dt, self.rise_tau if b > base else self.fall_tau)
        self.prev = b
        if wide is not None:                                  # montee rapide sur la bande large (telephone, kick sature) ; met toujours ses statistiques a jour
            rise = self.wide.check(wide, dt, now, sensitivity, self.last, self.elapsed > self.warmup_s, self.refractory)
            if rise and not hit:
                hit, self.last = True, now
        return hit
