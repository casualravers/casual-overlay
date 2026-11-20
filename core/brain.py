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
import threading
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
