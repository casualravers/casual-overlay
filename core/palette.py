"""Palette de commandes (Ctrl+K) : filtre et classe une liste de commandes d'apres ce que l'operateur tape.

Python pur (stdlib seulement). Chaque commande a un libelle (« Scene : Neon ») et des mots-cles facultatifs. La requete est coupee en mots ;
tous doivent se retrouver (sans accents ni casse) dans le libelle ou les mots-cles. Meilleur classement : debut du libelle, debut d'un mot,
puis simple sous-chaine ; a egalite l'ordre d'origine (stable) est garde.
"""
from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Minuscules sans accents : « Scène Néon » -> « scene neon »."""
    return "".join(ch for ch in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(ch) != "Mn")


def score(query: str, label: str, keywords: str = "") -> int | None:
    """Plus petit = meilleur ; None si un mot de la requete ne se trouve ni dans le libelle ni dans les mots-cles."""
    words = fold(query).split()
    if not words:
        return 0
    lab, hay = fold(label), fold(label + " " + keywords)
    total = 0
    for w in words:
        if lab.startswith(w):
            total += 0
        elif f" {w}" in " " + lab.replace(":", " ").replace("/", " "):
            total += 1
        elif w in lab:
            total += 2
        elif w in hay:
            total += 3
        else:
            return None
    return total


def rank(query: str, items: list[tuple[str, str]], limit: int | None = None) -> list[int]:
    """Indices de `items` ([(libelle, mots-cles)]) qui correspondent a `query`, du meilleur au moins bon (ordre d'origine a egalite)."""
    scored = []
    for i, (label, keywords) in enumerate(items):
        sc = score(query, label, keywords)
        if sc is not None:
            scored.append((sc, i))
    scored.sort()
    out = [i for _sc, i in scored]
    return out[:limit] if limit else out
