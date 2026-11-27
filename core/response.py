"""Reponse d'un curseur (#260) : position 0..1 du curseur <-> valeur du reglage, lineaire ou en puissance.

Python pur (stdlib seulement). Un curseur « a reponse en puissance p » (`curve` = p) donne la valeur `bas + (haut - bas) x position^p` : avec p = 2, le
debut de course est plus fin (2 a 3 fois) et la fin plus large, pour des reglages dont l'usage tient dans le bas de la plage (le halo). `curve` = 1 est le
curseur lineaire d'avant, a l'identique. Fiche : docs/fonctions/gui.md.
"""
from __future__ import annotations

import math


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _curve(curve: float) -> float:
    try:
        c = float(curve)
    except (TypeError, ValueError):
        return 1.0
    return c if math.isfinite(c) and c > 0.0 else 1.0


def _decimals(step: float) -> int | None:
    """Nombre de decimales d'un pas (0,01 -> 2, 0,05 -> 2, 1 -> 0) ; None sans pas (pas d'arrondi)."""
    try:
        s = float(step)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s) or s <= 0.0:
        return None
    return max(0, -int(math.floor(math.log10(s))))


def to_value(pos: float, lo: float, hi: float, curve: float = 1.0, step: float = 0.0) -> float:
    """Valeur du reglage pour la position `pos` du curseur (bornee a 0..1 ; NaN = 0). `step` > 0 : arrondie a la precision de ce pas (0,01 -> 2 decimales)."""
    try:
        p = float(pos)
    except (TypeError, ValueError):
        p = 0.0
    p = _clamp01(p) if math.isfinite(p) else 0.0
    value = lo + (hi - lo) * p ** _curve(curve)
    d = _decimals(step)
    return round(value, d) if d is not None else value


def to_pos(value: float, lo: float, hi: float, curve: float = 1.0) -> float:
    """Position 0..1 du curseur pour une valeur du reglage (hors plage ou NaN : bornee ; plage nulle : 0). Inverse de `to_value` sans arrondi."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v) or hi <= lo:
        return 0.0
    return _clamp01((v - lo) / (hi - lo)) ** (1.0 / _curve(curve))
