"""Cerveau : journal partage de ce que l'overlay entend et decide (visualizer du cerveau, issue #192).

Role : les fils du moteur (audio, rendu GL) et la GUI publient des evenements dates (un kick entendu, un eclair du strobe, une
cassure...) dans un `BrainLog` ; le panneau Tk les relit pour dessiner ses quatre vues. Ce module ne dessine rien.

Threads : `publish`, `set_clock`, `set_status` sont appeles depuis n'importe quel fil ; tout passe sous un verrou unique, et les
lectures (`events`, `status`) rendent des copies : le lecteur ne voit jamais une liste en cours de modification.

Regle : les dates `t` sont dans l'horloge du son (`t_audio` du fil audio). Un fil qui n'a que l'horloge murale
(`time.monotonic()`, fil GL ou Tk) passe `wall=` : la date est convertie par le decalage publie par le fil audio (`set_clock`).
Python pur, stdlib seulement (voir core/CLAUDE.md).
"""
from __future__ import annotations

import bisect
import copy
import math
import threading
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------------------------------------
# Constantes : familles, grille, retention, libelles et couleurs
# ---------------------------------------------------------------------------------------------------------------------------

INSTRUMENTS = ("kick", "snare", "hihat")
TRACKS = ("strobe", "foudre", "glitch", "ripple", "chroma", "wobble", "logo", "scene")
STRUCTURE = ("phase", "build", "drop")                     # familles de structure du morceau (pas de ligne a elles)
FAMILIES = frozenset(INSTRUMENTS + TRACKS + STRUCTURE)

STEPS_PER_BEAT = 4                                         # pas de double croche
STEPS_PER_BAR = 16
PAGE_STEPS = 64                                            # une page = 4 mesures
PHRASE_STEPS = 128                                         # une phrase = 8 mesures

KEEP_S = 120.0                                             # duree gardee, par rapport a l'evenement le plus recent
MAX_EVENTS = 4000

LABELS = {"hihat": "hi-hat"}                               # libelle affiche (anglais) ; sinon le nom tel quel

COLORS = {
    "kick": "#ff3d7f", "snare": "#2ef2c8", "hihat": "#ffd23f",
    "strobe": "#ffffff", "foudre": "#9d8cff", "glitch": "#ff7a3d", "ripple": "#3db8ff", "chroma": "#ff4fd8",
    "wobble": "#7dff6b", "logo": "#ffb347", "scene": "#c9d1ff",
}


def label(name: str) -> str:
    """Libelle affiche d'une famille (`hihat` -> `hi-hat`)."""
    return LABELS.get(name, name)


# ---------------------------------------------------------------------------------------------------------------------------
# Placement sur la grille
# ---------------------------------------------------------------------------------------------------------------------------

def step_of(t: float, anchor: float, period: float) -> int:
    """Pas (double croche) le plus proche de la date `t`, compte depuis l'ancre de la grille (`period` = duree d'un temps)."""
    return round((t - anchor) / period * STEPS_PER_BEAT)


def step_time(step: int, anchor: float, period: float) -> float:
    """Date du pas `step` (inverse de `step_of`)."""
    return anchor + step * period / STEPS_PER_BEAT


# ---------------------------------------------------------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """Un evenement date dans l'horloge du son. `cause` : instrument qui l'a declenche (ou None)."""
    t: float
    family: str
    strength: float = 1.0
    reason: str = ""
    cause: str | None = None


class BrainLog:
    """Journal partage entre les fils : evenements (purges a KEEP_S et MAX_EVENTS) et dernier etat publie."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []                     # toujours trie par date
        self._times: list[float] = []                      # dates de `_events`, pour l'insertion triee
        self._offset: float | None = None                  # t_audio - wall ; None tant que le fil audio ne l'a pas publie
        self._status: dict = {}

    # ---- horloge
    def set_clock(self, t_audio: float, wall: float) -> None:
        """Publie la correspondance entre l'horloge du son et l'horloge murale (fil audio)."""
        with self._lock:
            self._offset = float(t_audio) - float(wall)

    def to_audio(self, wall: float) -> float:
        """Date murale convertie en temps du son (telle quelle tant qu'aucune horloge n'est connue)."""
        with self._lock:
            offset = self._offset
        return float(wall) if offset is None else float(wall) + offset

    # ---- evenements
    def publish(self, family: str, strength: float = 1.0, reason: str = "", cause: str | None = None, *,
                t: float | None = None, wall: float | None = None) -> None:
        """Ajoute un evenement (date `t` en temps du son, ou `wall` convertie). Famille inconnue ou sans date : ignore."""
        if family not in FAMILIES:
            return
        if t is None:
            if wall is None:
                return
            t = self.to_audio(wall)
        ev = Event(float(t), family, float(strength), str(reason), cause if cause in INSTRUMENTS else None)
        with self._lock:
            i = bisect.bisect_right(self._times, ev.t)
            self._times.insert(i, ev.t)
            self._events.insert(i, ev)
            cut = bisect.bisect_left(self._times, self._times[-1] - KEEP_S)
            cut = max(cut, len(self._events) - MAX_EVENTS)
            if cut > 0:
                del self._times[:cut]
                del self._events[:cut]

    def events(self, since: float | None = None) -> list[Event]:
        """Copie des evenements (ordre chronologique), a partir de la date `since` (incluse) si elle est donnee."""
        with self._lock:
            if since is None:
                return list(self._events)
            return self._events[bisect.bisect_left(self._times, since):]

    # ---- etat
    def set_status(self, **fields) -> None:
        """Met a jour le dernier etat publie (bpm, confidence, origin, grid, phase, phase_since, next_scene, next_scene_at, gauges)."""
        with self._lock:
            self._status.update(copy.deepcopy(fields))

    def status(self) -> dict:
        """Copie du dernier etat publie."""
        with self._lock:
            return copy.deepcopy(self._status)


class FxEdge:
    """Front montant d'un effet, pour n'en publier qu'un evenement par impulsion (et non un par image).

    `step(t, value)` rend vrai quand `value` franchit `on` alors que le detecteur est arme ; il se rearme quand `value` redescend a
    `off` ou en dessous (hysteresis : une valeur qui oscille entre les deux seuils ne publie rien). Deux fronts sont separes d'au
    moins `min_gap_s` (meme horloge que `t`) : une valeur rearmee qui remonte trop tot attend, armee, la fin de l'ecart."""

    def __init__(self, on: float = 0.6, off: float = 0.3, min_gap_s: float = 0.12) -> None:
        self.on, self.off, self.min_gap_s = float(on), float(off), float(min_gap_s)
        self._armed = True
        self._last: float | None = None

    def step(self, t: float, value: float) -> bool:
        value = float(value)
        if value <= self.off:
            self._armed = True
            return False
        if self._armed and value >= self.on and (self._last is None or t - self._last >= self.min_gap_s):
            self._armed = False
            self._last = float(t)
            return True
        return False


# ---------------------------------------------------------------------------------------------------------------------------
# Prediction et statuts
# ---------------------------------------------------------------------------------------------------------------------------

GHOST_MIN = 0.2                                            # fantome affiche au-dessus
EXPECT_MIN = 0.5                                           # frappe « attendue » a partir de
RECENT_W = 0.6                                             # poids des 4 mesures recentes
PHRASE_W = 0.4                                             # poids du meme pas une phrase plus tot
STABLE_MIN = 0.6                                           # confiance a partir de laquelle le tempo est « stable »
BUILD_RAMP_S = 16.0                                        # duree de la montee du voile (carre)
DROP_FADE_S = 4.0                                          # relachement du voile apres un drop
FREE_S = 8.0                                               # frise libre (sans grille) : secondes montrees
FEED_LINES = 4


def hits_by_step(events: list[Event], anchor: float, period: float) -> dict[tuple[int, str], float]:
    """Frappes placees sur la grille : cle (pas, instrument), valeur = force max des frappes tombees sur ce pas."""
    hits: dict[tuple[int, str], float] = {}
    for ev in events:
        if ev.family in INSTRUMENTS:
            key = (step_of(ev.t, anchor, period), ev.family)
            if ev.strength > hits.get(key, 0.0):
                hits[key] = ev.strength
    return hits


def _hit(hits: dict, step: int, row: str) -> float:
    return 1.0 if hits.get((step, row), 0.0) > 0 else 0.0


def predict_page(hits: dict, step: int, row: str, page_first: int) -> float:
    """Probabilite d'une frappe au pas `step` d'une page : 0,6 x part des 4 mesures de la page precedente ayant frappe au meme pas
    de mesure + 0,4 x frappe au meme pas une phrase (128 pas) plus tot."""
    s = (step - page_first) % STEPS_PER_BAR
    recent = sum(_hit(hits, page_first - PAGE_STEPS + STEPS_PER_BAR * k + s, row) for k in range(4)) / 4
    return RECENT_W * recent + PHRASE_W * _hit(hits, step - PHRASE_STEPS, row)


def predict_rolling(hits: dict, step: int, row: str, cur: int) -> float:
    """Meme ponderation, pour le tunnel : les 4 mesures recentes sont les 4 dernieres mesures DEJA ENTENDUES au meme pas de mesure
    (pas - 16 k, k choisi pour que pas - 16 k <= cur) ; le pas une phrase plus tot compte 0 s'il n'est pas encore entendu."""
    m = max(1, -(-(step - cur) // STEPS_PER_BAR))         # plus petit k avec step - 16 k <= cur
    recent = sum(_hit(hits, step - STEPS_PER_BAR * k, row) for k in range(m, m + 4)) / 4
    back = step - PHRASE_STEPS
    return RECENT_W * recent + PHRASE_W * (_hit(hits, back, row) if back <= cur else 0.0)


def status_of(v: float, p: float, past: bool) -> str | None:
    """Statut d'une case : passe -> real / surprise (frappe venue) ou miss (attendue, absente) ; futur -> ghost (prediction)."""
    if past:
        if v > 0:
            return "surprise" if p < EXPECT_MIN else "real"
        return "miss" if p >= EXPECT_MIN else None
    return "ghost" if p > GHOST_MIN else None


def cycle_left(beat_index: int) -> dict[int, int]:
    """Temps restants avant la fin des cycles de 8, 16 et 32 temps (0 = dernier temps du cycle)."""
    return {n: n - 1 - beat_index % n for n in (8, 16, 32)}


def veil_at(t: float, build_t0: float | None, drop_t: float | None) -> tuple[str | None, float]:
    """Voile a la date `t` : montee ouverte a `build_t0` (intensite au carre sur 16 s), ou relachement 4 s apres un drop."""
    if build_t0 is not None and t >= build_t0:
        return ("build", min(1.0, ((t - build_t0) / BUILD_RAMP_S) ** 2))
    if drop_t is not None and t >= drop_t:
        k = max(0.0, 1.0 - (t - drop_t) / DROP_FADE_S)
        if k > 0:
            return ("drop", k)
    return (None, 0.0)


class _Structure:
    """Balayage chronologique des evenements de structure : phase courante, montee ouverte, dernier drop (a une date donnee)."""

    def __init__(self, events: list[Event], phase0: str) -> None:
        self._evs = [e for e in events if e.family in STRUCTURE]
        self._i = 0
        # avant le premier changement de phase garde, la phase est inconnue ; sans aucun, c'est celle du statut
        self.phase = "" if any(e.family == "phase" for e in self._evs) else phase0
        self.phase_t: float | None = None
        self.build_t0: float | None = None
        self.drop_t: float | None = None

    def advance(self, t: float) -> "_Structure":
        """Applique les evenements de date <= t (appels a dates croissantes)."""
        while self._i < len(self._evs) and self._evs[self._i].t <= t:
            ev = self._evs[self._i]
            self._i += 1
            if ev.family == "build":
                self.build_t0, self.drop_t = ev.t, None
            elif ev.family == "drop":
                self.build_t0, self.drop_t = None, ev.t
            else:                                          # phase : son nom est dans la raison
                self.phase, self.phase_t = ev.reason, ev.t
                if ev.reason != "build":
                    self.build_t0 = None
        return self


def _clock(t: float, now: float) -> str:
    """Heure locale (hh:mm:ss) d'un evenement date dans l'horloge du son."""
    return time.strftime("%H:%M:%S", time.localtime(time.time() - (now - t)))


# ---------------------------------------------------------------------------------------------------------------------------
# Instantane : tout ce que les vues dessinent, deja calcule
# ---------------------------------------------------------------------------------------------------------------------------

def snapshot(log: BrainLog, now_wall: float, rolling: bool = False) -> dict:
    """Instantane du journal a la date murale `now_wall` (sans etat garde d'un appel a l'autre).

    Page (iso, mandala, sismo) : 64 pas depuis le debut de la page courante (multiple de 64 depuis l'ancre). `rolling` (tunnel) :
    68 pas depuis le pas courant - 4 (les 4 premiers sont le passe). Le journal est copie une seule fois : il est borne (KEEP_S,
    MAX_EVENTS), et la prediction comme la phase ou la montee en cours peuvent remonter plus loin que 2 pages.
    """
    st = log.status()
    now = log.to_audio(now_wall)
    events = log.events()
    grid = st.get("grid")
    struct_now = _Structure(events, st.get("phase") or "").advance(now)

    confidence = st.get("confidence")
    phase = st.get("phase") or struct_now.phase or None
    phase_since = st.get("phase_since")
    if not phase_since:                                    # None, ou 0,0 publie par le fil audio avant le premier changement : inconnue
        phase_since = None
    if phase_since is None:
        phase_since = struct_now.phase_t
    next_at = st.get("next_scene_at")                      # publie par le fil Tk : horloge murale
    header = {
        "bpm": st.get("bpm"),
        "confidence": confidence,
        "stable": confidence is not None and confidence >= STABLE_MIN,
        "origin": st.get("origin"),
        "phase": phase,
        "phase_s": None if phase_since is None else max(0.0, now - phase_since),
        "page": None, "bar": None, "cycles": None,
        "next_scene": st.get("next_scene"),
        "next_scene_s": None if next_at is None else next_at - now_wall,
        "build_s": None if struct_now.build_t0 is None else now - struct_now.build_t0,
    }
    feed = [(_clock(e.t, now), e.family, e.reason, e.cause) for e in events if e.family in TRACKS and e.t <= now][-FEED_LINES:]
    snap = {
        "grid": False, "first": 0, "cursor": 0.0, "cells": [], "fx": [], "phase_steps": [], "veil": [],
        "score": (0, 0, 0), "header": header, "gauges": st.get("gauges", {}), "feed": feed,
    }

    period = grid[1] if grid else 0.0
    if not grid or not period or period <= 0:
        snap["free"] = [(now - e.t, e.family, e.strength) for e in events
                        if (e.family in INSTRUMENTS or e.family in TRACKS) and 0.0 <= now - e.t <= FREE_S]
        return snap

    anchor = float(grid[0])
    pos = (now - anchor) / period * STEPS_PER_BEAT         # pas courant + fraction
    cur = math.floor(pos)
    if rolling:
        first, n = cur - 4, PAGE_STEPS + 4
        page, bar = cur // PAGE_STEPS + 1, (cur % PAGE_STEPS) // STEPS_PER_BAR + 1
    else:
        first, n = (cur // PAGE_STEPS) * PAGE_STEPS, PAGE_STEPS
        page, bar = first // PAGE_STEPS + 1, min(4, (cur - first) // STEPS_PER_BAR + 1)
    header.update(page=page, bar=bar, cycles=cycle_left(cur // STEPS_PER_BEAT))

    hits = hits_by_step(events, anchor, period)
    cells = []
    for i in range(n):
        step = first + i
        past = step <= cur
        for row in INSTRUMENTS:
            v = hits.get((step, row), 0.0)
            p = predict_rolling(hits, step, row, cur) if rolling else predict_page(hits, step, row, first)
            cells.append((i, row, v, p, status_of(v, p, past)))

    # score : toujours les pas deja passes de la page courante (prediction de page), que la vue soit glissante ou non
    ok = miss = surprise = 0
    page_first = (cur // PAGE_STEPS) * PAGE_STEPS
    for step in range(page_first, cur + 1):
        for row in INSTRUMENTS:
            s = status_of(hits.get((step, row), 0.0), predict_page(hits, step, row, page_first), True)
            if s == "real":
                ok += 1
            elif s == "miss":
                miss += 1
            elif s == "surprise":
                surprise += 1

    fx = []
    for e in events:
        if e.family in TRACKS:
            i = step_of(e.t, anchor, period) - first
            if 0 <= i < n:
                fx.append((i, e.family, e.strength, e.reason, e.cause))

    struct = _Structure(events, st.get("phase") or "")
    phase_steps, veil = [], []
    for i in range(n):
        ts = step_time(first + i, anchor, period)
        struct.advance(min(ts, now))                       # apres le present : l'etat connu se prolonge
        phase_steps.append(struct.phase)
        veil.append(veil_at(ts, struct.build_t0, struct.drop_t))

    snap.update(grid=True, first=first, cursor=pos - first, cells=cells, fx=fx, phase_steps=phase_steps, veil=veil,
                score=(ok, miss, surprise))
    return snap
