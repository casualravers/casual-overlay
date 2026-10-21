"""Tempo (BPM) a partir de la PERIODICITE du son, sans dependre des kicks.

Python pur (stdlib seulement, temps injecte). `TempoTracker` recoit un Â« flux d'attaques Â» (somme des hausses du spectre d'une trame a la suivante,
calcule par `engine.audio.FeatureExtractor`) et cherche, par autocorrelation sur les ~10 dernieres secondes, la periode qui revient le plus :
c'est le tempo. Il marche quand le detecteur de kick ne suffit pas (haut-parleur de telephone sans basses, musique sans kick franc, charleston,
caisse claire, guitare...), puisqu'il regarde toutes les attaques entre 40 Hz et 6 kHz.

  * le flux est ramene sur une grille reguliere de 100 valeurs par seconde, debarrasse de sa moyenne locale (0,4 s) et rectifie ;
  * score d'un tempo = autocorrelation a sa periode + 1/2 a la periode double + 1/4 a la periode quadruple (une musique reguliere se repete
    aussi a la mesure), multiplie par un a-priori doux centre sur 125 BPM (0,6 octave) qui departage un tempo de sa moitie / son double ;
  * confiance = 1 - moyenne des scores / meilleur score (0 = bruit, 1 = pic net) ; l'estimation n'est donnee que si elle est stable (les derniers
    resultats s'accordent a 4 % pres).
`Rhythm` s'en sert quand les kicks ne donnent pas de BPM fiable.
"""
from __future__ import annotations

import math
import operator
from collections import deque

RATE = 100.0                  # valeurs de flux par seconde (grille reguliere)
WINDOW_S = 10.0               # fenetre d'analyse
MIN_S = 5.0                   # pas d'estimation avant ce temps de musique
BPM_RANGE = (70.0, 180.0)
EVERY_S = 0.5                 # une estimation toutes les 0,5 s
LOCAL_S = 0.4                 # moyenne locale retiree du flux
PRIOR_BPM, PRIOR_OCT = 125.0, 0.6
MIN_CONFIDENCE = 0.45
MIN_CONTRAST = 4.5            # crete / moyenne du flux : un sinus continu fait 3,3, de la musique de telephone 6 et plus
STABLE_N = 3                # derniers resultats qui doivent s'accorder
STABLE_TOL = 0.04
GAP_RESET_S = 2.0             # un trou de flux plus long que cela (analyse arretee) repart de zero


class TempoTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bins: deque[float] = deque(maxlen=int(WINDOW_S * RATE))
        self.last_bin: int | None = None
        self._at = -1e9
        self.history: deque[float] = deque(maxlen=STABLE_N)
        self.bpm: float | None = None
        self.confidence = 0.0

    def feed(self, t: float, flux: float) -> None:
        """Un pas d'analyse : `flux` = hausse du spectre depuis la trame precedente (>= 0)."""
        idx = int(t * RATE)
        if self.last_bin is None or idx - self.last_bin > GAP_RESET_S * RATE:
            self.bins.clear()
            self.bins.append(0.0)
        else:
            for _ in range(min(idx - self.last_bin, int(WINDOW_S * RATE))):
                self.bins.append(0.0)
        if idx >= (self.last_bin if self.last_bin is not None else idx):
            self.last_bin = idx
        self.bins[-1] += max(float(flux), 0.0)

    # ---- analyse
    def _onsets(self) -> list[float]:
        x = list(self.bins)
        n = len(x)
        w = max(int(LOCAL_S * RATE), 3)
        cs = [0.0]
        for v in x:
            cs.append(cs[-1] + v)
        out = []
        for i in range(n):
            lo, hi = max(i - w // 2, 0), min(i + w // 2 + 1, n)
            mean = (cs[hi] - cs[lo]) / (hi - lo)
            out.append(max(x[i] - mean, 0.0))
        return out

    def _analyse(self) -> tuple[float | None, float]:
        if len(self.bins) < MIN_S * RATE:
            return None, 0.0
        x = self._onsets()
        n = len(x)
        if max(x) <= 1e-9:
            return None, 0.0
        raw = list(self.bins)
        if max(raw) < MIN_CONTRAST * (sum(raw) / len(raw)):        # flux sans attaques (son continu, souffle) : pas de tempo
            return None, 0.0
        lmin, lmax = int(RATE * 60.0 / BPM_RANGE[1]), int(RATE * 60.0 / BPM_RANGE[0])
        top = min(4 * lmax, n // 2)
        ac = {}

        def corr(lag: int) -> float:
            if lag >= n - 10:
                return 0.0
            if lag not in ac:
                ac[lag] = sum(map(operator.mul, x[:n - lag], x[lag:])) / (n - lag)
            return ac[lag]
        scores = []
        for lag in range(lmin - 1, lmax + 2):
            s = corr(lag) + (0.5 * corr(2 * lag) if 2 * lag <= top else 0.0) + (0.25 * corr(4 * lag) if 4 * lag <= top else 0.0)
            bpm = 60.0 * RATE / lag
            prior = math.exp(-0.5 * (math.log2(bpm / PRIOR_BPM) / PRIOR_OCT) ** 2)
            scores.append((lag, s * prior))
        best = max(range(1, len(scores) - 1), key=lambda i: scores[i][1])
        peak = scores[best][1]
        mean = sum(s for _, s in scores) / len(scores)
        if peak <= 0.0:
            return None, 0.0
        lag, (a, b, c) = scores[best][0], (scores[best - 1][1], scores[best][1], scores[best + 1][1])
        denom = a - 2.0 * b + c
        lag_f = lag + (0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0)        # interpolation parabolique : mieux que 1 % de precision
        conf = min(max(1.0 - mean / peak, 0.0), 1.0)
        return 60.0 * RATE / lag_f, conf

    def estimate(self, now: float) -> tuple[float | None, float]:
        """(bpm ou None, confiance). Recalcule au plus toutes les `EVERY_S` ; None tant que le resultat n'est pas stable."""
        if self.last_bin is None or now - self.last_bin / RATE > GAP_RESET_S:      # plus de flux : plus de tempo
            self.history.clear()
            self.bpm, self.confidence = None, 0.0
            return None, 0.0
        if now - self._at >= EVERY_S:
            self._at = now
            value, conf = self._analyse()
            if value is not None and conf >= MIN_CONFIDENCE:
                self.history.append(value)
                near = [v for v in self.history if abs(v / value - 1.0) <= STABLE_TOL]
                if len(self.history) >= STABLE_N and len(near) >= STABLE_N - 1:
                    self.bpm = sum(near) / len(near)
                    self.confidence = conf
                else:
                    self.bpm, self.confidence = None, conf
            else:
                self.history.clear()
                self.bpm, self.confidence = None, conf
        return self.bpm, self.confidence
