"""Horloge de pulsation et « avance » (lot 4 de la spec synchro son / image, issue #96).

Un effet declenche quand le kick est DETECTE arrive toujours apres le son : la chaine (analyse, rendu, ecran, videoprojecteur) retarde la lumiere de
plusieurs dizaines de ms. Pour un morceau regulier, on peut faire mieux que reagir : **predire** le prochain kick et declencher l'effet
`avance` secondes AVANT l'instant ou on le detectera, de sorte que la lumiere tombe avec le son (l'avance recommandee est celle que mesure
`tools/calibrer_avance.py`).

Python pur (stdlib), temps injecte.

* `PulseClock` : boucle a verrouillage de phase calee sur les kicks. Sortie : phase 0..1, periode, confiance 0..1, prochain instant predit.
  Un kick proche de la phase attendue la recale partiellement ; un kick manque ne la decale pas ; un kick hors fenetre est ignore ; sans kick elle
  tourne quelques mesures puis la confiance retombe a 0.
* `LeadGate` : decide, a chaque pas d'analyse, si un effet « sur kick » part (declenchement predit, ou reaction au kick detecte). Garantit un
  seul declenchement par kick (pas de double eclair) et retombe sur la detection reelle des que la prediction n'est pas fiable.
"""
from __future__ import annotations

import math

# --- horloge ----------------------------------------------------------------------------------------------------------
WINDOW = 0.25              # un kick a moins de 0,25 temps de la phase attendue la recale ; au-dela il est ignore pour la phase
KP = 0.30                  # part de l'ecart de phase corrigee a chaque kick
KI = 0.06                  # part de l'ecart qui corrige aussi la periode (les derives lentes de tempo)
SEED_CONF = 0.30           # confiance a la creation
HIT_GAIN = 0.25            # confiance gagnee par un kick bien place (part de ce qui manque a 1)
MISS_LOSS = 0.06           # confiance perdue par un temps sans kick : de 1 a 0 en ~16 temps = 4 mesures
HINT_TOL = 0.08            # le BPM detecte a moins de 8 % de la periode : il la regle doucement
HINT_PULL = 0.10
HINT_SNAP = 3              # ... en desaccord 3 fois de suite : changement de morceau, la periode saute
CALE = 0.60                # confiance a partir de laquelle la pulsation est « calee »
PERDUE = 0.15              # en dessous (apres avoir ete calee) : « perdue »

# --- declenchement predit ---------------------------------------------------------------------------------------------
RELIABLE = 0.75            # part des derniers temps qui ont eu un kick : en dessous on ne predit pas (breakbeat, kick un temps sur deux...)
RELIABLE_GAIN = 0.30
MATCH_EARLY = 0.06         # un kick detecte dans [predit - 60 ms, predit + 100 ms] est « le meme » que le declenchement predit
MATCH_LATE = 0.10
LATE_FIRE = 0.03           # un declenchement qui aurait du partir il y a plus de 30 ms est abandonne (la detection reelle prendra le relais)
REFRACTORY = 0.15
MAX_LEAD_FRAC = 0.6        # l'avance ne depasse jamais 60 % de la periode


def swell(phase: float | None, period_s: float = 0.5, lead_s: float = 0.0) -> float:
    """Courbe douce de la respiration sur la pulsation : 1 sur le temps, 0 a mi-chemin du suivant (cosinus : jamais de saut). `lead_s` :
    l'avance, pour que le sommet tombe sur le temps ENTENDU (l'horloge est calee sur la detection, qui est en retard sur le son)."""
    if phase is None or period_s <= 0.0:
        return 0.5
    ph = (phase + lead_s / period_s) % 1.0
    return 0.5 + 0.5 * math.cos(2.0 * math.pi * ph)


def cycle_seconds(seconds: float, beats: float, pulse_period_s: float | None, usable: bool) -> float:
    """Duree d'un cycle lent (fonte, respiration) : `beats` temps de la pulsation quand elle est calee ou libre, sinon `seconds` (le reglage en secondes,
    comme avant). `beats` = 0 : toujours `seconds`. Le cycle prend ainsi la cadence du morceau (la fonte dure 16 temps, quel que soit le BPM)."""
    if float(beats) >= 0.5 and usable and pulse_period_s and pulse_period_s > 0.0:
        return float(beats) * float(pulse_period_s)
    return float(seconds)


class PulseClock:
    def __init__(self) -> None:
        self.period = 0.5
        self.anchor: float | None = None          # un instant ou tombait un temps
        self.confidence = 0.0
        self.was_locked = False
        self.reliability = 0.0                    # EMA (par temps) de « ce temps a eu un kick »
        self._matched_beat = -1                   # numero du dernier temps qui a eu un kick
        self._checked_beat = -1                   # numero du dernier temps dont on a verifie l'absence de kick
        self._hint_off = 0

    # ---- lecture
    @property
    def active(self) -> bool:
        return self.anchor is not None

    @property
    def state(self) -> str:
        """`cale` (confiance >= 0,6), `libre` (en cours d'accrochage ou affaiblie), `perdue` (etait calee, plus rien) ; `absente` avant tout kick."""
        if self.anchor is None:
            return "absente"
        if self.confidence >= CALE:
            return "cale"
        if self.was_locked and self.confidence < PERDUE:
            return "perdue"
        return "libre"

    def beat_number(self, t: float) -> int:
        return int(math.floor((t - self.anchor) / self.period + 0.5))

    def phase(self, t: float) -> float | None:
        """0..1 : 0 = sur un temps, 0,5 = a mi-chemin du suivant."""
        if self.anchor is None:
            return None
        return ((t - self.anchor) / self.period) % 1.0

    def time_of_beat(self, n: int) -> float:
        return self.anchor + n * self.period

    def next_beat(self, t: float) -> float | None:
        """Premier instant de temps predit strictement apres `t`."""
        if self.anchor is None:
            return None
        return self.anchor + (math.floor((t - self.anchor) / self.period) + 1) * self.period

    def reset(self) -> None:
        self.__init__()

    # ---- mise a jour
    def update(self, now: float, kick: bool, bpm: float | None = None) -> None:
        """Un pas d'analyse a l'instant `now` : `kick` = un kick vient d'etre detecte ; `bpm` = le BPM du rythme (indication, jamais impose)."""
        hint = 60.0 / min(max(float(bpm), 30.0), 300.0) if bpm else None
        if self.anchor is None:
            if kick and hint is not None:                      # naissance : un kick et un BPM fiable
                self.anchor, self.period, self.confidence = now, hint, SEED_CONF
                self._matched_beat = self._checked_beat = 0
                self.reliability = 0.5
            return
        n_now = self.beat_number(now)
        if kick:
            p = (now - self.anchor) / self.period
            e = p - round(p)                                   # ecart en temps, -0,5..0,5
            if abs(e) <= WINDOW:
                self.anchor += KP * e * self.period
                self.period = max(self.period * (1.0 + KI * e / max(abs(round(p)), 1)), 0.2)       # derive lente : repartie sur le nombre de temps ecoules
                self.confidence += (1.0 - self.confidence) * HIT_GAIN
                n = int(round(p))
                if n > self._matched_beat:
                    self._matched_beat = n
                    self._account(n)                           # ce temps a eu son kick (les temps vides avant lui sont comptes par `_age`)
            if hint is not None:
                self._follow_hint(now, hint)
        self._age(now, n_now)
        if self.confidence >= CALE:
            self.was_locked = True
        if self.confidence < 0.02 and self.was_locked:
            self.anchor = None                                 # plus rien : on repart de zero (il faudra de nouveau un BPM fiable)
            self.__init__()

    def _follow_hint(self, now: float, hint: float) -> None:
        if abs(hint / self.period - 1.0) <= HINT_TOL:
            self.period += (hint - self.period) * HINT_PULL
            self._hint_off = 0
        else:
            self._hint_off += 1
            if self._hint_off >= HINT_SNAP:                    # le morceau a change de tempo : la periode saute, la phase est reprise sur ce kick
                self.period, self.anchor, self._hint_off = hint, now, 0
                self.confidence = min(self.confidence, SEED_CONF)
                self.reliability, self._matched_beat, self._checked_beat = 0.5, 0, 0

    def _account(self, n: int) -> None:
        """Le temps n a un kick : fiabilite en hausse (les temps sautes depuis le dernier compte sont des absences, comptees par `_age`)."""
        self.reliability += (1.0 - self.reliability) * RELIABLE_GAIN
        self._checked_beat = max(self._checked_beat, n)

    def _age(self, now: float, n_now: int) -> None:
        """Temps ecoules sans kick : la confiance et la fiabilite baissent. Un temps n'est juge absent que 0,3 temps apres son instant."""
        last = int(math.floor((now - 0.3 * self.period - self.anchor) / self.period))        # dernier temps dont l'instant + 0,3 temps est passe
        while self._checked_beat < last:
            self._checked_beat += 1
            if self._checked_beat > self._matched_beat:
                self.confidence = max(self.confidence - MISS_LOSS, 0.0)
                self.reliability -= self.reliability * RELIABLE_GAIN


class LeadGate:
    """Un seul declenchement par kick : predit (`avance` secondes avant la detection attendue) quand la pulsation est calee et fiable, sinon a la
    detection reelle. A appeler a chaque pas d'analyse ; `step` rend vrai quand un effet « sur kick » doit partir maintenant."""

    def __init__(self) -> None:
        self.armed: float | None = None           # instant predit du declenchement deja parti, en attente de sa detection reelle
        self.last_fire = -1e9
        self.delta_ms = 0.0                       # ecart lisse (detection reelle - instant predit), pour le voyant
        self.predicted = 0                        # declenchements predits comptes (voyant, tests)
        self.reactive = 0                         # declenchements sur detection reelle

    def predicting(self, clock: PulseClock, lead: float, allowed: bool) -> bool:
        return lead > 0.0 and allowed and clock.state == "cale" and clock.reliability >= RELIABLE

    def step(self, now: float, clock: PulseClock, kick: bool, lead: float, allowed: bool = True) -> bool:
        if not self.predicting(clock, lead, allowed):
            self.armed = None
            if kick:
                self.last_fire = now
                self.reactive += 1
            return kick
        lead = min(lead, clock.period * MAX_LEAD_FRAC)
        fire = False
        if kick:
            if self.armed is not None and self.armed - MATCH_EARLY <= now <= self.armed + MATCH_LATE:
                self.delta_ms += ((now - self.armed) * 1000.0 - self.delta_ms) * 0.25
                self.armed = None                                    # deja declenche en avance : la detection reelle n'allume pas une seconde fois
            elif now - self.last_fire > REFRACTORY:
                fire = True                                          # un kick que la prediction n'attendait pas : on reagit, comme sans avance
                self.reactive += 1
        if not fire and now - self.last_fire > REFRACTORY:
            tp = clock.time_of_beat(clock.beat_number(now + lead))   # le temps predit le plus proche de l'instant ou la detection tomberait si on partait maintenant
            if now - LATE_FIRE <= tp - lead <= now:                  # l'instant de declenchement (tp - avance) vient d'arriver
                fire = True
                self.armed = tp
                self.predicted += 1
        if self.armed is not None and now > self.armed + MATCH_LATE:
            self.armed = None                                        # le kick predit n'est pas venu (manque) : rien a rattraper
        if fire:
            self.last_fire = now
        return fire
