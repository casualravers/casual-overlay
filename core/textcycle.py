"""Textes en rotation du logo : le texte principal puis des textes supplementaires, qui se succedent en rythme (toutes les N mesures).

Pur (stdlib seulement) : kicks et horloge sont passes en arguments. Le changement se fait par un fondu court : le logo s'efface (FADE_S),
le texte change, puis il revient (FADE_S). Sans kick la mesure est comptee au temps (garde-fou de `BarTimer`)."""
from __future__ import annotations

from core.controls import BarTimer

FADE_S = 0.2                      # duree de chaque moitie du fondu
BARS = (1, 2, 4, 8, 16)           # durees permises, en mesures de 4 kicks


def texts_of(main: str, extra: str) -> list[str]:
    """Textes de la boucle : le principal, puis chaque ligne non vide de `extra` (`\\n` dans une ligne = retour a la ligne)."""
    out = [str(main)]
    out.extend(line.strip().replace("\\n", "\n") for line in str(extra or "").splitlines() if line.strip())
    return out


def text_at(main: str, extra: str, index: int) -> str:
    items = texts_of(main, extra)
    return items[int(index) % len(items)]


class TextCycle:
    """Etat de la rotation : `update` rend (indice du texte a afficher, facteur de fondu 0..1)."""

    def __init__(self) -> None:
        self.index = 0
        self.state = "show"          # show | out | in
        self.t0 = 0.0
        self.timer: BarTimer | None = None
        self.bars = 0

    def reset(self) -> None:
        self.index, self.state, self.timer = 0, "show", None

    def update(self, now: float, beats: int, count: int, bars: int, tempo: float = 1.0, bpm: float | None = None) -> tuple[int, float]:
        if count <= 1:                                   # rien a faire tourner
            self.reset()
            return 0, 1.0
        bars = max(int(bars), 1)
        if self.timer is None or bars != self.bars:
            self.bars = bars
            self.timer = BarTimer(bars).arm(beats, now, tempo, bpm)
        self.index %= count
        if self.state == "show":
            if self.timer.due(beats, now):
                self.state, self.t0 = "out", now
            return self.index, 1.0
        if self.state == "out":
            k = (now - self.t0) / FADE_S
            if k < 1.0:
                return self.index, 1.0 - max(k, 0.0)
            self.index = (self.index + 1) % count
            self.state, self.t0 = "in", now
            return self.index, 0.0
        k = (now - self.t0) / FADE_S
        if k < 1.0:
            return self.index, max(k, 0.0)
        self.state = "show"
        self.timer = BarTimer(self.bars).arm(beats, now, tempo, bpm)
        return self.index, 1.0

    def label(self, count: int) -> str:
        return f"texte {self.index % max(count, 1) + 1}/{count}" if count > 1 else "un seul texte"