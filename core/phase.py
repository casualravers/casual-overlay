"""Phases du morceau : comprendre OU l'on en est (silence, ambiance, groove, montee, cassure, pic) pour regler le visuel tout seul.

Python pur (stdlib seulement, le temps est injecte) : nourri par le fil d'analyse audio (`engine.audio.AudioAnalyzer.phase`) a chaque pas, lu par la
boucle de rendu qui en tire des multiplicateurs (`PhaseTracker.snapshot()["mult"]`). Aucun reglage : les seuils s'adaptent au morceau et au micro.

Ce qui est regarde :
  * l'energie (rms brut, lisse sur ~1 s) comparee a la moyenne de l'energie PENDANT LES PASSAGES A KICKS (constante de temps 40 s, amorcee au premier
    passage a kicks) : un morceau regulier est « groove », le « pic » est un passage nettement plus fort que d'habitude ; planchers absolus pour le
    silence et l'ambiance ;
  * la densite de kicks (kicks des 4 dernieres secondes rapportes au tempo detecte) et le temps depuis le dernier ;
  * la repartition des bandes (part des basses, luminosite = poids des aigus) ;
  * la tendance de l'energie sur 8 s (une montee : pente du log de l'energie) et les etats de `core.rhythm.Rhythm` (cassure / montee vues sur les basses).

Les changements de phase passent par une hysteresis (la nouvelle phase doit durer avant d'etre adoptee, et chaque phase dure au moins 4 s)
et les multiplicateurs glissent doucement (constante de temps ~2,5 s) : jamais de saut brutal a l'ecran.
"""
from __future__ import annotations

import math
from collections import deque

PHASES = ("silence", "ambient", "groove", "build", "break", "peak")
LABELS = {"silence": "silence", "ambient": "ambiance", "groove": "groove", "build": "montee", "break": "cassure", "peak": "pic"}

# Multiplicateurs de chaque phase : tempo des animations, reactivite a l'audio, intensite des effets, presence de l'overlay, luminosite du fond,
# derive de teinte (degres par seconde, s'ajoute au cycle de teinte), respiration (0..1). Neutre = (1, 1, 1, 1, 1, 0, 0). Jamais de strobe automatique.
KEYS = ("tempo", "react", "fx", "overlay", "bg", "hue", "breath")
NEUTRAL = {"tempo": 1.0, "react": 1.0, "fx": 1.0, "overlay": 1.0, "bg": 1.0, "hue": 0.0, "breath": 0.0}
PROFILES = {
    # tres lent : le visuel ne s'eteint pas, il respire doucement et la teinte derive a peine
    "silence": {"tempo": 0.15, "react": 0.3, "fx": 0.2, "overlay": 0.9, "bg": 0.9, "hue": 2.0, "breath": 0.6},
    "ambient": {"tempo": 0.30, "react": 0.6, "fx": 0.35, "overlay": 0.95, "bg": 1.0, "hue": 3.0, "breath": 0.5},
    "groove": dict(NEUTRAL),
    "build": {"tempo": 1.1, "react": 1.3, "fx": 1.25, "overlay": 1.0, "bg": 1.0, "hue": 10.0, "breath": 0.0},
    "break": {"tempo": 0.55, "react": 0.8, "fx": 0.6, "overlay": 1.0, "bg": 0.85, "hue": 6.0, "breath": 0.3},
    "peak": {"tempo": 1.1, "react": 1.2, "fx": 1.3, "overlay": 1.0, "bg": 1.0, "hue": 0.0, "breath": 0.0},
}

WARMUP_S = 3.0                # avant cela on ne sait rien : phase « ambient »
SILENCE_ABS = 0.004           # rms brut (1 = pleine echelle) sous lequel c'est du silence (~ -48 dBFS)
CALM_ABS = 0.012              # sous cela l'energie est faible quelle que soit la moyenne
QUIET_ABS = 0.04              # sans kick, sous cette energie : ambiance
MEAN_TAU_S = 40.0
E_TAU_S = 1.0
PEAK_BOOST = 1.2              # energie / moyenne pour entrer en « pic » (on en sort sous PEAK_KEEP)
PEAK_KEEP = 1.08
BUILD_MIN = 0.03              # une montee se reconnait au-dessus de cette energie (une entree en douceur depuis le silence n'en est pas une)
BUILD_SLOPE = 0.04            # pente du log de l'energie (par seconde) d'une montee : elle double en ~17 s
KICK_WINDOW_S = 4.0
TREND_WINDOW_S = 8.0
MIN_DWELL_S = 4.0             # une phase dure au moins cela
ENTER_S = {"silence": 2.5, "ambient": 2.5, "groove": 1.5, "build": 1.5, "break": 1.5, "peak": 1.5}
MULT_TAU_S = 2.5              # glissement des multiplicateurs vers le profil de la phase
BUILD_MAX_S = 40.0
BUILD_RAMP_S = 16.0           # la montee gagne en intensite sur ce temps


class PhaseTracker:
    """Suit les phases du morceau. `update()` a chaque pas d'analyse, `snapshot()` depuis le rendu (ne bloque pas : un simple dict)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.phase = "ambient"
        self.since = 0.0
        self.t0: float | None = None
        self._last: float | None = None
        self._e = 0.0                       # energie lissee
        self._mean = 0.0                    # moyenne de l'energie pendant les passages a kicks (0 tant qu'il n'y en a pas eu)
        self._boost = 1.0                   # energie / moyenne
        self._kicks: deque[float] = deque()
        self._trend: deque[tuple[float, float]] = deque()
        self._trend_at = -1e9
        self._cand: str | None = None
        self._cand_since = 0.0
        self._silent_since: float | None = None
        self._bright = 0.0
        self._bass_share = 0.0
        self._density = 0.0
        self._slope = 0.0
        self._mult = dict(NEUTRAL)
        self._build_t: float | None = None
        self._grooving = False
        self._recent_groove = -1e9          # derniere fois qu'on etait en groove / pic / montee (distingue cassure et ambiance)

    # ---- entree
    def update(self, now: float, levels: dict, beat: bool = False, rhythm_state: str = "idle", bpm: float | None = None) -> str:
        """Un pas d'analyse. `levels` : niveaux bruts (`raw_rms`, `raw_bass`, `raw_mid`, `raw_high`, 0..1 environ, voir `FeatureExtractor.process`)."""
        if self.t0 is None:
            self.t0 = self._last = now
            self.since = now
        dt = min(max(now - (self._last if self._last is not None else now), 0.0), 0.5)
        self._last = now
        rms = max(float(levels.get("raw_rms", levels.get("rms", 0.0))), 0.0)
        bass, mid, high = (max(float(levels.get(k, 0.0)), 0.0) for k in ("raw_bass", "raw_mid", "raw_high"))
        if beat:
            self._kicks.append(now)
        while self._kicks and now - self._kicks[0] > KICK_WINDOW_S:
            self._kicks.popleft()
        rate = len(self._kicks) / KICK_WINDOW_S
        self._grooving = self._density >= 0.55 or rate >= 1.2          # (etat du pas precedent pour l'amorce)
        if dt > 0:
            self._e += (rms - self._e) * (1.0 - math.exp(-dt / E_TAU_S))
            if self._grooving:
                if self._mean <= 0.0:
                    self._mean = max(self._e, CALM_ABS)       # amorce : la moyenne part du premier passage a kicks, pas de zero
                else:
                    self._mean += (self._e - self._mean) * (1.0 - math.exp(-dt / MEAN_TAU_S))
        total = bass + mid + high
        if total > 1e-6:
            self._bass_share += (bass / total - self._bass_share) * 0.1
            self._bright += ((mid + 2.0 * high) / (total + high) - self._bright) * 0.05
        rate = len(self._kicks) / KICK_WINDOW_S
        self._density = min(rate / (max(bpm, 60.0) / 60.0), 1.0) if bpm else min(rate / 2.0, 1.0)
        self._grooving = self._density >= 0.55 or rate >= 1.2
        self._boost = self._e / self._mean if self._mean > 0.0 else 1.0
        if now - self._trend_at >= 0.5:
            self._trend_at = now
            self._trend.append((now, math.log(max(self._e, 1e-5))))
            while self._trend and now - self._trend[0][0] > TREND_WINDOW_S:
                self._trend.popleft()
            if len(self._trend) >= 6:
                self._slope = (self._trend[-1][1] - self._trend[0][1]) / max(self._trend[-1][0] - self._trend[0][0], 1e-6)
        if self._e < SILENCE_ABS:
            if self._silent_since is None:
                self._silent_since = now
        else:
            self._silent_since = None
        self._adopt(now, self._classify(now, rate, rhythm_state))
        self._ease(dt)
        return self.phase

    def _classify(self, now: float, kick_rate: float, rhythm_state: str) -> str:
        if now - (now if self.t0 is None else self.t0) < WARMUP_S:
            return self.phase
        if self._silent_since is not None and now - self._silent_since >= ENTER_S["silence"]:
            return "silence"
        since_kick = now - self._kicks[-1] if self._kicks else 1e9
        kicks_on = self._density >= 0.55 or kick_rate >= 1.2
        if kicks_on:
            limit = PEAK_KEEP if self.phase == "peak" else PEAK_BOOST
            if self._boost >= limit and self._bass_share >= 0.3 and self._e > 0.03:
                return "peak"
            return "groove"
        rising = self._slope > BUILD_SLOPE and self._e > BUILD_MIN and kick_rate < 0.5 and self.phase in ("ambient", "break", "groove", "build")
        if rhythm_state == "build" or rising:
            return "build"
        if (self.phase == "build" and self._build_t is not None and now - self._build_t < BUILD_MAX_S and self._slope > -0.01
                and self._e > CALM_ABS):
            return "build"                                 # la montee continue tant que l'energie ne retombe pas
        if rhythm_state == "break":
            return "break"
        if self._e < QUIET_ABS:
            return "ambient"
        # pas de kick mais de l'energie : cassure si on vient d'un groove / pic (le kick vient de s'arreter), sinon la musique continue
        if now - self._recent_groove < 20.0 and since_kick > 1.2:
            return "break"
        return "groove"

    def _adopt(self, now: float, target: str) -> None:
        if target == self.phase:
            self._cand = None
            return
        if self._cand != target:
            self._cand, self._cand_since = target, now
            return
        if now - self._cand_since >= ENTER_S[target] and now - self.since >= MIN_DWELL_S:
            self.phase, self.since, self._cand = target, now, None
            self._build_t = now if target == "build" else None

    def _ease(self, dt: float) -> None:
        if self.phase in ("groove", "peak", "build"):
            self._recent_groove = self._last or 0.0
        prof = PROFILES[self.phase]
        k = 1.0 - math.exp(-dt / MULT_TAU_S) if dt > 0 else 0.0
        ramp = 1.0
        if self.phase == "build" and self._build_t is not None:
            ramp = min(max(((self._last or 0.0) - self._build_t) / BUILD_RAMP_S, 0.0), 1.0)
        for key in KEYS:
            goal = NEUTRAL[key] + (prof[key] - NEUTRAL[key]) * ramp
            self._mult[key] += (goal - self._mult[key]) * k

    # ---- sortie
    def snapshot(self) -> dict:
        """Etat lisible sans verrou : nom de la phase, depuis quand, mesures et multiplicateurs lisses."""
        return {"name": self.phase, "label": LABELS[self.phase], "since": self.since, "energy": round(self._boost, 3),
                "density": round(self._density, 3), "bass": round(self._bass_share, 3), "bright": round(self._bright, 3),
                "slope": round(self._slope, 4), "mult": dict(self._mult)}


def apply_to_params(params: dict, snapshot: dict | None) -> str:
    """Ecrit dans `params` les multiplicateurs de la phase (`phase_tempo`, `phase_react`...) lus par le rendu et l'automation. Sans « phases auto »
    (`auto_phase` < 0,5) ou sans mesure, tout est neutre. Rend le nom de la phase appliquee (« neutre » si aucune)."""
    on = snapshot is not None and float(params.get("auto_phase", 1.0)) >= 0.5
    mult = snapshot["mult"] if on else NEUTRAL
    for key in KEYS:
        params["phase_" + key] = float(mult[key])
    return snapshot["name"] if on else "neutre"
