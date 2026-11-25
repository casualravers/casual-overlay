"""Recettes de la planche de preuves (#195) : un module par fonctionnalite, qui expose `RECETTE`.

Une recette decrit des cas a rendre avec le vrai moteur (voir tools/planche.py) :
- `Cas(etiquette, reglages, instant, zoom)` : `reglages` s'ajoutent a la base de la recette ; `instant` = secondes apres l'origine (le depart de
  la recette, ou ce que rend `attente`) ; `zoom` = zone (x0, y0, x1, y1) de l'image rendue, agrandie x3 dans `<nom>_zoom.png`, ou None ;
- `Recette(nom, texte, taille, base, cas, attente)` : `texte` = le texte du logo, `taille` = taille du rendu, `base` = reglages communs
  (poses sur DEFAULT_PARAMS) ; `attente(renderer, dessiner)` prepare le rendu (ex. attendre le motif du texte) et rend l'instant d'origine, ou None.
"""
from __future__ import annotations

from typing import Callable, NamedTuple


class Cas(NamedTuple):
    etiquette: str
    reglages: dict
    instant: float
    zoom: tuple[int, int, int, int] | None


class Recette(NamedTuple):
    nom: str
    texte: str
    taille: tuple[int, int]
    base: dict
    cas: list
    attente: Callable | None
