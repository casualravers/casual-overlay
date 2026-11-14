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


# --- Reactivite PAR EFFET (spec 2026-11-13) : en mode auto, chaque effet de chaque couche a SA source et son dosage selon la phase -----------------
# Les quatre effets de `post.frag` (`layer_fx`), dans l'ordre des vecteurs audio envoyes au shader : ondulation, onde de choc, glitch, aberration.
EFFECTS = ("wobble", "ripple", "glitch", "chroma")
DERIVE = "derive"                        # dosage : mouvement lent independant de la musique (ondulation et aberration seulement)
REST = ("rien", 0.0)                     # effet au repos
DOSE_TAU_S = 1.0                         # un dosage glisse vers sa cible avec cette constante de temps (jamais de saut)
DRIFT_WOBBLE = 0.25                      # amplitude de la derive de l'ondulation (niveau de basses equivalent)
DRIFT_CHROMA = 0.15                      # ... de l'aberration (niveau de kick equivalent)
DRIFT_PERIOD_S = 20.0
DOSE_OFF = 0.02                          # dosage lisse sous lequel l'effet est considere au repos (since = NO_KICK)


def _fx(wobble=REST, ripple=REST, glitch=REST, chroma=REST) -> dict:
    return {"wobble": wobble, "ripple": ripple, "glitch": glitch, "chroma": chroma}


# phase -> couche -> effet -> (source, dosage). Le fond porte les basses / le kick, le logo les frappes / aigus : ils ne battent pas a l'unisson.
# Hors du pic, deux effets au plus par couche ; au pic les quatre effets sont actifs mais repartis entre les deux couches. A regler a l'oeil.
FX_TABLE = {
    "silence": {"bg": _fx(wobble=("basses", DERIVE)), "logo": _fx(chroma=("rien", DERIVE))},
    "ambient": {"bg": _fx(wobble=("basses", DERIVE)), "logo": _fx(chroma=("rien", DERIVE))},
    "groove": {"bg": _fx(wobble=("basses", 0.6), ripple=("kick", 0.5)),
               "logo": _fx(glitch=("air", 0.5), chroma=("frappes", 0.6))},
    "build": {"bg": _fx(wobble=("mediums", 0.7), chroma=("elan", 0.5)),
              "logo": _fx(glitch=("aigus", 0.7), chroma=("frappes", 0.6))},
    "break": {"bg": _fx(), "logo": _fx(wobble=("tout", 0.6))},
    "peak": {"bg": _fx(wobble=("basses", 1.0), ripple=("kick", 1.0)),
             "logo": _fx(glitch=("frappes", 1.0), chroma=("kick", 1.0))},
}


def auto_fx(phase, since_hit=None) -> dict | None:
    """Assignations {couche: {effet: (source, dosage)}} que le mode auto donne pour une phase ; None si la phase est inconnue ou absente
    (comportement d'avant : un seul vecteur par couche). Groove : l'aberration du logo suit les frappes si on en a vu depuis moins de `HIT_RECENT_S`, sinon les aigus."""
    if phase not in FX_TABLE:
        return None
    table = {layer: dict(effects) for layer, effects in FX_TABLE[phase].items()}
    if phase == "groove":
        recent = since_hit is not None and float(since_hit) < HIT_RECENT_S
        table["logo"]["chroma"] = ("frappes" if recent else "aigus", table["logo"]["chroma"][1])
    return table


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


class EffectBank:
    """Les quatre vecteurs audio (un par effet, ordre `EFFECTS`) de chaque couche en mode auto : un `SourceMixer` (fondu de 1,5 s quand la source
    change) et un dosage lisse (`DOSE_TAU_S`) par couple (couche, effet). Un effet au repos rend `(0, 0, 0, NO_KICK)` : l'onde de choc ne part jamais de lui.
    La derive ajoute un petit mouvement lent independant du son (ondulation : composante `bass`, aberration : composante `beat`)."""

    def __init__(self) -> None:
        self._mix = {layer: [SourceMixer() for _ in EFFECTS] for layer in LAYERS}
        self._dose: dict[str, list[float | None]] = {layer: [None] * len(EFFECTS) for layer in LAYERS}       # None : pas encore de dosage, la premiere cible est prise tout de suite
        self._info: dict[str, list[tuple[str, str, float]]] = {layer: [] for layer in LAYERS}

    def levels(self, now: float, dt: float, state: dict, layer: str, assign: dict, kick_layer: str, fade: bool = True) -> list:
        a = 1.0 - math.exp(-max(float(dt), 0.0) / DOSE_TAU_S)
        swing = 0.5 + 0.5 * math.sin(2.0 * math.pi * now / DRIFT_PERIOD_S)
        out, info = [], []
        for i, effect in enumerate(EFFECTS):
            source, dose = assign.get(effect, REST)
            drift = dose == DERIVE
            target = 0.0 if drift else min(max(float(dose), 0.0), 1.0)
            prev = self._dose[layer][i]
            d = target if prev is None else prev + (target - prev) * a
            self._dose[layer][i] = d
            bass, high, beat, since = self._mix[layer][i].levels(now, state, source, kick_layer, fade)
            vec = [bass * d, high * d, beat * d, since] if d >= DOSE_OFF else [0.0, 0.0, 0.0, NO_KICK]
            if drift and effect in ("wobble", "chroma"):
                k = DRIFT_WOBBLE if effect == "wobble" else DRIFT_CHROMA
                vec[0 if effect == "wobble" else 2] += k * swing
                info.append((effect, DERIVE, k * swing))
            elif d >= DOSE_OFF:
                info.append((effect, source, d))
            out.append(tuple(vec))
        self._info[layer] = info
        return out

    def active(self, layer: str) -> list[tuple[str, str, float]]:
        """(effet, source ou « derive », dosage lisse) des effets non au repos, pour le voyant."""
        return list(self._info.get(layer, []))


ATTACK_MAX_S = 0.12        # lissage a 1 : attaque de 120 ms
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


# --- Onde de choc sur le 1er temps de la mesure (reglage `ripple_beat`) ---------------------------------------------------------------------
# L'onde de choc est l'effet le plus fort : a chaque kick elle sature l'image. En mode « mesure », elle ne part que sur le « 1 » de chaque
# mesure (un temps sur quatre). Le « 1 » se cale sur la grille Temps 1 (onglet Masters, touche virgule) ; sans grille, il est arbitraire.
RIPPLE_BEATS = ("mesure", "kick")
RIPPLE_FX = 1                            # indice de l'onde de choc dans les vecteurs par effet (wobble, onde de choc, glitch, aberration)
BEATS_PER_BAR = 4


class DownbeatGate:
    """Secondes depuis le dernier 1er temps de mesure (`NO_KICK` si aucun). Ordre de confiance :
    1. pulsation calee (`state["pulse_since"]["mesure"]`) : predictive, deja en avance, comptee depuis le « 1 » de la grille Temps 1 ;
    2. sinon, a chaque nouveau kick (`state["beats"]` augmente) : c'est un 1 si son numero de temps depuis la grille (`beat_index`) est un multiple de 4 ;
    3. sinon (ni pulsation ni grille) : un kick sur quatre, le premier entendu compte comme un 1 (l'onde ne disparait pas faute de calage)."""

    def __init__(self) -> None:
        self.last_beats = None           # compteur de kicks vu a l'image precedente
        self.count = 0                   # kicks comptes sans grille (repli 3)
        self.t_down = None               # instant du dernier 1 detecte par les kicks

    def update(self, now: float, state: dict) -> float:
        beats = int(state.get("beats") or 0)
        new = 0 if self.last_beats is None else max(0, beats - self.last_beats)
        if self.last_beats is None and beats > 0:
            new = 1                      # premier appel en plein morceau : le kick en cours compte
        self.last_beats = beats
        if new:
            idx = state.get("beat_index")
            if idx is not None:
                if int(idx) % BEATS_PER_BAR == 0:
                    self.t_down = now
                self.count = 0           # la grille fait foi : le repli repartira d'un 1 si elle disparait
            else:
                for _ in range(new):
                    if self.count % BEATS_PER_BAR == 0:
                        self.t_down = now
                    self.count += 1
        pulse = (state.get("pulse_since") or {}).get("mesure")
        if pulse is not None:
            return float(pulse)
        return NO_KICK if self.t_down is None else max(0.0, now - self.t_down)


def ripple_on_downbeat(vecs, since_down: float) -> list:
    """Copie des quatre vecteurs (bass, high, beat, since) d'une couche ou l'onde de choc compte depuis le dernier 1er temps au lieu du dernier
    kick. Une onde de choc au repos (source sans attaque : since = NO_KICK) le reste ; les autres effets ne changent pas."""
    out = list(vecs)
    v = out[RIPPLE_FX]
    if v[3] < NO_KICK - 1e-3:
        out[RIPPLE_FX] = (v[0], v[1], v[2], float(since_down))
    return out
