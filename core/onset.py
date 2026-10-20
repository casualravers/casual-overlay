"""Detecteur d'attaques (kick, frappes aigues) : une energie de bande nettement au-dessus d'une ligne de base glissante.

Python pur (stdlib seulement, temps injecte). C'est la logique de `FeatureExtractor._detect_kick` (constantes reglees par balayage sur signaux
synthetiques, voir CLAUDE.md), rendue parametrable pour servir a plusieurs usages : les frappes des aigus (charleys, claps), et des kicks a sensibilite
propre a chaque couche. `sensitivity` > 1 abaisse les seuils ; il est donne a chaque appel (la GUI le change en route).
"""
from __future__ import annotations

import math


def _coef(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau)


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

    def update(self, b: float, dt: float, now: float, sensitivity: float = 1.0) -> bool:
        """Energie `b` de la bande sur `dt` secondes ; vrai si une attaque vient de partir."""
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
        return hit
