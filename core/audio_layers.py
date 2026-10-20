"""Sources audio par couche : le fond et le logo peuvent ne pas reagir a la meme partie du son.

Python pur (stdlib seulement). Chaque couche choisit une SOURCE parmi `SOURCES` ; `layer_levels` en tire les quatre grandeurs que lisent les
effets de la couche (`post.frag`, `layer_fx`) et les reactions du logo :
  * `bass`  : amplitude du wobble, rayon du halo du logo, derive du fond et des animations ;
  * `high`  : declenchement du glitch, tremblement du logo ;
  * `beat`  : enveloppe du kick (onde de choc, aberration, pulsation du logo, flash du fond) ;
  * `since` : secondes depuis le dernier kick (rayon de l'onde de choc).
« tout » rend les niveaux tels quels (comportement d'avant). Les masters de reactivite et les phases du morceau s'appliquent AVANT (aux niveaux
de `state`), donc la panique et les phases agissent sur toutes les sources.
"""
from __future__ import annotations

SOURCES = ("tout", "basses", "mediums", "aigus", "kick", "rien")
DEFAULT = "tout"
NO_KICK = 1000.0            # « since » d'une couche sans kick : l'onde de choc est eteinte depuis longtemps

LABELS = {"tout": "Tout", "basses": "Basses", "mediums": "Mediums", "aigus": "Aigus", "kick": "Kick", "rien": "Rien"}


def valid(source) -> str:
    return source if source in SOURCES else DEFAULT


def has_kick(source) -> bool:
    """Vrai si la source comprend les kicks (compteur de temps, ondes de choc, aberration, pulsation)."""
    return valid(source) in ("tout", "basses", "kick")


def layer_levels(state: dict, source) -> tuple[float, float, float, float]:
    """(bass, high, beat, since) vus par une couche reglee sur `source`, a partir des niveaux de `state` (deja mis a l'echelle)."""
    source = valid(source)
    bass, mid, high = float(state["bass"]), float(state.get("mid", 0.0)), float(state["high"])
    beat, since = float(state["beat"]), float(state["since_beat"])
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
    return 0.0, 0.0, 0.0, NO_KICK
