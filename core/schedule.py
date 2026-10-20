"""Planning de la soiree : une ligne par passage (« 22:30 NOM DU DJ »), le texte du logo change tout seul a l'heure dite.

Python pur (stdlib seulement) : l'heure est injectee, tout se teste sans horloge. Une soiree passe minuit : les heures avant `DAY_START`
(18 h par defaut) comptent comme apres minuit (« 01:00 » vient apres « 23:00 »).

Format d'une ligne : `HH:MM texte`, `HHhMM texte` ou `HH:MM - texte` ; `\\n` dans le texte fait un retour a la ligne ; les lignes qui
ne commencent pas par une heure valide sont ignorees (commentaires, lignes vides).
"""
from __future__ import annotations

import re

DAY_START = 18 * 60                     # minutes : l'heure a partir de laquelle commence la « journee » de la soiree
_LINE = re.compile(r"^\s*(\d{1,2})\s*[:hH]\s*(\d{2})\s*(?:[-–—]\s*)?(.*?)\s*$")


def parse(text: str) -> list[tuple[int, str]]:
    """Lignes du planning -> [(minute du jour, texte)], dans l'ordre de la soiree (apres minuit a la suite). Heure ou texte invalide : ligne ignoree."""
    entries: list[tuple[int, str]] = []
    for line in str(text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        hour, minute, label = int(m.group(1)), int(m.group(2)), m.group(3).replace("\\n", "\n")
        if hour > 23 or minute > 59 or not label:
            continue
        entries.append((hour * 60 + minute, label))
    entries.sort(key=lambda e: _key(e[0]))
    return entries


def _key(minute: int, day_start: int = DAY_START) -> int:
    return minute if minute >= day_start else minute + 1440


def current(entries: list[tuple[int, str]], now_minute: int, day_start: int = DAY_START) -> str | None:
    """Le texte en cours : celui du dernier passage deja commence. None avant le premier passage."""
    now = _key(now_minute, day_start)
    best = None
    for minute, label in entries:
        if _key(minute, day_start) <= now:
            best = label
    return best


def upcoming(entries: list[tuple[int, str]], now_minute: int, day_start: int = DAY_START) -> tuple[int, str] | None:
    """Le prochain passage (minute du jour, texte), ou None s'il n'y en a plus."""
    now = _key(now_minute, day_start)
    for minute, label in entries:
        if _key(minute, day_start) > now:
            return minute, label
    return None


def clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"
