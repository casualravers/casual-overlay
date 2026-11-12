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

# Lot 5 de la spec synchro son / image : sources par BANDE FINE (enveloppes continues, `state["bands"]`) et par PULSATION (grille de temps de
# `core/pulse.py`, `state["pulse_since"]`) en plus des sources par attaque ci-dessus.
BAND_SOURCES = {"sub": (0,), "corps": (2, 3), "presence": (4, 5), "air": (6, 7)}      # nom -> indices des bandes (voir engine.audio.BANDS8), la plus forte compte
# nom -> (subdivisions par temps, un evenement sur N) : « pouls » = chaque temps, « croches » = 2 par temps, « doubles » = 4 par temps,
# « mesure » = 1 temps sur 4, « phrase8 / 16 / 32 » = 1 temps sur 8 / 16 / 32 (#85)
PULSE_GRIDS = {"pouls": (1, 1), "croches": (2, 1), "doubles": (4, 1), "mesure": (1, 4), "phrase8": (1, 8), "phrase16": (1, 16), "phrase32": (1, 32)}
SOURCES = ("tout", "basses", "mediums", "aigus", "kick", "frappes", "rien", "auto", *BAND_SOURCES, "elan", *PULSE_GRIDS)
# Menus : les sources par famille, une ligne de separation entre deux familles (voir gui_effects.source_menu)
SOURCE_GROUPS = (("tout", "basses", "mediums", "aigus", "kick", "frappes", "rien", "auto"), (*BAND_SOURCES, "elan"), tuple(PULSE_GRIDS))
RISE_REF = 3.0              # montee d'enveloppe (unites / s) qui donne un « elan » de 1
DEFAULT = "tout"
PULSE_DECAY_S = 0.20        # decroissance de l'enveloppe d'un evenement de pulsation (comme celle du kick)
NO_KICK = 1000.0            # « since » d'une couche sans attaque : l'onde de choc est eteinte depuis longtemps
LAYERS = ("bg", "logo")

LABELS = {"tout": "Tout", "basses": "Basses", "mediums": "Mediums", "aigus": "Aigus", "kick": "Kick", "frappes": "Frappes", "rien": "Rien", "auto": "Auto",
          "elan": "Elan (montee du son)", "sub": "Sub-basses", "corps": "Corps (150-1000 Hz)", "presence": "Presence (1-5 kHz)", "air": "Air (5-12 kHz)",
          "pouls": "Pouls (chaque temps)", "croches": "Pouls x2 (croches)", "doubles": "Pouls x4 (doubles)", "mesure": "Mesure (1 temps sur 4)",
          "phrase8": "Phrase (1 temps sur 8)", "phrase16": "Phrase (1 temps sur 16)", "phrase32": "Phrase (1 temps sur 32)"}

# Source « auto » : chaque couche prend la source que le tableau donne pour la phase du morceau en cours (core/phase.py). (fond, logo)
AUTO_TABLE = {
    "silence": ("rien", "rien"),         # les animations tres lentes de la phase suffisent
    "ambient": ("rien", "rien"),
    "groove": ("basses", "aigus"),       # deux rythmes qui se repondent ; le logo suit les frappes si on en detecte (voir HIT_RECENT_S)
    "build": ("mediums", "frappes"),     # la tension monte dans les mediums et les aigus
    "break": ("rien", "tout"),           # seul le logo reste vivant
    "peak": ("kick", "kick"),            # tout bat ensemble
}
HIT_RECENT_S = 4.0                       # groove : le logo suit les frappes aigues si on en a vu depuis moins de ce temps, sinon les aigus
FADE_S = 1.5                             # fondu entre deux sources choisies par Auto (jamais de saut a l'image)


def auto_sources(phase, since_hit=None) -> tuple[str, str]:
    """(source du fond, source du logo) que « auto » donne pour une phase. Phase inconnue ou absente : « tout » (comportement connu)."""
    if phase not in AUTO_TABLE:
        return "tout", "tout"
    bg, logo = AUTO_TABLE[phase]
    if phase == "groove" and since_hit is not None and float(since_hit) < HIT_RECENT_S:
        logo = "frappes"
    return bg, logo


def valid(source) -> str:
    return source if source in SOURCES else DEFAULT


def has_kick(source) -> bool:
    """Vrai si la source comprend les kicks (compteur de temps, bascule du damier, derive des basses)."""
    return valid(source) in ("tout", "basses", "kick")


def layer_levels(state: dict, source, layer: str = "bg") -> tuple[float, float, float, float]:
    """(bass, high, beat, since) vus par une couche (`layer` : « bg » ou « logo ») reglee sur `source`, a partir des niveaux de `state`
    (deja mis a l'echelle). Le kick d'une couche est celui de sa propre sensibilite si le moteur d'analyse en donne un."""
    source = valid(source)
    if source == "auto":                 # non resolue (pas de phase) : comme « tout »
        source = "tout"
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
    if source in BAND_SOURCES:          # une bande fine : son enveloppe continue (le corps du son, pas seulement l'impact), sans attaque
        bands = state.get("bands")
        v = max((float(bands[i]) for i in BAND_SOURCES[source] if bands is not None and i < len(bands)), default=0.0)
        return (v, 0.0, 0.0, NO_KICK) if source == "sub" else (v, v, 0.0, NO_KICK)
    if source == "elan":                # la MONTEE du son : la plus forte pente montante parmi les bandes fines (un son qui gonfle, une attaque, un riser)
        rises = state.get("band_rise")
        r = min(max((float(v) for v in rises), default=0.0) / RISE_REF, 1.0) if rises is not None else 0.0
        return r, r, 0.0, NO_KICK
    if source in PULSE_GRIDS:           # la grille de la pulsation : des evenements reguliers meme sans kick sur chaque temps (silence si la pulsation est perdue)
        since = (state.get("pulse_since") or {}).get(source)
        if since is None:
            return 0.0, 0.0, 0.0, NO_KICK
        env = math.exp(-max(float(since), 0.0) / PULSE_DECAY_S) * float(state.get("react_m", 1.0))
        return 0.8 * env, 0.0, env, float(since)
    return 0.0, 0.0, 0.0, NO_KICK


SILENT = (0.0, 0.0, 0.0, NO_KICK)
class SourceMixer:
    """Fondu entre deux sources quand « auto » en change (la phase change) : les niveaux de l'ancienne glissent vers ceux de la nouvelle en
    `FADE_S` s. Une source choisie a la main (`fade` faux) passe tout de suite, sans memoire."""

    def __init__(self) -> None:
        self.cur: str | None = None
        self.prev: str | None = None
        self.t0 = 0.0

    def levels(self, now: float, state: dict, source: str, layer: str, fade: bool) -> tuple[float, float, float, float]:
        if not fade or self.cur is None:
            self.cur, self.prev = source, None
            return layer_levels(state, source, layer)
        if source != self.cur:
            self.prev, self.cur, self.t0 = self.cur, source, now
        new = layer_levels(state, self.cur, layer)
        if self.prev is None:
            return new
        w = (now - self.t0) / FADE_S
        if w >= 1.0:
            self.prev = None
            return new
        old = layer_levels(state, self.prev, layer)
        w = w * w * (3.0 - 2.0 * w)
        return (old[0] + (new[0] - old[0]) * w, old[1] + (new[1] - old[1]) * w, old[2] + (new[2] - old[2]) * w, new[3] if w > 0.5 else old[3])


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
