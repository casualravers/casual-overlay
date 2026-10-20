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
    """Montee et drop a partir des basses (niveau 0..1 normalise et lisse par l'analyse) et de l'energie des mediums / aigus.

    Un morceau electronique alterne des passages pleins (kick + basse) et des cassures : les basses disparaissent un moment, une
    **montee** (riser, caisse claire qui s'accelere, filtre qui s'ouvre) fait monter les mediums et les aigus, puis les basses
    reviennent d'un coup = le drop. Etats : `idle` -> `break` (basses sous 20 % de leur niveau habituel pendant au moins
    `BREAK_MIN_S`) -> `build` (la cassure dure `BUILD_AFTER_S` ET l'energie des mediums / aigus monte nettement : sans cette montee
    reelle une simple baisse des basses ne compte pas) -> drop (les basses reviennent a plus de 60 % de leur niveau habituel).
    `update` rend "build" au passage en montee, "drop" au drop, sinon None. Pause de `COOLDOWN_S` entre deux drops."""

    BREAK_MIN_S = 3.0                  # basses absentes depuis ce temps = cassure (une demi-mesure sans kick ne suffit pas)
    BUILD_AFTER_S = 5.0                # cassure qui dure ET qui monte : montee
    BREAK_MAX_S = 45.0                 # au-dela, ce n'est plus une cassure (fin de morceau, silence) : on abandonne
    COOLDOWN_S = 12.0
    SLOW_TAU = 2.0                     # moyenne lente des basses
    TOP_TAU = 1.2                      # moyenne des mediums / aigus (la montee se juge sur cette courbe)
    REF_HALF_LIFE = 40.0               # niveau habituel des basses : plus haut recent, oublie lentement
    REF_MIN = 0.30                     # sans basses franches avant, rien a detecter
    LOW_RATIO = 0.20
    BACK_RATIO = 0.60
    RISE_MIN = 0.12                    # la courbe des mediums / aigus doit avoir gagne au moins ca en 4 s...
    RISE_RECENT = 0.03                 # ... et continuer de monter sur les 2 dernieres secondes
    TOP_MIN = 0.15                     # ... en etant audible
    SAMPLE_EVERY_S = 0.25

    def __init__(self) -> None:
        self.state = "idle"
        self.slow = 0.0
        self.ref = 0.0
        self.top = 0.0
        self._hist: list[tuple[float, float]] = []             # (instant, courbe des mediums / aigus) toutes les 0,25 s, 8 dernieres secondes
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

    def _rising(self, now: float) -> bool:
        """Les mediums / aigus ont-ils nettement monte ? (courbe lissee : maintenant contre il y a 4 s, et contre il y a 2 s)"""
        def at(age: float):
            old = [v for t_, v in self._hist if t_ <= now - age]
            return old[-1] if old else None
        v4, v2 = at(4.0), at(2.0)
        return (v4 is not None and v2 is not None and self.top >= self.TOP_MIN
                and self.top - v4 >= self.RISE_MIN and self.top - v2 >= self.RISE_RECENT)

    def update(self, now: float, bass: float, rms: float = 0.0, top: float | None = None) -> str | None:
        """`top` : niveau des mediums / aigus (0..1) ; a defaut, le niveau global `rms`."""
        dt = 0.0 if self._last is None else min(max(now - self._last, 0.0), 0.5)
        self._last = now
        bass = min(max(float(bass), 0.0), 1.0)
        top = min(max(float(rms if top is None else top), 0.0), 1.0)
        if dt > 0:
            self.slow += (bass - self.slow) * (1.0 - math.exp(-dt / self.SLOW_TAU))
            self.top += (top - self.top) * (1.0 - math.exp(-dt / self.TOP_TAU))
            self.ref = max(self.slow, self.ref * 0.5 ** (dt / self.REF_HALF_LIFE))
        else:
            self.slow = max(self.slow, bass * 0.5)
            self.top = max(self.top, top * 0.5)
            self.ref = max(self.ref, self.slow)
        if not self._hist or now - self._hist[-1][0] >= self.SAMPLE_EVERY_S:
            self._hist.append((now, self.top))
            while self._hist and self._hist[0][0] < now - 8.0:
                self._hist.pop(0)
        low = self.ref >= self.REF_MIN and self.slow < self.LOW_RATIO * self.ref
        if not low:
            self._armed = True
        event = None
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
                self.state, self._low_since, self._break_at = "idle", None, None      # basses revenues pendant la pause : retour au calme, sans drop en retard
                self.slow = bass                             # la moyenne lente suit tout de suite : pas de fausse cassure derriere
            elif self.state == "break" and age >= self.BUILD_AFTER_S and self._rising(now):
                self.state = "build"
                event = "build"
        return event


class BeatGrid:
    """Grille du temps : ou tombe le « 1 » de la mesure. L'operateur l'ancre d'un appui (`set_one`) ; ensuite chaque kick detecte la
    recale doucement (`nudge`) et le BPM detecte regle le pas. Pure : le temps est injecte.

    `beat_index(t)` : numero du temps depuis l'ancre (0 = le « 1 » ancre) ; `bar_index(t)` : numero de la mesure ; `nearest_downbeat(t)` :
    l'instant du « 1 » le plus proche de `t`. Inactive tant qu'aucun « 1 » n'a ete donne ; effacee quand le BPM est perdu."""

    BEATS_PER_BAR = 4
    CAPTURE = 0.3          # un kick a moins de 0,3 temps d'une ligne de la grille la recale
    NUDGE = 0.15           # part de l'ecart corrigee a chaque kick
    SNAP_S = 0.4           # un « 1 » tape moins de 0,4 s apres un kick se cale sur ce kick (l'oreille entend le kick, le doigt suit)

    def __init__(self) -> None:
        self.anchor: float | None = None
        self.period = 0.5

    @property
    def active(self) -> bool:
        return self.anchor is not None

    def clear(self) -> None:
        self.anchor = None

    def set_one(self, now: float, bpm: float, last_kick: float | None = None) -> None:
        self.period = 60.0 / min(max(float(bpm), 30.0), 300.0)
        self.anchor = last_kick if last_kick is not None and 0.0 <= now - last_kick <= self.SNAP_S else now

    def pos(self, t: float) -> float:
        return (t - self.anchor) / self.period

    def beat_index(self, t: float) -> int | None:
        return None if self.anchor is None else int(round(self.pos(t)))

    def bar_index(self, t: float) -> int | None:
        return None if self.anchor is None else int(math.floor(self.pos(t) / self.BEATS_PER_BAR))

    def beat_in_bar(self, t: float) -> int | None:
        """1..4 : le temps de la mesure ou l'on est a l'instant `t`."""
        return None if self.anchor is None else int(math.floor(self.pos(t))) % self.BEATS_PER_BAR + 1

    def time_of_beat(self, n: int) -> float:
        return self.anchor + n * self.period

    def nearest_downbeat(self, t: float) -> float | None:
        if self.anchor is None:
            return None
        bar = self.period * self.BEATS_PER_BAR
        return self.anchor + round((t - self.anchor) / bar) * bar

    def nudge(self, kick_t: float, bpm: float | None = None) -> None:
        """Un kick vient d'etre detecte : recale l'ancre et regle le pas sur le BPM detecte (sans faire sauter la grille)."""
        if self.anchor is None:
            return
        bar = self.period * self.BEATS_PER_BAR
        if bpm:
            self.anchor = self.anchor + round((kick_t - self.anchor) / bar) * bar       # ancre ramenee pres de maintenant avant de changer le pas
            self.period += (60.0 / min(max(float(bpm), 30.0), 300.0) - self.period) * 0.5
        p = self.pos(kick_t)
        off = p - round(p)
        if abs(off) <= self.CAPTURE:
            self.anchor += off * self.period * self.NUDGE


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
        self.grid = BeatGrid()
        self.beat_index: int | None = None      # numero de temps (depuis le « 1 » de la grille) du dernier kick, None sans grille

    @property
    def drops(self) -> int:
        return self.drop.drops

    @property
    def state(self) -> str:
        return self.drop.state

    def reset(self) -> None:
        self.est.reset()
        self.bpm, self.confidence, self._off, self._last_kick = None, 0.0, 0, None
        self.grid.clear()
        self.beat_index = None

    def _evaluate(self, now: float) -> None:
        if self._last_kick is not None and now - self._last_kick > BPM_LOST_S:
            self.est.reset()
            self.bpm, self.confidence, self._off = None, 0.0, 0
            self.grid.clear()                       # plus de kick : la mesure est perdue, il faudra redonner le « 1 »
            self.beat_index = None
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
            self.grid.nudge(now, self.bpm)
            self.beat_index = self.grid.beat_index(now)
        if now - self._eval_at >= self.EVAL_EVERY_S:
            self._eval_at = now
            self._evaluate(now)
        top = max(float(levels.get("mid", 0.0)), float(levels.get("high", 0.0)))
        event = self.drop.update(now, float(levels.get("bass", 0.0)), float(levels.get("rms", 0.0)), top)
        if event == "build":
            self.builds += 1
        return event
