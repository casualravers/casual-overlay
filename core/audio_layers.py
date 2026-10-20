"""Sources audio par couche : le fond et le logo peuvent ne pas reagir a la meme partie du son.

Python pur (stdlib seulement). Chaque couche choisit une SOURCE parmi `SOURCES` ; `layer_levels` en tire les quatre grandeurs que lisent les
effets de la couche (`post.frag`, `layer_fx`) et les reactions du logo :
  * `bass`  : amplitude du wobble, rayon du halo du logo, derive du fond et des animations ;
  * `high`  : declenchement du glitch, tremblement du logo ;
  * `beat`  : enveloppe de l'attaque (onde de choc, aberration, pulsation du logo, flash du fond) ;
  * `since` : secondes depuis la derniere attaque (rayon de l'onde de choc).
« tout » rend les niveaux tels quels (comportement d'avant). Les masters de reactivite et les phases du morceau s'appliquent AVANT (aux niveaux
de `state`), donc la panique et les phases agissent sur toutes les sources.

Niveau 2 : « frappes » = les attaques des aigus (charleys, claps : `OnsetDetector` sur la bande aigue, `state["hit"]` / `["since_hit"]`), un rythme
secondaire ; et le kick d'une couche peut avoir sa SENSIBILITE propre (`state["beat_bg"]` / `["beat_logo"]`, sinon le kick commun).
Niveau 3 : `LayerFilter` = retard (la couche reagit un peu apres) et lissage (attaque / relachement plus doux) propres a chaque couche.
"""
from __future__ import annotations

import math
from collections import deque

SOURCES = ("tout", "basses", "mediums", "aigus", "kick", "frappes", "rien")
DEFAULT = "tout"
NO_KICK = 1000.0            # « since » d'une couche sans attaque : l'onde de choc est eteinte depuis longtemps
LAYERS = ("bg", "logo")

LABELS = {"tout": "Tout", "basses": "Basses", "mediums": "Mediums", "aigus": "Aigus", "kick": "Kick", "frappes": "Frappes", "rien": "Rien"}


def valid(source) -> str:
    return source if source in SOURCES else DEFAULT


def has_kick(source) -> bool:
    """Vrai si la source comprend les kicks (compteur de temps, bascule du damier, derive des basses)."""
    return valid(source) in ("tout", "basses", "kick")


def layer_levels(state: dict, source, layer: str = "bg") -> tuple[float, float, float, float]:
    """(bass, high, beat, since) vus par une couche (`layer` : « bg » ou « logo ») reglee sur `source`, a partir des niveaux de `state`
    (deja mis a l'echelle). Le kick d'une couche est celui de sa propre sensibilite si le moteur d'analyse en donne un."""
    source = valid(source)
    bass, mid, high = float(state["bass"]), float(state.get("mid", 0.0)), float(state["high"])
    beat = float(state.get("beat_" + layer, state["beat"]))
    since = float(state.get("since_" + layer, state["since_beat"]))
    if source == "tout":
        return bass, high, beat, since
    if source == "basses":              # le kick vit dans les basses : wobble et kick, pas de glitch aux aigus
        return bass, 0.0, beat, since
    if source == "mediums":
        return mid, mid, 0.0, NO_KICK
    if source == "aigus":
        return high, high, 0.0, NO_KICK
    if source == "kick":                # seulement ce qui part des kicks : onde de choc, aberration, pulsation
        return 0.0, 0.0, beat, since
    if source == "frappes":             # seulement les attaques des aigus : charleys, claps (rythme secondaire)
        return 0.0, 0.0, float(state.get("hit", 0.0)), float(state.get("since_hit", NO_KICK))
    return 0.0, 0.0, 0.0, NO_KICK


SILENT = (0.0, 0.0, 0.0, NO_KICK)
ATTACK_MAX_S = 0.12         # lissage a 1 : attaque de 120 ms
RELEASE_MAX_S = 0.8         # ... relachement de 800 ms


class LayerFilter:
    """Retard et lissage d'une couche. `step(now, levels, lag, smooth)` rend les niveaux (bass, high, beat, since) a utiliser.

    `lag` (secondes) : la couche voit le son tel qu'il etait il y a `lag` s (effet d'echo entre les couches). `smooth` (0..1) : attaque et
    relachement plus doux des niveaux (bass, high, beat) ; `since` reste net pour que l'onde de choc parte pile. Sans retard ni lissage, les
    niveaux passent tels quels (aucun calcul)."""

    def __init__(self) -> None:
        self.hist: deque[tuple[float, tuple]] = deque()
        self.sm: list[float] | None = None
        self.t: float | None = None

    def step(self, now: float, levels: tuple, lag: float = 0.0, smooth: float = 0.0) -> tuple:
        lag = min(max(float(lag), 0.0), 1.0)
        smooth = min(max(float(smooth), 0.0), 1.0)
        dt = 0.0 if self.t is None else min(max(now - self.t, 0.0), 0.2)
        self.t = now
        if lag <= 1e-4:
            self.hist.clear()
            out = levels
        else:
            self.hist.append((now, levels))
            while len(self.hist) > 1 and self.hist[1][0] <= now - lag:      # on garde l'echantillon le plus recent qui a au moins `lag` s
                self.hist.popleft()
            out = self.hist[0][1] if self.hist[0][0] <= now - lag + 1e-9 else SILENT      # pas encore assez d'historique : silence
        if smooth <= 1e-4:
            self.sm = None
            return out
        if self.sm is None:
            self.sm = [out[0], out[1], out[2]]
        ta, tr = ATTACK_MAX_S * smooth, RELEASE_MAX_S * smooth
        for i in range(3):
            v = out[i]
            tau = ta if v > self.sm[i] else tr
            if dt > 0 and tau > 1e-6:
                self.sm[i] += (v - self.sm[i]) * (1.0 - math.exp(-dt / tau))
            elif tau <= 1e-6:
                self.sm[i] = v
        return (self.sm[0], self.sm[1], self.sm[2], out[3])
