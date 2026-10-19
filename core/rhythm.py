"""Rythme du morceau : BPM estime a partir des kicks detectes, montee et drop detectes a partir des niveaux.

Python pur (stdlib seulement, aucune fenetre ni GL ni numpy) : le temps est injecte, tout se teste sur des suites de kicks et de
niveaux synthetiques. `Rhythm` est nourri par le fil d'analyse audio (`engine.audio.AudioAnalyzer`) ; la GUI lit ses attributs.
"""
from __future__ import annotations

import math

BPM_LO, BPM_HI = 70.0, 180.0           # plage de repli : un tempo a la double ou a la moitie est ramene dans cette plage
BPM_MIN_KICKS = 6                      # pas d'estimation avant ce nombre de kicks recents
BPM_WINDOW_S = 24.0                    # on ne regarde que les kicks des dernieres secondes (le morceau change)
BPM_CLUSTER = 0.03                     # deux estimations a moins de 3 % l'une de l'autre votent pour le meme tempo
BPM_MIN_CONFIDENCE = 0.45              # part des estimations qui s'accordent ; en dessous : pas de BPM
BPM_LOST_S = 5.0                       # sans kick depuis ce temps, le BPM est perdu


def fold_bpm(bpm: float, lo: float = BPM_LO, hi: float = BPM_HI) -> float:
    """Ramene un tempo dans [lo, hi] par doublements / divisions (un kick a la blanche = le meme morceau)."""
    if bpm <= 0:
        return bpm
    while bpm < lo:
        bpm *= 2.0
    while bpm > hi:
        bpm /= 2.0
    return bpm


class BpmEstimator:
    """Tempo d'une suite de kicks. Les intervalles entre kicks consecutifs, mais aussi entre kicks distants de 2, 3 ou 4 rangs
    (divises par l'ecart : un kick manque ne fausse pas l'estimation), sont ramenes dans la plage de tempo ; le groupe d'estimations
    qui s'accordent (a 3 % pres) le plus nombreux donne le BPM, sa part du total donne la confiance."""

    def __init__(self, window: float = BPM_WINDOW_S, min_kicks: int = BPM_MIN_KICKS) -> None:
        self.window = window
        self.min_kicks = min_kicks
        self.times: list[float] = []

    def reset(self) -> None:
        self.times.clear()

    def feed(self, t: float) -> None:
        self.times.append(float(t))
        cut = t - self.window
        while self.times and self.times[0] < cut:
            self.times.pop(0)

    def estimate(self) -> tuple[float | None, float]:
        """(bpm ou None, confiance 0..1)."""
        times = self.times
        if len(times) < self.min_kicks:
            return None, 0.0
        cands: list[float] = []
        for i in range(1, len(times)):
            for k in range(1, 5):
                if i - k < 0:
                    break
                gap = (times[i] - times[i - k]) / k
                if 0.12 <= gap <= 3.0:
                    cands.append(fold_bpm(60.0 / gap))
        if len(cands) < self.min_kicks:
            return None, 0.0
        cands.sort()
        best_n, best_lo = 0, 0
        hi = 0
        for lo in range(len(cands)):                          # fenetre glissante de largeur BPM_CLUSTER sur les estimations triees
            while hi < len(cands) and cands[hi] <= cands[lo] * (1.0 + BPM_CLUSTER):
                hi += 1
            if hi - lo > best_n:
                best_n, best_lo = hi - lo, lo
        group = cands[best_lo:best_lo + best_n]
        return sum(group) / len(group), best_n / len(cands)


class DropDetector:
    """Montee et drop a partir des basses (niveau 0..1 normalise et lisse par l'analyse) et du niveau global.

    Un morceau electronique alterne des passages pleins (kick + basse) et des cassures : les basses disparaissent un moment (la
    montee y prepare le retour), puis reviennent d'un coup = le drop. Etats : `idle` -> `break` (basses sous 30 % de leur niveau
    habituel pendant au moins `BREAK_MIN_S`) -> `build` (cassure qui dure : la montee est probablement lancee) -> drop (les basses
    reviennent a plus de 60 % de leur niveau habituel). `update` rend "build" au passage en montee, "drop" au drop, sinon None.
    Une pause entre deux drops (`COOLDOWN_S`) evite les doubles declenchements."""

    BREAK_MIN_S = 1.5                  # basses absentes depuis ce temps = cassure
    BUILD_AFTER_S = 3.5                # cassure qui dure : on parle de montee
    BREAK_MAX_S = 45.0                 # au-dela, ce n'est plus une cassure (fin de morceau, silence) : on abandonne
    COOLDOWN_S = 12.0
    SLOW_TAU = 1.0                     # moyenne lente des basses
    REF_HALF_LIFE = 40.0               # niveau habituel des basses : plus haut recent, oublie lentement
    REF_MIN = 0.25                     # sans basses franches avant, rien a detecter
    LOW_RATIO = 0.30
    BACK_RATIO = 0.60

    def __init__(self) -> None:
        self.state = "idle"
        self.slow = 0.0
        self.ref = 0.0
        self._last: float | None = None
        self._low_since: float | None = None
        self._break_at: float | None = None
        self._cool_until = -1e9
        self._armed = True                  # faux apres une cassure abandonnee : il faut d'abord retrouver des basses
        self.drops = 0
        self.last_drop: float | None = None

    @property
    def break_age(self) -> float:
        return 0.0 if self._break_at is None or self._last is None else self._last - self._break_at

    def update(self, now: float, bass: float, rms: float = 0.0) -> str | None:
        dt = 0.0 if self._last is None else min(max(now - self._last, 0.0), 0.5)
        self._last = now
        bass = min(max(float(bass), 0.0), 1.0)
        if dt > 0:
            self.slow += (bass - self.slow) * (1.0 - math.exp(-dt / self.SLOW_TAU))
            self.ref = max(self.slow, self.ref * 0.5 ** (dt / self.REF_HALF_LIFE))
        else:
            self.slow = max(self.slow, bass * 0.5)
            self.ref = max(self.ref, self.slow)
        low = self.ref >= self.REF_MIN and self.slow < self.LOW_RATIO * self.ref
        event = None
        if not low:
            self._armed = True
        if self.state == "idle":
            if low and self._armed:
                if self._low_since is None:
                    self._low_since = now
                if now - self._low_since >= self.BREAK_MIN_S:
                    self.state, self._break_at = "break", self._low_since
            else:
                self._low_since = None
        else:
            age = now - (self._break_at if self._break_at is not None else now)
            if age > self.BREAK_MAX_S:
                self.state, self._low_since, self._break_at = "idle", None, None
                self._armed = False
            elif bass >= self.BACK_RATIO * self.ref:
                if now >= self._cool_until:
                    event = "drop"
                    self.drops += 1
                    self.last_drop = now
                    self._cool_until = now + self.COOLDOWN_S
                self.state, self._low_since, self._break_at = "idle", None, None      # basses revenues pendant la pause : retour au calme, sans drop
                self.slow = bass                             # la moyenne lente suit tout de suite : pas de fausse cassure derriere
            elif self.state == "break" and age >= self.BUILD_AFTER_S:
                self.state = "build"
                event = "build"
        return event


class Rhythm:
    """BPM et drop d'un morceau, nourris kick par kick et niveau par niveau par le fil d'analyse audio.

    `bpm` : tempo lisse (None tant qu'il n'est pas fiable), `confidence` 0..1, `drops` : compteur de drops detectes (la GUI compare
    sa derniere valeur lue), `state` : "idle" / "break" / "build"."""

    EVAL_EVERY_S = 0.25
    SNAP_AFTER = 3                     # estimations de suite en desaccord avec le tempo lisse = changement de morceau, on saute

    def __init__(self) -> None:
        self.est = BpmEstimator()
        self.drop = DropDetector()
        self.bpm: float | None = None
        self.confidence = 0.0
        self.builds = 0
        self._eval_at = -1e9
        self._last_kick: float | None = None
        self._off = 0

    @property
    def drops(self) -> int:
        return self.drop.drops

    @property
    def state(self) -> str:
        return self.drop.state

    def reset(self) -> None:
        self.est.reset()
        self.bpm, self.confidence, self._off, self._last_kick = None, 0.0, 0, None

    def _evaluate(self, now: float) -> None:
        if self._last_kick is not None and now - self._last_kick > BPM_LOST_S:
            self.est.reset()
            self.bpm, self.confidence, self._off = None, 0.0, 0
            return
        value, conf = self.est.estimate()
        self.confidence = conf
        if value is None or conf < BPM_MIN_CONFIDENCE:
            return
        if self.bpm is None:
            self.bpm, self._off = value, 0
        elif abs(value / self.bpm - 1.0) < 0.06:
            self.bpm += (value - self.bpm) * 0.25
            self._off = 0
        else:
            self._off += 1
            if self._off >= self.SNAP_AFTER:
                self.bpm, self._off = value, 0

    def update(self, now: float, levels: dict, beat: bool = False) -> str | None:
        """Un pas d'analyse : `beat` = un kick vient d'etre detecte. Rend "build" / "drop" quand la montee ou le drop est detecte."""
        if beat:
            self.est.feed(now)
            self._last_kick = now
        if now - self._eval_at >= self.EVAL_EVERY_S:
            self._eval_at = now
            self._evaluate(now)
        event = self.drop.update(now, float(levels.get("bass", 0.0)), float(levels.get("rms", 0.0)))
        if event == "build":
            self.builds += 1
        return event
