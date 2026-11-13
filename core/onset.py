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


class KickClassifier:
    """Kick ou caisse claire ? Apprend, sur les dernieres attaques detectees, s'il y a DEUX familles de sons (avec et sans graves) et, si oui,
    ecarte du kick celles qui n'ont pas de graves.

    Chaque attaque est decrite par un seul nombre, calcule sur le spectre qui vient d'apparaitre (voir `FeatureExtractor`) : `low` = log10 de
    l'energie nouvelle sous 150 Hz. Un kick en a beaucoup, une caisse claire (bruit large, corps vers 200 Hz) presque pas, et un kick pose sur une
    caisse claire en a autant qu'un kick seul : il reste un kick. Ce qui change d'un systeme son a l'autre (un haut-parleur de telephone ne rend
    presque plus de graves) est le NIVEAU de ce nombre, pas l'ecart entre les deux familles : on compare donc les attaques entre elles (coupure
    d'Otsu) au lieu de fixer un seuil. Pas de deux familles nettes (ecart de moins de `GAP` decades ou de `GAP_STD` ecarts-types) : tout est kick,
    comme avant (un morceau sans caisse claire, ou un kick d'accent plus faible, n'est jamais ecarte)."""

    MAXN = 48                    # attaques gardees
    MIN_EACH = 4                 # attaques minimum dans chaque famille
    GAP = 0.8                    # ecart minimal entre les deux familles (decades : un facteur 6)
    GAP_STD = 2.5                # ... et en ecarts-types

    def __init__(self) -> None:
        self.events: deque[float] = deque(maxlen=self.MAXN)
        self.armed = False
        self.thr = 0.0           # coupure : en dessous, ce n'est pas un kick

    def reset(self) -> None:
        self.events.clear()
        self.armed = False

    def observe(self, low: float) -> None:
        self.events.append(float(low))
        self._fit()

    def _fit(self) -> None:
        n = len(self.events)
        self.armed = False
        if n < 2 * self.MIN_EACH:
            return
        v = sorted(self.events)
        best, best_k = -1.0, 0
        for k in range(self.MIN_EACH, n - self.MIN_EACH + 1):
            gap = sum(v[k:]) / (n - k) - sum(v[:k]) / k
            between = k * (n - k) * gap * gap
            if between > best:
                best, best_k = between, k
        lo, hi = v[:best_k], v[best_k:]
        mlo, mhi = sum(lo) / len(lo), sum(hi) / len(hi)
        std = math.sqrt((sum((x - mlo) ** 2 for x in lo) + sum((x - mhi) ** 2 for x in hi)) / n)
        gap = mhi - mlo
        if gap < self.GAP or gap < self.GAP_STD * max(std, 0.05):
            return
        self.armed = True
        self.thr = 0.5 * (lo[-1] + hi[0])

    def is_kick(self, low: float) -> bool:
        return (not self.armed) or low >= self.thr

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


class FastAttack:
    """Detecteur d'attaque RAPIDE sur les basses, dans le domaine temporel (lot 3 de la spec synchro son / image).

    La FFT de 2048 echantillons (43 ms) ne voit un kick que quand assez de son neuf est entre dans sa fenetre : ~30 ms apres le debut du son.
    Ici l'entree est le signal des basses DEJA ramene a ~1 kHz (moyenne de blocs de ~1 ms, faite par le moteur avec numpy) : passe-bas, energie
    lissee sur quelques ms, et une attaque est declaree quand cette energie sort de son creux des 100 dernieres ms (montee brusque) ET depasse
    nettement sa moyenne lente (une note de basse tenue ne sort pas de sa moyenne : elle n'est pas un kick). Le retard descend a quelques ms.
    Python pur (stdlib), temps injecte. Le declenchement est partage avec la FFT par la periode refractaire (`last_hit`) : un kick = une detection."""

    FC_HZ = 170.0            # passe-bas des basses
    TAU_S = 0.006            # lissage de l'energie : assez court pour une attaque, assez long pour lisser un sinus de 50 Hz
    LOOK_S = 0.10            # le creux est cherche sur les 100 dernieres ms
    RISE = 3.0               # l'energie doit depasser (1 + RISE / sensibilite) fois ce creux
    SLOW_RATIO = 1.2         # ... et (1 + SLOW_RATIO / sensibilite) fois sa moyenne lente
    SLOW_TAU_S = 0.8
    MIN_ENV = 0.0003         # energie minimale (amplitude), comme KICK_MIN
    SETTLE_S = 1.5           # la moyenne lente n'est fiable qu'apres quelques kicks : avant, seule la FFT declare (comme KICK_SETTLE_S)
    BASS_FRAC = 0.15        # part de la PUISSANCE totale que les basses doivent representer : un clap, une caisse claire, un charley ont de l'energie
                             # partout (les basses n'en sont qu'1 a 5 %), un kick est presque tout en basses

    def __init__(self) -> None:
        self.y = 0.0
        self.p2 = 0.0
        self.b2 = 0.0
        self.slow = 0.0
        self.hist: deque[float] = deque()
        self.seen = 0.0
        self.prev = 0.0

    def update(self, chunk, broad, period_s: float, now: float, sensitivity: float, last_hit: float, ready: bool, refractory: float) -> bool:
        """`chunk` : echantillons des basses a la periode `period_s` ; `broad` : puissance moyenne (x au carre) du signal COMPLET sur chaque meme
        intervalle ; `now` = fin du dernier. Vrai si une attaque part dans ce paquet."""
        s = max(sensitivity, 0.05)
        a = 1.0 - math.exp(-2.0 * math.pi * self.FC_HZ * period_s)
        ke = _coef(period_s, self.TAU_S)
        ks = _coef(period_s, self.SLOW_TAU_S)
        keep = max(int(self.LOOK_S / period_s), 4)
        n = len(chunk)
        hit = False
        for i, x in enumerate(chunk):
            self.y += a * (x - self.y)
            self.p2 += (self.y * self.y - self.p2) * ke
            self.b2 += (broad[i] - self.b2) * ke
            env = math.sqrt(self.p2)
            t = now - (n - 1 - i) * period_s
            if len(self.hist) >= keep:
                self.hist.popleft()
            lo = min(self.hist) if self.hist else env
            self.hist.append(env)
            self.slow += (env - self.slow) * ks
            self.seen += period_s
            if (not hit and ready and self.seen > self.SETTLE_S and t - last_hit > refractory and env > self.MIN_ENV and env > self.prev
                    and env > lo * (1.0 + self.RISE / s) and env > self.slow * (1.0 + self.SLOW_RATIO / s)
                    and self.p2 > self.BASS_FRAC * self.b2):
                hit = True
                last_hit = t
            self.prev = env
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
